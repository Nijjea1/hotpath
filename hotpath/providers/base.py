"""Provider interface. A provider turns a request into a validated PlanResponse / PatchResponse.

Swapping OpenAI for a Baseten-served open model, or for the offline mock, changes nothing
else in the system: the harness is identical either way.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from hotpath.schema import Edit, Experiment, Hypothesis, PatchResponse, PlanResponse, ProviderConfig


@dataclass
class PlanRequest:
    profile_table: str
    source_context: str
    history: list[Experiment]
    strategies: list[str]
    editable: list[str]
    locked: list[str]
    metric: str
    higher_is_better: bool
    max_hypotheses: int
    iteration: int
    best_speedup: float


@dataclass
class PatchRequest:
    hypothesis: Hypothesis
    target_source: str
    related_context: str
    editable: list[str]
    locked: list[str]
    experiment_id: str = ""
    previous_failure: str = ""
    correctness_contract: str = "Preserve all externally observable behavior."
    previous_edits: list[Edit] = field(default_factory=list)
    # The worker needs enough lineage to avoid reintroducing a known-bad change on
    # a different beam head.  These are evidence, not instructions from the model.
    parent_commit: str | None = None
    history: list[Experiment] = field(default_factory=list)
    source_complete: bool = True
    extra: dict = field(default_factory=dict)
    extra_sources: dict[str, str] = field(default_factory=dict)  # complete text of the other existing files
    new_files: list[str] = field(default_factory=list)            # declared files that do not exist yet


class Provider(Protocol):
    name: str

    async def plan(self, req: PlanRequest) -> PlanResponse: ...
    async def generate_patch(self, req: PatchRequest) -> PatchResponse: ...


def build_provider(kind: str, cfg: ProviderConfig, role: str, timeout: float) -> Provider:
    if kind == "mock":
        from hotpath.providers.mock import MockProvider
        return MockProvider(cfg.mock_patches_dir)
    if kind == "openai":
        from hotpath.providers.openai_provider import OpenAIProvider
        if role == "planner":
            return OpenAIProvider(model=cfg.planner_model, api_key_env=cfg.planner_api_key_env, timeout=timeout,
                                  role="planner")
        return OpenAIProvider(model=cfg.worker_model, api_key_env=cfg.worker_api_key_env,
                              base_url=cfg.worker_base_url, timeout=timeout, role="worker")
    raise ValueError(f"unknown provider kind {kind!r}")
