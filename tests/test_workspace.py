from pathlib import Path
import subprocess

import pytest

from hotpath.schema import Edit
from hotpath.workspace import LockedFileError, PatchError, Workspace, path_allowed

E = ["*.py"]
L = ["tests/*", "bench.py"]


def test_path_rules():
    assert path_allowed("mod.py", E, L)[0]
    assert not path_allowed("tests/check.py", E, L)[0]
    assert not path_allowed("bench.py", E, L)[0]
    assert not path_allowed("../outside.py", E, L)[0]
    assert not path_allowed("/etc/passwd", E, L)[0]
    assert not path_allowed("notes.txt", E, L)[0]


def test_worktrees_are_isolated(ws: Workspace, tiny_repo: Path):
    head = ws.head()
    a = ws.create_worktree(head, "a")
    b = ws.create_worktree(head, "b")
    (a / "mod.py").write_text("changed = 1\n")
    assert (b / "mod.py").read_text() != "changed = 1\n"
    assert (tiny_repo / "mod.py").read_text() != "changed = 1\n", "user checkout must never change"
    ws.remove_worktree(a)
    ws.remove_worktree(b)
    assert not a.exists() and not b.exists()


def test_checkout_into_supports_destination_with_spaces(ws: Workspace, tmp_path):
    dest = tmp_path / "export with spaces" / "optimized source"
    ws.checkout_into(ws.head(), dest)
    assert (dest / "mod.py").is_file()
    assert "def work" in (dest / "mod.py").read_text()


def test_worktree_name_cannot_escape_managed_directory(ws: Workspace):
    with pytest.raises(LockedFileError, match="invalid worktree name"):
        ws.create_worktree(ws.head(), "..\\outside")


def test_missing_git_has_actionable_error(ws, monkeypatch):
    import hotpath.workspace as workspace

    def missing(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(workspace.subprocess, "run", missing)
    with pytest.raises(RuntimeError, match="git executable was not found"):
        workspace._git(["status"], ws.target)


def test_git_trusts_only_the_exact_worktree_path(ws, monkeypatch):
    import hotpath.workspace as workspace

    commands = []

    def capture(args, **kwargs):
        commands.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(workspace.subprocess, "run", capture)
    workspace._git(["status", "--porcelain"], ws.target)
    expected = f"safe.directory={ws.target.resolve()}"
    assert len(commands) == 2
    assert all(expected in command for command in commands)
    assert all("safe.directory=*" not in command for command in commands)


def test_archive_failure_has_actionable_export_error(ws, tmp_path, monkeypatch):
    import hotpath.workspace as workspace

    def failed(*args, **kwargs):
        raise workspace.subprocess.CalledProcessError(128, args[0], stderr=b"bad commit")

    monkeypatch.setattr(workspace.subprocess, "run", failed)
    with pytest.raises(PatchError, match="bad commit"):
        ws.checkout_into("missing-commit", tmp_path / "export")


def test_apply_edits_and_commit_survive_worktree_removal(ws: Workspace):
    head = ws.head()
    wt = ws.create_worktree(head, "x")
    files, diff = ws.apply_edits(wt, [Edit(file="mod.py", search="    out = []\n", replace="    out = []\n    seen = set()\n")], E, L)
    assert files == ["mod.py"] and "+    seen = set()" in diff
    sha = ws.commit(wt, "exp_x", "test")
    ws.remove_worktree(wt)
    wt2 = ws.create_worktree(sha, "y")  # commit is still reachable via refs/hotpath/experiments/exp_x
    assert "seen = set()" in (wt2 / "mod.py").read_text()
    ws.remove_worktree(wt2)


def test_diff_ignores_repository_external_diff_command(ws: Workspace):
    from hotpath.workspace import _git
    wt = ws.create_worktree(ws.head(), "diff-safe")
    _git(["config", "--local", "diff.external", "false"], wt)
    files, diff = ws.apply_edits(wt, [Edit(file="mod.py", search="    out = []\n",
                        replace="    out = []\n    seen = set()\n")], E, L)
    assert files == ["mod.py"] and "+    seen = set()" in diff
    ws.remove_worktree(wt)


def test_existing_repo_workdir_is_ignored_without_editing_user_gitignore(ws: Workspace, tiny_repo: Path):
    from hotpath.workspace import _git
    ws.ensure_repo()
    assert _git(["status", "--porcelain"], tiny_repo) == ""
    assert "/.hotpath/" in (tiny_repo / ".git" / "info" / "exclude").read_text()


def test_symlink_to_locked_file_cannot_be_edited(ws: Workspace):
    wt = ws.create_worktree(ws.head(), "locked-link")
    link = wt / "alias.py"
    try:
        link.symlink_to(wt / "tests" / "check.py")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    before = (wt / "tests" / "check.py").read_text()
    with pytest.raises(LockedFileError):
        ws.apply_edits(wt, [Edit(file="alias.py", search="print('ok')", replace="pass")], E, L)
    assert (wt / "tests" / "check.py").read_text() == before
    ws.remove_worktree(wt)


def test_export_rejects_archive_symlinks(ws: Workspace, tmp_path, monkeypatch):
    import io
    import tarfile
    from types import SimpleNamespace
    import hotpath.workspace as workspace
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w") as tar:
        entry = tarfile.TarInfo("escape")
        entry.type = tarfile.SYMTYPE
        entry.linkname = str(tmp_path)
        tar.addfile(entry)
    monkeypatch.setattr(workspace.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout=archive.getvalue()))
    with pytest.raises(PatchError, match="unsafe archive"):
        ws.checkout_into("unused", tmp_path / "export")


