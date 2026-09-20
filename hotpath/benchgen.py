"""Give a Python repository a benchmark it does not have yet.

A benchmark defines "faster", so a generated one is treated like any other locked file: it must earn
trust before Hotpath optimizes against it.

1. Profile the test suite (the one workload every repository already has) to find where the project's
   own code spends time.
2. Ask the planner model to write `hotpath_workload.py`: a deterministic `workload()` that exercises
   those hot paths through the project's API, using inputs shaped like the tests' own.
3. Wrap it in two fixed templates, `hotpath_bench.py` (benchlib) and `hotpath_profile.py` (profilelib),
   so the model never writes the timing code.
4. Validate it by running it: it must succeed, take a measurable but bounded time, be quiet enough to
   detect a few-percent change, spend most of its time in the project's code, return the same result
   in two fresh processes, and import nothing from the test suite or mocking libraries.
5. On failure, feed the reason back and try again. If nothing validates, fall back to timing the test
   suite itself, and say so in the pull request.

The caller shows the chosen benchmark to the user for approval before anything is committed.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from pydantic import BaseModel, Field

from hotpath.assess import BENCH_FILE, PROFILE_FILE, WORKLOAD_FILE
from hotpath.benchmark import BenchmarkParseError, parse_benchmark_output
from hotpath.pathrules import is_test_path as _is_test_path
from hotpath.sandbox import Runner

MIN_MEDIAN_S = 0.005        # below this, timer resolution and interpreter jitter dominate
MAX_MEDIAN_S = 5.0          # above this, a search iteration becomes too slow to be useful
MAX_NOISE = 0.12            # robust coefficient of variation (MAD / median) within one run
NOISY_BUT_USABLE = 0.25     # above the bar, but still better than timing the whole test suite
MIN_OWN_SHARE = 0.5         # fraction of profiled time spent in the project's own code
FORBIDDEN_IMPORTS = ("tests", "test", "conftest", "unittest.mock", "mock", "pytest", "hypothesis")

BENCH_TEMPLATE = '''\
"""Benchmark for Hotpath: times `workload()` from hotpath_workload.py.

Locked. Hotpath runs this file but can never edit it; the pull request that adds it shows exactly what
"faster" means for this repository.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
for path in (os.path.join(HERE, "src"), HERE):
    if os.path.isdir(path) and path not in sys.path:
        sys.path.insert(0, path)

from hotpath.benchlib import run  # noqa: E402
from hotpath_workload import workload  # noqa: E402

