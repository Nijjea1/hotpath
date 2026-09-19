"""End-to-end: mock provider proposes, real harness decides, tree is recorded."""
import json
from pathlib import Path

from hotpath.export import build_bundle
from hotpath.orchestrator import Orchestrator
from hotpath.schema import ExperimentStatus, HotpathConfig


def _write_patches(d: Path):
    d.mkdir()
    fast = {"idea": "set-based dedupe", "strategy": "data structure", "target_file": "mod.py", "rationale": "O(n^2)->O(n)", "risk": "low",
            "edits": [{"file": "mod.py", "search": "        if i not in out:\n            out.append(i)\n",
                       "replace": "        if i not in seen:\n            seen.add(i)\n            out.append(i)\n"},
                      {"file": "mod.py", "search": "    out = []\n", "replace": "    out = []\n    seen = set()\n"}],
            "reasoning": "set membership"}
    wrong = {"idea": "off by one", "strategy": "x", "target_file": "mod.py", "rationale": "r", "risk": "high",
             "edits": [{"file": "mod.py", "search": "    return len(out)", "replace": "    return len(out) + 1"}], "reasoning": ""}
    locked = {"idea": "weaken tests", "strategy": "x", "target_file": "tests/check.py", "rationale": "r", "risk": "low",
              "edits": [{"file": "tests/check.py", "search": "assert", "replace": "pass #"}], "reasoning": ""}
    crash = {"idea": "worker explodes", "strategy": "x", "target_file": "mod.py", "rationale": "r", "risk": "low", "raise": "model API 500"}
    for i, p in enumerate([fast, wrong, locked, crash]):
        (d / f"{i}.json").write_text(json.dumps(p))


async def test_full_run(cfg: HotpathConfig, tmp_path: Path):
    _write_patches(tmp_path / "patches")
    cfg.provider.mock_patches_dir = str(tmp_path / "patches")
    cfg.search.candidates_per_iteration = 4
    orch = Orchestrator(cfg)
    run = await orch.execute()
    assert run.status == "finished", run.error
    exps = orch.store.list_experiments(run.id)
    by_idea = {e.hypothesis.idea: e for e in exps}
    assert by_idea["set-based dedupe"].status == ExperimentStatus.accepted
    assert by_idea["off by one"].status == ExperimentStatus.rejected_correctness
    assert by_idea["weaken tests"].status == ExperimentStatus.locked_file
    assert by_idea["worker explodes"].status == ExperimentStatus.patch_failed
    assert run.head_experiment_id == by_idea["set-based dedupe"].id
    assert run.best_speedup > 1.03 and run.head_commit != run.base_commit
    assert run.head_benchmark.median < run.baseline_benchmark.median
    assert run.head_profile is not None
    # The user's checkout is untouched; the improvement lives in a commit.
    assert "seen = set()" not in (Path(cfg.target) / "mod.py").read_text()
    out = tmp_path / "export"
    assert orch.export_best(out) == run.head_commit
    assert "seen = set()" in (out / "mod.py").read_text()
    assert not orch.ws.worktrees_dir.exists() or not any(orch.ws.worktrees_dir.iterdir())


async def test_retry_with_feedback_recovers_a_failed_patch(cfg: HotpathConfig, tmp_path: Path):
    """A wrong first attempt fails correctness; the retry (with the failure fed back) is accepted."""
    d = tmp_path / "patches"
    d.mkdir()
    retryable = {
        "idea": "retryable set dedupe", "strategy": "data structure", "target_file": "mod.py",
        "rationale": "O(n^2)->O(n)", "risk": "low",
        # First attempt is deliberately wrong: it changes the return value, so correctness fails.
        "edits": [{"file": "mod.py", "search": "    return len(out)", "replace": "    return len(out) + 1"}],
        "reasoning": "intentionally wrong first attempt",
        # After the failure is fed back, the corrected attempt applies and is faster.
        "retry": {"edits": [{"file": "mod.py", "search": "        if i not in out:\n            out.append(i)\n",
                             "replace": "        if i not in seen:\n            seen.add(i)\n            out.append(i)\n"},
                            {"file": "mod.py", "search": "    out = []\n", "replace": "    out = []\n    seen = set()\n"}],
                  "reasoning": "set membership after feedback"},
    }
    (d / "0.json").write_text(json.dumps(retryable))
    cfg.provider.mock_patches_dir = str(d)
    cfg.search.iterations = 1
    cfg.search.max_patch_retries = 1
    orch = Orchestrator(cfg)
    run = await orch.execute()
    assert run.status == "finished", run.error
    exps = orch.store.list_experiments(run.id)
    original = next(e for e in exps if e.retry_of is None)
    retry = next(e for e in exps if e.retry_of == original.id)
    assert original.status == ExperimentStatus.rejected_correctness
    assert retry.status == ExperimentStatus.accepted
    assert run.head_experiment_id == retry.id
    assert run.best_speedup > 1.03


