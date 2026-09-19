"""`hotpath pr`: a real run becomes a branch of verified commits, pushed, and opened as a PR.

One real optimization run (mock model, real harness) is shared by the module. Every test publishes
through its own remote so the tests stay independent of order.
"""
import asyncio
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from hotpath import github
from hotpath.orchestrator import Orchestrator
from hotpath.pr import BODY_MARKER, PRError, branch_name, build_branch, publish
from hotpath.schema import ExperimentStatus, HotpathConfig, ProviderConfig, RunState
from hotpath.store import Store
from hotpath.workspace import Workspace


def git(repo: Path, *args: str) -> str:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True, env=env).stdout.strip()


def _patches(d: Path) -> None:
    d.mkdir()
    fast = {"idea": "set-based dedupe", "strategy": "data structure", "target_file": "mod.py",
            "rationale": "`i not in out` scans a list; a set makes it O(1)", "risk": "low",
            "edits": [{"file": "mod.py", "search": "        if i not in out:\n            out.append(i)\n",
                       "replace": "        if i not in seen:\n            seen.add(i)\n            out.append(i)\n"},
                      {"file": "mod.py", "search": "    out = []\n", "replace": "    out = []\n    seen = set()\n"}],
            "reasoning": "set membership"}
    locked = {"idea": "weaken tests", "strategy": "x", "target_file": "tests/check.py", "rationale": "r", "risk": "low",
              "edits": [{"file": "tests/check.py", "search": "assert", "replace": "pass #"}], "reasoning": ""}
    faster = {"idea": "count distinct values arithmetically", "strategy": "algorithmic", "target_file": "mod.py",
              "rationale": "range(n) holds max(n, 0) distinct values, so no loop is needed", "risk": "low",
              "edits": [{"file": "mod.py",
                         "search": "    out = []\n    seen = set()\n    for i in range(n):\n        if i not in seen:\n"
                                   "            seen.add(i)\n            out.append(i)\n    return len(out)\n",
                         "replace": "    return max(n, 0)\n"}],
              "reasoning": "builtin"}
    wrong = {"idea": "off by one", "strategy": "x", "target_file": "mod.py", "rationale": "r", "risk": "high",
             "edits": [{"file": "mod.py", "search": "def work(n):\n", "replace": "def work(n):\n    n = n + 1\n"}],
             "reasoning": ""}
    for i, p in enumerate([fast, locked, faster, wrong]):
        (d / f"{i}.json").write_text(json.dumps(p))


@pytest.fixture(scope="module")
def finished(tmp_path_factory):
    """(cfg, store, run, ws) for a finished run with two accepted, stacked changes."""
    root = tmp_path_factory.mktemp("prrun")
    repo = root / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text(
        "def work(n):\n    out = []\n    for i in range(n):\n        if i not in out:\n            out.append(i)\n    return len(out)\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "check.py").write_text(
        "import sys; sys.path.insert(0, '.')\nfrom mod import work\nassert work(50) == 50\nassert work(0) == 0\nprint('ok')\n")
    (repo / "bench.py").write_text(
        "import sys; sys.path.insert(0, '.')\nfrom hotpath.benchlib import run\nfrom mod import work\nrun(lambda: work(3000), warmup=1, trials=10)\n")
    _patches(root / "patches")
    old_env = {k: os.environ.get(k) for k in ("PATH", "SENTRY_DSN")}
    os.environ["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")
    os.environ["SENTRY_DSN"] = ""
    try:
        cfg = HotpathConfig(
            name="tiny", target=str(repo), test_cmd="python tests/check.py", bench_cmd="python bench.py",
            execution={"backend": "local"}, editable=["*.py"], locked=["tests/*", "bench.py"],
            timeouts={"test": 30, "bench": 60, "profile": 30, "model": 10},
            benchmark={"min_speedup": 1.03, "noise_multiplier": 2.0, "baseline_repeats": 2, "bootstrap_samples": 300},
            search={"iterations": 2, "candidates_per_iteration": 2, "max_patch_retries": 0},
            provider=ProviderConfig(planner="mock", worker="mock", mock_patches_dir=str(root / "patches")))
        ws = Workspace(repo, Path(cfg.workdir))
        ws.ensure_repo()
        git(repo, "config", "user.name", "Test Dev")
        git(repo, "config", "user.email", "dev@example.test")
        orch = Orchestrator(cfg)
        run = asyncio.run(orch.execute())
    finally:
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    assert run.status == "finished", run.error
    by_idea = {e.hypothesis.idea: e for e in orch.store.list_experiments(run.id)}
    # Both wins are large (O(n^2) -> O(n) -> O(1)) so they clear the noise gate even on a loaded machine.
    for idea in ("set-based dedupe", "count distinct values arithmetically"):
        assert by_idea[idea].status == ExperimentStatus.accepted, (idea, by_idea[idea].reject_reason)
    assert run.head_experiment_id == by_idea["count distinct values arithmetically"].id
    return cfg, orch.store, run, orch.ws


