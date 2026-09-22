"""Git worktree isolation and patch application.

Each experiment gets its own worktree checked out from the parent commit, so many
experiments can be prepared and tested without touching each other or the user's
checkout. Locked paths are enforced here in code, never merely in the prompt.
"""
from __future__ import annotations

import fnmatch
import functools
import io
import os
import shutil
import subprocess
import tarfile
from pathlib import Path, PurePosixPath, PureWindowsPath

from hotpath.schema import Edit

GIT_ENV = {
    "GIT_AUTHOR_NAME": "hotpath", "GIT_AUTHOR_EMAIL": "hotpath@localhost",
    "GIT_COMMITTER_NAME": "hotpath", "GIT_COMMITTER_EMAIL": "hotpath@localhost",
}


class PatchError(Exception):
    """The edit could not be applied (bad path, search text not found, ambiguous)."""


class LockedFileError(PatchError):
    """The edit targeted a locked or non-editable path."""


@functools.lru_cache(maxsize=64)
def _eol_options(cwd: str) -> tuple[str, ...]:
    """Line-ending settings, re-supplied because `GIT_CONFIG_NOSYSTEM` drops them.

    Git for Windows ships `core.autocrlf=true` in its *system* config. A checkout made by ordinary
    git therefore has CRLF in the working tree while the index holds LF. Hotpath's own git calls
    disable the system config, so without this they judge every rewritten file to be modified —
    and `ensure_repo` then refuses a repository that is, to everyone else, perfectly clean.

    Unlike hooks, filters and external diffs, these two settings only choose which bytes land on
    disk; they cannot run anything. The value is matched against a closed set before it is
    forwarded, so untrusted config still cannot inject an option.
    """
    options: list[str] = []
    for key, allowed in (("core.autocrlf", ("true", "false", "input")), ("core.eol", ("lf", "crlf", "native"))):
        try:
            res = subprocess.run(["git", "-c", f"safe.directory={cwd}", "config", "--get", key],
                                 cwd=cwd, capture_output=True, text=True)
        except OSError:
            return ()
        value = res.stdout.strip().lower()
        if value in allowed:
            options += ["-c", f"{key}={value}"]
    return tuple(options)


def _git(args: list[str], cwd: Path, *, strip: bool = True) -> str:
    # Never execute repository-provided hooks, fsmonitor, filters, or external diffs
    # on the orchestrator host. Repository-local config is untrusted input.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_ENV)
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_TERMINAL_PROMPT": "0", "GIT_ALLOW_PROTOCOL": ""})
    # Git may see a target created by another container user as "dubious
    # ownership". Trust only the exact directory this invocation operates in,
    # never a wildcard or the target's own local config.
    options = ["-c", f"safe.directory={cwd.resolve()}",
               "-c", f"core.hooksPath={os.devnull}", "-c", "core.fsmonitor=false",
               "-c", "core.untrackedCache=false",
               "-c", "core.attributesFile=" + os.devnull,
               *_eol_options(str(cwd.resolve()))]
    try:
        config = subprocess.run(["git", "-c", f"safe.directory={cwd.resolve()}", "config", "--local", "--name-only", "--get-regexp", "^filter\\."],
                                cwd=str(cwd), capture_output=True, text=True, env=env)
    except FileNotFoundError as exc:
        raise RuntimeError("git executable was not found; install Git and ensure it is on PATH") from exc
    except OSError as exc:
        raise RuntimeError(f"could not start git in {cwd}: {exc}") from exc
    for key in config.stdout.splitlines():
        # Override every command-bearing filter attribute (and required).
        options += ["-c", key + ("=false" if key.endswith(".required") else "=")]
    if args and args[0] == "diff":
        args = ["diff", "--no-ext-diff", *args[1:]]
    try:
        res = subprocess.run(["git", *options, *args], cwd=str(cwd), capture_output=True, text=True, env=env)
    except FileNotFoundError as exc:
        raise RuntimeError("git executable was not found; install Git and ensure it is on PATH") from exc
    except OSError as exc:
        raise RuntimeError(f"could not start git in {cwd}: {exc}") from exc
    if res.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed in {cwd}: {res.stderr.strip()}")
    return res.stdout.strip() if strip else res.stdout


