"""Sentry integration. Every stage of the loop is a span; agent reasoning goes to logs.

Everything here degrades to a no-op when SENTRY_DSN is unset, so local development
never needs credentials.
"""
from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import sentry_sdk
from dotenv import load_dotenv
from sentry_sdk import logger as sentry_logger, metrics
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.transport import HttpTransport

log = logging.getLogger("hotpath")
_enabled = False


class _BoundedHttpTransport(HttpTransport):
    """Telemetry is best-effort: never retry or stall the optimization CLI."""
    TIMEOUT = 2

    def _get_pool_options(self):
        options = super()._get_pool_options()
        options["retries"] = False
        return options


class _NoopSpan:
    def set_data(self, *args: Any, **kwargs: Any) -> None:
        pass

    def set_status(self, *args: Any, **kwargs: Any) -> None:
        pass

    def set_tag(self, *args: Any, **kwargs: Any) -> None:
        pass


def init_sentry(release: str | None = None) -> bool:
    global _enabled
    if os.environ.get("HOTPATH_DISABLE_SENTRY", "").lower() in {"1", "true", "yes"}:
        if _enabled:
            sentry_sdk.get_client().close(timeout=0)
            sentry_sdk.get_global_scope().set_client(None)
            _enabled = False
        return False
    # Never override explicitly exported variables (including an empty DSN).
    load_dotenv(Path.cwd() / ".env", override=False)
    if _enabled:
        return True
    dsn = os.environ.get("SENTRY_DSN")
    if not dsn:
        return False
    from sentry_sdk.integrations.logging import LoggingIntegration
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration

    sentry_sdk.init(
        dsn=dsn,
        release=release or os.environ.get("SENTRY_RELEASE") or f"hotpath@{_version()}",
        environment=os.environ.get("HOTPATH_ENV", "development"),
        send_default_pii=os.environ.get("SENTRY_SEND_DEFAULT_PII", "false").lower() == "true",
        include_local_variables=False,
        max_request_body_size="never",
        traces_sample_rate=float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "1.0")),
        profile_session_sample_rate=float(os.environ.get("SENTRY_PROFILE_SESSION_SAMPLE_RATE", "1.0")),
        profile_lifecycle="trace",
        enable_logs=True,
        enable_metrics=True,
        before_send=_scrub,
        before_send_transaction=_scrub,
        before_send_log=_scrub,
        before_send_metric=_scrub,
        transport=_BoundedHttpTransport,
        shutdown_timeout=2,
        integrations=[FastApiIntegration(), StarletteIntegration(),
                      LoggingIntegration(level=logging.INFO, event_level=logging.ERROR,
                                         sentry_logs_level=logging.INFO)],
    )
    _enabled = True
    log.setLevel(logging.INFO)
    event("Sentry initialized", release=release or os.environ.get("SENTRY_RELEASE") or f"hotpath@{_version()}")
    return True


def _scrub(payload: Any, hint: Any = None) -> Any:
    """Remove configured credentials even when embedded in an exception or log text."""
    secrets = [v for k, v in os.environ.items() if v and len(v) >= 8
               and (k.endswith(("_KEY", "_TOKEN", "_SECRET", "_PASSWORD")) or k == "SENTRY_DSN")]

    def clean(value: Any) -> Any:
        if isinstance(value, str):
            for secret in secrets:
                value = value.replace(secret, "[Filtered]")
        elif isinstance(value, dict):
            return {k: clean(v) for k, v in value.items()}
        elif isinstance(value, (list, tuple)):
            return [clean(v) for v in value]
        return value
    return clean(payload)


def event(message: str, **attributes: Any) -> None:
    if _enabled:
        sentry_logger.info(message, attributes=attributes)


def flush(timeout: float = 2.0) -> None:
    if _enabled:
        sentry_sdk.flush(timeout=timeout)


def record_run(run: Any, experiments: list[Any]) -> None:
    """Emit final verdicts once, after beam selection, with low-cardinality metric attributes."""
    if not _enabled:
        return
    attrs = {"config": run.config_name, "status": run.status}
    metrics.count("hotpath.runs", 1, attributes=attrs)
    metrics.gauge("hotpath.best_speedup", run.best_speedup, attributes=attrs)
    metrics.distribution("hotpath.baseline_noise_cv", run.baseline_noise_cv, attributes=attrs)
    for exp in experiments:
        tags = {"config": run.config_name, "status": exp.status.value}
        metrics.count("hotpath.experiments", 1, attributes=tags)
        for stage, duration in exp.timings.items():
            metrics.distribution("hotpath.stage.duration", duration, unit="second",
                                 attributes={**tags, "stage": stage})
        if exp.comparison:
            metrics.distribution("hotpath.experiment.speedup", exp.comparison.speedup_vs_parent,
                                 attributes=tags)
        event("Experiment completed", run_id=run.id, experiment_id=exp.id,
              status=exp.status.value, idea=exp.hypothesis.idea, reason=exp.reject_reason or "")


