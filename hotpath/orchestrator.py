"""The search loop: profile -> plan -> generate -> verify -> benchmark -> accept/reject -> repeat."""
from __future__ import annotations

import asyncio
import logging
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from hotpath import observability as obs
from hotpath.agent import Agent, describe_error
from hotpath.config import COMMAND_KEYS, config_snapshot, legacy_redacted_command
from hotpath.execution import execution_metadata
from hotpath.harness import Harness
from hotpath.providers.base import Provider, build_provider
from hotpath.schema import (TERMINAL_STATUSES, BenchmarkStats, Experiment, ExperimentStatus, HotpathConfig,
                            ProfileSummary, RunState)
from hotpath.store import Store
from hotpath.workspace import Workspace

log = logging.getLogger("hotpath")

# A run fails, rather than spinning through its budget, once the planner has failed on every head
# for this many consecutive iterations. A single failure only skips that head for one iteration.
MAX_PLANNER_OUTAGES = 3

# Settings that decide what a measurement means. A resumed run compares new candidates against
# benchmarks already in the store, so these must be identical. rebenchmark_parent may be turned on:
# it only adds a fresh parent measurement beside each candidate.
MEASUREMENT_KEYS = ("test_cmd", "bench_cmd", "profile_cmd", "editable", "locked", "execution", "benchmark")


class ResumeError(ValueError):
    """The stored run cannot be continued without mixing incomparable measurements."""


@dataclass
class BeamNode:
    """One head the search can expand from: a commit, its benchmark, and its profile."""
    commit: str
    experiment_id: Optional[str]        # None for the baseline
    benchmark: BenchmarkStats
    profile: Optional[ProfileSummary]
    speedup: float                      # vs baseline


