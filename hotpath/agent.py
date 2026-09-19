"""Planner and workers: the side of Hotpath that is allowed to be wrong."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

from hotpath import observability as obs
from hotpath.context import build_source_context, read_target_file, source_exists
from hotpath.providers.base import PatchRequest, PlanRequest, Provider
from hotpath.profiler import render_profile
from hotpath.schema import (MAX_FILES_PER_HYPOTHESIS, Experiment, ExperimentStatus, HotpathConfig, PlanResponse,
                            ProfileSummary, RunState)
from hotpath.store import Store
from hotpath.workspace import Workspace


def is_transient(exc: BaseException) -> bool:
    """A failure the same request may not hit a little later: timeouts, rate limits, 5xx."""
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status in (408, 409, 429) or status >= 500
    # The OpenAI SDK's connection and timeout errors carry no status code.
    return type(exc).__name__ in {"APIConnectionError", "APITimeoutError"}


def describe_error(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__


class Agent:
    def __init__(self, cfg: HotpathConfig, planner: Provider, worker: Provider, ws: Workspace, store: Store):
        self.cfg = cfg
        self.planner = planner
        self.worker = worker
        self.ws = ws
        self.store = store
        self.worker_sem = asyncio.Semaphore(cfg.search.max_parallel_workers)

    async def plan(self, run: RunState, profile: ProfileSummary, history: list[Experiment], repo: Path) -> tuple[PlanResponse, str, list[tuple]]:
        """Returns (plan with runnable hypotheses, source context, [(blocked hypothesis, reason)])."""
        source = build_source_context(repo, profile, self.cfg.context, self.cfg.editable, self.cfg.locked)
        bench = run.head_benchmark or run.baseline_benchmark
        req = PlanRequest(
            profile_table=render_profile(profile, self.cfg.context.max_hotspots), source_context=source,
            history=history[-self.cfg.context.history_limit:], strategies=self.cfg.strategies,
            editable=self.cfg.editable, locked=self.cfg.locked,
            metric=bench.metric if bench else "seconds", higher_is_better=bench.higher_is_better if bench else False,
            max_hypotheses=self.cfg.search.candidates_per_iteration, iteration=run.iteration, best_speedup=run.best_speedup,
        )
        with obs.span("hotpath.plan", f"iteration {run.iteration}", provider=self.planner.name,
                      context_chars=len(source) + len(req.profile_table)):
            t = time.perf_counter()
            attempts = self.cfg.search.planner_retries + 1
            for attempt in range(1, attempts + 1):
                try:
                    plan = await asyncio.wait_for(self.planner.plan(req), timeout=self.cfg.timeouts.model)
                    break
                except Exception as e:
                    if attempt == attempts or not is_transient(e):
                        raise
                    delay = self.cfg.search.planner_retry_backoff_s * 2 ** (attempt - 1)
                    run.log(f"planner attempt {attempt}/{attempts} failed ({describe_error(e)}); retrying in {delay:.0f}s")
                    await asyncio.sleep(delay)
            obs.event("Planner decision", run_id=run.id, iteration=run.iteration, notes=plan.notes,
                      hypotheses=len(plan.hypotheses))
            for hypothesis in plan.hypotheses:
                obs.event("Planner hypothesis", run_id=run.id, idea=hypothesis.idea,
                          rationale=hypothesis.rationale, target_file=hypothesis.target_file)
            run.log(f"planner ({self.planner.name}) proposed {len(plan.hypotheses)} hypotheses in {time.perf_counter()-t:.1f}s")
        # Deterministic filtering: no model call needed to drop obviously invalid targets.
        from hotpath.workspace import path_allowed
        kept, blocked = [], []
        for h in plan.hypotheses:
            why = next((reason for ok, reason in (path_allowed(f, self.cfg.editable, self.cfg.locked)
                                                  for f in h.files) if not ok), None)
            if why is None:
                kept.append(h)
            else:
                blocked.append((h, why))
        plan.hypotheses = kept[: self.cfg.search.candidates_per_iteration]
        return plan, source, blocked

    async def generate_patches(self, exps: list[Experiment], repo: Path, related_context: str,
                               previous_failures: dict[str, str] | None = None) -> None:
        """Fill exp.edits/reasoning for every experiment, in parallel. Failures become patch_failed.

        `previous_failures` maps experiment id -> the failure text of a prior attempt; when present
        the worker is told what went wrong so a retry can correct it.
        """
        failures = previous_failures or {}
        async def one(exp: Experiment) -> None:
            async with self.worker_sem:
                exp.set_status(ExperimentStatus.generating)
                self.store.save_experiment(exp)
                t = time.perf_counter()
                files = exp.hypothesis.files
                if len(files) > MAX_FILES_PER_HYPOTHESIS:
                    exp.set_status(ExperimentStatus.patch_failed, f"hypothesis touches {len(files)} files; "
                                   f"at most {MAX_FILES_PER_HYPOTHESIS} are allowed")
                    exp.timings["generate"] = time.perf_counter() - t
                    self.store.save_experiment(exp)
                    return
                try:
                    from hotpath.workspace import path_allowed
                    for f in files:
                        allowed, reason = path_allowed(f, self.cfg.editable, self.cfg.locked)
                        if not allowed:
                            raise ValueError(reason)
                    # The worker sees the complete current text of every existing file it may edit.
                    # Only extra files may be new (a kernel beside its call site): the target is the
                    # hotspot being attacked, so a missing target is a hallucinated path, not a creation.
                    sources, new_files = {}, []
                    for f in files:
                        if f == exp.hypothesis.target_file or source_exists(repo, f):
                            sources[f] = read_target_file(repo, f, self.cfg.context.max_source_chars)
                        else:
                            new_files.append(f)
                    req = PatchRequest(
                        hypothesis=exp.hypothesis,
                        target_source=sources.get(exp.hypothesis.target_file, ""),
                        extra_sources={f: src for f, src in sources.items() if f != exp.hypothesis.target_file},
                        new_files=new_files,
                        related_context=related_context[:4000] + ("\n[Related context truncated]" if len(related_context) > 4000 else ""),
                        editable=self.cfg.editable, locked=self.cfg.locked,
                        experiment_id=exp.id,
                        correctness_contract=getattr(self.cfg, "correctness_contract", "Preserve all externally observable behavior."),
                        previous_edits=getattr(exp, "previous_edits", []),
                        previous_failure=failures.get(exp.id, exp.previous_failure),
                        parent_commit=exp.parent_commit,
                        history=[item for item in self.store.list_experiments(exp.run_id)
                                 if item.id != exp.id][-self.cfg.context.history_limit:])
                    with obs.span("hotpath.generate", exp.hypothesis.idea, provider=self.worker.name,
                                  experiment_id=exp.id):
                        resp = await asyncio.wait_for(self.worker.generate_patch(req), timeout=self.cfg.timeouts.model)
                    exp.edits, exp.reasoning = resp.edits, resp.reasoning
                    undeclared = sorted({edit.file for edit in resp.edits} - set(files))
                    if undeclared:
                        raise ValueError(f"worker edited {', '.join(undeclared)}, which the hypothesis did not declare; "
                                         f"only {', '.join(files)} may be edited or created")
                    obs.event("Worker rationale", run_id=exp.run_id, experiment_id=exp.id,
                              reasoning=resp.reasoning, edit_count=len(resp.edits))
                    exp.log(f"worker ({self.worker.name}) returned {len(resp.edits)} edit(s)")
                    if not resp.edits:
                        exp.set_status(ExperimentStatus.patch_failed, "worker returned no edits")
                except asyncio.TimeoutError:
                    exp.set_status(ExperimentStatus.patch_failed, f"worker timed out after {self.cfg.timeouts.model}s")
                except Exception as e:
                    exp.set_status(ExperimentStatus.patch_failed, f"worker error: {type(e).__name__}: {e}")
                finally:
                    exp.timings["generate"] = time.perf_counter() - t
                    self.store.save_experiment(exp)

        await asyncio.gather(*(one(e) for e in exps))
