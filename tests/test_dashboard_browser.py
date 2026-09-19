"""Optional real-browser regression test. Set HOTPATH_PLAYWRIGHT_MODULE for an existing install."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from hotpath.schema import BenchmarkStats, CorrectnessResult, Experiment, ExperimentStatus, Hypothesis, ProfileSummary, RunState
from server.views import build_chart, build_funnel, build_tree


def test_dashboard_browser():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed for the optional dashboard browser test")
    module = os.environ.get("HOTPATH_PLAYWRIGHT_MODULE", "playwright")
    probe = subprocess.run([node, "-e", "require(process.argv[1])", module], capture_output=True)
    if probe.returncode:
        pytest.skip("Install Playwright or set HOTPATH_PLAYWRIGHT_MODULE to run browser tests")
    bench = BenchmarkStats(metric="tokens/s", higher_is_better=True, samples=[100, 101, 99],
                           n=3, median=100, mean=100, stdev=1, cv=.01)
    run = RunState(config_name='<img src=x onerror="window.injected=1">', target=".",
                   baseline_benchmark=bench, head_benchmark=bench, base_commit="abc", head_commit="abc")
    run.head_profile = ProfileSummary(
        tool="torch.profiler (cpu-time fallback)", commit="abc",
        flamegraph_source="torch.profiler CPU event tree",
        flamegraph=[{"function": "decode <unsafe>", "self_time": 0.2, "total_time": 1.0, "calls": 1,
                     "children": [{"function": "attention", "self_time": 0.8, "total_time": 0.8,
                                   "calls": 1}]}],
    )
    exp = Experiment(run_id=run.id, hypothesis=Hypothesis(idea="candidate", strategy="s",
                     target_file="mod.py", rationale="r", risk="low"), iteration=1,
                     status=ExperimentStatus.rejected_correctness,
                     reject_reason="Exact reason: output 42 differs from expected 41 <unsafe>",
                     correctness=CorrectnessResult(passed=False, exit_code=1, duration_s=0.1))
    payload = {
        "html": (Path(__file__).parents[1] / "server/static/index.html").read_text(encoding="utf-8"),
        "data": {"run": run.model_dump(mode="json"), "experiments": [exp.model_dump(mode="json")], "live": False},
        "tree": build_tree(run, [exp]).model_dump(mode="json"),
        "chart": build_chart(run, [exp]).model_dump(mode="json"),
        "funnel": build_funnel(run, [exp]).model_dump(mode="json"),
        "diff": {"comparable": False, "incomparable_reason": "No profile available"},
    }
    result = subprocess.run([node, str(Path(__file__).with_name("dashboard_browser.cjs"))],
                            input=json.dumps(payload), text=True, capture_output=True,
                            env={**os.environ, "HOTPATH_PLAYWRIGHT_MODULE": module}, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