async def test_retries_disabled_when_max_is_zero(cfg: HotpathConfig, tmp_path: Path):
    d = tmp_path / "patches"
    d.mkdir()
    (d / "0.json").write_text(json.dumps({
        "idea": "wrong then right", "strategy": "x", "target_file": "mod.py", "rationale": "r", "risk": "low",
        "edits": [{"file": "mod.py", "search": "    return len(out)", "replace": "    return len(out) + 1"}],
        "reasoning": "wrong", "retry": {"edits": [], "reasoning": "unused"}}))
    cfg.provider.mock_patches_dir = str(d)
    cfg.search.iterations = 1
    cfg.search.max_patch_retries = 0
    orch = Orchestrator(cfg)
    run = await orch.execute()
    exps = orch.store.list_experiments(run.id)
    assert all(e.retry_of is None for e in exps)  # no retry experiments created
    assert run.head_commit == run.base_commit     # nothing accepted


async def test_rebenchmark_parent_still_accepts_a_faster_candidate(cfg: HotpathConfig, tmp_path: Path):
    """With interleaved parent re-benchmarking on, a genuinely faster patch is still accepted."""
    d = tmp_path / "patches"
    d.mkdir()
    (d / "0.json").write_text(json.dumps({
        "idea": "set-based dedupe", "strategy": "data structure", "target_file": "mod.py",
        "rationale": "O(n^2)->O(n)", "risk": "low",
        "edits": [{"file": "mod.py", "search": "        if i not in out:\n            out.append(i)\n",
                   "replace": "        if i not in seen:\n            seen.add(i)\n            out.append(i)\n"},
                  {"file": "mod.py", "search": "    out = []\n", "replace": "    out = []\n    seen = set()\n"}],
        "reasoning": "set membership"}))
    cfg.provider.mock_patches_dir = str(d)
    cfg.search.iterations = 1
    cfg.benchmark.rebenchmark_parent = True
    orch = Orchestrator(cfg)
    run = await orch.execute()
    assert run.status == "finished", run.error
    exp = next(e for e in orch.store.list_experiments(run.id) if e.hypothesis.idea == "set-based dedupe")
    assert exp.status == ExperimentStatus.accepted
    assert any("re-benchmarked parent" in line for line in exp.logs)
    # no leaked worktrees, including the temporary parent_ worktree
    assert not orch.ws.worktrees_dir.exists() or not any(orch.ws.worktrees_dir.iterdir())


async def test_export_bundle_has_report_diff_and_source(cfg: HotpathConfig, tmp_path: Path):
    _write_patches(tmp_path / "patches")
    cfg.provider.mock_patches_dir = str(tmp_path / "patches")
    cfg.search.candidates_per_iteration = 4
    orch = Orchestrator(cfg)
    run = await orch.execute()
    assert run.head_commit != run.base_commit
    dest = build_bundle(cfg, orch.store, run, tmp_path / "bundle", orch.ws)
    report = (dest / "REPORT.md").read_text()
    patch = (dest / "changes.patch").read_text()
    assert "Hotpath optimization report" in report
    assert "set-based dedupe" in report and "vs baseline" in report
    assert "seen = set()" in patch                      # the accepted change is in the diff
    assert "seen = set()" in (dest / "optimized_src" / "mod.py").read_text()