class Orchestrator:
    def __init__(self, cfg: HotpathConfig, store: Store | None = None,
                 planner: Provider | None = None, worker: Provider | None = None, resume: str | None = None,
                 autocommit: bool = False):
        cfg = HotpathConfig.model_validate(cfg.model_dump())
        self.cfg = cfg
        self.target = Path(cfg.target).resolve()
        self.ws = Workspace(self.target, Path(cfg.workdir))
        self.store = store or Store(cfg.db_path or (self.ws.workdir / "hotpath.db"))
        self.planner = planner or build_provider(cfg.provider.planner, cfg.provider, "planner", cfg.timeouts.model)
        self.worker = worker or build_provider(cfg.provider.worker, cfg.provider, "worker", cfg.timeouts.model)
        self.harness = Harness(cfg, self.ws, self.store)
        self.agent = Agent(cfg, self.planner, self.worker, self.ws, self.store)
        self._autocommit = autocommit
        self._resume = resume is not None
        if resume is not None:
            self.run = self._load_resumable(resume)
        else:
            self.run = RunState(config_name=cfg.name, target=str(self.target), total_iterations=cfg.search.iterations,
                                config_snapshot=config_snapshot(cfg))
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    def _save(self) -> None:
        self.store.save_run(self.run)

    async def execute(self) -> RunState:
        run = self.run
        if self._resume:
            # Refuse before touching the stored run: a rejected resume must leave it as it was.
            await self._check_resume()
        self._save()
        with obs.transaction(f"hotpath.run {self.cfg.name}", run_id=run.id, config=self.cfg.name) as tx:
            try:
                await self._execute()
            except asyncio.CancelledError:
                self._stop.set()
                run.status = "stopped"
                run.log("run cancelled; active execution environments are being removed")
                raise
            except Exception as e:
                run.status = "failed"
                run.error = f"{type(e).__name__}: {e}"
                run.log(traceback.format_exc())
                tx.set_status("internal_error")
                obs.capture(e)
                log.exception("run failed")
            finally:
                if run.status not in ("failed",):
                    run.status = "stopped" if self._stop.is_set() else run.status
                self._save()
                self.ws.cleanup()
                tx.set_data("status", run.status)
                tx.set_data("best_speedup", run.best_speedup)
                obs.record_run(run, self.store.list_experiments(run.id))
        return run

    async def _execute(self) -> None:
        if self._resume:
            beam = await self._restore_beam()
            await self._search(beam, self.run.iteration + 1)
            return
        run, cfg = self.run, self.cfg
        run.log(f"target {self.target}")
        run.execution_environment = await execution_metadata(cfg.execution)
        if cfg.execution.backend == "docker":
            cfg.execution.image = run.execution_environment["image_id"]
        run.config_snapshot = config_snapshot(cfg)
        self._save()
        run.base_commit = run.head_commit = self.ws.ensure_repo(autocommit=self._autocommit)
        run.base_branch = self.ws.current_branch()
        run.log(f"base commit {run.base_commit[:8]}" + (f" on {run.base_branch}" if run.base_branch else ""))

        # ---- Baseline: the reference every experiment is compared against ----
        run.status = "baseline"; self._save()
        bench, noise, medians = await self.harness.measure_baseline(run.base_commit)
        run.baseline_benchmark = run.head_benchmark = bench
        run.baseline_noise_cv, run.baseline_runs = noise, medians
        run.log(f"baseline median {bench.median:.5f} {bench.metric}, run-to-run noise CV {noise:.2%}")
        run.baseline_profile = run.head_profile = await self.harness.run_profile(run.base_commit)
        run.log(f"profile: {len(run.head_profile.hotspots)} hotspots ({run.head_profile.tool})")
        run.status = "running"; self._save()
        await self._search([BeamNode(run.base_commit, None, bench, run.baseline_profile, 1.0)], 1)

    async def _search(self, beam: list[BeamNode], first_iteration: int) -> None:
        """A beam over the top-K accepted heads. beam_width 1 is greedy best-child."""
        run = self.run
        outages, last_planner_error = 0, ""
        for it in range(first_iteration, run.total_iterations + 1):
            if self._stop.is_set():
                break
            run.iteration = it; self._save()
            history = self.store.list_experiments(run.id)
            accepted_all: list[Experiment] = []
            any_hypotheses = False
            planned = planner_failures = 0
            for node in beam:
                if self._stop.is_set():
                    break
                history = self.store.list_experiments(run.id)
                # The agent edits on top of this head, so it must read that head's source, not the
                # pristine target — otherwise a hypothesis touching an already-optimized function fails
                # with "search text not found".
                node_src = self.ws.materialize(node.commit, f"head_{node.experiment_id or 'base'}")
                planning_state = run.model_copy(update={"head_commit": node.commit,
                    "head_experiment_id": node.experiment_id, "head_benchmark": node.benchmark,
                    "best_speedup": node.speedup})
                try:
                    plan, context, blocked = await self.agent.plan(planning_state, node.profile, history, node_src)
                except Exception as e:
                    # One planner outage must not end an overnight search: skip this head for this
                    # iteration and plan from it again in the next one.
                    planner_failures += 1
                    last_planner_error = describe_error(e)
                    run.logs = planning_state.logs
                    run.log(f"planner failed for head {node.experiment_id or 'baseline'} ({last_planner_error}); "
                            f"skipping it in iteration {it}")
                    obs.capture(e, run_id=run.id)
                    self._save()
                    continue
                planned += 1
                run.logs = planning_state.logs
                if self._stop.is_set():
                    break
                self._save()
                # Hypotheses aimed at locked/non-editable files are recorded, not executed: no worker call.
                for h, why in blocked:
                    e = Experiment(run_id=run.id, parent_id=node.experiment_id, parent_commit=node.commit,
                                   iteration=it, hypothesis=h)
                    e.log(f"hypothesis: {h.idea}")
                    e.set_status(ExperimentStatus.locked_file, f"blocked before generation: {why}")
                    self.store.save_experiment(e)
                    run.log(f"{e.id} [locked_file] {h.idea} ({why})")
                if not plan.hypotheses:
                    continue
                any_hypotheses = True
                exps = [Experiment(run_id=run.id, parent_id=node.experiment_id, parent_commit=node.commit,
                                   iteration=it, hypothesis=h) for h in plan.hypotheses]
                for e in exps:
                    e.log(f"hypothesis: {e.hypothesis.idea}")
                    self.store.save_experiment(e)
                results = await self._generate_and_run(run, exps, node_src, context, node.benchmark)
                for e in results:
                    run.log(f"{e.id} [{e.status.value}] {e.hypothesis.idea}" + (f" ({e.reject_reason})" if e.reject_reason else ""))
                results = await self._apply_retries(run, it, results, node_src, context, node)
                accepted_all += [e for e in results if e.status == ExperimentStatus.accepted]
            if planner_failures and not planned:
                outages += 1
                if outages >= MAX_PLANNER_OUTAGES:
                    raise RuntimeError(f"planner failed on every head for {outages} consecutive iterations "
                                       f"(last error: {last_planner_error}); continue later with --resume {run.id}")
                run.log(f"iteration {it}: planner failed on every head ({outages}/{MAX_PLANNER_OUTAGES} consecutive)")
                self._save()
                continue
            outages = 0
            if not any_hypotheses and not planner_failures:
                run.log("planner proposed nothing new; stopping early")
                break
            # ---- Choose the next beam: top-K distinct heads by speedup, over new candidates + old heads ----
            if accepted_all:
                pool = list(beam) + [BeamNode(e.commit, e.id, e.benchmark, None, e.comparison.speedup_vs_baseline)
                                     for e in accepted_all]
                beam = await self._advance_beam(pool, accepted_all)
            else:
                run.log(f"iteration {it}: nothing accepted")
            self._save()
        run.status = "stopped" if self._stop.is_set() else "finished"
        run.log(f"done: best {run.best_speedup:.3f}x vs baseline over {len(self.store.list_experiments(run.id))} experiments")

    async def _advance_beam(self, pool: list[BeamNode], candidates: list[Experiment]) -> list[BeamNode]:
        """Keep the top-K distinct heads by speedup, promote the best to the run head, record
        candidates that missed the cut as not_selected, and profile every surviving head."""
        run, cfg = self.run, self.cfg
        best_by_commit: dict[str, BeamNode] = {}
        for nd in sorted(pool, key=lambda n: n.speedup, reverse=True):
            best_by_commit.setdefault(nd.commit, nd)
        ranked = sorted(best_by_commit.values(), key=lambda n: n.speedup, reverse=True)
        new_beam = ranked[: cfg.search.beam_width]
        global_best = ranked[0]
        if global_best.experiment_id and global_best.experiment_id != run.head_experiment_id:
            # Profile first: a crash must never leave a new head recorded with the old head's profile.
            profile = await self.harness.run_profile(global_best.commit)
            run.head_commit, run.head_experiment_id = global_best.commit, global_best.experiment_id
            run.head_benchmark, run.best_speedup = global_best.benchmark, global_best.speedup
            run.head_profile = profile
            run.log(f"new head {global_best.experiment_id}: {run.best_speedup:.3f}x vs baseline")
        kept_ids = {n.experiment_id for n in new_beam}
        for e in candidates:
            if e.id not in kept_ids:
                e.set_status(ExperimentStatus.not_selected,
                             f"correct and {e.comparison.speedup_vs_parent:.3f}x faster, but not in the top-{cfg.search.beam_width} beam; may be re-proposed on a surviving head")
                self.store.save_experiment(e)
                # The verdict line already said [accepted]; without this the run log implies it shipped.
                run.log(f"{e.id} [not_selected] passed but not kept: {e.comparison.speedup_vs_baseline:.3f}x vs baseline "
                        f"is outside the top-{cfg.search.beam_width} beam")
        # Every surviving head needs a profile for the next iteration's planning.
        for nd in new_beam:
            if nd.profile is None:
                nd.profile = run.head_profile if nd.commit == run.head_commit else await self.harness.run_profile(nd.commit)
        return new_beam

    # ---- Resume ---------------------------------------------------------------------------------
    def _load_resumable(self, run_id: str) -> RunState:
        run = self.store.get_run(run_id)
        if run is None:
            raise ResumeError(f"no run {run_id} in {self.store.path}")
        if Path(run.target).resolve() != self.target:
            raise ResumeError(f"run {run_id} optimized {run.target}, not {self.target}")
        if not (run.base_commit and run.baseline_benchmark and run.baseline_runs):
            raise ResumeError(f"run {run_id} never finished its baseline; start a new run instead")
        if not run.config_snapshot:
            raise ResumeError(f"run {run_id} has no configuration snapshot, so its measurements cannot be checked for comparability")
        return run

    async def _check_resume(self) -> None:
        run, cfg = self.run, self.cfg
        environment = await execution_metadata(cfg.execution)
        if cfg.execution.backend == "docker":
            cfg.execution.image = environment["image_id"]
        now, then = config_snapshot(cfg), run.config_snapshot
        for key in COMMAND_KEYS:
            # Snapshots written before commands were shown stored only a digest of the raw text.
            raw = getattr(cfg, key)
            if raw and then.get(key) == legacy_redacted_command(raw):
                now[key] = then[key]
        changed = [k for k in MEASUREMENT_KEYS if _comparable(k, now.get(k)) != _comparable(k, then.get(k))]
        if changed:
            raise ResumeError(f"run {run.id} was measured with different {', '.join(changed)} settings; "
                              "resuming would compare new candidates against incomparable benchmarks")
        accepted = [e for e in self.store.list_experiments(run.id) if e.status == ExperimentStatus.accepted and e.commit]
        missing = [c for c in [run.base_commit] + [e.commit for e in accepted] if not self.ws.has_commit(c)]
        if missing:
            raise ResumeError(f"commits for run {run.id} are missing from the target repository: {', '.join(c[:8] for c in missing)}")
        self._resume_environment = environment

    async def _restore_beam(self) -> list[BeamNode]:
        """Rebuild the beam a stopped run would have carried into its next iteration.

        Each iteration keeps the top-K of (previous beam + newly accepted), which equals the top-K
        of the baseline plus every accepted experiment, so the store alone determines the beam."""
        run, cfg = self.run, self.cfg
        previous_status = run.status
        run.execution_environment = self._resume_environment
        run.config_snapshot = config_snapshot(cfg)
        run.total_iterations = max(run.total_iterations, cfg.search.iterations)
        run.status, run.error = "running", None
        run.log(f"resuming after status '{previous_status}' at iteration {run.iteration}")
        if previous_status == "running":
            run.log("the stored status was still 'running' (the process likely died); no other process may own this run")
        exps = self.store.list_experiments(run.id)
        for e in exps:
            if e.status not in TERMINAL_STATUSES:
                e.set_status(ExperimentStatus.error, "interrupted: the run ended before this experiment finished")
                self.store.save_experiment(e)
        accepted = [e for e in exps if e.status == ExperimentStatus.accepted
                    and e.commit and e.benchmark and e.comparison]
        pool = [BeamNode(run.base_commit, None, run.baseline_benchmark, run.baseline_profile, 1.0)]
        pool += [BeamNode(e.commit, e.id, e.benchmark, None, e.comparison.speedup_vs_baseline) for e in accepted]
        # Accepted results of the interrupted iteration never went through beam selection.
        beam = await self._advance_beam(pool, [e for e in accepted if e.iteration == run.iteration])
        run.log(f"restored beam: {', '.join(n.experiment_id or 'baseline' for n in beam)}; "
                f"best {run.best_speedup:.3f}x vs baseline")
        if not cfg.benchmark.rebenchmark_parent:
            run.log("parents are compared using benchmarks stored before the interruption; set "
                    "benchmark.rebenchmark_parent to re-measure each parent beside its candidates")
        if run.iteration >= run.total_iterations:
            run.log(f"all {run.total_iterations} iterations already ran; pass --iterations to extend the run")
        self._save()
        return beam

    async def _generate_and_run(self, run: RunState, exps: list[Experiment], head_src: Path, context: str,
                                parent_bench: BenchmarkStats, previous_failures: dict[str, str] | None = None) -> list[Experiment]:
        """Generate patches, run the runnable ones through the harness against `parent_bench`, and return
        every experiment with its final status (including ones that failed during generation).

        Each experiment is one Sentry transaction (generate -> patch -> test -> bench), tagged with the
        run and its verdict, so a rejected_correctness experiment can be found and opened on its own."""
        ran: set[int] = set()

        async def one(e: Experiment) -> None:
            with obs.transaction("hotpath.experiment", op="hotpath.experiment", run_id=run.id,
                                 experiment_id=e.id, iteration=e.iteration, retry=bool(e.retry_of)) as tx:
                await self.agent.generate_patches([e], head_src, context, previous_failures)
                if e.status == ExperimentStatus.generating:
                    ran.add(id(e))
                    await self.harness.run_experiment(e, parent_bench, run.baseline_benchmark, run.baseline_noise_cv)
                tx.set_tag("hotpath.status", e.status.value)
                if e.status == ExperimentStatus.error:
                    tx.set_status("internal_error")
                elif e.status == ExperimentStatus.timeout:
                    tx.set_status("deadline_exceeded")

        await asyncio.gather(*(one(e) for e in exps))
        # Experiments that failed during generation (e.g. worker returned no edits) never reach the
        # harness; include them so they are logged, recorded, and eligible for a retry.
        return [e for e in exps if id(e) in ran] + [e for e in exps if id(e) not in ran]

    async def _apply_retries(self, run: RunState, iteration: int, results: list[Experiment], head_src: Path,
                             context: str, parent_node: "BeamNode") -> list[Experiment]:
        """Re-ask the worker (up to max_patch_retries times) for experiments that failed to apply or
        failed correctness, feeding the failure back. Each retry is its own recorded experiment, so the
        original failure stays visible to the planner and the retry can be accepted like any candidate."""
        retryable = {ExperimentStatus.patch_failed, ExperimentStatus.rejected_correctness}
        all_results = list(results)
        frontier = results
        for attempt in range(1, self.cfg.search.max_patch_retries + 1):
            if self._stop.is_set():
                break
            failed = [e for e in frontier if e.status in retryable]
            if not failed:
                break
            retries, feedback = [], {}
            for e in failed:
                # Store the fed-back text on the retry: it is what the worker was actually told, and
                # the dashboard shows it verbatim rather than reconstructing it and drifting.
                previous_failure = self._failure_feedback(e)
                r = Experiment(run_id=run.id, parent_id=parent_node.experiment_id, parent_commit=parent_node.commit,
                               iteration=iteration, retry_of=e.id, hypothesis=e.hypothesis,
                               previous_failure=previous_failure, previous_edits=e.edits)
                r.log(f"retry (attempt {attempt}) of {e.id}; previous failure fed back to the worker")
                self.store.save_experiment(r)
                feedback[r.id] = previous_failure
                retries.append(r)
                run.log(f"retrying {e.id} [{e.status.value}] -> {r.id} (attempt {attempt})")
            res = await self._generate_and_run(run, retries, head_src, context, parent_node.benchmark, feedback)
            for r in res:
                run.log(f"{r.id} [retry of {r.retry_of}] [{r.status.value}] {r.hypothesis.idea}"
                        + (f" ({r.reject_reason})" if r.reject_reason else ""))
            all_results.extend(res)
            frontier = res
        return all_results

    @staticmethod
    def _failure_feedback(e: Experiment) -> str:
        """What to tell the worker about a failed attempt: the reason plus the test output if any."""
        parts = []
        if e.reject_reason:
            parts.append(e.reject_reason)
        if e.status == ExperimentStatus.rejected_correctness and e.correctness and e.correctness.output_tail:
            parts.append("Test output:\n" + e.correctness.output_tail)
        feedback = "\n".join(parts) or e.status.value
        limit = 8192
        if len(feedback) > limit:
            feedback = "[earlier diagnostic text truncated]\n" + feedback[-limit:]
        return feedback

    def export_best(self, dest: Path) -> str | None:
        """Write the accepted state's source tree to dest. Returns the commit exported."""
        if not self.run.head_commit or self.run.head_commit == self.run.base_commit:
            return None
        self.ws.checkout_into(self.run.head_commit, dest)
        return self.run.head_commit


def _comparable(key: str, value):
    if key == "benchmark" and isinstance(value, dict):
        return {k: v for k, v in value.items() if k != "rebenchmark_parent"}
    return value
