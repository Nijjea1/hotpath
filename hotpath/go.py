"""`hotpath go <repo>`: from a repository URL to a verified pull request in one command.

    [1/8] Setup      tools, GitHub access, API keys
    [2/8] Fetch      clone (or use a local checkout), find the default branch, check push access
    [3/8] Assess     static look at the repo: ecosystem, tests, benchmark, editable vs locked files
    [4/8] Baseline   install dependencies (never the project), run the tests 3 times: green and not flaky
    [5/8] Benchmark  existing, generated from the tests' hot paths and validated, or the test suite itself
    [6/8] Configure  commit .hotpath.yaml + benchmark + CI check on a setup branch (marked Hotpath-Setup)
    [7/8] Optimize   the normal search, with token and time budgets
    [8/8] Publish    one commit per verified change, draft PR, opened in the browser

Every stage either finishes or stops with the reason and the next step. Nothing is pushed without a
confirmation (or --yes), and nothing is ever pushed to the default branch.
"""
from __future__ import annotations

import asyncio
import getpass
import json
import os
import re
import shutil
import subprocess
import sys
import time
import webbrowser
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from hotpath import observability as obs
from hotpath.assess import GENERATED_FILES, Assessment, assess
from hotpath.config import CONFIG_NAMES, hotpath_home, load_config
from hotpath.schema import TERMINAL_STATUSES

STAGES = ["Setup", "Fetch", "Assess", "Baseline", "Benchmark", "Configure", "Optimize", "Publish"]
_GH_URL = re.compile(r"^(?:https?://github\.com/|git@github\.com:)([\w.-]+)/([\w.-]+?)(?:\.git)?/?$")
_SHORTHAND = re.compile(r"^([\w.-]+)/([\w.-]+)$")
_PYTEST_SUMMARY = re.compile(r"(\d+) passed")
_ASCII = str.maketrans({"✓": "ok", "·": "|", "×": "x", "…": "...", "≥": ">=", "–": "-"})


class GoStop(Exception):
    """A stage cannot continue. `ok` marks an honest non-failure (for example, no verified speedup)."""

    def __init__(self, message: str, ok: bool = False):
        super().__init__(message)
        self.ok = ok


@dataclass
class GoOptions:
    target: str
    yes: bool = False
    provider: Optional[str] = None            # openai | mock (default: openai)
    mock_patches: Optional[str] = None
    iterations: int = 3
    candidates: int = 3
    beam: int = 1
    max_tokens: Optional[int] = None
    max_minutes: Optional[float] = None
    sandbox: str = "auto"                     # auto | local | docker
    test_cmd: Optional[str] = None
    bench_cmd: Optional[str] = None
    no_generate: bool = False
    test_runs: int = 3
    test_timeout: float = 900.0
    no_pr: bool = False
    pr_method: str = "auto"
    ready: bool = False                       # open a ready-for-review PR instead of a draft
    open_browser: bool = True
    dashboard: bool = False
    port: int = 8765
    workspaces: Optional[str] = None
    remote: str = "origin"
    resume: bool = False


@dataclass
class GoState:
    target: str = ""
    github: str = ""                          # owner/repo, when the remote is on GitHub
    base_branch: str = ""
    base_commit: str = ""
    original_branch: str = ""
    setup_branch: str = ""
    setup_commit: str = ""
    sandbox: str = ""
    venv: str = ""
    image: str = ""
    bench_kind: str = ""
    bench_description: str = ""
    run_id: str = ""
    pr_url: str = ""
    stages_done: list[str] = field(default_factory=list)


def _default_workspaces() -> Path:
    checkout = Path(__file__).resolve().parents[1]
    if (checkout / "pyproject.toml").is_file() and (checkout / "hotpath").is_dir():
        return checkout / "workspaces"
    return hotpath_home() / "workspaces"


