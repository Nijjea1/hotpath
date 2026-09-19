"""FastAPI backend for the dashboard. Reads the same SQLite store the orchestrator writes.

No state is invented here: every number the UI shows was written by the harness.
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from hotpath import observability as obs
from hotpath.config import COMMAND_KEYS, command_digest, config_snapshot
from hotpath.orchestrator import Orchestrator
from hotpath.profilediff import diff_profiles
from hotpath.schema import HotpathConfig, ProviderKind
from hotpath.store import Store
from server.views import build_chart, build_funnel, build_tree

log = logging.getLogger("hotpath.server")
STATIC = Path(__file__).parent / "static"


class StartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    config: Optional[str] = None
    iterations: Optional[int] = Field(None, ge=1, strict=True)
    provider: Optional[ProviderKind] = None


class PublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base: Optional[str] = Field(None, min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_][A-Za-z0-9._/-]*$")
    draft: bool = False


def create_app(cfg: HotpathConfig | None = None, db_path: str | None = None) -> FastAPI:
    obs.init_sentry()  # Also covers callers that use the app factory without the CLI.

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            yield
        finally:
            task = state["task"]
            if task is not None and not task.done():
                state["orch"].stop()
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=10)
                except asyncio.TimeoutError:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                except Exception:
                    log.exception("Run failed during server shutdown")
            await asyncio.to_thread(obs.flush)

    app = FastAPI(title="Hotpath", version="0.1.0", lifespan=lifespan)

    @app.middleware("http")
    async def local_dashboard_only(request: Request, call_next):
        if not request.client or request.client.host not in {"127.0.0.1", "::1", "testclient"}:
            return JSONResponse({"detail": "dashboard is available only from the local host"}, status_code=403)
        return await call_next(request)

    @app.get("/api/observability")
    def observability_status():
        return {"enabled": obs.enabled(), "environment": os.environ.get("HOTPATH_ENV", "development"),
                "debug_path": "/sentry-debug", "restart_required_after_env_change": True}

    @app.get("/sentry-debug", include_in_schema=False)
    async def sentry_debug(request: Request):
        if os.environ.get("HOTPATH_ENV", "development") != "development" or not request.client or request.client.host not in {"127.0.0.1", "::1", "testclient"}:
            raise HTTPException(404)
        if not obs.enabled():
            raise HTTPException(503, "Set SENTRY_DSN in the project .env and restart Hotpath first.")
        obs.verification_signals()
        raise RuntimeError("Hotpath Sentry verification: intentional test error")
    if db_path:
        store = Store(db_path)
    elif cfg:
        target = Path(cfg.target).resolve()
        wd = Path(cfg.workdir)
        store = Store(cfg.db_path or ((wd if wd.is_absolute() else target / wd) / "hotpath.db"))
    else:
        raise ValueError("serve needs a config or --db")
    state: dict = {"cfg": cfg, "store": store, "orch": None, "task": None, "publishing": False}

    @app.get("/api/runs")
    def runs():
        return [r.model_dump(mode="json", exclude={"logs", "baseline_profile", "head_profile"}) for r in store.list_runs()]

    @app.get("/api/state")
    def get_state(run_id: Optional[str] = None):
        run = store.get_run(run_id) if run_id else store.latest_run()
        if run_id and run is None:
            raise HTTPException(404, "no such run")
        if run is None:
            return {"run": None, "experiments": [], "config": _cfg_summary(cfg), "live": False}
        exps = store.list_experiments(run.id)
        live = state["task"] is not None and not state["task"].done() and state["orch"].run.id == run.id
        return {"run": run.model_dump(mode="json"), "experiments": [e.model_dump(mode="json") for e in exps],
                "config": _cfg_summary(_run_config(run)),
                "config_source": "snapshot" if _run_config(run) else "unknown", "live": live}

    def _run_or_404(run_id: Optional[str]):
        run = store.get_run(run_id) if run_id else store.latest_run()
        if run is None:
            raise HTTPException(404, "no such run")
        return run

    @app.get("/api/runs/{run_id}/tree")
    def run_tree(run_id: str):
        """Experiment forest with layout, retry links and derived beam membership."""
        run = _run_or_404(run_id)
        return build_tree(run, store.list_experiments(run.id), _run_config(run)).model_dump(mode="json")

    @app.get("/api/runs/{run_id}/chart")
    def run_chart(run_id: str):
        """Both progress series (raw metric and speedup) plus the acceptance noise band."""
        run = _run_or_404(run_id)
        return build_chart(run, store.list_experiments(run.id), _run_config(run)).model_dump(mode="json")

    @app.get("/api/runs/{run_id}/funnel")
    def run_funnel(run_id: str):
        """Attempts surviving each gate, with accepted and shipped counted separately."""
        run = _run_or_404(run_id)
        return build_funnel(run, store.list_experiments(run.id)).model_dump(mode="json")

    @app.get("/api/runs/{run_id}/profile_diff")
    def run_profile_diff(run_id: str):
        """Before/after bottleneck diff, or the reason the two profiles cannot be compared."""
        run = _run_or_404(run_id)
        return diff_profiles(run.baseline_profile, run.head_profile, run.best_speedup).model_dump(mode="json")

    @app.get("/api/experiments/{exp_id}")
    def experiment(exp_id: str):
        e = store.get_experiment(exp_id)
        if not e:
            raise HTTPException(404)
        return e.model_dump(mode="json")

    @app.post("/api/runs")
    async def start_run(req: StartRequest, request: Request):
        if not request.client or request.client.host not in {"127.0.0.1", "::1", "testclient"}:
            raise HTTPException(403, "run control is available only from the local host")
        if req.config is not None:
            raise HTTPException(400, "start the server with the selected config; API config paths are disabled")
        if state["task"] is not None and not state["task"].done():
            raise HTTPException(409, "a run is already in progress")
        try:
            run_cfg = cfg
            if run_cfg is None:
                raise HTTPException(400, "no config available; start the server with a config path")
            data = run_cfg.model_dump(mode="json")
            if req.iterations is not None:
                data["search"]["iterations"] = req.iterations
            if req.provider is not None:
                data["provider"]["planner"] = data["provider"]["worker"] = req.provider
            run_cfg = HotpathConfig.model_validate(data)
            orch = Orchestrator(run_cfg, store=store)
        except (OSError, ValueError, RuntimeError) as e:
            raise HTTPException(400, str(e))
        state["orch"] = orch
        state["task"] = asyncio.create_task(orch.execute())
        return {"run_id": orch.run.id}

    @app.post("/api/runs/{run_id}/pr")
    async def publish_pr(run_id: str, req: PublishRequest, request: Request):
        """Push the run's verified changes as `hotpath/<run id>` and open (or refresh) its pull request."""
        if not request.client or request.client.host not in {"127.0.0.1", "::1", "testclient"}:
            raise HTTPException(403, "publishing is available only from the local host")
        if not request.headers.get("content-type", "").startswith("application/json"):
            # A cross-site form post cannot set this header without a CORS preflight, which is never granted.
            raise HTTPException(415, "send the request as application/json")
        run = store.get_run(run_id)
        if run is None:
            raise HTTPException(404, "no such run")
        if state["task"] is not None and not state["task"].done() and state["orch"].run.id == run_id:
            raise HTTPException(409, "this run is still in progress; publish it when it finishes")
        if state["publishing"]:
            raise HTTPException(409, "a pull request is already being published")
        run_cfg = _run_config(run) or cfg
        if run_cfg is None:
            raise HTTPException(400, "this run has no configuration snapshot; publish it with `hotpath pr`")
        from hotpath.pr import PRError, publish
        from hotpath.workspace import Workspace
        messages: list[str] = []
        state["publishing"] = True
        try:
            ws = Workspace(Path(run.target), Path(run_cfg.workdir))
            record = await asyncio.to_thread(publish, run_cfg, store, run, ws, base=req.base, draft=req.draft,
                                             say=messages.append)
        except PRError as e:
            raise HTTPException(400, str(e))
        finally:
            state["publishing"] = False
        return {"pull_request": record.model_dump(mode="json"), "log": messages}

    @app.post("/api/runs/{run_id}/stop")
    def stop_run(run_id: str, request: Request):
        if not request.client or request.client.host not in {"127.0.0.1", "::1", "testclient"}:
            raise HTTPException(403, "run control is available only from the local host")
        orch = state["orch"]
        if orch and orch.run.id == run_id and state["task"] and not state["task"].done():
            orch.stop()
            return {"stopping": True}
        raise HTTPException(404, "no live run with that id")

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


def _run_config(run) -> HotpathConfig | None:
    """Never attribute the server's current settings to a historical experiment."""
    snapshot = getattr(run, "config_snapshot", None)
    if not snapshot:
        return None
    try:
        return HotpathConfig.model_validate(snapshot)
    except ValidationError:
        log.warning("Run %s has an unreadable configuration snapshot", run.id)
        return None


def _cfg_summary(cfg: HotpathConfig | None) -> dict | None:
    if not cfg:
        return None
    safe = config_snapshot(cfg)
    summary = {key: safe[key] for key in ("name", "target", "test_cmd", "bench_cmd", "profile_cmd",
                                          "editable", "locked", "provider", "benchmark", "search")}
    # A digest of each shown command, so two runs' verification contracts can be compared at a glance.
    summary["command_digests"] = {key: command_digest(safe[key]) for key in COMMAND_KEYS if safe.get(key)}
    return summary