def _two_winning_variants(d: Path):
    """Two independent, correct, faster implementations of work() — both branch from the baseline."""
    d.mkdir()
    a = {"idea": "set-based dedupe", "strategy": "data structure", "target_file": "mod.py",
         "rationale": "O(n^2)->O(n)", "risk": "low",
         "edits": [{"file": "mod.py", "search": "        if i not in out:\n            out.append(i)\n",
                    "replace": "        if i not in seen:\n            seen.add(i)\n            out.append(i)\n"},
                   {"file": "mod.py", "search": "    out = []\n", "replace": "    out = []\n    seen = set()\n"}],
         "reasoning": "set membership"}
    b = {"idea": "dict.fromkeys dedupe", "strategy": "data structure", "target_file": "mod.py",
         "rationale": "O(n^2)->O(n)", "risk": "low",
         "edits": [{"file": "mod.py",
                    "search": "    out = []\n    for i in range(n):\n        if i not in out:\n            out.append(i)\n    return len(out)\n",
                    "replace": "    return len(list(dict.fromkeys(range(n))))\n"}],
         "reasoning": "dict preserves insertion order and dedupes in one pass"}
    (d / "0.json").write_text(json.dumps(a))
    (d / "1.json").write_text(json.dumps(b))


async def test_beam_width_two_keeps_both_heads(cfg: HotpathConfig, tmp_path: Path):
    _two_winning_variants(tmp_path / "patches")
    cfg.provider.mock_patches_dir = str(tmp_path / "patches")
    cfg.search.iterations = 1
    cfg.search.candidates_per_iteration = 2
    cfg.search.beam_width = 2
    run = await Orchestrator(cfg).execute()
    exps = Orchestrator(cfg).store.list_experiments(run.id)
    accepted = [e for e in exps if e.status == ExperimentStatus.accepted]
    not_selected = [e for e in exps if e.status == ExperimentStatus.not_selected]
    assert len(accepted) == 2 and len(not_selected) == 0  # both heads survive into the beam


async def test_beam_width_one_is_greedy(cfg: HotpathConfig, tmp_path: Path):
    _two_winning_variants(tmp_path / "patches")
    cfg.provider.mock_patches_dir = str(tmp_path / "patches")
    cfg.search.iterations = 1
    cfg.search.candidates_per_iteration = 2
    cfg.search.beam_width = 1
    run = await Orchestrator(cfg).execute()
    exps = Orchestrator(cfg).store.list_experiments(run.id)
    accepted = [e for e in exps if e.status == ExperimentStatus.accepted]
    not_selected = [e for e in exps if e.status == ExperimentStatus.not_selected]
    assert len(accepted) == 1 and len(not_selected) == 1  # one head kept, the other set aside


async def test_run_fails_cleanly_when_baseline_tests_fail(cfg: HotpathConfig, tmp_path: Path):
    cfg.test_cmd = "python -c 'import sys; sys.exit(1)'"
    run = await Orchestrator(cfg).execute()
    assert run.status == "failed" and "baseline does not pass" in run.error


async def test_planner_with_nothing_to_say_stops_early(cfg: HotpathConfig, tmp_path: Path):
    (tmp_path / "empty").mkdir()
    cfg.provider.mock_patches_dir = str(tmp_path / "empty")
    orch = Orchestrator(cfg)
    run = await orch.execute()
    assert run.status == "finished" and orch.store.list_experiments(run.id) == [] and run.best_speedup == 1.0


async def test_export_report_includes_the_bottleneck_diff(cfg, tmp_path, monkeypatch):
    """The PR bundle explains where the time went, and says the benchmark is what decided."""
    monkeypatch.chdir(tmp_path)
    d = tmp_path / "patches"; d.mkdir()
    (d / "a.json").write_text(json.dumps({"idea": "seen set", "strategy": "s", "target_file": "mod.py",
        "rationale": "r", "risk": "low", "reasoning": "x",
        "edits": [{"file": "mod.py", "search": "    out = []\n    for i in range(n):\n        if i not in out:\n            out.append(i)\n",
                   "replace": "    out = []\n    seen = set()\n    for i in range(n):\n        if i not in seen:\n            seen.add(i)\n            out.append(i)\n"}]}))
    cfg.provider.mock_patches_dir = str(d)
    orch = Orchestrator(cfg)
    run = await orch.execute()
    assert run.status == "finished" and run.head_commit != run.base_commit

    from hotpath.export import build_bundle
    from hotpath.workspace import Workspace
    dest = build_bundle(cfg, orch.store, run, tmp_path / "bundle",
                        Workspace(Path(cfg.target), Path(cfg.workdir)))
    report = (dest / "REPORT.md").read_text(encoding="utf-8")
    assert "## Where the time went" in report
    assert "Total self time" in report
    assert "the benchmark table above is what" in report, "the report must say which number decided"
    assert "upper bound, not a measurement" in report