@pytest.mark.parametrize("edit,exc,msg", [
    (Edit(file="tests/check.py", search="assert", replace="pass #"), LockedFileError, "locked"),
    (Edit(file="bench.py", search="trials=8", replace="trials=1"), LockedFileError, "locked"),
    (Edit(file="mod.py", search="this text does not exist", replace="x"), PatchError, "not found"),
    (Edit(file="mod.py", search="out", replace="o"), PatchError, "ambiguous"),
    (Edit(file="missing.py", search="a", replace="b"), PatchError, "does not exist"),
    (Edit(file="mod.py", search="    out = []\n", replace="    out = []\n"), PatchError, "no change"),
])
def test_bad_edits_are_rejected_atomically(ws: Workspace, edit, exc, msg):
    wt = ws.create_worktree(ws.head(), "bad")
    before = (wt / "mod.py").read_text()
    with pytest.raises(exc) as ei:
        ws.apply_edits(wt, [Edit(file="mod.py", search="def work(n):", replace="def work(n):  # ok"), edit], E, L)
    assert msg in str(ei.value)
    assert (wt / "mod.py").read_text() == before, "a rejected batch must leave the file untouched"
    ws.remove_worktree(wt)


def test_no_edits_is_patch_error(ws: Workspace):
    wt = ws.create_worktree(ws.head(), "empty")
    with pytest.raises(PatchError):
        ws.apply_edits(wt, [], E, L)
    ws.remove_worktree(wt)

@pytest.mark.parametrize("rel", [r"..\outside.py", r"C:\outside.py", "C:outside.py", r"\\server\share\x.py", "./../x.py", "mod.py:stream", ""])
def test_portable_escape_rejected(rel):
    assert not path_allowed(rel, ["*"], [])[0]


def test_symlink_escape_rejected(tmp_path):
    from hotpath.workspace import safe_target_path
    repo = tmp_path / "repo"; repo.mkdir()
    outside = tmp_path / "secret.py"; outside.write_text("secret")
    try:
        (repo / "link.py").symlink_to(outside)
    except OSError:
        pytest.skip("platform does not permit creating symlinks")
    with pytest.raises(LockedFileError): safe_target_path(repo, "link.py")


def test_dirty_repo_not_automatically_committed(ws, tiny_repo, monkeypatch):
    monkeypatch.delenv("HOTPATH_AUTOCOMMIT", raising=False)
    before = ws.head()
    (tiny_repo / "mod.py").write_text("changed")
    with pytest.raises(RuntimeError, match="uncommitted") as err:
        ws.ensure_repo()
    assert ws.head() == before
    # The refusal names what is dirty and both ways out.
    assert "mod.py" in str(err.value) and "stash" in str(err.value) and "--autocommit" in str(err.value)