def _bare(tmp_path: Path, repo: Path, name: str, base: str) -> Path:
    bare = tmp_path / f"{name}.git"
    git(tmp_path, "init", "-q", "--bare", str(bare))
    git(repo, "remote", "add", name, str(bare))
    git(repo, "push", "-q", name, f"{base}:refs/heads/{base}")
    return bare


def test_branch_has_one_verified_commit_per_change(finished):
    cfg, store, run, ws = finished
    repo = ws.target
    head_before, status_before = git(repo, "rev-parse", "HEAD"), git(repo, "status", "--porcelain")
    plan = build_branch(cfg, store, run, ws)
    chain = [e for e in store.list_experiments(run.id) if e.status == ExperimentStatus.accepted]
    chain.sort(key=lambda e: e.iteration)
    assert plan.branch == branch_name(run) == f"hotpath/{run.id}"
    assert len(plan.commits) == 2
    shas = git(repo, "rev-list", "--reverse", f"{run.base_commit}..{plan.branch}").splitlines()
    assert shas == [sha for sha, _ in plan.commits]
    for sha, e in zip(shas, chain):
        # Byte-for-byte the tree the harness tested and benchmarked.
        assert git(repo, "rev-parse", f"{sha}^{{tree}}") == git(repo, "rev-parse", f"{e.commit}^{{tree}}")
        msg = git(repo, "log", "-1", "--format=%B", sha)
        assert msg.startswith(f"perf: {e.hypothesis.idea}")
        assert f"{e.comparison.speedup_vs_parent:.3f}x faster than its parent" in msg
        assert "95% CI" in msg and "`python tests/check.py` passed" in msg
        assert f"Hotpath-Run: {run.id}" in msg and f"Hotpath-Experiment: {e.id}" in msg
        assert git(repo, "log", "-1", "--format=%an <%ae>", sha) == "Test Dev <dev@example.test>"
    assert git(repo, "rev-parse", f"{shas[0]}^") == run.base_commit
    # Rebuilding is deterministic, so republishing is a no-op push.
    assert build_branch(cfg, store, run, ws).head_sha == plan.head_sha
    # The user's checkout, index, and branch are untouched.
    assert git(repo, "rev-parse", "HEAD") == head_before
    assert git(repo, "status", "--porcelain") == status_before
    # Experiment commits live in a private namespace, not as branches.
    assert "exp_" not in git(repo, "branch", "--list")


def test_body_reports_evidence_rejections_and_reproduction(finished):
    cfg, store, run, ws = finished
    body = build_branch(cfg, store, run, ws).body
    assert body.startswith(BODY_MARKER.format(run_id=run.id))
    assert f"{run.best_speedup:.3f}x faster, 2 verified changes" in body
    assert "set-based dedupe" in body and "count distinct values arithmetically" in body
    assert "Rejected attempts" in body and "`locked_file`" in body and "`rejected_correctness`" in body
    assert "weaken tests" in body and "off by one" in body
    assert "`tests/*`" in body and "`bench.py`" in body
    assert f"git checkout hotpath/{run.id}" in body and "python bench.py" in body