async def test_a_change_that_creates_a_module_is_verified_and_shipped(cfg: HotpathConfig, tmp_path: Path):
    """The fused-kernel shape: a new module plus its call site, through the real harness."""
    d = tmp_path / "patches"; d.mkdir()
    (d / "a.json").write_text(json.dumps({
        "idea": "move the dedupe into a fast helper module", "strategy": "s", "target_file": "mod.py",
        "extra_files": ["helpers/fast.py"], "rationale": "r", "risk": "low", "reasoning": "x",
        "edits": [{"file": "helpers/fast.py", "search": "", "replace": "def unique_count(n):\n    return len(set(range(n)))\n"},
                  {"file": "mod.py", "search": "def work(n):\n",
                   "replace": "from helpers.fast import unique_count\n\n\ndef work(n):\n    return unique_count(n)\n"}]}))
    cfg.provider.mock_patches_dir = str(d)
    cfg.search.iterations = 1
    orch = Orchestrator(cfg)
    run = await orch.execute()
    assert run.status == "finished", run.error
    [exp] = orch.store.list_experiments(run.id)
    assert exp.status == ExperimentStatus.accepted, exp.reject_reason
    assert sorted(exp.files_changed) == ["helpers/fast.py", "mod.py"]
    assert "+def unique_count(n):" in exp.diff
    out = tmp_path / "exported"
    assert orch.export_best(out) == run.head_commit
    assert (out / "helpers" / "fast.py").read_text() == "def unique_count(n):\n    return len(set(range(n)))\n"


async def test_export_of_a_verified_prune_ships_the_pruned_stack_and_says_so(cfg: HotpathConfig, tmp_path: Path):
    from hotpath.ablation import PruneResult
    _write_patches(tmp_path / "patches")
    cfg.provider.mock_patches_dir = str(tmp_path / "patches")
    cfg.search.candidates_per_iteration = 4
    orch = Orchestrator(cfg)
    run = await orch.execute()
    assert run.head_commit != run.base_commit
    # A verified prune that dropped the only accepted change ships the baseline tree.
    pruned = PruneResult(status="pruned", dropped=[run.head_experiment_id], kept=[], reason="test",
                         commit=run.base_commit, full_median=0.01, pruned_median=0.0101, speedup_vs_baseline=1.0)
    dest = build_bundle(cfg, orch.store, run, tmp_path / "bundle", orch.ws, "ablation text", pruned)
    report = (dest / "REPORT.md").read_text()
    assert "**1.000x** vs baseline" in report and "0 change(s) shipped" in report
    assert "ships the **pruned** stack" in report and "dropped by pruning" in report
    assert f"shipped `{run.base_commit[:8]}`" in report and "not re-profiled" in report
    assert "seen = set()" not in (dest / "optimized_src" / "mod.py").read_text()
    assert (dest / "changes.patch").read_text().strip() == ""


async def test_export_labels_only_the_final_lineage_as_shipped(cfg: HotpathConfig, tmp_path: Path):
    _two_winning_variants(tmp_path / "patches")
    cfg.provider.mock_patches_dir = str(tmp_path / "patches")
    cfg.search.iterations = 1
    cfg.search.beam_width = 2
    orch = Orchestrator(cfg)
    run = await orch.execute()
    accepted = [e for e in orch.store.list_experiments(run.id) if e.status == ExperimentStatus.accepted]
    assert len(accepted) == 2, "both variants enter the beam"
    report = (build_bundle(cfg, orch.store, run, tmp_path / "bundle", orch.ws) / "REPORT.md").read_text()
    assert report.count("shipped in this bundle") == 1
    assert report.count("accepted, but not in the final head") == 1
    assert "1 change(s) shipped" in report
