"""Typed data model shared by every Hotpath module.

Two rules govern this file:
1. Anything a model produces is validated through a schema here (PlanResponse, PatchResponse).
2. Anything the harness measures is recorded here (CorrectnessResult, BenchmarkStats, SpeedComparison).
The Experiment record joins the two, and the status field says which side decided its fate.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, ConfigDict, model_validator


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


def now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

DEFAULT_STRATEGIES = [
    "algorithmic: replace an O(n^2) pattern with a hash-based or sorted approach",
    "data structure: use set/dict/deque/Counter instead of list scans",
    "caching: memoize repeated pure computations",
    "vectorize: batch work or push loops into C-implemented builtins",
    "io: avoid repeated file reads, syscalls, or allocations in hot loops",
    "gpu: preallocate buffers (e.g. KV cache) instead of growing tensors with torch.cat",
    "gpu: remove CPU-GPU sync points (.item(), .cpu(), print) from inner loops",
    "gpu: use backend-portable torch.compile or fused PyTorch operations when their preconditions hold",
    "gpu kernel: add a minimal Triton kernel plus a safe PyTorch fallback for CUDA/ROCm",
    "apple gpu: use MPS-supported PyTorch operations and retain a CPU fallback for unsupported operators",
]


class TimeoutConfig(ConfigModel):
    test: float = Field(300.0, gt=0)
    bench: float = Field(900.0, gt=0)
    profile: float = Field(300.0, gt=0)
    model: float = Field(180.0, gt=0)


class BenchmarkConfig(ConfigModel):
    min_speedup: float = Field(1.03, ge=1, description="Absolute floor: below this a change is never accepted")
    noise_multiplier: float = Field(2.0, ge=0, description="Threshold = max(min_speedup, 1 + multiplier * baseline noise CV)")
    baseline_repeats: int = Field(3, ge=2, description="How many times to re-run the baseline to measure noise")
    bootstrap_samples: int = Field(2000, ge=100)
    confidence: float = Field(0.95, gt=0, lt=1)
    exclusive: bool = Field(True, description="Benchmarks wait for all tests to finish and run alone (CPU targets). Set False when tests and benchmarks use different resources.")
    rebenchmark_parent: bool = Field(False, description="Re-measure the parent right before each candidate so machine drift between iterations cannot bias the decision. Doubles benchmark cost.")
    required_workloads: list[str] = Field(default_factory=list, description="Fixed workload IDs that every benchmark invocation must measure")
    min_workload_retention: float = Field(0.98, gt=0, le=1, description="Minimum candidate/parent throughput ratio for each required workload")

    @model_validator(mode="after")
    def validate_workloads(self):
        if len(self.required_workloads) != len(set(self.required_workloads)) or any(not x.strip() for x in self.required_workloads):
            raise ValueError("benchmark.required_workloads must contain unique nonempty IDs")
        return self


class SearchConfig(ConfigModel):
    iterations: int = Field(3, ge=1)
    candidates_per_iteration: int = Field(3, ge=1)
    max_parallel_tests: int = Field(2, ge=1)
    max_parallel_workers: int = Field(4, ge=1)
    max_parallel_benchmarks: int = Field(1, ge=1, le=1, description="One benchmark per runner resource")
    max_patch_retries: int = Field(1, ge=0, description="Times to re-ask the worker after a patch fails to apply or fails correctness, feeding the failure back. 0 disables retries.")
    beam_width: int = Field(1, ge=1, description="How many accepted heads to keep and expand each iteration. 1 is greedy best-child.")
    planner_retries: int = Field(2, ge=0, description="Extra planner attempts after a transient failure (timeout, rate limit, 5xx) before that head is skipped for the iteration.")
    planner_retry_backoff_s: float = Field(30.0, ge=0, description="Wait before the first planner retry; doubles on each further attempt.")


ProviderKind = Literal["openai", "mock"]


class ProviderConfig(ConfigModel):
    planner: ProviderKind = "mock"
    worker: ProviderKind = "mock"
    planner_model: str = "gpt-4.1"
    worker_model: str = "gpt-4.1-mini"
    worker_base_url: Optional[str] = Field(None, description="OpenAI-compatible endpoint, e.g. a Baseten deployment")
    worker_api_key_env: str = "OPENAI_API_KEY"
    planner_api_key_env: str = "OPENAI_API_KEY"
    mock_patches_dir: Optional[str] = None


class ProfileConfig(ConfigModel):
    """How much of the profile to keep. Storage depth and planner depth are deliberately separate:
    the before/after diff needs a deep list so a hotspot that merely fell in the ranking is not
    mistaken for one that was eliminated, while the planner keeps its small prompt budget."""
    retain: int = Field(40, ge=1, description="Hotspot rows kept in storage for the before/after diff")


class ContextConfig(ConfigModel):
    max_hotspots: int = Field(12, ge=1, description="Hotspot rows shown to the planner (prompt budget)")
    max_source_chars: int = Field(14000, ge=1)
    history_limit: int = Field(30, ge=1)


class ExecutionConfig(ConfigModel):
    backend: Literal["docker", "local"] = "docker"
    image: str = Field("hotpath-runner:local", min_length=1)
    cpus: float = Field(2, gt=0)
    memory_mb: int = Field(4096, ge=128)
    pids_limit: int = Field(128, ge=1)
    tmpfs_mb: int = Field(512, ge=1)
    output_limit_bytes: int = Field(1048576, ge=1024)
    gpu: Optional[str] = None
    devices: list[str] = Field(default_factory=list,
                               description="Linux device paths passed to Docker, e.g. /dev/kfd and /dev/dri for ROCm")
    group_add: list[str] = Field(default_factory=list,
                                 description="Supplemental container groups needed by accelerator devices")
    runtime: Optional[str] = None

    @model_validator(mode="after")
    def validate_device_paths(self):
        if any(not device.startswith("/dev/") or "," in device for device in self.devices):
            raise ValueError("execution.devices entries must be absolute /dev paths without commas")
        if any(not group or any(ch in group for ch in ",/\\") for group in self.group_add):
            raise ValueError("execution.group_add entries must be group names or IDs")
        return self


class HotpathConfig(ConfigModel):
    name: str
    target: str = Field(description="Path to the target repository")
    test_cmd: str
    bench_cmd: str
    profile_cmd: Optional[str] = None
    editable: list[str] = Field(description="Glob patterns (relative to target) the agent may edit")
    locked: list[str] = Field(default_factory=list, description="Glob patterns the agent must never touch")
    strategies: list[str] = Field(default_factory=lambda: list(DEFAULT_STRATEGIES))
    timeouts: TimeoutConfig = TimeoutConfig()
    benchmark: BenchmarkConfig = BenchmarkConfig()
    search: SearchConfig = SearchConfig()
    provider: ProviderConfig = ProviderConfig()
    context: ContextConfig = ContextConfig()
    profile: ProfileConfig = ProfileConfig()
    execution: ExecutionConfig = ExecutionConfig()
    correctness_contract: str = "Preserve outputs, ordering, exceptions, and supported inputs exactly."
    workdir: str = Field(".hotpath", description="Where worktrees and the database live (relative to target)")
    db_path: Optional[str] = None

    @model_validator(mode="after")
    def validate_shared_gpu(self):
        if (self.execution.gpu or self.execution.devices) and not self.benchmark.exclusive:
            raise ValueError("GPU tests and benchmarks share the same device; benchmark.exclusive must be true")
        return self


# --------------------------------------------------------------------------- #
# Profiling
# --------------------------------------------------------------------------- #

class Hotspot(BaseModel):
    function: str
    file: str
    line: int = 0
    self_time: float
    total_time: float
    pct: float = Field(description="Share of total self time, 0-100")
    calls: int = 0


class ProfileFrame(BaseModel):
    """An observed, nested profiler event; children are actual call relationships."""
    function: str
    file: str = ""
    line: int = 0
    self_time: float
    total_time: float
    calls: int = 0
    children: list[ProfileFrame] = Field(default_factory=list)


class ProfileSummary(BaseModel):
    tool: str = "none"
    total_time: float = 0.0
    hotspots: list[Hotspot] = Field(default_factory=list)
    commit: str = ""
    note: str = ""
    # Truncation provenance. Without these, a hotspot absent from a profile is indistinguishable
    # from one measured at zero, and a before/after diff would claim eliminations it cannot prove.
    # Defaulted so runs already in SQLite keep deserializing; `retained == 0` marks such a run.
    n_functions_total: int = Field(0, description="Rows the profiler emitted before truncation; 0 if unknown")
    retained: int = Field(0, description="Rows actually stored; 0 marks a profile written before this was recorded")
    residual_self_time: float = Field(0.0, description="total_time minus the self time of retained rows")
    cutoff_self_time: float = Field(0.0, description="Smallest retained self time: the upper bound on any omitted row")
    completeness_known: bool = False
    flamegraph: list[ProfileFrame] = Field(default_factory=list)
    flamegraph_source: str = ""
    flamegraph_unavailable_reason: str = ""


# --------------------------------------------------------------------------- #
# Model I/O (validated structured outputs)
# --------------------------------------------------------------------------- #

class Edit(BaseModel):
    """A search/replace edit. Far more robust from a model than a raw unified diff.

    An empty `search` creates `file`, which must not exist yet, with `replace` as its full contents."""
    file: str
    search: str
    replace: str


#: A hypothesis may touch this many files in total (for example a new Triton kernel plus its call site).
MAX_FILES_PER_HYPOTHESIS = 3


class Hypothesis(BaseModel):
    idea: str
    strategy: str
    target_file: str
    rationale: str
    risk: Literal["low", "medium", "high"]
    extra_files: list[str] = Field(default_factory=list, description=(
        "Other files the change must also touch, existing or new, at most "
        f"{MAX_FILES_PER_HYPOTHESIS - 1}; each must match an editable pattern. Empty for a one-file change."))

    @property
    def files(self) -> list[str]:
        """Every file this hypothesis may edit or create, target first, without duplicates."""
        return list(dict.fromkeys([self.target_file, *self.extra_files]))


class PlanResponse(BaseModel):
    hypotheses: list[Hypothesis]
    notes: str


class PatchResponse(BaseModel):
    edits: list[Edit]
    reasoning: str


# --------------------------------------------------------------------------- #
# Harness measurements
# --------------------------------------------------------------------------- #

class CmdResult(BaseModel):
    cmd: str
    exit_code: int
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool = False


class CorrectnessResult(BaseModel):
    passed: bool
    exit_code: int
    duration_s: float
    timed_out: bool = False
    output_tail: str = ""


class BenchmarkStats(BaseModel):
    metric: str = "seconds"
    higher_is_better: bool = False
    samples: list[float]
    n: int
    median: float
    mean: float
    stdev: float
    cv: float = Field(description="Coefficient of variation within this run")
    duration_s: float = 0.0
    output_tail: str = ""
    workload_samples: dict[str, list[float]] = Field(default_factory=dict, description="Per-workload samples in the headline metric's direction")


class SpeedComparison(BaseModel):
    speedup_vs_parent: float
    speedup_vs_baseline: float
    ci_low: float
    ci_high: float
    threshold: float
    significant: bool
    reason: str
    confidence: float = 0.95
    workload_ratios: dict[str, float] = Field(default_factory=dict, description="Candidate/parent throughput ratio by required workload")


# --------------------------------------------------------------------------- #
# Experiment tree
# --------------------------------------------------------------------------- #

class ExperimentStatus(str, Enum):
    pending = "pending"
    generating = "generating"          # worker model is writing the patch
    patch_failed = "patch_failed"      # model produced no usable edit, or edit did not apply
    locked_file = "locked_file"        # edit touched a locked path (tests/bench). Reward hacking blocked.
    testing = "testing"
    rejected_correctness = "rejected_correctness"
    benchmarking = "benchmarking"
    rejected_speed = "rejected_speed"  # correct but not faster beyond the noise threshold
    accepted = "accepted"              # correct and faster: became the new head
    not_selected = "not_selected"      # correct and faster, but a sibling was faster still
    timeout = "timeout"
    error = "error"


TERMINAL_STATUSES = {
    ExperimentStatus.patch_failed, ExperimentStatus.locked_file, ExperimentStatus.rejected_correctness,
    ExperimentStatus.rejected_speed, ExperimentStatus.accepted, ExperimentStatus.not_selected,
    ExperimentStatus.timeout, ExperimentStatus.error,
}
REJECTED_STATUSES = TERMINAL_STATUSES - {ExperimentStatus.accepted, ExperimentStatus.not_selected}


class Experiment(BaseModel):
    id: str = Field(default_factory=lambda: new_id("exp"))
    run_id: str
    parent_id: Optional[str] = Field(None, description="None means the parent is the baseline")
    parent_commit: str = ""
    iteration: int
    retry_of: Optional[str] = Field(None, description="If set, this experiment is a retry of that experiment id after feeding its failure back to the worker")
    previous_failure: str = Field("", description="The failure text fed back to the worker on a retry, verbatim as the prompt received it")
    previous_edits: list[Edit] = Field(default_factory=list)
    hypothesis: Hypothesis
    edits: list[Edit] = Field(default_factory=list)
    diff: str = ""
    files_changed: list[str] = Field(default_factory=list)
    reasoning: str = ""
    status: ExperimentStatus = ExperimentStatus.pending
    correctness: Optional[CorrectnessResult] = None
    benchmark: Optional[BenchmarkStats] = None
    parent_benchmark: Optional[BenchmarkStats] = None
    comparison: Optional[SpeedComparison] = None
    reject_reason: Optional[str] = None
    commit: Optional[str] = Field(None, description="Commit sha of the accepted state")
    logs: list[str] = Field(default_factory=list)
    timings: dict[str, float] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)

    def log(self, msg: str) -> None:
        self.logs.append(f"{now().strftime('%H:%M:%S')} {msg}")
        self.updated_at = now()
        from hotpath.observability import event
        event(msg, run_id=self.run_id, experiment_id=self.id, status=self.status.value)

    def set_status(self, status: ExperimentStatus, reason: Optional[str] = None) -> None:
        self.status = status
        if reason is not None:
            self.reject_reason = reason
        self.updated_at = now()
        self.log(f"status -> {status.value}" + (f": {reason}" if reason else ""))


RunStatus = Literal["starting", "baseline", "running", "finished", "failed", "stopped"]


class PullRequestRecord(BaseModel):
    """Where a run's verified changes were published. `url` is empty when only the branch was pushed
    (no GitHub credentials), in which case `compare_url` is the pre-filled link to open it by hand."""
    branch: str
    base: str
    head_sha: str
    remote: str = "origin"
    method: Literal["gh", "token", "link", "local"] = "local"
    url: str = ""
    number: Optional[int] = None
    compare_url: str = ""
    pruned: bool = False
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)


class RunState(BaseModel):
    id: str = Field(default_factory=lambda: new_id("run"))
    config_name: str
    target: str
    config_snapshot: dict = Field(default_factory=dict)
    execution_environment: dict = Field(default_factory=dict)
    status: RunStatus = "starting"
    base_commit: str = ""
    base_branch: str = Field("", description="Branch checked out when the run measured its baseline; empty if detached or unknown")
    head_commit: str = ""
    head_experiment_id: Optional[str] = None
    baseline_benchmark: Optional[BenchmarkStats] = None
    baseline_noise_cv: float = 0.0
    baseline_runs: list[float] = Field(default_factory=list, description="Median of each repeated baseline run")
    baseline_profile: Optional[ProfileSummary] = None
    head_profile: Optional[ProfileSummary] = None
    head_benchmark: Optional[BenchmarkStats] = None
    best_speedup: float = 1.0
    iteration: int = 0
    total_iterations: int = 0
    error: Optional[str] = None
    pull_request: Optional[PullRequestRecord] = None
    logs: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)

    def log(self, msg: str) -> None:
        self.logs.append(f"{now().strftime('%H:%M:%S')} {msg}")
        self.updated_at = now()
        from hotpath.observability import event
        event(msg, run_id=self.id, config=self.config_name, status=self.status)