def test_autocommit_snapshots_dirty_changes_only_when_asked(ws, tiny_repo, monkeypatch):
    monkeypatch.delenv("HOTPATH_AUTOCOMMIT", raising=False)
    before = ws.head()
    (tiny_repo / "mod.py").write_text("changed")
    after = ws.ensure_repo(autocommit=True)
    assert after != before
    assert ws.ensure_repo() == after, "a clean tree needs no flag"


def test_repository_filter_never_executed(ws, tiny_repo):
    import subprocess
    from hotpath.workspace import _git
    marker = tiny_repo / "filter-ran"
    subprocess.run(["git", "config", "filter.bad.clean", "echo unsafe > filter-ran"], cwd=tiny_repo, check=True)
    (tiny_repo / ".gitattributes").write_text("*.py filter=bad\n")
    (tiny_repo / "mod.py").write_text("changed")
    _git(["add", "-A"], tiny_repo)
    assert not marker.exists()


# --------------------------------------------------------------------------- #
# Multi-file patches and new files
# --------------------------------------------------------------------------- #

HELPER = "def dedupe(n):\n    return len(set(range(n)))\n"
CALL_SITE = Edit(file="mod.py", search="def work(n):\n", replace="from helpers.fast import dedupe\n\n\ndef work(n):\n    return dedupe(n)\n")


def test_patch_can_create_a_module_and_edit_its_call_site(ws: Workspace):
    from hotpath.execution import stage_source
    wt = ws.create_worktree(ws.head(), "multi")
    files, diff = ws.apply_edits(wt, [Edit(file="helpers/fast.py", search="", replace=HELPER), CALL_SITE],
                                 E + ["helpers/*.py"], L)
    assert sorted(files) == ["helpers/fast.py", "mod.py"]
    assert "+def dedupe(n):" in diff and "+from helpers.fast import dedupe" in diff, "new file must be in the diff"
    # Isolated execution stages tracked files only; the new file must be among them.
    stage = wt.parent / "multi-stage"
    stage.mkdir()
    stage_source(wt, stage)
    assert (stage / "helpers" / "fast.py").read_text() == HELPER
    sha = ws.commit(wt, "exp_multi", "multi")
    ws.remove_worktree(wt)
    wt2 = ws.create_worktree(sha, "multi2")
    assert (wt2 / "helpers" / "fast.py").read_text() == HELPER
    ws.remove_worktree(wt2)


def test_later_edits_search_the_text_earlier_edits_produced(ws: Workspace):
    wt = ws.create_worktree(ws.head(), "chain")
    first = Edit(file="mod.py", search="    out = []\n", replace="    out = []\n    seen = set()\n")
    second = Edit(file="mod.py", search="    seen = set()\n", replace="    seen = set()  # fast path\n")
    ws.apply_edits(wt, [first, second], E, L)
    assert "seen = set()  # fast path" in (wt / "mod.py").read_text()
    ws.remove_worktree(wt)


@pytest.mark.parametrize("edits, error, message", [
    ([Edit(file="mod.py", search="", replace="x = 1\n")], PatchError, "already exists"),
    ([Edit(file="new.py", search="", replace="  \n")], PatchError, "would be empty"),
    ([Edit(file="new.py", search="x = 1", replace="x = 2")], PatchError, "use an empty search to create it"),
    ([Edit(file="tests/extra.py", search="", replace="assert True\n")], LockedFileError, "locked"),
    ([Edit(file="notes.txt", search="", replace="hi\n")], LockedFileError, "editable"),
    # A locked edit is reported as locked even when an earlier edit is also malformed.
    ([Edit(file="mod.py", search="not in the file", replace="x"),
      Edit(file="tests/check.py", search="assert", replace="assert True or")], LockedFileError, "locked"),
])
def test_bad_multi_file_patches_are_refused_before_anything_is_written(ws: Workspace, edits, error, message):
    wt = ws.create_worktree(ws.head(), "bad")
    before = (wt / "mod.py").read_text()
    with pytest.raises(error, match=message):
        ws.apply_edits(wt, edits, E, L)
    assert (wt / "mod.py").read_text() == before and not (wt / "new.py").exists()
    ws.remove_worktree(wt)