def verification_signals() -> None:
    """Called within the debug HTTP transaction; no model requests or benchmarks."""
    if not _enabled:
        return
    start = time.perf_counter()
    with span("hotpath.verify", "Sentry installation check"):
        sentry_logger.info("Hotpath Sentry verification: info", attributes={"verification": True})
        sentry_logger.warning("Hotpath Sentry verification: warning")
        sentry_logger.error("Hotpath Sentry verification: error log")
        metrics.count("hotpath.verification", 1)
        metrics.gauge("hotpath.queue.depth", 0)
        metrics.distribution("hotpath.verification.duration", time.perf_counter() - start, unit="second")


def _version() -> str:
    from hotpath import __version__
    return __version__


def enabled() -> bool:
    return _enabled


@contextmanager
def transaction(name: str, op: str = "hotpath", **data: Any) -> Iterator[Any]:
    if not _enabled:
        yield _NoopSpan()
        return
    with sentry_sdk.isolation_scope() as scope:
        for k, v in data.items():
            scope.set_tag(f"hotpath.{k}", str(v))
        with sentry_sdk.start_transaction(name=name, op=op) as tx:
            for k, v in data.items():
                tx.set_data(k, v)
            yield tx


@contextmanager
def span(op: str, description: str = "", **data: Any) -> Iterator[Any]:
    if not _enabled:
        yield _NoopSpan()
        return
    start = time.perf_counter()
    with sentry_sdk.start_span(op=op, name=description or op) as sp:
        for k, v in data.items():
            try:
                sp.set_data(k, v)
            except Exception:  # pragma: no cover
                pass
        try:
            yield sp
        finally:
            if _enabled:
                metrics.distribution("hotpath.operation.duration", time.perf_counter() - start,
                                     unit="second", attributes={"operation": op})


@contextmanager
def ai_chat(model: str, agent: str, system: str) -> Iterator[Any]:
    """A model call as a `gen_ai.chat` span in Sentry's AI agent monitoring conventions.

    Recorded by hand because the planner and worker use `chat.completions.parse`, which posts
    directly and bypasses the `create` method Sentry's OpenAI integration patches. Prompts are not
    attached: they contain the target's source code."""
    with span(OP.GEN_AI_CHAT, f"chat {model}", **{
            SPANDATA.GEN_AI_OPERATION_NAME: "chat", SPANDATA.GEN_AI_REQUEST_MODEL: model,
            SPANDATA.GEN_AI_SYSTEM: system, SPANDATA.GEN_AI_AGENT_NAME: agent}) as sp:
        yield sp


#: Tokens used by model calls in this process, whether or not Sentry is enabled. `hotpath go` reads it
#: to enforce a token budget.
token_usage: dict[str, int] = {"input": 0, "output": 0, "total": 0, "calls": 0}


def record_ai_usage(sp: Any, resp: Any) -> None:
    usage = getattr(resp, "usage", None)
    if usage is not None:
        try:
            token_usage["input"] += int(usage.prompt_tokens or 0)
            token_usage["output"] += int(usage.completion_tokens or 0)
            token_usage["total"] += int(usage.total_tokens or 0)
            token_usage["calls"] += 1
        except (TypeError, ValueError, AttributeError):
            pass
    try:
        if getattr(resp, "model", None):
            sp.set_data(SPANDATA.GEN_AI_RESPONSE_MODEL, resp.model)
        if usage is not None:
            sp.set_data(SPANDATA.GEN_AI_USAGE_INPUT_TOKENS, usage.prompt_tokens)
            sp.set_data(SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS, usage.completion_tokens)
            sp.set_data(SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS, usage.total_tokens)
    except Exception:  # pragma: no cover - telemetry must never break a model call
        pass


def set_tag(key: str, value: Any) -> None:
    if _enabled:
        sentry_sdk.set_tag(key, str(value))


def capture(exc: BaseException, **tags: Any) -> None:
    if not _enabled:
        return
    with sentry_sdk.isolation_scope() as scope:
        for key, value in tags.items():
            scope.set_tag(f"hotpath.{key}", str(value))
        sentry_sdk.capture_exception(exc)


def breadcrumb(category: str, message: str, **data: Any) -> None:
    if _enabled:
        sentry_sdk.add_breadcrumb(category=category, message=message, data=data, level="info")