def test_no_push_keeps_the_branch_local_and_records_it(finished):
    cfg, store, run, ws = finished
    rec = publish(cfg, store, run, ws, push=False)
    assert rec.method == "local" and rec.branch == f"hotpath/{run.id}" and not rec.url
    stored = store.get_run(run.id)
    assert stored.pull_request and stored.pull_request.head_sha == rec.head_sha
    assert (ws.workdir / "prs" / f"{run.id}.md").read_text(encoding="utf-8").startswith("<!-- hotpath:run=")


def test_push_to_a_plain_git_remote(finished, tmp_path):
    cfg, store, run, ws = finished
    bare = _bare(tmp_path, ws.target, "plain", run.base_branch)
    rec = publish(cfg, store, run, ws, remote="plain")
    assert rec.base == run.base_branch and rec.method == "local" and not rec.url
    assert git(bare, "rev-parse", f"refs/heads/hotpath/{run.id}") == rec.head_sha


def test_refuses_when_the_base_moved_unless_allowed(finished, tmp_path):
    cfg, store, run, ws = finished
    bare = _bare(tmp_path, ws.target, "moved", run.base_branch)
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", str(bare), str(clone))
    (clone / "README.md").write_text("someone else's change\n")
    git(clone, "add", "README.md")
    git(clone, "-c", "user.name=x", "-c", "user.email=x@x", "commit", "-q", "-m", "move base")
    git(clone, "push", "-q", "origin", f"HEAD:{run.base_branch}")
    with pytest.raises(PRError, match="the speedup and tests describe the old base|this run measured"):
        publish(cfg, store, run, ws, remote="moved")
    assert "hotpath/" not in git(bare, "branch", "--list")
    rec = publish(cfg, store, run, ws, remote="moved", allow_moved_base=True)
    assert git(bare, "rev-parse", f"refs/heads/hotpath/{run.id}") == rec.head_sha


def test_missing_remote_and_bad_names_are_clear_errors(finished):
    cfg, store, run, ws = finished
    with pytest.raises(PRError, match="no git remote named 'nope'"):
        publish(cfg, store, run, ws, remote="nope")
    with pytest.raises(PRError, match="not a valid remote name"):
        publish(cfg, store, run, ws, remote="--upload-pack=evil")


def test_refuses_runs_that_are_not_publishable(finished):
    cfg, store, run, ws = finished
    live = run.model_copy(deep=True)
    live.status = "running"
    with pytest.raises(PRError, match="only a finished or stopped run"):
        build_branch(cfg, store, live, ws)
    empty = run.model_copy(deep=True)
    empty.head_commit, empty.head_experiment_id = empty.base_commit, None
    with pytest.raises(PRError, match="accepted no changes"):
        build_branch(cfg, store, empty, ws)


def test_refuses_a_diff_that_touches_a_locked_path(finished):
    cfg, store, run, ws = finished
    tampered = run.model_copy(deep=True)
    tampered.config_snapshot = {**run.config_snapshot, "locked": ["tests/*", "bench.py", "mod.py"]}
    with pytest.raises(PRError, match="mod.py.*locked"):
        build_branch(cfg, store, tampered, ws)


