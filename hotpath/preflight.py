"""`hotpath check <repo>`: say in seconds whether a repository is worth a run.

Every stage of the guided flow that can stop does so *after* the expensive part — cloning, building a
virtualenv, running the suite three times. Four of the first six live runs died that way, ten minutes
in, and every one of their causes was visible in the source the whole time:

- test dependencies declared somewhere `assess` did not read
- a suite that needs optional libraries and fails without them
- property tests carrying a per-example deadline, so correctness depends on how busy the machine is
- a single module larger than the source budget a worker is given, so no patch is ever written

So this runs the cheap checks first, names what will go wrong, suggests the flag that avoids it, and
estimates what a run would cost. It never executes the repository's code: everything here is reading
files and asking git what it tracks.

The verdict is deliberately coarse — `go`, `caution`, `stop` — because the useful output is the list
of findings, not a score.
"""
from __future__ import annotations

import ast
import configparser
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from hotpath.assess import Assessment, assess, tracked_files
from hotpath.pathrules import is_test_path

#: Rough characters-per-token for English source. Only used for an order-of-magnitude estimate.
CHARS_PER_TOKEN = 4
#: Default price per million input tokens, stated so the arithmetic can be checked and overridden.
#: Model prices move; this is a documented assumption, not a quote.
USD_PER_MTOK = 2.50

#: Marker names that usually mean "needs something this machine may not have".
OPTIONAL_MARKERS = ("external", "integration", "network", "online", "remote", "slow", "requires",
                    "gpu", "cuda", "docker", "db", "database")

_MARKER_USE = re.compile(r"@(?:pytest\.)?mark\.(\w+)")
_DEADLINE_OFF = re.compile(r"deadline\s*=\s*None")


@dataclass
class Finding:
    """One thing worth knowing before spending money.

    `level` is `blocker` (a run cannot succeed), `caution` (a run can succeed but is likely to waste
    time or produce a result you should not trust), or `note` (worth knowing, not worth acting on).
    """
    level: str
    code: str
    message: str
    fix: str = ""

    def render(self) -> str:
        mark = {"blocker": "x", "caution": "!", "note": "-"}.get(self.level, "-")
        out = f"  {mark} {self.message}"
        return out + (f"\n      fix: {self.fix}" if self.fix else "")


@dataclass
class Estimate:
    """What a run would cost, from the sizes we can see without running anything."""
    planner_calls: int = 0
    worker_calls: int = 0
    benchgen_calls: int = 0
    source_chars: int = 0
    tokens: int = 0
    usd: float = 0.0

    def render(self) -> str:
        return (f"~{self.tokens:,} input tokens across {self.planner_calls + self.worker_calls + self.benchgen_calls} "
                f"model call(s), about ${self.usd:,.2f} at ${USD_PER_MTOK:.2f}/Mtok")


@dataclass
class Preflight:
    assessment: Assessment
    findings: list[Finding] = field(default_factory=list)
    suggested_test_cmd: Optional[str] = None
    estimate: Estimate = field(default_factory=Estimate)

    @property
    def blockers(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "blocker"]

    @property
    def cautions(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "caution"]

    @property
    def verdict(self) -> str:
        if self.blockers:
            return "stop"
        return "caution" if self.cautions else "go"

    def to_dict(self) -> dict:
        return {"verdict": self.verdict,
                "findings": [{"level": f.level, "code": f.code, "message": f.message, "fix": f.fix}
                             for f in self.findings],
                "suggested_test_cmd": self.suggested_test_cmd,
                "estimate": {"tokens": self.estimate.tokens, "usd": round(self.estimate.usd, 2),
                             "planner_calls": self.estimate.planner_calls,
                             "worker_calls": self.estimate.worker_calls,
                             "benchgen_calls": self.estimate.benchgen_calls},
                "assessment": self.assessment.to_dict()}

    def render(self) -> str:
        headline = {"go": "GO - nothing here should stop a run",
                    "caution": "CAUTION - a run can work, but read these first",
                    "stop": "STOP - a run cannot succeed as things stand"}[self.verdict]
        rows = [f"# Hotpath preflight: {self.assessment.name}", "", headline, ""]
        for level in ("blocker", "caution", "note"):
            group = [f for f in self.findings if f.level == level]
            if group:
                rows.append({"blocker": "Blockers", "caution": "Cautions", "note": "Notes"}[level] + ":")
                rows += [f.render() for f in group]
                rows.append("")
        if self.suggested_test_cmd:
            rows += ["Suggested correctness command:", f"  --test-cmd '{self.suggested_test_cmd}'", ""]
        rows += ["Estimated cost of one run (3 iterations x 3 candidates):", f"  {self.estimate.render()}",
                 "  Wall-clock depends on how long the suite takes; `hotpath go` measures that at stage 4.", ""]
        if self.verdict != "stop":
            rows.append(f"Next: hotpath go {self.assessment.name}"
                        + (f" --test-cmd '{self.suggested_test_cmd}'" if self.suggested_test_cmd else ""))
        return "\n".join(rows)