def path_allowed(rel: str, editable: list[str], locked: list[str]) -> tuple[bool, str]:
    p = PurePosixPath(rel)
    if (not rel or "\\" in rel or ":" in rel or "\x00" in rel
            or p.is_absolute() or PureWindowsPath(rel).drive or ".." in p.parts):
        return False, f"path escapes the repository: {rel}"
    for pat in locked:
        if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(p.name, pat):
            return False, f"'{rel}' matches locked pattern '{pat}'"
    for pat in editable:
        if fnmatch.fnmatch(rel, pat):
            return True, ""
    return False, f"'{rel}' does not match any editable pattern {editable}"


def safe_target_path(repo: Path, rel: str) -> Path:
    """Validate portable relative syntax and resolved containment before any access."""
    ok, why = path_allowed(rel, ["*"], [])
    if not ok:
        raise LockedFileError(why)
    root = repo.resolve()
    current = root
    for component in PurePosixPath(rel).parts:
        current = current / component
        if current.is_symlink():
            raise LockedFileError(f"symlinked source paths are not editable: {rel}")
    result = (root / rel).resolve()
    if not result.is_relative_to(root):
        raise LockedFileError(f"path escapes the repository through a symlink: {rel}")
    return result


class DirtyRepoError(RuntimeError):
    """The target has uncommitted changes and the caller did not ask to snapshot them."""