def _git(args: list[str], cwd: Path, check: bool = True, timeout: float = 600) -> str:
    res = subprocess.run(["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=" + os.devnull, *args],
                         cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace",
                         timeout=timeout, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    if check and res.returncode != 0:
        raise GoStop(f"git {' '.join(args[:3])} failed: {(res.stderr or res.stdout).strip()[-800:]}")
    return res.stdout.strip()


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", text).strip("-.") or "target"


def parse_target(target: str) -> tuple[str, Optional[str], Optional[str]]:
    """(kind, clone_url, owner/repo). kind is 'local', 'github', or 'git'."""
    if Path(target).expanduser().exists():
        return "local", None, None
    m = _GH_URL.match(target)
    if m:
        return "github", f"https://github.com/{m.group(1)}/{m.group(2)}.git", f"{m.group(1)}/{m.group(2)}"
    m = _SHORTHAND.match(target)
    if m:
        return "github", f"https://github.com/{m.group(1)}/{m.group(2)}.git", f"{m.group(1)}/{m.group(2)}"
    if "://" in target or target.endswith(".git") or target.startswith("git@"):
        return "git", target, None
    raise GoStop(f"'{target}' is not a local directory, a GitHub URL, owner/repo, or a git URL")


class Go:
    def __init__(self, opts: GoOptions, out: Optional[Callable[[str], None]] = None,
                 ask: Optional[Callable[[str], str]] = None):
        self.o = opts
        self.out = out or self._emit
        self.ask = ask if ask is not None else (input if sys.stdin.isatty() else None)
        self.state = GoState()
        self.repo: Optional[Path] = None
        self.a: Optional[Assessment] = None
        self.tenv = None
        self.runner = None
        self.baseline_wt: Optional[Path] = None
        self.choice = None
        self.test_cmd = ""
        self.pr_method = "link"
        self.worker = "openai"
        self.can_pr = True
        self.pr_blocker = ""
        self.dashboard_proc: Optional[subprocess.Popen] = None
        self.existing_config = False
        self.previous_stages: list[str] = []
        self.test_seconds = 0.0
        self.bench_seconds = 0.0
        self.t0 = time.monotonic()

    # ------------------------------------------------------------------ ui
    def step(self, i: int, summary: str) -> None:
        name = STAGES[i - 1]
        self.out(f"[{i}/{len(STAGES)}] {name} {'.' * max(2, 12 - len(name))} {summary}")
        self.state.stages_done.append(name)
        self._save()

    def info(self, msg: str) -> None:
        self.out("      " + msg)

    def _emit(self, text: str) -> None:
        """Print, falling back to ASCII symbols on consoles that cannot encode them (cp1252 pipes)."""
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        try:
            text.encode(encoding)
        except (UnicodeEncodeError, LookupError):
            text = text.translate(_ASCII)
        print(text, flush=True)

    def confirm(self, question: str, default: bool = True) -> bool:
        if self.o.yes:
            return True
        if self.ask is None:
            return False  # never assume consent without a terminal; --yes exists for automation
        suffix = " [Y/n] " if default else " [y/N] "
        answer = (self.ask("      " + question + suffix) or "").strip().lower()
        return default if not answer else answer in ("y", "yes")

    def _state_dir(self) -> Path:
        assert self.repo is not None
        d = self.repo / ".hotpath" / "go"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _save(self) -> None:
        if self.repo is not None and (self.repo / ".git").is_dir():
            (self._state_dir() / "state.json").write_text(json.dumps(asdict(self.state), indent=2), encoding="utf-8")

    # ------------------------------------------------------------------ main
    def run(self) -> int:
        try:
            sys.stdout.reconfigure(errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
        obs.init_sentry()
        code = 0
        with obs.transaction("hotpath go", op="hotpath.go", target=self.o.target):
            try:
                for i, stage in enumerate([self.setup, self.fetch, self.assess, self.baseline, self.benchmark,
                                           self.configure, self.optimize, self.publish], start=1):
                    with obs.span("hotpath.go.stage", STAGES[i - 1]):
                        stage(i)
                self.out(f"\ndone in {self._elapsed()}.")
            except GoStop as e:
                self.out(("\n" if not e.ok else "\n") + ("stopped: " if not e.ok else "") + str(e))
                code = 0 if e.ok else 1
            except KeyboardInterrupt:
                self.out("\ninterrupted; rerun with --resume to continue")
                code = 130
            finally:
                self._cleanup()
        return code

    def _elapsed(self) -> str:
        s = int(time.monotonic() - self.t0)
        return f"{s // 60}m {s % 60:02d}s"

    def _cleanup(self) -> None:
        if self.dashboard_proc and self.dashboard_proc.poll() is None:
            self.dashboard_proc.terminate()
        if self.repo is not None and self.baseline_wt is not None and self.baseline_wt.exists():
            _git(["worktree", "remove", "--force", str(self.baseline_wt)], self.repo, check=False)
        if self.repo is not None and self.state.original_branch:
            current = _git(["rev-parse", "--abbrev-ref", "HEAD"], self.repo, check=False)
            if current != self.state.original_branch:
                _git(["checkout", "-q", self.state.original_branch], self.repo, check=False)
        self._save()

    # ------------------------------------------------------------------ 1
    def setup(self, i: int) -> None:
        from hotpath.github import GhClient, RepoRef, gh_binary, github_token
        if sys.version_info < (3, 11):
            raise GoStop("Hotpath needs Python 3.11 or newer")
        if not shutil.which("git"):
            raise GoStop("git is not installed: https://git-scm.com/downloads")
        parts = [f"Python {sys.version_info.major}.{sys.version_info.minor} ✓", "git ✓"]
        binary = gh_binary()
        if binary and GhClient(RepoRef("github.com", "x", "x"), binary).available():
            self.pr_method, gh = "gh", "gh ✓"
        elif github_token():
            self.pr_method, gh = "token", "GITHUB_TOKEN ✓"
        else:
            self.pr_method, gh = "link", "no gh/GITHUB_TOKEN (PR opens as a pre-filled link)"
        if self.o.pr_method != "auto":
            self.pr_method = self.o.pr_method
        parts.append(gh)
        provider = self.o.provider or "openai"
        if provider == "openai":
            self._ensure_key("OPENAI_API_KEY", "OpenAI (planner)", "https://platform.openai.com/api-keys")
            self.worker = "openai"
            if os.environ.get("BASETEN_API_KEY"):
                if self._key_works("BASETEN_API_KEY", "https://inference.baseten.co/v1"):
                    self.worker = "baseten"
                else:
                    self.info("BASETEN_API_KEY was rejected; workers will use OpenAI instead")
            parts.append("keys ✓" + (" (workers on Baseten)" if self.worker == "baseten" else ""))
        else:
            self.worker = "mock"
            parts.append("offline mock provider")
        if obs.enabled():
            parts.append("Sentry ✓")
        self.step(i, "  ".join(parts))

    def _ensure_key(self, name: str, label: str, where: str) -> None:
        if not os.environ.get(name):
            if self.ask is None or self.o.yes:
                raise GoStop(f"{name} is not set. Get a key at {where}, then `set {name}=...` "
                             "(or put it in .env), or run offline with --provider mock")
            value = getpass.getpass(f"      {label} API key ({name}): ").strip()
            if not value:
                raise GoStop(f"{name} is required")
            os.environ[name] = value
            env_file = Path.cwd() / ".env"
            with env_file.open("a", encoding="utf-8") as f:
                f.write(f"\n{name}={value}\n")
            self.info(f"saved {name} to {env_file} (git-ignored)")
        if not self._key_works(name, None):
            raise GoStop(f"{name} was rejected by the API; check the key at {where}")

    @staticmethod
    def _key_works(name: str, base_url: Optional[str]) -> bool:
        try:
            from openai import APIConnectionError, AuthenticationError, OpenAI
        except ImportError:
            return True
        try:
            OpenAI(api_key=os.environ[name], base_url=base_url, timeout=20, max_retries=0).models.list()
            return True
        except AuthenticationError:
            return False
        except (APIConnectionError, Exception):  # offline or rate limited: do not block on the check itself
            return True

    # ------------------------------------------------------------------ 2
    def fetch(self, i: int) -> None:
        kind, url, gh = parse_target(self.o.target)
        ws_root = Path(self.o.workspaces) if self.o.workspaces else _default_workspaces()
        if kind == "local":
            repo = Path(self.o.target).expanduser().resolve()
            if not (repo / ".git").is_dir():
                raise GoStop(f"{repo} is not a git repository (run `git init` and commit first)")
            self.repo = repo
            self._load_state()
            dirty = _git(["status", "--porcelain"], repo)
            if dirty:
                shown = "\n".join("  " + ln for ln in dirty.splitlines()[:8])
                raise GoStop(f"{repo} has uncommitted or untracked files; commit, stash, or ignore them first:\n"
                             f"{shown}")
            branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], repo)
            if self.o.resume and self.state.original_branch:
                branch = self.state.original_branch
            elif branch == "HEAD":
                raise GoStop("the checkout is on a detached HEAD; switch to the branch you want optimized")
            elif branch.startswith(("hotpath-setup/", "hotpath/")):
                raise GoStop(f"the checkout is on Hotpath's own branch {branch}; switch back to your branch")
            self.state.original_branch = branch
            self.state.base_branch = branch
            self.state.base_commit = _git(["rev-parse", branch], repo)
            remote_url = _git(["config", "--get", f"remote.{self.o.remote}.url"], repo, check=False)
            m = _GH_URL.match(remote_url or "")
            gh = f"{m.group(1)}/{m.group(2)}" if m else None
            where = f"local checkout {repo} on {branch}"
            if not remote_url:
                self.can_pr, self.pr_blocker = False, f"no '{self.o.remote}' remote to push to"
            else:
                _git(["fetch", "-q", self.o.remote, branch], repo, check=False)
                tip = _git(["rev-parse", "-q", "--verify", f"{self.o.remote}/{branch}"], repo, check=False)
                if tip != self.state.base_commit:
                    self.can_pr = False
                    self.pr_blocker = (f"{self.o.remote}/{branch} is not at your local {branch}; push it first so the "
                                       "PR's base is exactly what Hotpath measured")
        else:
            name = gh.replace("/", "__") if gh else _slug(Path(url.rstrip("/")).stem)
            repo = ws_root / name
            ws_root.mkdir(parents=True, exist_ok=True)
            if (repo / ".git").is_dir():
                _git(["fetch", "-q", "--prune", self.o.remote], repo, timeout=900)
                self.repo = repo
                self._load_state()
                if _git(["status", "--porcelain"], repo):
                    raise GoStop(f"the workspace clone {repo} has uncommitted changes; clean it or delete the folder")
                where = f"updated {repo}"
            else:
                res = subprocess.run(["git", "clone", "-q", url, str(repo)], capture_output=True, text=True,
                                     env={**os.environ, "GIT_TERMINAL_PROMPT": "0"}, timeout=1800)
                if res.returncode != 0:
                    raise GoStop(f"could not clone {url}: {res.stderr.strip()[-500:]}\n"
                                 "For a private repository, log in first (`gh auth login`) or use a URL with access.")
                self.repo = repo
                self._load_state()
                where = f"cloned into {repo}"
            from hotpath.pr import remote_default_branch
            branch = remote_default_branch(repo, self.o.remote) or _git(["rev-parse", "--abbrev-ref", "HEAD"], repo)
            self.state.base_branch = branch
            if not (self.o.resume and self.state.base_commit):
                self.state.base_commit = _git(["rev-parse", f"{self.o.remote}/{branch}"], repo)
        self.state.target = str(self.repo)
        self.state.github = gh or ""
        access = ""
        if gh and not self.o.no_pr:
            push = self._push_access(gh)
            if push is False:
                raise GoStop(f"you don't have push access to {gh}. Fork it on GitHub, then run "
                             f"`hotpath go https://github.com/<you>/{gh.split('/')[1]}` (or pass --no-pr)")
            access = " (you have push access)" if push else " (push access not checked: no gh/GITHUB_TOKEN)"
        self.step(i, f"{where} @ {self.state.base_commit[:8]}, PR base {self.state.base_branch}{access}")
        if not self.can_pr and not self.o.no_pr:
            self.info(f"no pull request will be opened: {self.pr_blocker}")

    def _load_state(self) -> None:
        f = self.repo / ".hotpath" / "go" / "state.json" if self.repo else None
        if self.o.resume and f and f.is_file():
            data = json.loads(f.read_text(encoding="utf-8"))
            self.state = GoState(**{k: v for k, v in data.items() if k in GoState.__dataclass_fields__})
            self.previous_stages = list(self.state.stages_done)
            self.state.stages_done = []

    def _push_access(self, gh: str) -> Optional[bool]:
        from hotpath.github import gh_binary, github_token
        if self.pr_method == "gh" and gh_binary():
            res = subprocess.run([gh_binary(), "api", f"repos/{gh}", "--jq", ".permissions.push"],
                                 capture_output=True, text=True, timeout=30)
            if res.returncode == 0:
                return res.stdout.strip() == "true"
        token = github_token()
        if token:
            import urllib.request
            req = urllib.request.Request(f"https://api.github.com/repos/{gh}",
                                         headers={"Authorization": f"Bearer {token}",
                                                  "Accept": "application/vnd.github+json"})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    return bool(json.load(r).get("permissions", {}).get("push"))
            except Exception:
                return None
        return None

    # ------------------------------------------------------------------ 3
    def assess(self, i: int) -> None:
        assert self.repo is not None
        if not self.state.original_branch:
            # A Hotpath-managed clone: look at exactly the commit that will be measured.
            _git(["checkout", "-q", "--detach", self.state.base_commit], self.repo)
        a = assess(self.repo, name=self.state.github.split("/")[-1] if self.state.github else None)
        if self.o.test_cmd:
            a.test_cmd = self.o.test_cmd
            a.blockers = [b for b in a.blockers if "no test suite" not in b]
        if self.o.bench_cmd:
            a.bench_cmd, a.bench_wrap_cmd = self.o.bench_cmd, None
        self.existing_config = any((self.repo / n).is_file() for n in CONFIG_NAMES)
        (self._state_dir() / "ASSESSMENT.md").write_text(a.to_markdown(), encoding="utf-8")
        self.a = a
        if a.blockers:
            raise GoStop("the repository is not ready for Hotpath:\n" + "\n".join(f"  - {b}" for b in a.blockers))
        test_cmd = a.test_cmd or ""
        if "pytest" in test_cmd and "cacheprovider" not in test_cmd:
            test_cmd += " -p no:cacheprovider"  # read-only sandboxes, and no cache state between trials
        self.test_cmd = test_cmd
        langs = ", ".join(sorted(a.languages, key=lambda k: -a.languages[k])[:2]) or a.ecosystem
        can_generate = a.tier == 1 and not self.o.no_generate and (self.o.provider or "openai") == "openai"
        bench = ("existing benchmark" if a.bench_cmd else "existing command to time" if a.bench_wrap_cmd
                 else "no benchmark (one will be generated)" if can_generate else "no benchmark found")
        self.step(i, f"{a.ecosystem} ({langs}), Tier {a.tier} · tests: `{self.test_cmd}` · {bench}")
        self.info(f"editable: {', '.join(a.editable)}")
        for n in a.notes:
            self.info("note: " + n)
        if self.existing_config:
            self.info("the repository already has a .hotpath.yaml; Hotpath will use it as-is")

    # ------------------------------------------------------------------ 4
    def baseline(self, i: int) -> None:
        from hotpath.sandbox import (SandboxError, build_image, docker_available, make_runner, make_venv,
                                     run_install_cmds)
        from hotpath.workspace import Workspace
        assert self.repo is not None and self.a is not None
        Workspace(self.repo, Path(".hotpath"))._ignore_workdir()
        mode = self.o.sandbox
        if mode == "auto":
            mode = "docker" if self.a.tier == 1 and docker_available() else "local"
            if mode == "local":
                self.info("Docker is not running, so candidates run directly on this machine")
                if not self.confirm(f"Run {self.a.name}'s tests and model-written changes on this machine?"):
                    raise GoStop("no consent to run locally. Start Docker Desktop, or rerun with "
                                 "--sandbox local (or --yes)")
        elif mode == "docker" and not docker_available():
            raise GoStop("--sandbox docker was requested, but Docker is not running")
        deps = list(self.a.dependencies)
        if "pytest" in self.test_cmd and not any(re.match(r"pytest\b", d) for d in deps):
            deps.append("pytest")
        try:
            if mode == "docker":
                self.tenv = build_image(self.a.name, deps, src_layout=self.a.src_layout, say=self.info)
                self.state.image = self.tenv.image or ""
            else:
                venv = (Path(self.o.workspaces) if self.o.workspaces else _default_workspaces()) / ".venvs" / _slug(self.a.name)
                self.tenv = make_venv(venv, deps if self.a.tier == 1 else [], src_layout=self.a.src_layout, say=self.info)
                self.state.venv = str(venv)
                if self.a.install_cmds:
                    run_install_cmds(self.repo, self.a.install_cmds, self.info)
        except SandboxError as e:
            raise GoStop(str(e))
        self.state.sandbox = mode
        self.runner = make_runner(self.tenv)
        if self.o.resume and "Optimize" in self._done_before() and self.state.setup_commit:
            self.step(i, f"{mode} environment ready (resumed; baseline already verified)")
            return
        wt = self._state_dir() / "baseline"
        if wt.exists():
            _git(["worktree", "remove", "--force", str(wt)], self.repo, check=False)
            shutil.rmtree(wt, ignore_errors=True)
        _git(["worktree", "add", "-q", "--detach", str(wt), self.state.base_commit], self.repo)
        self.baseline_wt = wt
        results = []
        for n in range(self.o.test_runs):
            r = self.runner(self.test_cmd, wt, self.o.test_timeout)
            results.append(r)
            if r.returncode != 0 and n == 0:
                break
        passed = [r for r in results if r.returncode == 0]
        if not passed:
            r = results[0]
            why = "timed out" if r.timed_out else f"exited {r.returncode}"
            raise GoStop(f"the tests fail on the untouched code ({why}), so there is nothing to prove a change "
                         f"against. Fix them first. Last output:\n{(r.stdout + r.stderr)[-2500:]}")
        if len(passed) != len(results):
            raise GoStop(f"the tests are flaky: {len(passed)} of {len(results)} runs passed on the same code. "
                         "Hotpath would reject good changes at random; fix or skip the flaky tests "
                         "(or pass --test-cmd with a stable subset)")
        durations = sorted(r.duration_s for r in results)
        median = durations[len(durations) // 2]
        self.test_seconds = median
        m = _PYTEST_SUMMARY.search(results[0].stdout or "")
        count = f"{m.group(1)} passed · " if m else ""
        self.step(i, f"{mode} · {count}{len(results)}/{len(results)} runs green, not flaky · {median:.1f}s each")
        if median > 300:
            self.info("the tests take over 5 minutes; every candidate runs them, so consider --test-cmd "
                      "with a focused subset")

    def _done_before(self) -> list[str]:
        return self.previous_stages if self.o.resume else []

    # ------------------------------------------------------------------ 5
    def benchmark(self, i: int) -> None:
        from hotpath import benchgen
        from hotpath.benchmark import BenchmarkParseError, parse_benchmark_output
        assert self.a is not None and self.repo is not None
        if self.existing_config:
            self.step(i, "using the benchmark in the repository's .hotpath.yaml")
            return
        if self.o.resume and self.state.setup_commit:
            self.step(i, f"{self.state.bench_kind} benchmark from the setup commit (resumed)")
            return
        wt = self.baseline_wt
        assert wt is not None
        choice = None
        if self.a.bench_cmd:
            choice = benchgen.BenchChoice("existing", self.a.bench_cmd, self.a.profile_cmd,
                                          description=f"the repository's own benchmark (`{self.a.bench_cmd}`)")
        elif self.a.tier == 1 and not self.o.no_generate and (self.o.provider or "openai") == "openai":
            self.info("profiling the test suite to find the project's hot paths")
            try:
                cfg_model = "gpt-4.1"
                generator = benchgen.openai_generator(cfg_model)
                prepare = self._commit_in_worktree if self.state.sandbox == "docker" else (lambda _w: None)
                choice = benchgen.generate_benchmark(wt, self.runner, generator, prepare=prepare, say=self.info)
            except Exception as e:  # generation is best-effort; the fallback is always available
                self.info(f"benchmark generation failed: {str(e).splitlines()[0][:200]}")
            if choice is None:
                self.info("no generated benchmark passed validation")
        if choice is None and self.a.bench_wrap_cmd:
            choice = benchgen.BenchChoice("wrapped", f"python -m hotpath.benchwrap --trials 7 --warmup 1 -- "
                                                     f"{self.a.bench_wrap_cmd}", self.a.profile_cmd,
                                          description=f"the runtime of `{self.a.bench_wrap_cmd}`")
        if choice is None:
            choice = benchgen.tests_fallback(self.test_cmd, "no benchmark could be found or generated")
        if choice.kind != "generated":
            r = self.runner(choice.bench_cmd, wt, max(self.o.test_timeout * 8, 600))
            self.bench_seconds = r.duration_s
            try:
                samples = [float(s) for s in parse_benchmark_output(r.stdout)["samples"]]
            except (BenchmarkParseError, ValueError, TypeError):
                raise GoStop(f"the benchmark `{choice.bench_cmd}` did not produce samples:\n"
                             f"{(r.stdout + r.stderr)[-1500:]}")
            noise = benchgen.robust_noise(samples)
            choice.validation = benchgen.Validation(True, median_s=sorted(samples)[len(samples) // 2], noise=noise,
                                                    details=[f"noise {noise:.1%}"])
            if noise > 0.10:
                self.info(f"warning: this benchmark is noisy ({noise:.0%}); only large speedups will clear the bar")
        v = choice.validation
        if choice.kind == "generated" and v:
            self.bench_seconds = v.median_s * 20   # benchlib runs 2 warmups + 15 trials
        self.info(f"benchmark: {choice.description}")
        self.info(f"command:   {choice.bench_cmd}")
        if v:
            self.info(f"measured:  {v.median_s * 1000:.1f} ms median · " + " · ".join(v.details))
        if choice.kind == "generated":
            code = choice.files[benchgen.WORKLOAD_FILE].splitlines()
            self.info(f"--- {benchgen.WORKLOAD_FILE} ({len(code)} lines) ---")
            for line in code[:40]:
                self.info("  " + line)
            if len(code) > 40:
                self.info(f"  ... {len(code) - 40} more lines in {wt / benchgen.WORKLOAD_FILE}")
        if choice.kind == "tests":
            self.info("note: timing the whole test suite is coarse; the PR will say so")
        if not self.confirm("This benchmark defines \"faster\" for the run. Use it?"):
            raise GoStop("benchmark not approved. Edit it and rerun with --bench-cmd, or write "
                         "hotpath_bench.py by hand (see hotpath_bench.py from `hotpath init`)")
        self.choice = choice
        self.state.bench_kind, self.state.bench_description = choice.kind, choice.description
        self.step(i, f"{choice.kind}: {choice.description}")

    def _commit_in_worktree(self, wt: Path) -> None:
        """Containers only see tracked files; commit the candidate benchmark in the throwaway worktree."""
        _git(["add", "-A"], wt)
        _git(["-c", "user.name=Hotpath", "-c", "user.email=hotpath@users.noreply.github.com",
              "commit", "-q", "--no-verify", "-m", "hotpath: benchmark candidate (throwaway)"], wt, check=False)

    # ------------------------------------------------------------------ 6
    def configure(self, i: int) -> None:
        from hotpath.init import InitAnswers, InitError, init_repo
        from hotpath.pr import SETUP_TRAILER
        assert self.repo is not None and self.a is not None
        if self.existing_config:
            if self.state.original_branch:
                _git(["checkout", "-q", self.state.original_branch], self.repo)
            self.step(i, "using the repository's .hotpath.yaml; no setup commit needed")
            return
        if self.o.resume and self.state.setup_commit and _git(["cat-file", "-e", self.state.setup_commit],
                                                              self.repo, check=False) == "":
            _git(["checkout", "-q", self.state.setup_branch], self.repo)
            self.step(i, f"setup commit {self.state.setup_commit[:8]} on {self.state.setup_branch} (resumed)")
            return
        assert self.choice is not None
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        branch = f"hotpath-setup/{stamp}"
        _git(["checkout", "-q", "-B", branch, self.state.base_commit], self.repo)
        for rel, text in self.choice.files.items():
            (self.repo / rel).write_text(text, encoding="utf-8", newline="\n")
        locked = list(dict.fromkeys(self.a.locked + [f for f in GENERATED_FILES]))
        answers = InitAnswers(
            name=self.a.name, test_cmd=self.test_cmd, bench_cmd=self.choice.bench_cmd,
            profile_cmd=self.choice.profile_cmd, editable=self.a.editable, locked=locked,
            execution="docker" if self.state.sandbox == "docker" else "local",
            planner="mock" if (self.o.provider == "mock") else "openai",
            worker="mock" if self.o.provider == "mock" else self.worker,
            mock_patches_dir=str(Path(self.o.mock_patches).resolve()) if self.o.mock_patches else None,
            iterations=self.o.iterations, candidates=self.o.candidates)
        try:
            result = init_repo(self.repo, answers, force=False, workflow=True)
        except InitError as e:
            raise GoStop(f"could not write the Hotpath config: {e}")
        files = [p.relative_to(self.repo).as_posix() for p in result.written] + list(self.choice.files)
        _git(["add", "--", *dict.fromkeys(files)], self.repo)
        body = [f"hotpath: set up benchmark, config, and verification check", "",
                f"Benchmark: {self.choice.description}.",
                f"Correctness: `{self.test_cmd}`.",
                "These files define what Hotpath measured. Hotpath may never edit them afterwards.", "",
                SETUP_TRAILER]
        identity = [] if _git(["config", "user.email"], self.repo, check=False) else [
            "-c", "user.name=Hotpath", "-c", "user.email=hotpath@users.noreply.github.com"]
        _git([*identity, "commit", "-q", "--no-verify", "-m", "\n".join(body)], self.repo)
        self.state.setup_branch = branch
        self.state.setup_commit = _git(["rev-parse", "HEAD"], self.repo)
        self.step(i, f"setup commit {self.state.setup_commit[:8]} on {branch}: {', '.join(dict.fromkeys(files))}")

    # ------------------------------------------------------------------ 7
    def optimize(self, i: int) -> None:
        from hotpath.orchestrator import Orchestrator
        from hotpath.schema import HotpathConfig
        assert self.repo is not None and self.tenv is not None
        config_path = next(self.repo / n for n in CONFIG_NAMES if (self.repo / n).is_file())
        cfg = load_config(config_path)
        # Timeouts sized from what this repository actually took, with generous headroom: a hung
        # command then fails in minutes instead of after the generic 15-minute default.
        if self.test_seconds:
            cfg.timeouts.test = max(120.0, min(self.o.test_timeout, self.test_seconds * 8))
        if self.bench_seconds:
            cfg.timeouts.bench = max(300.0, self.bench_seconds * 8)
        cfg.search.iterations = self.o.iterations
        cfg.search.candidates_per_iteration = self.o.candidates
        cfg.search.beam_width = self.o.beam
        if self.o.provider:
            cfg.provider.planner = cfg.provider.worker = self.o.provider
        if self.o.mock_patches:
            cfg.provider.mock_patches_dir = str(Path(self.o.mock_patches).resolve())
        if self.state.sandbox == "docker":
            cfg.execution.backend, cfg.execution.image = "docker", self.state.image
        else:
            cfg.execution.backend = "local"
        cfg = HotpathConfig.model_validate(cfg.model_dump())
        saved_env = {k: os.environ.get(k) for k in self.tenv.env}
        os.environ.update(self.tenv.env)
        try:
            if self.o.resume and self.state.run_id:
                from hotpath.store import Store
                from hotpath.workspace import Workspace
                ws = Workspace(Path(cfg.target), Path(cfg.workdir))
                store = Store(cfg.db_path or (ws.workdir / "hotpath.db"))
                prior = store.get_run(self.state.run_id)
                if prior and prior.status in ("finished", "stopped"):
                    self._finish_optimize(i, prior, store, ws, cfg, resumed=True)
                    return
            orch = Orchestrator(cfg)
            self.state.run_id = orch.run.id
            self._save()
            self.info(f"run {orch.run.id}: planner {orch.planner.name}, workers {orch.worker.name}, "
                      f"{cfg.search.iterations} iteration(s) × {cfg.search.candidates_per_iteration} candidates")
            if self.o.dashboard:
                self._start_dashboard(config_path)
            run = asyncio.run(self._execute_with_budget(orch))
        finally:
            for k, v in saved_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self._finish_optimize(i, run, orch.store, orch.ws, cfg)

    async def _execute_with_budget(self, orch):
        task = asyncio.create_task(orch.execute())
        start, last = time.monotonic(), 0.0
        reason = ""
        while not task.done():
            await asyncio.wait({task}, timeout=2)
            used = obs.token_usage["total"]
            if not reason and self.o.max_tokens and used >= self.o.max_tokens:
                reason = f"token budget reached ({used:,} ≥ {self.o.max_tokens:,})"
            if not reason and self.o.max_minutes and time.monotonic() - start >= self.o.max_minutes * 60:
                reason = f"time budget reached ({self.o.max_minutes:g} min)"
            if reason and not orch._stop.is_set():
                self.info(f"stopping: {reason}; finishing the experiments in flight")
                orch.stop()
            if time.monotonic() - last >= 20 and not task.done():
                last = time.monotonic()
                exps = orch.store.list_experiments(orch.run.id)
                acc = sum(1 for e in exps if e.status.value == "accepted")
                done = sum(1 for e in exps if e.status in TERMINAL_STATUSES or e.status.value == "accepted")
                self.info(f"… {self._elapsed()}: {done} experiment(s) finished, {acc} accepted, "
                          f"best {orch.run.best_speedup:.2f}x"
                          + (f", {used:,} tokens" if used else ""))
        return task.result()

    def _finish_optimize(self, i, run, store, ws, cfg, resumed: bool = False) -> None:
        from hotpath.export import _accepted_chain
        self._run, self._store, self._ws, self._cfg = run, store, ws, cfg
        exps = store.list_experiments(run.id)
        chain = _accepted_chain(run, exps)
        tried = len(exps)
        if run.status not in ("finished", "stopped"):
            tail = "\n".join(run.logs[-15:])
            raise GoStop(f"the run ended with status '{run.status}':\n{tail}")
        if not chain:
            rejected: dict[str, int] = {}
            for e in exps:
                rejected[e.status.value] = rejected.get(e.status.value, 0) + 1
            why = ", ".join(f"{n} {k}" for k, n in sorted(rejected.items())) or "no candidates"
            raise GoStop(f"no change was both correct and measurably faster ({tried} tried: {why}). That is a "
                         f"real result, not a failure: the code may already be near its limit for this "
                         f"benchmark. Details: `hotpath serve {self.repo / '.hotpath.yaml'}`", ok=True)
        self.step(i, f"{tried} candidates · {len(chain)} accepted · {run.best_speedup:.2f}x vs baseline"
                  + (" (resumed)" if resumed else ""))
        for e in chain:
            c = e.comparison
            sp = f"{c.speedup_vs_parent:.2f}x (95% CI {c.ci_low:.2f}-{c.ci_high:.2f})" if c else ""
            self.info(f"✓ {sp}  {e.hypothesis.idea[:90]}")
        if obs.token_usage["total"]:
            self.info(f"model usage: {obs.token_usage['total']:,} tokens in {obs.token_usage['calls']} call(s)")

    def _start_dashboard(self, config_path: Path) -> None:
        url = f"http://127.0.0.1:{self.o.port}"
        self.dashboard_proc = subprocess.Popen(
            [sys.executable, "-m", "hotpath.cli", "serve", str(config_path), "--port", str(self.o.port)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.info(f"live dashboard: {url}")
        if self.o.open_browser:
            time.sleep(1.5)
            webbrowser.open(url)

    # ------------------------------------------------------------------ 8
    def publish(self, i: int) -> None:
        from hotpath.pr import PRError, publish
        assert self.repo is not None
        push = not self.o.no_pr and self.can_pr
        if push and not self.confirm(f"Push branch hotpath/{self._run.id} and open a "
                                     f"{'ready' if self.o.ready else 'draft'} PR on "
                                     f"{self.state.github or self.o.remote} (base {self.state.base_branch})?"):
            push = False
            self.info("not pushing (no confirmation; pass --yes to skip this question)")
        try:
            rec = publish(self._cfg, self._store, self._run, self._ws, base=self.state.base_branch,
                          remote=self.o.remote, draft=not self.o.ready, push=push, method=self.pr_method,
                          say=self.info)
        except PRError as e:
            raise GoStop(f"the pull request was not published: {e}")
        summary_file = self._state_dir() / "SUMMARY.md"
        body_file = self._ws.workdir / "prs" / f"{self._run.id}.md"
        if body_file.exists():
            shutil.copyfile(body_file, summary_file)
        if rec.url:
            self.state.pr_url = rec.url
            self.step(i, rec.url + (" (draft)" if not self.o.ready else ""))
            if self.o.open_browser:
                webbrowser.open(rec.url)
        elif push and rec.compare_url:
            self.step(i, f"branch {rec.branch} pushed; open the PR: {rec.compare_url[:120]}...")
            if self.o.open_browser:
                webbrowser.open(rec.compare_url)
        elif push:
            self.step(i, f"branch {rec.branch} pushed to {self.o.remote} (not a GitHub remote: open the PR in "
                         f"your forge); the description is in {summary_file}")
        else:
            why = self.pr_blocker if not self.can_pr else "--no-pr" if self.o.no_pr else "not confirmed"
            self.step(i, f"branch {rec.branch} built locally ({why}); the description is in {summary_file}")
            self.info(f"to publish later: cd {self.repo} && hotpath pr --run-id {self._run.id} --base "
                      f"{self.state.base_branch} --draft")