# --------------------------------------------------------------------------- #
# Individual checks. Each returns findings; none runs the repository's code.

def _read(path: Path, limit: int = 400_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def declared_markers(repo: Path) -> dict[str, str]:
    """Marker name -> its description, from wherever pytest markers get declared."""
    out: dict[str, str] = {}
    blocks: list[str] = []
    cfg = configparser.ConfigParser()
    for name, section in (("setup.cfg", "tool:pytest"), ("pytest.ini", "pytest"), ("tox.ini", "pytest")):
        path = repo / name
        if not path.is_file():
            continue
        try:
            cfg.read(path, encoding="utf-8")
        except (configparser.Error, OSError, UnicodeDecodeError):
            continue
        if cfg.has_section(section):
            blocks.append(cfg.get(section, "markers", fallback=""))
    pyproject = _read(repo / "pyproject.toml")
    m = re.search(r"markers\s*=\s*\[(.*?)\]", pyproject, re.S)
    if m:
        blocks.append("\n".join(re.findall(r"['\"]([^'\"]+)['\"]", m.group(1))))
    for block in blocks:
        for line in block.splitlines():
            line = line.strip()
            if not line:
                continue
            name, _, description = line.partition(":")
            out.setdefault(name.strip().split("(")[0], description.strip())
    return out


def test_sources(repo: Path) -> list[tuple[str, str]]:
    """(relative path, source) for every tracked Python test file."""
    out = []
    for rel in tracked_files(repo):
        if rel.endswith(".py") and is_test_path(rel):
            out.append((rel, _read(repo / rel)))
    return out


def check_timing_sensitive_tests(repo: Path, tests: list[tuple[str, str]]) -> list[Finding]:
    """Property tests with a per-example deadline make correctness depend on machine load.

    This is what made `textdistance` unusable: Hypothesis fails a test that took 251 ms against a
    200 ms deadline, so the suite goes red under load. A correctness check that is itself a timing
    measurement cannot gate a timing experiment, and Hotpath is right to refuse it — but there is no
    reason to discover that ten minutes into a run.
    """
    users = [(rel, src) for rel, src in tests if re.search(r"^\s*(from|import)\s+hypothesis", src, re.M)]
    if not users:
        return []
    # A conftest that registers a profile with no deadline and loads it covers the whole suite.
    for conf in ("conftest.py", "tests/conftest.py", "test/conftest.py"):
        text = _read(repo / conf)
        if "register_profile" in text and _DEADLINE_OFF.search(text) and "load_profile" in text:
            return [Finding("note", "hypothesis-deadlines-disabled",
                            f"{len(users)} test file(s) use Hypothesis, and {conf} registers a profile with "
                            f"deadline=None for the whole suite.")]

    exposed: list[str] = []
    for rel, src in users:
        unguarded = property_tests_without_deadline(src)
        if unguarded:
            exposed.append(f"{rel} ({unguarded})")
    if not exposed:
        return [Finding("note", "hypothesis-deadlines-disabled",
                        f"{len(users)} test file(s) use Hypothesis, and every property test sets deadline=None.")]
    total = sum(int(item.rsplit("(", 1)[1].rstrip(")")) for item in exposed)
    shown = ", ".join(exposed[:3]) + (f", and {len(exposed) - 3} more file(s)" if len(exposed) > 3 else "")
    return [Finding(
        "caution", "timing-sensitive-tests",
        f"{total} property test(s) across {len(exposed)} file(s) run under Hypothesis without deadline=None "
        f"({shown}). Hypothesis fails a test that runs slower than its deadline, so this suite can go red "
        f"purely because the machine is busy — and a benchmark keeps the machine busy. A correctness check "
        f"that is itself a timing measurement cannot decide a timing experiment.",
        "deselect those files with --test-cmd, or add a conftest profile with deadline=None and load it")]


def property_tests_without_deadline(source: str) -> int:
    """How many `@given` tests in this source do not turn Hypothesis' deadline off.

    Counted per test rather than per file: `textdistance` sets deadline=None on two of its seven
    Hypothesis files, and a file-level check read that as the whole suite being safe. The five that
    were still exposed are the ones that failed.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        # Unparseable means unknown, and unknown should not be reported as safe.
        return 1 if _DEADLINE_OFF.search(source) is None else 0

    def name_of(node: ast.AST) -> str:
        node = node.func if isinstance(node, ast.Call) else node
        if isinstance(node, ast.Attribute):
            return node.attr
        return node.id if isinstance(node, ast.Name) else ""

    count = 0
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        decorators = [name_of(d) for d in node.decorator_list]
        if "given" not in decorators:
            continue
        guarded = False
        for dec in node.decorator_list:
            if not (isinstance(dec, ast.Call) and name_of(dec) == "settings"):
                continue
            for kw in dec.keywords:
                if kw.arg == "deadline" and isinstance(kw.value, ast.Constant) and kw.value.value is None:
                    guarded = True
        if not guarded:
            count += 1
    return count


def check_optional_dependency_tests(repo: Path, tests: list[tuple[str, str]]) -> tuple[list[Finding], Optional[str]]:
    """Tests behind a marker that implies something this machine may not have.

    Returns the findings and, when one marker clearly dominates, the `-m 'not <marker>'` to suggest.
    """
    declared = declared_markers(repo)
    used: dict[str, int] = {}
    for _rel, src in tests:
        for name in _MARKER_USE.findall(src):
            if name in ("parametrize", "fixture", "usefixtures", "filterwarnings", "xfail", "skip",
                        "skipif", "asyncio", "timeout"):
                continue
            used[name] = used.get(name, 0) + 1
    suspicious = {n: c for n, c in used.items() if n.lower() in OPTIONAL_MARKERS}
    if not suspicious:
        return [], None
    worst = max(suspicious, key=lambda n: suspicious[n])
    described = declared.get(worst, "")
    detail = f' ("{described}")' if described else ""
    return ([Finding(
        "caution", "optional-dependency-tests",
        f"{suspicious[worst]} test(s) are marked `{worst}`{detail}. Markers like this usually need "
        f"libraries or services that are not installed, and a red baseline stops the run before "
        f"anything is measured.",
        f"""--test-cmd 'python -m pytest -q -m "not {worst}"'""")],
        f'python -m pytest -q -m "not {worst}"')


def check_module_sizes(repo: Path, a: Assessment, budget: int) -> list[Finding]:
    """A worker is shown a whole file, and refuses one larger than its budget.

    `inflect`'s package is one 107,342-character module against a 14,000-character default, so all
    twelve candidates failed before a model was called. The budget is fitted to the repository now,
    but it has a ceiling, and a file past the ceiling can never be edited.
    """
    sizes: dict[str, int] = {}
    for pattern in a.editable:
        for path in repo.glob(pattern):
            if path.is_file() and not path.is_symlink():
                try:
                    sizes[path.relative_to(repo).as_posix()] = path.stat().st_size
                except OSError:
                    continue
    if not sizes:
        return [Finding("blocker", "nothing-editable",
                        f"no files match the editable patterns {a.editable}, so there is nothing to optimize.")]
    over = sorted(((rel, n) for rel, n in sizes.items() if n > budget), key=lambda kv: -kv[1])
    findings = []
    if over:
        rel, n = over[0]
        findings.append(Finding(
            "blocker" if len(over) == len(sizes) else "caution", "module-too-large",
            f"{rel} is {n:,} characters, past the {budget:,}-character ceiling for what a worker can be "
            f"shown, so no patch to it can be written"
            + (f" (and {len(over) - 1} other file(s) are too)" if len(over) > 1 else "") + ".",
            "narrow `editable` in .hotpath.yaml to smaller modules, or split the file"))
    biggest = max(sizes.values())
    if not over and biggest > 14_000:
        findings.append(Finding("note", "large-module",
                                f"the largest editable file is {biggest:,} characters; the worker's source "
                                f"budget will be raised to fit it, which makes each call cost more."))
    return findings


def check_benchmark_outlook(repo: Path, a: Assessment) -> list[Finding]:
    """What "faster" will mean, and how much that costs you in threshold.

    A repository with its own Hotpath benchmark is the best case. Failing that one is generated and
    validated. Failing *that*, the whole test suite's runtime is timed — which is legitimate but
    coarse: `boltons` came out at 8.1% noise, so the bar rose to about 16% of total suite runtime and
    nothing could clear it.
    """
    if a.bench_cmd:
        return [Finding("note", "benchmark-exists",
                        f"the repository has its own benchmark (`{a.bench_cmd}`), which is what will define faster.")]
    if a.bench_wrap_cmd:
        return [Finding("note", "benchmark-wrapped",
                        f"`{a.bench_wrap_cmd}` looks like a benchmark but does not print Hotpath's JSON, so its "
                        f"whole runtime is timed instead. That is coarser than a workload over one hot function: "
                        f"anything it does besides the work being measured — imports, setup, printing — dilutes "
                        f"the speedup and raises the bar.")]
    findings = []
    if a.tier != 1:
        findings.append(Finding("caution", "no-generated-benchmark",
                                f"{a.ecosystem} is Tier {a.tier}: Hotpath cannot generate a benchmark for it, so "
                                f"an existing command is timed instead.",
                                "point --bench-cmd at a benchmark that prints Hotpath JSON"))
    else:
        findings.append(Finding("note", "benchmark-generated",
                                "no benchmark found, so one will be written over the test suite's hot paths and "
                                "validated by running it. If none validates, the whole suite's runtime is timed, "
                                "which raises the bar a lot."))
    compiled = [rel for rel in tracked_files(repo) if rel.endswith((".pyx", ".c", ".cpp", ".rs", ".so", ".pyd"))]
    if compiled and a.ecosystem == "python":
        findings.append(Finding("caution", "hot-code-may-be-compiled",
                                f"{len(compiled)} compiled source file(s) are tracked. If the work happens in a C or "
                                f"Rust extension, the Python that Hotpath may edit is a thin wrapper and there is "
                                f"little to win.",
                                "check that the hot path is really Python before spending a run"))
    return findings


def check_test_ordering(repo: Path, a: Assessment) -> list[Finding]:
    """`pytest-randomly` reorders tests every run, which reads as flakiness to the baseline gate."""
    haystack = " ".join(a.dependencies) + _read(repo / "pyproject.toml") + _read(repo / "setup.cfg")
    if "pytest-randomly" in haystack or "pytest_randomly" in haystack:
        return [Finding("caution", "randomised-test-order",
                        "pytest-randomly is configured, so test order changes every run. Hotpath runs the suite "
                        "three times and refuses a baseline that is not identical each time.",
                        "add -p no:randomly to --test-cmd")]
    return []


def estimate_cost(repo: Path, a: Assessment, *, iterations: int = 3, candidates: int = 3,
                  retries: int = 1) -> Estimate:
    """Order-of-magnitude input cost, from the size of what the models get shown.

    Only input tokens, and only the source that dominates them: the worker is handed a whole file, so
    the largest editable file times the number of candidates is most of the bill. Deliberately an
    over-estimate — every candidate is priced as if it were retried.
    """
    sizes = [p.stat().st_size for pattern in a.editable for p in repo.glob(pattern)
             if p.is_file() and not p.is_symlink()]
    biggest = max(sizes) if sizes else 0
    est = Estimate(source_chars=biggest)
    est.planner_calls = iterations
    est.worker_calls = iterations * candidates * (1 + retries)
    est.benchgen_calls = 0 if a.bench_cmd else 3
    planner_chars = min(biggest, 40_000) + 4_000        # hotspots plus a slice of context
    worker_chars = biggest + 4_000                      # a whole file plus the instructions
    benchgen_chars = 12_000                             # hotspot list plus the prompt
    est.tokens = int((est.planner_calls * planner_chars
                      + est.worker_calls * worker_chars
                      + est.benchgen_calls * benchgen_chars) / CHARS_PER_TOKEN)
    est.usd = est.tokens / 1_000_000 * USD_PER_MTOK
    return est


def preflight(repo: Path, *, name: Optional[str] = None, iterations: int = 3, candidates: int = 3,
              source_ceiling: int = 240_000) -> Preflight:
    """Everything cheap that can be known about a repository before a run costs anything."""
    repo = repo.expanduser().resolve()
    a = assess(repo, name)
    result = Preflight(assessment=a)

    for blocker in a.blockers:
        result.findings.append(Finding("blocker", "assessment", blocker))
    if a.blockers:
        # Without an ecosystem or a test suite nothing else is meaningful.
        result.estimate = Estimate()
        return result

    tests = test_sources(repo)
    if not tests:
        result.findings.append(Finding("caution", "no-test-files",
                                       "a test command was detected but no test files were found; make sure the "
                                       "command really proves correctness."))
    result.findings += check_timing_sensitive_tests(repo, tests)
    optional_findings, suggested = check_optional_dependency_tests(repo, tests)
    result.findings += optional_findings
    result.suggested_test_cmd = suggested
    result.findings += check_module_sizes(repo, a, source_ceiling)
    result.findings += check_benchmark_outlook(repo, a)
    result.findings += check_test_ordering(repo, a)
    for note in a.notes:
        result.findings.append(Finding("note", "assessment", note))
    result.estimate = estimate_cost(repo, a, iterations=iterations, candidates=candidates)
    return result
