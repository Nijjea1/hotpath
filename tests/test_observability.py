"""Exercise real SDK envelopes through an in-memory transport: no Sentry account needed."""
import json
import logging

import httpx
import pytest
import sentry_sdk
from sentry_sdk.transport import Transport

from hotpath import observability as obs
from hotpath.schema import Experiment, ExperimentStatus, Hypothesis, RunState
from server.app import create_app


class MemoryTransport(Transport):
    def __init__(self):
        super().__init__()
        self.items = []

    def capture_envelope(self, envelope):
        for item in envelope.items:
            self.items.append((item.type, json.loads(item.payload.get_bytes())))


@pytest.fixture
def telemetry(monkeypatch):
    transport = MemoryTransport()
    original_init = sentry_sdk.init

    def initialize(**options):
        return original_init(**options, transport=transport, auto_enabling_integrations=False)

    monkeypatch.setattr(sentry_sdk, "init", initialize)
    monkeypatch.setattr(obs, "_enabled", False)
    monkeypatch.setenv("SENTRY_DSN", "https://public@example.invalid/1")
    monkeypatch.setenv("HOTPATH_ENV", "development")
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "1.0")
    monkeypatch.setenv("SENTRY_PROFILE_SESSION_SAMPLE_RATE", "1.0")
    monkeypatch.setenv("SENTRY_SEND_DEFAULT_PII", "false")
    yield transport
    if obs.enabled():
        obs.flush()
        sentry_sdk.get_client().close()
    sentry_sdk.get_global_scope().set_client(None)


def items(transport, kind):
    result = []
    for typ, data in transport.items:
        if typ == kind:
            result.extend(data["items"] if kind in {"log", "trace_metric"} else [data])
    return result


