import asyncio
from pathlib import Path

import pytest

from fake_provider import FakeProvider
from hotpath.agent import Agent, is_transient
from hotpath.providers.base import PatchRequest
from hotpath.providers.prompts import worker_messages
from hotpath.schema import Edit, Experiment, ExperimentStatus, Hypothesis, PatchResponse, PlanResponse, ProfileSummary, RunState


def hypothesis(target="mod.py"):
    return Hypothesis(idea="reduce work", strategy="algorithm", target_file=target, rationale="less work", risk="low")


async def test_missing_source_is_per_experiment_failure(cfg, store):
    provider = FakeProvider()
    agent = Agent(cfg, provider, provider, None, store)
    exp = Experiment(run_id="run", iteration=1, hypothesis=hypothesis("missing.py"))
    await agent.generate_patches([exp], Path(cfg.target), "")
    assert exp.status == ExperimentStatus.patch_failed
    assert "FileNotFoundError" in exp.reject_reason
    assert provider.patch_requests == []
    assert store.get_experiment(exp.id).status == exp.status


async def test_worker_cannot_edit_unprovided_source(cfg, store):
    provider = FakeProvider(patches=[PatchResponse(edits=[Edit(file="other.py", search="x", replace="y")], reasoning="x")])
    agent = Agent(cfg, provider, provider, None, store)
    exp = Experiment(run_id="run", iteration=1, hypothesis=hypothesis())
    await agent.generate_patches([exp], Path(cfg.target), "")
    assert exp.status == ExperimentStatus.patch_failed
    assert "other.py, which the hypothesis did not declare" in exp.reject_reason


async def test_worker_sees_every_declared_file_and_which_are_new(cfg, store):
    (Path(cfg.target) / "util.py").write_text("def helper():\n    return 1\n")
    provider = FakeProvider(patches=[PatchResponse(edits=[
        Edit(file="kernels/fused.py", search="", replace="def fused():\n    return 2\n"),
        Edit(file="mod.py", search="def work(n):", replace="def work(n):  # uses fused")], reasoning="x")])
    agent = Agent(cfg, provider, provider, None, store)
    h = hypothesis().model_copy(update={"extra_files": ["kernels/fused.py", "util.py"]})
    exp = Experiment(run_id="run", iteration=1, hypothesis=h)
    await agent.generate_patches([exp], Path(cfg.target), "")
    request = provider.patch_requests[0]
    assert request.target_source == (Path(cfg.target) / "mod.py").read_text()
    assert request.extra_sources == {"util.py": "def helper():\n    return 1\n"}
    assert request.new_files == ["kernels/fused.py"]
    prompt = worker_messages(request)[1]["content"]
    assert "## Current contents of util.py" in prompt and "kernels/fused.py (new file" in prompt
    assert exp.status == ExperimentStatus.generating and len(exp.edits) == 2, exp.reject_reason


async def test_hypothesis_touching_too_many_files_never_reaches_the_worker(cfg, store):
    provider = FakeProvider()
    agent = Agent(cfg, provider, provider, None, store)
    h = hypothesis().model_copy(update={"extra_files": ["a.py", "b.py", "c.py"]})
    exp = Experiment(run_id="run", iteration=1, hypothesis=h)
    await agent.generate_patches([exp], Path(cfg.target), "")
    assert exp.status == ExperimentStatus.patch_failed and "touches 4 files; at most 3" in exp.reject_reason
    assert provider.patch_requests == []


async def test_planner_hypothesis_with_a_locked_extra_file_is_blocked(cfg, store):
    locked = hypothesis().model_copy(update={"idea": "edit the checker too", "extra_files": ["tests/check.py"]})
    fine = hypothesis().model_copy(update={"idea": "new kernel", "extra_files": ["kernels/k.py"]})
    provider = FakeProvider(plans=[PlanResponse(hypotheses=[locked, fine], notes="")])
    agent = Agent(cfg, provider, provider, None, store)
    plan, _, blocked = await agent.plan(RunState(config_name="t", target=cfg.target), ProfileSummary(), [], Path(cfg.target))
    assert [h.idea for h in plan.hypotheses] == ["new kernel"]
    assert [(h.idea, "locked" in why) for h, why in blocked] == [("edit the checker too", True)]


async def test_retry_request_includes_contract_and_attempt(cfg, store):
    cfg.correctness_contract = "Tokens must match exactly; logits atol=1e-5."
    provider = FakeProvider(patches=[PatchResponse(edits=[], reasoning="no safe change")])
    agent = Agent(cfg, provider, provider, None, store)
    prior = Edit(file="mod.py", search="return x", replace="return x + 1")
    earlier = Experiment(run_id="run", iteration=1, hypothesis=hypothesis(), parent_commit="base")
    earlier.set_status(ExperimentStatus.rejected_correctness, "cache shape mismatch")
    store.save_experiment(earlier)
    exp = Experiment(run_id="run", iteration=1, hypothesis=hypothesis(), parent_commit="head123",
                     previous_failure="token mismatch", previous_edits=[prior])
    await agent.generate_patches([exp], Path(cfg.target), "context" * 1000)
    request = provider.patch_requests[0]
    assert request.previous_edits == [prior]
    assert request.previous_failure == "token mismatch"
    assert request.target_source == (Path(cfg.target) / "mod.py").read_text()
    assert request.parent_commit == "head123"
    assert [item.id for item in request.history] == [earlier.id]
    assert request.source_complete
    prompt = worker_messages(request)[1]["content"]
    assert cfg.correctness_contract in prompt and "return x + 1" in prompt
    assert "Parent commit: head123" in prompt
    assert "cache shape mismatch" in prompt
    assert "locate its exact `search`" in worker_messages(request)[0]["content"]
    assert "Related context truncated" in prompt


async def test_planner_timeout_applies_to_any_provider_and_is_retried(cfg, store):
    class HangingProvider(FakeProvider):
        async def plan(self, request):
            self.plan_requests.append(request)
            await asyncio.Event().wait()
    cfg.timeouts.model = 0.01
    cfg.search.planner_retry_backoff_s = 0
    provider = HangingProvider()
    agent = Agent(cfg, provider, provider, None, store)
    run = RunState(config_name="test", target=cfg.target)
    with pytest.raises(asyncio.TimeoutError):
        await agent.plan(run, ProfileSummary(), [], Path(cfg.target))
    assert len(provider.plan_requests) == 3
    assert sum("retrying in 0s" in line for line in run.logs) == 2


class StatusError(Exception):
    def __init__(self, status_code):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


@pytest.mark.parametrize("error, transient", [
    (TimeoutError(), True), (ConnectionError("reset"), True), (StatusError(429), True),
    (StatusError(503), True), (StatusError(401), False), (StatusError(400), False),
    (RuntimeError("planner returned no parseable plan"), False),
])
def test_only_transient_planner_errors_are_retried(error, transient):
    assert is_transient(error) is transient


async def test_permanent_planner_error_is_not_retried(cfg, store):
    cfg.search.planner_retry_backoff_s = 0
    provider = FakeProvider(plans=[StatusError(401)])
    agent = Agent(cfg, provider, provider, None, store)
    with pytest.raises(StatusError):
        await agent.plan(RunState(config_name="test", target=cfg.target), ProfileSummary(), [], Path(cfg.target))
    assert len(provider.plan_requests) == 1