run(workload, warmup=2, trials={trials})
'''

PROFILE_TEMPLATE = '''\
"""Profile of the benchmark workload, for Hotpath's planner. Locked."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
for path in (os.path.join(HERE, "src"), HERE):
    if os.path.isdir(path) and path not in sys.path:
        sys.path.insert(0, path)

from hotpath.profilelib import run  # noqa: E402
from hotpath_workload import workload  # noqa: E402

run(workload, top=30, repeat=2)
'''

DIGEST_FILE = "_hotpath_digest.py"
_DIGEST_SCRIPT = '''\
import hashlib, os, re, sys
HERE = os.getcwd()
for path in (os.path.join(HERE, "src"), HERE):
    if os.path.isdir(path) and path not in sys.path:
        sys.path.insert(0, path)
import hotpath_workload
out = repr(hotpath_workload.workload())
out = re.sub(r"0x[0-9a-fA-F]+", "0x?", out)
print(hashlib.sha256(out.encode()).hexdigest())
'''


class WorkloadResponse(BaseModel):
    code: str = Field(description="The complete source of hotpath_workload.py")
    description: str = Field(description="One sentence: what the workload does, in the project's terms")
    functions: list[str] = Field(description="The project functions the workload exercises")


@dataclass
class Hotspot:
    function: str
    file: str
    line: int
    self_time: float
    total_time: float


@dataclass
class Validation:
    ok: bool
    reason: str = ""
    median_s: float = 0.0
    noise: float = 0.0
    own_share: float = 0.0
    details: list[str] = field(default_factory=list)


@dataclass
class BenchChoice:
    kind: str                         # generated | existing | wrapped | tests
    bench_cmd: str
    profile_cmd: Optional[str]
    files: dict[str, str] = field(default_factory=dict)   # relative path -> content to commit
    description: str = ""
    validation: Optional[Validation] = None
    attempts: list[str] = field(default_factory=list)     # why earlier generations were rejected


# --------------------------------------------------------------------------- #
# 1. Where the time goes
# --------------------------------------------------------------------------- #

def profile_tests(worktree: Path, runner: Runner, timeout: float = 900) -> list[Hotspot]:
    res = runner("python -m hotpath.testprofile -q -x", worktree, timeout)
    if res.returncode != 0:
        raise RuntimeError("profiling the test suite failed:\n" + (res.stderr or res.stdout)[-2000:])
    line = next((ln for ln in reversed(res.stdout.splitlines()) if ln.startswith('{"hotpath_profile"')), None)
    if line is None:
        raise RuntimeError("the test profiler printed no profile")
    rows = json.loads(line)["hotspots"]
    return [Hotspot(r["function"], r["file"].replace("\\", "/"), r["line"], r["self_time"], r["total_time"])
            for r in rows]


def _function_source(worktree: Path, spot: Hotspot, limit: int = 3000) -> str:
    from hotpath.context import _functions
    path = worktree / spot.file
    try:
        for name, start, end, src in _functions(path):
            if start == spot.line or name.split(".")[-1] == spot.function and start <= spot.line <= end:
                return src[:limit]
    except (OSError, UnicodeDecodeError):
        pass
    return ""


def _test_usages(worktree: Path, names: list[str], budget: int = 6000) -> str:
    chunks: list[str] = []
    used = 0
    tests = [p for p in worktree.rglob("*.py") if _is_test_path(p.relative_to(worktree).as_posix())
             and ".hotpath" not in p.parts]
    for name in names:
        pat = re.compile(rf"\b{re.escape(name)}\s*\(")
        for path in tests:
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            hit = next((i for i, ln in enumerate(lines) if pat.search(ln)), None)
            if hit is None:
                continue
            lo, hi = max(0, hit - 12), min(len(lines), hit + 12)
            chunk = f"# {path.relative_to(worktree).as_posix()}:{lo + 1}\n" + "\n".join(lines[lo:hi])
            if used + len(chunk) > budget:
                return "\n\n".join(chunks)
            chunks.append(chunk)
            used += len(chunk)
            break
    return "\n\n".join(chunks)


def build_prompt(worktree: Path, hotspots: list[Hotspot], feedback: list[str]) -> list[dict]:
    by_total = sorted(hotspots, key=lambda h: h.total_time, reverse=True)[:8]
    table = "\n".join(f"- {h.file}:{h.line} {h.function}: total {h.total_time:.4f}s, self {h.self_time:.4f}s"
                      for h in by_total)
    sources = "\n\n".join(f"# {h.file}:{h.line} ({h.function})\n{src}"
                          for h in by_total[:5] if (src := _function_source(worktree, h)))
    usages = _test_usages(worktree, [h.function for h in by_total[:5]])
    system = (
        "You write benchmark workloads for Hotpath, a tool that speeds code up and proves each change "
        "correct and faster. You write ONE Python module, hotpath_workload.py, placed at the repository "
        "root. It must define `workload()`, which Hotpath times many times.\n"
        "Rules:\n"
        "- Build all input data at import time, deterministically (seed any randomness; no clocks, "
        "network, environment variables, or files outside the repository).\n"
        "- `workload()` calls the project's own hot functions (listed below) through their normal API on "
        "that data, and RETURNS the result so the work cannot be skipped. It must not print.\n"
        "- `workload()` must not mutate the shared input in a way that changes later calls; copy first if "
        "the API mutates.\n"
        "- Scale the input so one call takes roughly 0.2 to 0.5 seconds in the current implementation: "
        "shorter calls are dominated by timing noise, longer ones make every search iteration slow.\n"
        "- Import only the project, the standard library, and packages the project already depends on. "
        "Never import from the test suite, conftest, pytest, or any mocking library.\n"
        "- Make the inputs realistic: shaped like the test suite's own inputs, but larger.\n"
        "- The module is imported with the repository root (and src/, if present) on sys.path.")
    user = (f"Profile of the test suite (functions in this project, by cumulative time):\n{table}\n\n"
            f"Source of the hottest functions:\n{sources or '(unavailable)'}\n\n"
            f"How the tests call them:\n{usages or '(no direct calls found in tests)'}")
    if feedback:
        user += ("\n\nPrevious attempts were rejected when Hotpath ran them. Fix these problems:\n"
                 + "\n".join(f"- {f}" for f in feedback))
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def openai_generator(model: str, api_key_env: str = "OPENAI_API_KEY",
                     base_url: Optional[str] = None) -> Callable[[list[dict]], WorkloadResponse]:
    from openai import OpenAI

    from hotpath import observability as obs
    key = os.environ.get(api_key_env)
    if not key:
        raise RuntimeError(f"{api_key_env} is not set, so no benchmark can be generated")
    client = OpenAI(api_key=key, base_url=base_url, timeout=180)

    def generate(messages: list[dict]) -> WorkloadResponse:
        with obs.ai_chat(model, "benchmark-writer", "openai") as sp:
            resp = client.beta.chat.completions.parse(model=model, messages=messages,
                                                      response_format=WorkloadResponse)
            obs.record_ai_usage(sp, resp)
        parsed = resp.choices[0].message.parsed
        if parsed is None:
            raise RuntimeError("the model returned no parseable workload")
        return parsed
    return generate


# --------------------------------------------------------------------------- #
# 2. Is it a benchmark we can trust?
# --------------------------------------------------------------------------- #

def forbidden_imports(code: str) -> list[str]:
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [f"syntax error: {e}"]
    bad = []
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names = [node.module]
        for n in names:
            if any(n == f or n.startswith(f + ".") for f in FORBIDDEN_IMPORTS):
                bad.append(n)
    if not any(isinstance(n, ast.FunctionDef) and n.name == "workload" for n in tree.body):
        bad.append("no top-level `def workload()`")
    return bad


def robust_noise(samples: list[float]) -> float:
    med = statistics.median(samples)
    mad = statistics.median(abs(s - med) for s in samples)
    return 1.4826 * mad / med if med > 0 else float("inf")


def bench_files(workload_code: str, trials: int = 15) -> dict[str, str]:
    return {WORKLOAD_FILE: workload_code.rstrip() + "\n",
            BENCH_FILE: BENCH_TEMPLATE.format(trials=trials),
            PROFILE_FILE: PROFILE_TEMPLATE}


def validate(worktree: Path, runner: Runner, *, timeout: float = 300, check_share: bool = True,
             check_determinism: bool = True) -> Validation:
    """Run the benchmark files already written into `worktree` and decide whether to trust them."""
    medians: list[float] = []
    noises: list[float] = []
    for attempt in range(2):
        res = runner(f"python {BENCH_FILE}", worktree, timeout)
        if res.timed_out:
            return Validation(False, f"the benchmark did not finish within {timeout:.0f}s; use a smaller input")
        if res.returncode != 0:
            return Validation(False, "the benchmark crashed:\n" + (res.stderr or res.stdout)[-1500:])
        try:
            samples = [float(s) for s in parse_benchmark_output(res.stdout)["samples"]]
        except (BenchmarkParseError, ValueError, TypeError) as e:
            return Validation(False, f"the benchmark printed no usable samples: {e}")
        medians.append(statistics.median(samples))
        noises.append(robust_noise(samples))
    median, noise = statistics.median(medians), max(noises)
    v = Validation(True, median_s=median, noise=noise)
    v.details.append(f"median {median * 1000:.1f} ms per call, noise {noise:.1%} (two runs)")
    if median < MIN_MEDIAN_S:
        return Validation(False, f"one call takes only {median * 1000:.2f} ms; make the input about "
                                 f"{max(2, round(0.3 / max(median, 1e-6)))}x larger so timing noise does not dominate",
                          median, noise)
    if median > MAX_MEDIAN_S:
        return Validation(False, f"one call takes {median:.1f} s; shrink the input to about 0.2-0.5 s", median, noise)
    if noise > MAX_NOISE:
        direction = ("Make one call do more work (aim for 0.2-0.5 s) so per-call jitter averages out"
                     if median < 0.2 else
                     "Use a smaller input (aim for 0.2-0.5 s): very long calls pick up garbage-collection and "
                     "memory-pressure noise")
        return Validation(False, f"timings vary by {noise:.0%} between trials, too noisy to detect a small speedup. "
                                 f"One call currently takes {median * 1000:.0f} ms. {direction}, and keep I/O, "
                                 "caching between calls, and data building out of workload()", median, noise)
    if abs(medians[0] - medians[1]) / median > 0.25:
        return Validation(False, "two runs disagreed by more than 25%; the workload's cost depends on state "
                                 "from earlier calls", median, noise)
    if check_share:
        res = runner(f"python {PROFILE_FILE}", worktree, timeout)
        if res.returncode == 0:
            line = next((ln for ln in reversed(res.stdout.splitlines()) if ln.startswith('{"hotpath_profile"')), None)
            if line:
                doc = json.loads(line)
                total = doc.get("total_time") or 0
                # Builtins (an empty file, e.g. `list.count`) are counted as the project's time: they are
                # called by its code and are exactly what an optimization would remove. What must not
                # dominate is the benchmark's own data building, or library code outside the project
                # (which the profiler drops, so it lands in `total` but not in any row).
                own = sum(h["self_time"] for h in doc["hotspots"]
                          if not h["file"] or (not _is_test_path(h["file"].replace("\\", "/"))
                                               and h["file"].replace("\\", "/")
                                               not in (WORKLOAD_FILE, BENCH_FILE, PROFILE_FILE)))
                v.own_share = own / total if total else 0.0
                v.details.append(f"{v.own_share:.0%} of profiled time is in the project's own code")
                if v.own_share < MIN_OWN_SHARE:
                    v.ok, v.reason = False, (f"only {v.own_share:.0%} of the time is spent in the project's own code; "
                                             "move data building to import time and call the hot functions directly")
                    return v
    if check_determinism:
        digests = set()
        for _ in range(2):
            res = runner(f"python {DIGEST_FILE}", worktree, timeout)
            if res.returncode != 0:
                return Validation(False, "calling workload() directly failed:\n" + res.stderr[-1000:], median, noise)
            digests.add(res.stdout.strip())
        if len(digests) != 1:
            v.ok, v.reason = False, ("workload() returned different results in two fresh processes; seed the "
                                     "input data and avoid clocks or unordered iteration in what it returns")
            return v
        v.details.append("same result in two fresh processes")
    return v


# --------------------------------------------------------------------------- #
# 3. The loop
# --------------------------------------------------------------------------- #

def generate_benchmark(worktree: Path, runner: Runner, generator: Callable[[list[dict]], WorkloadResponse], *,
                       attempts: int = 3, prepare: Callable[[Path], None] = lambda _w: None,
                       say: Callable[[str], None] = lambda _m: None) -> Optional[BenchChoice]:
    """`prepare` runs after files are written and before they run: the Docker path commits them, because
    only tracked files are staged into a container."""
    hotspots = profile_tests(worktree, runner)
    if not hotspots:
        say("the test suite spends no measurable time in the project's own code")
        return None
    top = sorted(hotspots, key=lambda h: h.total_time, reverse=True)[0]
    say(f"hottest project function in the tests: {top.function} ({top.file}:{top.line}, {top.total_time:.3f}s)")
    feedback: list[str] = []
    noisy_best: Optional[BenchChoice] = None
    for i in range(1, attempts + 1):
        resp = generator(build_prompt(worktree, hotspots, feedback))
        bad = forbidden_imports(resp.code)
        if bad:
            feedback.append(f"not allowed in the workload: {', '.join(bad)}")
            say(f"attempt {i}: rejected before running ({feedback[-1]})")
            continue
        files = bench_files(resp.code)
        for rel, text in {**files, DIGEST_FILE: _DIGEST_SCRIPT}.items():
            (worktree / rel).write_text(text, encoding="utf-8", newline="\n")
        prepare(worktree)
        # 150s is already far past the 5s-per-call ceiling, so a runaway attempt fails fast.
        v = validate(worktree, runner, timeout=150)
        if v.ok:
            say(f"attempt {i}: accepted, {'; '.join(v.details)}")
            return BenchChoice("generated", f"python {BENCH_FILE}", f"python {PROFILE_FILE}", files,
                               resp.description, v, feedback)
        if MAX_NOISE < v.noise <= NOISY_BUT_USABLE and MIN_MEDIAN_S <= v.median_s <= MAX_MEDIAN_S:
            # Only the noise bar failed. Remember it: a noisy real benchmark still measures the code
            # this repository runs, which beats falling back to timing the whole test suite.
            if noisy_best is None or v.noise < (noisy_best.validation.noise if noisy_best.validation else 1):
                v.details.append(f"kept despite {v.noise:.0%} noise")
                noisy_best = BenchChoice("generated", f"python {BENCH_FILE}", f"python {PROFILE_FILE}", files,
                                         resp.description + f" (noisy: +/-{v.noise:.0%})", v, list(feedback))
        feedback.append(v.reason)
        say(f"attempt {i}: rejected ({v.reason.splitlines()[0]})")
    if noisy_best is not None:
        for rel, text in noisy_best.files.items():
            (worktree / rel).write_text(text, encoding="utf-8", newline="\n")
        prepare(worktree)
        v = noisy_best.validation
        say(f"no attempt was quiet enough; keeping the least noisy one ({v.noise:.0%}). Hotpath's acceptance "
            f"threshold rises with measured noise, so only clear wins will pass.")
    return noisy_best


def tests_fallback(test_cmd: str, reason: str) -> BenchChoice:
    """Time the test suite itself. Honest but coarse: a change must speed up what the tests exercise."""
    cmd = test_cmd if "cacheprovider" in test_cmd or "pytest" not in test_cmd else test_cmd + " -p no:cacheprovider"
    profile = "python -m hotpath.testprofile -q" if "pytest" in test_cmd else None
    return BenchChoice("tests", f"python -m hotpath.benchwrap --trials 5 --warmup 1 -- {cmd}", profile,
                       description=f"the test suite's runtime ({reason})")


def digest(files: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()[:12]