class FakeGitHub:
    """Just enough of the REST API: list, create, and edit pull requests for one repository."""

    def __init__(self):
        self.prs: list[dict] = []
        self.requests: list[tuple[str, str, dict, str]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _reply(self, code, payload):
                raw = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(n) or b"{}")

            def do_GET(self):
                outer.requests.append(("GET", self.path, {}, self.headers.get("Authorization", "")))
                head = self.path.split("head=")[-1].split("&")[0].replace("%3A", ":").replace("%2F", "/")
                owner, _, branch = head.partition(":")
                self._reply(200, [p for p in outer.prs if p["head"]["ref"] == branch and p["state"] == "open"])

            def do_POST(self):
                body = self._body()
                outer.requests.append(("POST", self.path, body, self.headers.get("Authorization", "")))
                pr = {"number": len(outer.prs) + 1, "state": "open", "head": {"ref": body["head"]},
                      "base": {"ref": body["base"]}, "title": body["title"], "body": body["body"], "draft": body["draft"]}
                pr["html_url"] = f"https://github.com/acme/widgets/pull/{pr['number']}"
                outer.prs.append(pr)
                self._reply(201, pr)

            def do_PATCH(self):
                body = self._body()
                outer.requests.append(("PATCH", self.path, body, self.headers.get("Authorization", "")))
                n = int(self.path.rstrip("/").split("/")[-1])
                pr = outer.prs[n - 1]
                pr.update({k: body[k] for k in ("title", "body") if k in body})
                self._reply(200, pr)

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture
def fake_github(monkeypatch):
    gh = FakeGitHub()
    monkeypatch.setenv("HOTPATH_GITHUB_API", gh.url)
    monkeypatch.setenv("GITHUB_TOKEN", "test-token-not-real")
    monkeypatch.setattr(github, "gh_binary", lambda: None)
    yield gh
    gh.close()


def test_opens_then_updates_the_same_pull_request(finished, tmp_path, fake_github):
    cfg, store, run, ws = finished
    repo = ws.target
    bare = tmp_path / "widgets.git"
    git(tmp_path, "init", "-q", "--bare", str(bare))
    # The PR belongs to the GitHub repository the user named; git itself is redirected to a local copy.
    git(repo, "remote", "add", "gh", "https://github.com/acme/widgets.git")
    git(repo, "config", f"url.{bare.as_posix()}.insteadOf", "https://github.com/acme/widgets.git")
    git(repo, "push", "-q", "gh", f"{run.base_branch}:refs/heads/{run.base_branch}")

    rec = publish(cfg, store, run, ws, remote="gh", draft=True)
    assert rec.method == "token" and rec.number == 1
    assert rec.url == "https://github.com/acme/widgets/pull/1"
    created = fake_github.prs[0]
    assert created["head"]["ref"] == f"hotpath/{run.id}" and created["base"]["ref"] == run.base_branch
    assert created["draft"] is True and created["title"].startswith("perf: ") and "[hotpath]" in created["title"]
    assert created["body"].startswith(BODY_MARKER.format(run_id=run.id))
    assert git(bare, "rev-parse", f"refs/heads/hotpath/{run.id}") == rec.head_sha
    assert all(auth == "Bearer test-token-not-real" for *_, auth in fake_github.requests)

    again = publish(cfg, store, run, ws, remote="gh")
    assert again.number == 1 and len(fake_github.prs) == 1
    assert [m for m, *_ in fake_github.requests] == ["GET", "POST", "GET", "PATCH"]
    assert again.created_at == rec.created_at
    assert store.get_run(run.id).pull_request.url == "https://github.com/acme/widgets/pull/1"


