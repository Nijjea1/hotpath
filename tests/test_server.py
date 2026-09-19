import asyncio
import json
from pathlib import Path

import httpx
import pytest

from hotpath.schema import HotpathConfig, RunState, BenchmarkStats
from hotpath.store import Store
from server.app import create_app


@pytest.mark.parametrize("body", [{"iterations": 0}, {"iterations": -1}, {"iterations": True},
                                  {"provider": "anything"}, {"unknown": "field"}])
async def test_invalid_start_request_rejected(cfg, tmp_path, body):
    app = create_app(cfg, str(tmp_path / "db.sqlite"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.post("/api/runs", json=body)).status_code == 422
        assert (await c.get("/api/runs")).json() == []


async def test_start_revalidates_mutated_config_and_reports_missing_file(cfg, tmp_path):
    cfg.search.max_parallel_workers = 0
    app = create_app(cfg, str(tmp_path / "db.sqlite"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.post("/api/runs", json={})).status_code == 400
        assert (await c.post("/api/runs", json={"config": str(tmp_path / "missing.yaml")})).status_code == 400
        assert (await c.get("/api/state?run_id=missing")).status_code == 404


async def test_run_control_rejects_nonlocal_clients_and_config_paths(cfg, tmp_path):
    app = create_app(cfg, str(tmp_path / "db.sqlite"))
    remote = httpx.ASGITransport(app=app, client=("203.0.113.8", 1234))
    async with httpx.AsyncClient(transport=remote, base_url="http://t") as c:
        assert (await c.get("/api/state")).status_code == 403
        assert (await c.get("/")).status_code == 403
        assert (await c.post("/api/runs", json={})).status_code == 403
        assert (await c.post("/api/runs/any/stop")).status_code == 403
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.post("/api/runs", json={"config": str(tmp_path / "arbitrary.yaml")})).status_code == 400


async def test_state_summary_does_not_expose_embedded_credentials(cfg, tmp_path):
    cfg.test_cmd = "python check.py --token=private-test-token"
    cfg.provider.worker_base_url = "https://user:private-password@example.test/v1?key=private-query-token"
    app = create_app(cfg, str(tmp_path / "db.sqlite"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        payload = (await c.get("/api/state")).json()
    serialized = json.dumps(payload)
    assert "private-test-token" not in serialized
    assert "private-password" not in serialized
    assert "private-query-token" not in serialized
    assert payload["config"]["provider"]["worker_base_url"] == "https://example.test/v1"
    # The command itself is shown so the verification contract is readable, with its digest.
    assert payload["config"]["test_cmd"] == "python check.py --token=[Filtered]"
    assert len(payload["config"]["command_digests"]["test_cmd"]) == 16


async def test_historical_views_use_snapshot_never_server_config(cfg, tmp_path):
    path = str(tmp_path / "db.sqlite")
    store = Store(path)
    bench = BenchmarkStats(metric="seconds", samples=[1, 1], n=2, median=1, mean=1, stdev=0, cv=0)
    run = RunState(config_name="historical", target=".", baseline_benchmark=bench,
                   config_snapshot=cfg.model_dump(mode="json"))
    run.config_snapshot["search"]["beam_width"] = 4
    run.config_snapshot["benchmark"]["min_speedup"] = 1.5
    store.save_run(run)
    legacy = RunState(config_name="legacy", target=".", baseline_benchmark=bench)
    store.save_run(legacy)
    cfg.search.beam_width = 8
    app = create_app(cfg, path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get(f"/api/runs/{run.id}/tree")).json()["beam_width"] == 4
        assert (await c.get(f"/api/runs/{run.id}/chart")).json()["threshold_speedup"] == 1.5
        assert (await c.get(f"/api/state?run_id={run.id}")).json()["config_source"] == "snapshot"
        assert (await c.get(f"/api/runs/{legacy.id}/tree")).json()["beam_width"] is None
        assert (await c.get(f"/api/runs/{legacy.id}/chart")).json()["noise_band"] is None
        state = (await c.get(f"/api/state?run_id={legacy.id}")).json()
        assert state["config"] is None and state["config_source"] == "unknown"


async def test_lifespan_stops_active_run(cfg, tmp_path, monkeypatch):
    instances = []

    class ControlledOrchestrator:
        def __init__(self, config, store):
            self.run = RunState(config_name=config.name, target=config.target)
            self.finished = asyncio.Event()
            self.stopped = False
            instances.append(self)

        async def execute(self):
            await self.finished.wait()

        def stop(self):
            self.stopped = True
            self.finished.set()

    monkeypatch.setattr("server.app.Orchestrator", ControlledOrchestrator)
    app = create_app(cfg, str(tmp_path / "db.sqlite"))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            assert (await c.post("/api/runs", json={})).status_code == 200
            await asyncio.sleep(0)
    assert instances[0].stopped


async def test_api_end_to_end(cfg: HotpathConfig, tmp_path: Path):
    d = tmp_path / "patches"; d.mkdir()
    (d / "a.json").write_text(json.dumps({"idea": "seen set", "strategy": "s", "target_file": "mod.py", "rationale": "r", "risk": "low",
        "edits": [{"file": "mod.py", "search": "    out = []\n    for i in range(n):\n        if i not in out:\n            out.append(i)\n",
                   "replace": "    out = []\n    seen = set()\n    for i in range(n):\n        if i not in seen:\n            seen.add(i)\n            out.append(i)\n"}], "reasoning": "x"}))
    cfg.provider.mock_patches_dir = str(d)
    app = create_app(cfg, db_path=str(tmp_path / "db.sqlite"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get("/")).status_code == 200
        s = (await c.get("/api/state")).json()
        assert s["run"] is None and s["config"]["name"] == "tiny"
        r = await c.post("/api/runs", json={})
        assert r.status_code == 200, r.text
        run_id = r.json()["run_id"]
        assert (await c.post("/api/runs", json={})).status_code == 409, "only one live run"
        for _ in range(200):
            s = (await c.get(f"/api/state?run_id={run_id}")).json()
            if s["run"]["status"] in ("finished", "failed"):
                break
            await asyncio.sleep(0.2)
        assert s["run"]["status"] == "finished", s["run"].get("error")
        assert s["live"] is False and s["experiments"][0]["status"] == "accepted"
        eid = s["experiments"][0]["id"]
        assert (await c.get(f"/api/experiments/{eid}")).json()["diff"]
        assert (await c.get("/api/experiments/nope")).status_code == 404
        assert (await c.get("/api/runs")).json()[0]["id"] == run_id


async def test_derived_view_endpoints(cfg: HotpathConfig, tmp_path: Path):
    """tree / chart / profile_diff on a real (mock-provider) run, plus their 404 and empty paths."""
    d = tmp_path / "patches"; d.mkdir()
    (d / "a.json").write_text(json.dumps({"idea": "seen set", "strategy": "s", "target_file": "mod.py", "rationale": "r", "risk": "low",
        "edits": [{"file": "mod.py", "search": "    out = []\n    for i in range(n):\n        if i not in out:\n            out.append(i)\n",
                   "replace": "    out = []\n    seen = set()\n    for i in range(n):\n        if i not in seen:\n            seen.add(i)\n            out.append(i)\n"}], "reasoning": "x"}))
    cfg.provider.mock_patches_dir = str(d)
    app = create_app(cfg, db_path=str(tmp_path / "db.sqlite"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get("/api/runs/nope/tree")).status_code == 404
        assert (await c.get("/api/runs/nope/chart")).status_code == 404
        assert (await c.get("/api/runs/nope/profile_diff")).status_code == 404

        run_id = (await c.post("/api/runs", json={})).json()["run_id"]
        for _ in range(200):
            s = (await c.get(f"/api/state?run_id={run_id}")).json()
            if s["run"]["status"] in ("finished", "failed"):
                break
            await asyncio.sleep(0.2)
        assert s["run"]["status"] == "finished", s["run"].get("error")
        exps = s["experiments"]

        tree = (await c.get(f"/api/runs/{run_id}/tree")).json()
        assert len(tree["nodes"]) == len(exps) + 1, "one node per experiment plus the baseline"
        assert tree["nodes"][0]["id"] == "baseline"
        assert {(n["column"], n["row"]) for n in tree["nodes"]}.__len__() == len(tree["nodes"])
        accepted = [n for n in tree["nodes"] if n["kind"] == "accepted"]
        assert accepted and any(n["is_head"] for n in tree["nodes"])
        assert all(e["from_id"] and e["to_id"] for e in tree["edges"])

        chart = (await c.get(f"/api/runs/{run_id}/chart")).json()
        assert chart["metric"] == "seconds" and chart["higher_is_better"] is False
        assert len(chart["points"]) == len(exps)
        assert chart["raw_available"] is True and chart["baseline_raw"] > 0
        assert chart["noise_band"]["derived_from_config"] is True
        assert chart["points"][-1]["best_speedup"] >= 1.0
        # Lower-is-better: the frontier must never rise above the baseline.
        assert chart["points"][-1]["best_raw"] <= chart["baseline_raw"]

        diff = (await c.get(f"/api/runs/{run_id}/profile_diff")).json()
        assert diff["comparable"] is True, diff["incomparable_reason"]
        assert diff["before_tool"] == diff["after_tool"] == "cProfile"
        assert diff["before_commit"] != diff["after_commit"]
        assert diff["rows"] and all(r["classification"] for r in diff["rows"])
        assert diff["benchmark_speedup"] == pytest.approx(s["run"]["best_speedup"])

        assert (await c.get("/api/runs/nope/funnel")).status_code == 404
        funnel = (await c.get(f"/api/runs/{run_id}/funnel")).json()
        assert [r["label"] for r in funnel["rows"]] == ["proposed", "patch applied", "passed correctness",
                                                        "accepted", "shipped"]
        assert funnel["attempts"] == len(exps)
        assert funnel["shipped_ids"] == [n["id"] for n in tree["nodes"] if n["on_head_chain"] and n["id"] != "baseline"]


async def test_views_on_a_run_without_a_profile_command(cfg: HotpathConfig, tmp_path: Path):
    """No profile_cmd must degrade to an explanation, never a 500 or an invented diff."""
    cfg.profile_cmd = None
    app = create_app(cfg, db_path=str(tmp_path / "db.sqlite"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        run_id = (await c.post("/api/runs", json={})).json()["run_id"]
        for _ in range(200):
            s = (await c.get(f"/api/state?run_id={run_id}")).json()
            if s["run"]["status"] in ("finished", "failed"):
                break
            await asyncio.sleep(0.2)
        r = await c.get(f"/api/runs/{run_id}/profile_diff")
        assert r.status_code == 200
        body = r.json()
        assert body["comparable"] is False and body["rows"] == []
        assert "profile" in body["incomparable_reason"]
        assert (await c.get(f"/api/runs/{run_id}/tree")).status_code == 200
        assert (await c.get(f"/api/runs/{run_id}/chart")).status_code == 200