def test_failing_later_edit_leaves_no_new_file_behind(ws: Workspace):
    wt = ws.create_worktree(ws.head(), "atomic")
    with pytest.raises(PatchError, match="not found"):
        ws.apply_edits(wt, [Edit(file="helpers/fast.py", search="", replace=HELPER),
                            Edit(file="mod.py", search="not in the file", replace="x")], E + ["helpers/*.py"], L)
    assert not (wt / "helpers").exists()
    ws.remove_worktree(wt)


@pytest.mark.parametrize("eol", ["\n", "\r\n"])
def test_edits_keep_the_files_line_endings_and_utf8(ws: Workspace, eol: str):
    """An edit changes only the lines it names. Text-mode writes on Windows used to turn every LF into
    CRLF, so a one-line optimization showed up in its PR as a whole-file rewrite."""
    wt = ws.create_worktree(ws.head(), "eol")
    source = eol.join(["# café: ünïcode survives", "def f(x):", "    return x + 1", "", "def g():", "    return 2", ""])
    (wt / "u.py").write_bytes(source.encode("utf-8"))
    ws.commit(wt, "eol_base", "add u.py")
    files, diff = ws.apply_edits(wt, [Edit(file="u.py", search="def f(x):\n    return x + 1\n",
                                           replace="def f(x):\n    return x + 2  # faster → better\n")],
                                 ["*.py"], [])
    after = (wt / "u.py").read_bytes()
    assert after == source.replace("x + 1", "x + 2  # faster → better").encode("utf-8")
    assert after.count(b"\r\n") == (6 if eol == "\r\n" else 0)
    ws.remove_worktree(wt)


def test_crlf_checkout_is_not_reported_as_dirty(tmp_path):
    """A repository checked out by ordinary git on Windows has CRLF in the working tree and LF in
    the index, because `core.autocrlf=true` lives in Git for Windows' *system* config. Hotpath's
    git calls disable the system config, so unless the setting is forwarded they judge every
    rewritten file modified and `ensure_repo` refuses a repository everyone else calls clean."""
    from hotpath.workspace import _eol_options, _git

    repo = tmp_path / "repo"
    repo.mkdir()
    plain = lambda *a: subprocess.run(["git", *a], cwd=repo, capture_output=True, text=True, check=True)
    plain("init", "-q", "-b", "main")
    plain("config", "user.name", "t")
    plain("config", "user.email", "t@t")
    plain("config", "core.autocrlf", "true")           # what Git for Windows sets system-wide
    (repo / "notes.txt").write_bytes(b"alpha\nbeta\n")  # committed as LF
    plain("add", "-A")
    plain("commit", "-q", "-m", "initial")
    (repo / "notes.txt").unlink()
    plain("checkout", "--", "notes.txt")                 # re-materialise: working tree becomes CRLF
    assert (repo / "notes.txt").read_bytes() == b"alpha\r\nbeta\r\n", "precondition: CRLF working tree"

    assert "-c" in _eol_options(str(repo.resolve())), "the autocrlf setting should be forwarded"
    assert _git(["status", "--porcelain"], repo) == "", "a CRLF checkout is clean, not modified"


def test_gitignore_entries_are_not_appended_twice(tmp_path):
    """Initialising a not-yet-versioned target used to append the whole ignore block unconditionally,
    so a directory already listing `.hotpath/` and `__pycache__/` ended up listing them twice."""
    target = tmp_path / "target"
    target.mkdir()
    (target / "mod.py").write_text("x = 1\n")
    gitignore = target / ".gitignore"
    gitignore.write_text(".hotpath/\n__pycache__/\n")
    Workspace(target, Path(".hotpath")).ensure_repo()
    lines = [ln for ln in gitignore.read_text().splitlines() if ln.strip()]
    assert lines.count(".hotpath/") == 1 and lines.count("__pycache__/") == 1
    assert ".env" in lines and ".env.*" in lines      # what was genuinely missing still gets added