def test_without_credentials_a_prefilled_link_is_offered(finished, tmp_path, monkeypatch):
    cfg, store, run, ws = finished
    repo = ws.target
    for k in ("GITHUB_TOKEN", "GH_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(github, "gh_binary", lambda: None)
    bare = tmp_path / "nocreds.git"
    git(tmp_path, "init", "-q", "--bare", str(bare))
    git(repo, "remote", "add", "nocreds", "git@github.com:acme/nocreds.git")
    git(repo, "config", f"url.{bare.as_posix()}.insteadOf", "git@github.com:acme/nocreds.git")
    git(repo, "push", "-q", "nocreds", f"{run.base_branch}:refs/heads/{run.base_branch}")
    rec = publish(cfg, store, run, ws, remote="nocreds")
    assert rec.method == "link" and not rec.number
    assert rec.compare_url.startswith(
        f"https://github.com/acme/nocreds/compare/{run.base_branch}...hotpath%2F{run.id}?expand=1&title=perf")
    assert git(bare, "rev-parse", f"refs/heads/hotpath/{run.id}") == rec.head_sha


def test_github_errors_surface_without_the_token(finished, tmp_path, monkeypatch):
    cfg, store, run, ws = finished
    repo = ws.target
    monkeypatch.setattr(github, "gh_binary", lambda: None)
    monkeypatch.setenv("GITHUB_TOKEN", "secret-token-value")
    monkeypatch.setenv("HOTPATH_GITHUB_API", "http://127.0.0.1:9")  # nothing listens on the discard port
    bare = tmp_path / "down.git"
    git(tmp_path, "init", "-q", "--bare", str(bare))
    git(repo, "remote", "add", "down", "https://github.com/acme/down.git")
    git(repo, "config", f"url.{bare.as_posix()}.insteadOf", "https://github.com/acme/down.git")
    git(repo, "push", "-q", "down", f"{run.base_branch}:refs/heads/{run.base_branch}")
    with pytest.raises(PRError) as err:
        publish(cfg, store, run, ws, remote="down")
    assert "the branch is pushed" in str(err.value) and "secret-token-value" not in str(err.value)
    assert git(bare, "rev-parse", f"refs/heads/hotpath/{run.id}")


def test_old_runs_without_pr_fields_still_load(tmp_path):
    legacy = RunState(config_name="x", target=str(tmp_path)).model_dump(mode="json")
    legacy.pop("pull_request"), legacy.pop("base_branch")
    store = Store(tmp_path / "db.sqlite")
    with store._conn() as c:
        c.execute("INSERT INTO runs VALUES (?,?,?,?,?)", (legacy["id"], "finished", legacy["created_at"],
                                                         legacy["updated_at"], json.dumps(legacy)))
    loaded = store.get_run(legacy["id"])
    assert loaded.pull_request is None and loaded.base_branch == ""


async def test_dashboard_publishes_a_finished_run(finished, tmp_path):
    import httpx
    from server.app import create_app

    cfg, store, run, ws = finished
    bare = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", str(bare))
    if "origin" not in git(ws.target, "remote").split():
        git(ws.target, "remote", "add", "origin", str(bare))
    else:
        git(ws.target, "remote", "set-url", "origin", str(bare))
    git(ws.target, "push", "-q", "origin", f"{run.base_branch}:refs/heads/{run.base_branch}")
    app = create_app(cfg, str(store.path))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.post("/api/runs/missing/pr", json={})).status_code == 404
        # A cross-site form post (no JSON content type) is refused before anything is pushed.
        assert (await c.post(f"/api/runs/{run.id}/pr", content=b"{}",
                             headers={"Content-Type": "text/plain"})).status_code in (415, 422)
        assert (await c.post(f"/api/runs/{run.id}/pr", json={"base": "--evil"})).status_code == 422
        r = await c.post(f"/api/runs/{run.id}/pr", json={})
        assert r.status_code == 200, r.text
        pr = r.json()["pull_request"]
        assert pr["branch"] == f"hotpath/{run.id}" and pr["base"] == run.base_branch
        assert any("pushing" in line for line in r.json()["log"])
        state = (await c.get(f"/api/state?run_id={run.id}")).json()
        assert state["run"]["pull_request"]["head_sha"] == pr["head_sha"]
    assert git(bare, "rev-parse", f"refs/heads/hotpath/{run.id}") == pr["head_sha"]
    remote = httpx.ASGITransport(app=app, client=("203.0.113.8", 1234))
    async with httpx.AsyncClient(transport=remote, base_url="http://t") as c:
        assert (await c.post(f"/api/runs/{run.id}/pr", json={})).status_code == 403