class Workspace:
    def __init__(self, target: Path, workdir: Path):
        self.target = target.resolve()
        self.workdir = workdir if workdir.is_absolute() else (self.target / workdir)
        if not self.workdir.resolve().is_relative_to(self.target):
            raise LockedFileError("Hotpath workdir must stay inside the target repository")
        self.workdir = self.workdir.resolve()
        self.worktrees_dir = self.workdir / "worktrees"

    def _check_workdir(self) -> None:
        for path in (self.workdir, self.worktrees_dir):
            if path.is_symlink() or not path.resolve().is_relative_to(self.target):
                raise LockedFileError(f"Hotpath workspace path escapes target: {path}")

    def _ignore_workdir(self) -> None:
        git_dir = self.target / ".git"
        if git_dir.is_symlink() or not git_dir.is_dir():
            raise LockedFileError("target .git must be a real directory inside the target")
        exclude = git_dir / "info" / "exclude"
        if not exclude.resolve().is_relative_to(git_dir.resolve()) or exclude.is_symlink():
            raise LockedFileError("unsafe Git exclude path")
        rel = self.workdir.relative_to(self.target).as_posix()
        marker = f"/{rel}/"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        existing = exclude.read_text() if exclude.exists() else ""
        if marker not in existing.splitlines():
            with exclude.open("a") as f:
                f.write("\n" + marker + "\n")

    # -- repository state ---------------------------------------------------
    def ensure_repo(self, autocommit: bool = False) -> str:
        """Make sure the target is a git repo with at least one commit. Returns HEAD sha.

        Uncommitted changes are refused unless `autocommit` (or HOTPATH_AUTOCOMMIT=1) asks for them
        to be snapshotted into a commit: Hotpath measures committed code only, and silently
        committing someone's work is not a tool's call to make."""
        if (self.target / ".git").is_symlink() or (self.target / ".git").is_file():
            raise LockedFileError("target .git cannot be a symlink or external gitdir file")
        fresh = not (self.target / ".git").exists()
        if fresh:
            _git(["init", "-q"], self.target)
        self._check_workdir()
        self._ignore_workdir()
        has_commit = subprocess.run(["git", "rev-parse", "-q", "--verify", "HEAD"], cwd=self.target,
                                    capture_output=True).returncode == 0
        status = _git(["status", "--porcelain"], self.target)
        autocommit = autocommit or os.environ.get("HOTPATH_AUTOCOMMIT") == "1"
        if has_commit and status and not autocommit:
            lines = status.splitlines()
            shown = "\n".join("  " + line for line in lines[:10])
            if len(lines) > 10:
                shown += f"\n  ... and {len(lines) - 10} more"
            raise DirtyRepoError(
                f"target repository {self.target} has uncommitted changes:\n{shown}\n"
                "Hotpath measures committed code only. Either commit or stash them "
                f"(git -C \"{self.target}\" stash), or rerun with --autocommit to snapshot them into a commit first.")
        if not has_commit or status:
            if fresh:
                gitignore = self.target / ".gitignore"
                if gitignore.is_symlink():
                    raise LockedFileError("target .gitignore is a symlink")
                # Append only what is missing. Appending the whole block unconditionally leaves a
                # duplicate every time a fresh run touches a repository that already has these.
                present = gitignore.read_text().splitlines() if gitignore.exists() else []
                missing = [ln for ln in (".hotpath/", "__pycache__/", ".env", ".env.*") if ln not in present]
                if missing:
                    with gitignore.open("a") as f:
                        f.write("\n" + "\n".join(missing) + "\n")
            _git(["add", "-A"], self.target)
            _git(["commit", "-q", "-m", "hotpath: snapshot before optimization run", "--allow-empty"], self.target)
        self.workdir.mkdir(parents=True, exist_ok=True)
        return self.head()

    def head(self) -> str:
        return _git(["rev-parse", "HEAD"], self.target)

    def current_branch(self) -> str:
        """The checked-out branch name, or "" when HEAD is detached."""
        try:
            return _git(["symbolic-ref", "-q", "--short", "HEAD"], self.target)
        except RuntimeError:
            return ""

    # -- worktrees ----------------------------------------------------------
    def create_worktree(self, commit: str, name: str) -> Path:
        self._check_workdir()
        # Worktree names are generated identifiers. Reject path syntax before
        # constructing the path so cleanup cannot escape the managed directory.
        if (not name or name in {".", ".."} or "/" in name or "\\" in name
                or Path(name).name != name or "\x00" in name):
            raise LockedFileError(f"invalid worktree name: {name!r}")
        self.worktrees_dir.mkdir(parents=True, exist_ok=True)
        path = self.worktrees_dir / name
        if path.exists():
            self.remove_worktree(path)
        _git(["worktree", "add", "-q", "--detach", str(path), commit], self.target)
        return path

    def remove_worktree(self, path: Path) -> None:
        path = Path(path)
        if not path.resolve().is_relative_to(self.worktrees_dir.resolve()):
            raise LockedFileError(f"worktree path escapes managed directory: {path}")
        try:
            _git(["worktree", "remove", "--force", str(path)], self.target)
        except RuntimeError:
            shutil.rmtree(path, ignore_errors=True)
            try:
                _git(["worktree", "prune"], self.target)
            except RuntimeError:
                pass

    def cleanup(self) -> None:
        self._check_workdir()
        if self.worktrees_dir.exists():
            for p in self.worktrees_dir.iterdir():
                self.remove_worktree(p)

    # -- patching -----------------------------------------------------------
    def apply_edits(self, worktree: Path, edits: list[Edit], editable: list[str], locked: list[str]) -> tuple[list[str], str]:
        if not edits:
            raise PatchError("model returned no edits")
        # Every path first: an edit to a locked file is reported as locked_file even when another
        # edit in the same patch is also malformed.
        for e in edits:
            ok, why = path_allowed(e.file, editable, locked)
            if not ok:
                raise LockedFileError(why)
            safe_target_path(worktree, e.file)
        # Then apply every edit, in order, to an in-memory copy, so each search is checked against
        # the text it will actually be applied to and nothing is written unless all of them succeed.
        contents: dict[str, str] = {}
        crlf: dict[str, bool] = {}
        created: list[str] = []
        for e in edits:
            f = safe_target_path(worktree, e.file)
            if e.search == "":
                if e.file in contents or f.exists():
                    raise PatchError(f"cannot create {e.file}: it already exists (an empty search creates a new file)")
                if not e.replace.strip():
                    raise PatchError(f"new file {e.file} would be empty")
                contents[e.file] = e.replace
                created.append(e.file)
                continue
            if e.file not in contents:
                if not f.is_file():
                    raise PatchError(f"file does not exist: {e.file} (use an empty search to create it)")
                # Match on "\n" (what models write) but remember the file's own line endings, so an
                # edit never rewrites every line of a file -- which on Windows text mode otherwise would.
                with f.open(encoding="utf-8", newline="") as fh:
                    raw = fh.read()
                crlf[e.file] = "\r\n" in raw
                contents[e.file] = raw.replace("\r\n", "\n")
            if not e.search.strip():
                raise PatchError(f"empty search block for {e.file}")
            if e.search == e.replace:
                raise PatchError(f"edit to {e.file} is a no-op (search equals replace): no change")
            n = contents[e.file].count(e.search)
            if n == 0:
                raise PatchError(f"search text not found in {e.file}: {e.search[:80]!r}")
            if n > 1:
                raise PatchError(f"search text is ambiguous ({n} matches) in {e.file}")
            contents[e.file] = contents[e.file].replace(e.search, e.replace, 1)
        for rel, text in contents.items():
            f = safe_target_path(worktree, rel)
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text.replace("\n", "\r\n") if crlf.get(rel) else text, encoding="utf-8", newline="")
        if created:
            # Intent-to-add puts new files in the index without staging their content, so they appear
            # in the recorded diff and in isolated execution, which stages tracked files only.
            _git(["add", "--intent-to-add", "--", *created], worktree)
        diff = _git(["diff", "--no-color"], worktree)
        if not diff.strip():
            raise PatchError("edits produced no change to the source")
        files = _git(["diff", "--name-only"], worktree).splitlines()
        return files, diff

    def commit(self, worktree: Path, exp_id: str, message: str) -> str:
        _git(["add", "-A"], worktree)
        _git(["commit", "-q", "-m", message], worktree)
        sha = _git(["rev-parse", "HEAD"], worktree)
        # Keep the commit reachable after the worktree is removed. A private ref namespace, not a
        # branch: a run makes dozens of these, and they must not clutter the user's `git branch`.
        _git(["update-ref", f"refs/hotpath/experiments/{exp_id}", sha], self.target)
        return sha

    def has_commit(self, sha: str) -> bool:
        try:
            _git(["cat-file", "-e", f"{sha}^{{commit}}"], self.target)
            return True
        except RuntimeError:
            return False

    def materialize(self, commit: str, name: str = "_head") -> Path:
        """A full checkout of `commit` at a stable worktree path, refreshed each call.

        The agent must see the *current* accepted source, not the pristine target: once a
        change is accepted, later hypotheses edit on top of it, so their search/replace
        anchors must match the edited file, not the original.
        """
        return self.create_worktree(commit, name)

    def checkout_into(self, commit: str, dest: Path) -> None:
        """Export a commit's tree to a plain directory (used to hand results to the user)."""
        dest = dest.resolve()
        dest.mkdir(parents=True, exist_ok=True)
        try:
            archive = subprocess.run(["git", "archive", commit], cwd=self.target,
                                     capture_output=True, check=True)
        except FileNotFoundError as exc:
            raise PatchError("cannot export checkout: git executable was not found") from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or b"").decode(errors="replace").strip()
            raise PatchError(f"cannot export commit {commit}: {detail or 'git archive failed'}") from exc
        except OSError as exc:
            raise PatchError(f"cannot export checkout: {exc}") from exc
        with tarfile.open(fileobj=io.BytesIO(archive.stdout), mode="r:") as tar:
            for member in tar:
                path = safe_target_path(dest, member.name)
                if member.isdir():
                    path.mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    source = tar.extractfile(member)
                    if source is None:
                        raise PatchError(f"archive member has no data: {member.name}")
                    with source, path.open("wb") as output:
                        shutil.copyfileobj(source, output)
                    path.chmod(0o755 if member.mode & 0o111 else 0o644)
                else:
                    raise PatchError(f"unsafe archive member type: {member.name}")

    def diff_commits(self, base: str, head: str) -> str:
        """Unified diff from `base` to `head` (the accepted change set)."""
        return _git(["diff", "--no-color", base, head], self.target)