def test_environment_loading_and_no_dsn(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(obs, "_enabled", False)
    (tmp_path / ".env").write_text("SENTRY_DSN=https://public@example.invalid/1\nHOTPATH_ENV=from_file\n")
    monkeypatch.delenv("HOTPATH_ENV", raising=False)
    assert not obs.init_sentry()  # An explicitly empty environment DSN overrides the file.
    import os
    assert os.environ["HOTPATH_ENV"] == "from_file"


def test_no_dsn_is_a_complete_noop_even_with_dotenv(monkeypatch, tmp_path):
    """An explicit empty DSN disables setup and prevents SDK initialization."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(obs, "_enabled", False)
    (tmp_path / ".env").write_text("SENTRY_DSN=https://public@example.invalid/1\n")
    monkeypatch.setenv("SENTRY_DSN", "")
    monkeypatch.setattr(sentry_sdk, "init", lambda **kwargs: pytest.fail("SDK initialized without a DSN"))
    assert obs.init_sentry() is False
    assert obs.enabled() is False


def test_disabled_wrappers_never_touch_global_sdk(monkeypatch):
    monkeypatch.setattr(obs, "_enabled", False)
    def forbidden(*args, **kwargs):
        raise AssertionError("disabled telemetry touched the SDK")
    for name in ("isolation_scope", "start_transaction", "start_span", "set_tag",
                 "capture_exception", "add_breadcrumb", "flush"):
        monkeypatch.setattr(sentry_sdk, name, forbidden)
    with obs.transaction("disabled") as tx:
        tx.set_status("ok")
        with obs.span("disabled") as sp:
            sp.set_data("key", "value")
    obs.set_tag("key", "value")
    obs.capture(RuntimeError("ignored"))
    obs.breadcrumb("category", "ignored")
    obs.event("ignored")
    obs.record_run(None, [])
    obs.verification_signals()
    obs.flush()


async def test_debug_route_captures_connected_error_trace_logs_metrics(telemetry, tmp_path):
    app = create_app(db_path=str(tmp_path / "db.sqlite"))
    client = sentry_sdk.get_client()
    assert client.options["enable_logs"] is True
    assert client.options["profile_lifecycle"] == "trace"
    assert client.options["profile_session_sample_rate"] == 1.0
    assert client.options["profiles_sample_rate"] is None
    assert client.options["include_local_variables"] is False
    assert obs.init_sentry() and sentry_sdk.get_client() is client  # idempotent
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                                 base_url="http://localhost") as http:
        assert (await http.get("/api/observability")).json()["enabled"] is True
        assert (await http.get("/sentry-debug")).status_code == 500
    obs.flush()
    errors = items(telemetry, "event")
    error = next(e for e in errors if "intentional test error" in json.dumps(e))
    tx = next(t for t in items(telemetry, "transaction") if t["transaction"] == "/sentry-debug")
    assert error["contexts"]["trace"]["trace_id"] == tx["contexts"]["trace"]["trace_id"]
    assert any(s["op"] == "hotpath.verify" for s in tx["spans"])
    assert any("verification: info" in l["body"] for l in items(telemetry, "log"))
    assert {m["name"] for m in items(telemetry, "trace_metric")} >= {
        "hotpath.verification", "hotpath.queue.depth", "hotpath.verification.duration"}


async def test_debug_route_without_dsn_and_outside_development(monkeypatch, tmp_path):
    monkeypatch.setattr(obs, "_enabled", False)
    monkeypatch.setenv("HOTPATH_ENV", "development")
    app = create_app(db_path=str(tmp_path / "db.sqlite"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as http:
        response = await http.get("/sentry-debug")
        assert response.status_code == 503 and "SENTRY_DSN" in response.text
        monkeypatch.setenv("HOTPATH_ENV", "production")
        assert (await http.get("/sentry-debug")).status_code == 404
    monkeypatch.setenv("HOTPATH_ENV", "development")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("203.0.113.1", 123)),
                                 base_url="http://localhost") as http:
        assert (await http.get("/sentry-debug")).status_code == 403


def test_run_logs_metrics_redaction_and_scope_isolation(telemetry, monkeypatch):
    secret = "test-secret-that-must-never-be-sent"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    obs.init_sentry()
    run = RunState(config_name="test", target=".", status="finished", best_speedup=2.0)
    exp = Experiment(run_id=run.id, iteration=1,
                     hypothesis=Hypothesis(idea="dedupe", strategy="set", target_file="mod.py",
                                           rationale="linear membership", risk="low"),
                     status=ExperimentStatus.not_selected, timings={"test": 0.25})
    with obs.transaction("test run", run_id=run.id):
        run.log("run log " + secret)
        exp.log("experiment log")
        obs.event("Planner decision", rationale="use a set " + secret)
        logging.getLogger("hotpath").info("standard Python logging works")
        obs.record_run(run, [exp])
        obs.capture(ValueError("error containing " + secret))
    with obs.transaction("another run"):
        pass
    obs.flush()
    assert secret not in json.dumps(telemetry.items)
    logs = items(telemetry, "log")
    assert any("[Filtered]" in row["body"] for row in logs)
    assert any("standard Python logging works" in row["body"] for row in logs)
    assert any(row["body"] == "experiment log" for row in logs)
    counts = [m for m in items(telemetry, "trace_metric") if m["name"] == "hotpath.experiments"]
    assert len(counts) == 1 and counts[0]["attributes"]["status"]["value"] == "not_selected"
    txs = items(telemetry, "transaction")
    assert txs[0]["tags"]["hotpath.run_id"] == run.id
    assert "hotpath.run_id" not in txs[1].get("tags", {})


async def test_each_experiment_is_its_own_transaction_tagged_with_its_verdict(telemetry, cfg, store, tmp_path):
    from test_search_wiring import FakeProvider, patch, plan, setup
    assert obs.init_sentry()
    cfg.search.iterations = 1
    cfg.search.max_patch_retries = 0
    provider = FakeProvider(plans=[plan("A", "B")], patches=[patch(0), patch(1)])
    run = await setup(cfg, store, tmp_path, provider, [50, None]).execute()
    obs.flush()
    txs = [t for t in items(telemetry, "transaction") if t["transaction"] == "hotpath.experiment"]
    stored = {e.id: e.status.value for e in store.list_experiments(run.id)}
    assert {t["tags"]["hotpath.experiment_id"]: t["tags"]["hotpath.status"] for t in txs} == stored
    assert set(stored.values()) == {"accepted", "rejected_correctness"}
    assert all(t["tags"]["hotpath.run_id"] == run.id for t in txs)
    assert all(any(s["op"] == "hotpath.generate" for s in t["spans"]) for t in txs)


async def test_model_calls_are_ai_monitoring_spans_with_token_usage(telemetry, monkeypatch):
    from types import SimpleNamespace
    from hotpath.providers.base import PlanRequest
    from hotpath.providers.openai_provider import OpenAIProvider
    from hotpath.schema import PlanResponse
    assert obs.init_sentry()
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")
    provider = OpenAIProvider(model="gpt-4.1", role="planner")
    response = SimpleNamespace(
        model="gpt-4.1-2025-04-14", usage=SimpleNamespace(prompt_tokens=120, completion_tokens=30, total_tokens=150),
        choices=[SimpleNamespace(message=SimpleNamespace(parsed=PlanResponse(hypotheses=[], notes="none")))])

    async def parse(**kwargs):
        return response

    monkeypatch.setattr(provider.client.beta.chat.completions, "parse", parse)
    request = PlanRequest(profile_table="", source_context="", history=[], strategies=[], editable=["*.py"],
                          locked=[], metric="seconds", higher_is_better=False, max_hypotheses=1, iteration=1,
                          best_speedup=1.0)
    with obs.transaction("hotpath.run test"):
        await provider.plan(request)
    obs.flush()
    # The SDK sends gen_ai spans as standalone v2 span items, parented to the enclosing transaction.
    spans = [s for typ, payload in telemetry.items if typ == "span" for s in payload["items"]]
    [span] = [s for s in spans if s["attributes"]["sentry.op"]["value"] == "gen_ai.chat"]
    [tx] = [t for t in items(telemetry, "transaction") if t["transaction"] == "hotpath.run test"]
    assert span["trace_id"] == tx["contexts"]["trace"]["trace_id"]
    data = {key: attr["value"] for key, attr in span["attributes"].items()}
    assert data["gen_ai.agent.name"] == "planner" and data["gen_ai.system"] == "openai"
    assert data["gen_ai.request.model"] == "gpt-4.1" and data["gen_ai.response.model"] == "gpt-4.1-2025-04-14"
    assert (data["gen_ai.usage.input_tokens"], data["gen_ai.usage.output_tokens"],
            data["gen_ai.usage.total_tokens"]) == (120, 30, 150)


def test_worker_endpoint_is_named_in_ai_monitoring(monkeypatch):
    from hotpath.providers.openai_provider import OpenAIProvider
    monkeypatch.setenv("BASETEN_API_KEY", "not-a-real-key")
    baseten = OpenAIProvider(model="coder", api_key_env="BASETEN_API_KEY",
                             base_url="https://model-abc.api.baseten.co/environments/production/sync/v1")
    other = OpenAIProvider(model="coder", api_key_env="BASETEN_API_KEY", base_url="https://llm.example.test/v1")
    assert (baseten.system, other.system, baseten.role) == ("baseten", "openai-compatible", "worker")
