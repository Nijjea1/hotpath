"""`hotpath assess`: a static, read-only look at an unfamiliar repository.

Nothing here runs the target's code. It reads manifests and the file tree and decides:

- which ecosystem the repository is, and how well Hotpath supports it (the tier);
- how to check correctness and where a benchmark would come from;
- which files the agent may edit, and which ones define "correct" and "faster" and must stay locked;
- which dependencies to install, *without* installing the project itself. Hotpath runs candidates in
  git worktrees, so an installed copy of the project would shadow the code being measured.

Tier 1 (Python) gets profiling and a generated benchmark. Tier 2 (Node, Rust, Go) gets correctness plus
timing of an existing command. Tier 0 is unsupported and stops the guided flow with the reason.
"""
from __future__ import annotations

import ast
import configparser
import json
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Optional

from hotpath.init import ALWAYS_LOCKED, detect_bench_cmd, detect_profile_cmd, detect_test_cmd
from hotpath.pathrules import TEST_DIRS as _TEST_DIRS, is_test_path

#: Files the guided flow writes into the target. They define the benchmark and the rules, so they are
#: locked for the agent, and the setup commit that adds them is the only commit allowed to touch them.
WORKLOAD_FILE = "hotpath_workload.py"
BENCH_FILE = "hotpath_bench.py"
PROFILE_FILE = "hotpath_profile.py"
GENERATED_FILES = [WORKLOAD_FILE, BENCH_FILE, PROFILE_FILE]

_SKIP_DIRS = {".git", ".hotpath", ".venv", "venv", "env", "node_modules", "__pycache__", "build", "dist",
              ".tox", ".nox", ".mypy_cache", ".pytest_cache", "target", "vendor", "site-packages", ".idea",
              ".vscode", "docs", "doc", "examples", "example", "scripts", "benchmarks", "bench"}
_SECRET_NAMES = re.compile(r"(?i)(^\.env($|\.)|\.pem$|\.key$|\.p12$|\.pfx$|id_rsa|credentials?\.json$|secrets?\.(json|ya?ml)$)")
_REQ_FILES = ("requirements.txt", "requirements-dev.txt", "requirements_dev.txt", "requirements-test.txt",
              "requirements_test.txt", "dev-requirements.txt", "test-requirements.txt",
              "requirements/test.txt", "requirements/dev.txt", "requirements/base.txt")
_TEST_EXTRAS = ("test", "tests", "testing", "dev")
_EXT_LANG = {".py": "Python", ".js": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript", ".ts": "TypeScript",
             ".tsx": "TypeScript", ".rs": "Rust", ".go": "Go", ".java": "Java", ".c": "C", ".cpp": "C++",
             ".rb": "Ruby", ".cs": "C#"}


@dataclass
class Assessment:
    repo: str
    name: str
    ecosystem: str                      # python | node | rust | go | unknown
    tier: int                           # 1 full, 2 correctness + existing timing, 0 unsupported
    languages: dict[str, int] = field(default_factory=dict)
    tracked_files: int = 0
    source_lines: int = 0
    test_cmd: Optional[str] = None
    bench_cmd: Optional[str] = None     # an existing benchmark that already prints Hotpath JSON
    bench_wrap_cmd: Optional[str] = None  # an existing benchmark command that only needs timing
    profile_cmd: Optional[str] = None
    editable: list[str] = field(default_factory=list)
    locked: list[str] = field(default_factory=list)
    src_layout: bool = False
    python_requires: Optional[str] = None
    requirement_files: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    install_cmds: list[str] = field(default_factory=list)   # non-Python ecosystems
    secret_files: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.blockers

    def to_dict(self) -> dict:
        return asdict(self)

    def to_markdown(self) -> str:
        tier = {1: "Tier 1: full support (profiling, generated benchmark, patching)",
                2: "Tier 2: correctness + timing of an existing command (experimental)",
                0: "unsupported"}[self.tier]
        langs = ", ".join(f"{k} {v}" for k, v in sorted(self.languages.items(), key=lambda kv: -kv[1])) or "none"
        rows = [
            f"# Hotpath assessment: {self.name}", "",
            f"- **Ecosystem:** {self.ecosystem} ({tier})",
            f"- **Files:** {self.tracked_files} tracked, ~{self.source_lines} source lines ({langs})",
            f"- **Correctness check:** `{self.test_cmd}`" if self.test_cmd else "- **Correctness check:** none found",
        ]
        if self.bench_cmd:
            rows.append(f"- **Benchmark:** existing, `{self.bench_cmd}`")
        elif self.bench_wrap_cmd:
            rows.append(f"- **Benchmark:** existing command, timed by Hotpath: `{self.bench_wrap_cmd}`")
        else:
            rows.append("- **Benchmark:** none found; one will be generated from the test suite's hot paths"
                        if self.tier == 1 else "- **Benchmark:** none found")
        rows += [f"- **Editable:** {', '.join(f'`{p}`' for p in self.editable) or 'nothing'}",
                 f"- **Locked:** {', '.join(f'`{p}`' for p in self.locked)}"]
        if self.ecosystem == "python":
            deps = f"{len(self.dependencies)} package(s)" + (f" + {', '.join(self.requirement_files)}"
                                                             if self.requirement_files else "")
            rows.append(f"- **Dependencies:** {deps}; the project itself is never installed "
                        "(candidates run from their own worktree)")
            if self.src_layout:
                rows.append("- **Layout:** `src/`, so candidates run with `PYTHONPATH=src`")
        elif self.install_cmds:
            rows.append(f"- **Install:** {'; '.join(f'`{c}`' for c in self.install_cmds)}")
        if self.secret_files:
            rows.append(f"- **Secret-looking files (never staged or sent to a model):** "
                        f"{', '.join(self.secret_files[:10])}")
        if self.notes:
            rows += ["", "## Notes", *(f"- {n}" for n in self.notes)]
        if self.blockers:
            rows += ["", "## Blockers", *(f"- {b}" for b in self.blockers)]
        return "\n".join(rows) + "\n"


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def tracked_files(repo: Path) -> list[str]:
    """Tracked files when the repo is a git checkout (what Hotpath stages), else a filtered walk."""
    if (repo / ".git").is_dir():
        res = subprocess.run(["git", "-c", "core.fsmonitor=false", "ls-files", "-z"], cwd=repo,
                             capture_output=True, text=True, encoding="utf-8", errors="replace")
        if res.returncode == 0:
            return [f for f in res.stdout.split("\0") if f]
    out = []
    for p in repo.rglob("*"):
        rel = p.relative_to(repo)
        if p.is_file() and not any(part in _SKIP_DIRS or part.startswith(".git") for part in rel.parts):
            out.append(rel.as_posix())
    return out


_is_test_path = is_test_path


# --------------------------------------------------------------------------- #
# Python
# --------------------------------------------------------------------------- #

def _toml(path: Path) -> dict:
    import tomllib
    try:
        return tomllib.loads(_read(path)) if path.is_file() else {}
    except tomllib.TOMLDecodeError:
        return {}


_SELF_INSTALL = re.compile(r"^(-e\s+)?(\.|\./|\.\[.*\]|file:\.?)\s*$")


def requirement_lines(path: Path, seen: set[Path] | None = None) -> list[str]:
    """Requirement specs from a requirements file, following `-r` includes, without ever installing
    the project itself (`.`, `-e .`, local paths): an installed copy would shadow the worktree."""
    seen = seen if seen is not None else set()
    path = path.resolve()
    if path in seen or not path.is_file():
        return []
    seen.add(path)
    out: list[str] = []
    for raw in _read(path).splitlines():
        line = raw.split(" #", 1)[0].strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("-r ", "--requirement ", "-c ", "--constraint ")):
            ref = line.split(None, 1)[1].strip()
            out += requirement_lines(path.parent / ref, seen)
            continue
        if _SELF_INSTALL.match(line) or line.startswith(("-e ", "--editable", "./", "../", "/", "file:")):
            continue
        if line.startswith("-"):
            continue  # index URLs and pip flags are not ours to pass through
        out.append(line)
    return out


def setup_py_requirements(source: str) -> tuple[list[str], list[str]]:
    """(install_requires, test extras) read out of setup.py with `ast` — parsed, never executed.

    A regex over `extras_require=` misses the common case, because projects build the dict above the
    `setup()` call and pass the variable (`extras_require=extras`). So instead: walk every dict literal
    in the file and take the lists under a test-ish key. Test dependencies matter as much as runtime
    ones here — without them the baseline suite fails to import and the whole run stops."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return [], []

    def strings(node: ast.AST) -> list[str]:
        if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return []
        return [e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]

    install: list[str] = []
    extras: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "install_requires":
            install += strings(node.value)
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and isinstance(key.value, str) \
                        and key.value.strip().lower() in _TEST_EXTRAS:
                    extras += strings(value)
    return install, extras


def setup_cfg_requirements(path: Path) -> tuple[list[str], list[str]]:
    """(install_requires, test extras) from setup.cfg's `[options]` and `[options.extras_require]`."""
    parser = configparser.ConfigParser()
    try:
        parser.read(path, encoding="utf-8")
    except (configparser.Error, OSError, UnicodeDecodeError):
        return [], []

    def lines(raw: Optional[str]) -> list[str]:
        return [ln.strip() for ln in (raw or "").splitlines() if ln.strip() and not ln.strip().startswith("#")]

    install = lines(parser.get("options", "install_requires", fallback=""))
    extras: list[str] = []
    if parser.has_section("options.extras_require"):
        for key in parser.options("options.extras_require"):
            if key.strip().lower() in _TEST_EXTRAS:
                extras += lines(parser.get("options.extras_require", key))
    return install, extras


def python_dependencies(repo: Path) -> tuple[list[str], list[str], Optional[str], list[str]]:
    """(specs, requirement files used, requires-python, notes)."""
    notes: list[str] = []
    specs: list[str] = []
    files: list[str] = []
    py = _toml(repo / "pyproject.toml")
    project = py.get("project", {}) if isinstance(py.get("project"), dict) else {}
    requires_python = project.get("requires-python")
    specs += [d for d in project.get("dependencies", []) or [] if isinstance(d, str)]
    extras = project.get("optional-dependencies", {}) or {}
    for key in _TEST_EXTRAS:
        specs += [d for d in extras.get(key, []) or [] if isinstance(d, str)]
    groups = py.get("dependency-groups", {}) or {}
    for key in _TEST_EXTRAS:
        specs += [d for d in groups.get(key, []) or [] if isinstance(d, str)]
    poetry = py.get("tool", {}).get("poetry", {}) if isinstance(py.get("tool"), dict) else {}
    for section in ("dependencies", "dev-dependencies"):
        for name in (poetry.get(section) or {}):
            if name.lower() != "python":
                specs.append(name)
    for gname in _TEST_EXTRAS:
        for name in ((poetry.get("group", {}) or {}).get(gname, {}) or {}).get("dependencies", {}) or {}:
            specs.append(name)
    for rel in _REQ_FILES:
        if (repo / rel).is_file():
            files.append(rel)
            specs += requirement_lines(repo / rel)
    cfg_install, cfg_extras = setup_cfg_requirements(repo / "setup.cfg")
    specs += cfg_extras
    setup_py = _read(repo / "setup.py")
    py_install, py_extras = setup_py_requirements(setup_py) if setup_py else ([], [])
    specs += py_extras
    if not project.get("dependencies"):
        specs += cfg_install
        if py_install:
            specs += py_install
            notes.append("dependencies were read from setup.py by parsing it; check them")
        elif setup_py and not files and not cfg_install and not py_extras:
            notes.append("setup.py computes its dependencies; if the baseline tests fail to import, add a "
                         "requirements.txt")
    seen, unique = set(), []
    for s in specs:
        key = s.strip()
        if key and key not in seen:
            seen.add(key)
            unique.append(key)
    return unique, files, requires_python, notes


def _python_editable(repo: Path, files: list[str]) -> tuple[list[str], bool]:
    if (repo / "src").is_dir() and any(f.startswith("src/") and f.endswith(".py") for f in files):
        return ["src/*.py"], True
    packages = sorted({PurePosixPath(f).parts[0] for f in files
                       if f.endswith("__init__.py") and len(PurePosixPath(f).parts) == 2
                       and PurePosixPath(f).parts[0] not in _SKIP_DIRS
                       and PurePosixPath(f).parts[0] not in _TEST_DIRS})
    modules = sorted(f for f in files if "/" not in f and f.endswith(".py") and not _is_test_path(f)
                     and f not in ("setup.py", "noxfile.py", "conftest.py", *GENERATED_FILES))
    editable = [f"{p}/*.py" for p in packages] + modules
    return (editable or ["*.py"]), False


# --------------------------------------------------------------------------- #
# Other ecosystems (Tier 2)
# --------------------------------------------------------------------------- #

def _node(repo: Path, a: Assessment) -> None:
    try:
        pkg = json.loads(_read(repo / "package.json") or "{}")
    except json.JSONDecodeError:
        a.blockers.append("package.json is not valid JSON")
        return
    scripts = pkg.get("scripts", {}) or {}
    test = scripts.get("test", "")
    if test and "no test specified" not in test:
        a.test_cmd = "npm test --silent"
    for key in ("bench", "benchmark", "perf"):
        if key in scripts:
            a.bench_wrap_cmd = f"npm run {key} --silent"
            break
    a.install_cmds = ["npm ci" if (repo / "package-lock.json").is_file() else "npm install"]
    src = "src" if (repo / "src").is_dir() else ""
    exts = ("js", "mjs", "cjs", "ts", "tsx")
    a.editable = [f"{src}/*.{e}" if src else f"*.{e}" for e in exts]
    a.locked += ["*.test.*", "*.spec.*", "__tests__/*", "package.json", "package-lock.json", "yarn.lock",
                 "pnpm-lock.yaml", "tsconfig.json"]


def _rust(repo: Path, a: Assessment) -> None:
    a.test_cmd = "cargo test --quiet"
    if (repo / "benches").is_dir():
        a.bench_wrap_cmd = "cargo bench --quiet"
    a.install_cmds = ["cargo fetch"]
    a.editable = ["src/*.rs"]
    a.locked += ["Cargo.toml", "Cargo.lock", "benches/*", "build.rs"]


def _go(repo: Path, a: Assessment, files: list[str]) -> None:
    a.test_cmd = "go test ./..."
    if any(f.endswith("_test.go") and "Benchmark" in _read(repo / f) for f in files if f.endswith("_test.go")):
        a.bench_wrap_cmd = "go test -run ^$ -bench . ./..."
    a.install_cmds = ["go mod download"]
    a.editable = ["*.go"]
    a.locked += ["*_test.go", "go.mod", "go.sum"]


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def assess(repo: Path, name: Optional[str] = None) -> Assessment:
    repo = repo.resolve()
    files = tracked_files(repo)
    a = Assessment(repo=str(repo), name=name or repo.name, ecosystem="unknown", tier=0, tracked_files=len(files))
    lines = 0
    for f in files:
        ext = PurePosixPath(f).suffix.lower()
        if ext in _EXT_LANG:
            a.languages[_EXT_LANG[ext]] = a.languages.get(_EXT_LANG[ext], 0) + 1
            if lines < 2_000_000:
                lines += _read(repo / f).count("\n")
        if _SECRET_NAMES.search(PurePosixPath(f).name):
            a.secret_files.append(f)
    a.source_lines = lines
    a.locked = list(ALWAYS_LOCKED) + [f for f in GENERATED_FILES if f not in ALWAYS_LOCKED]

    has_py = a.languages.get("Python", 0) > 0
    if (repo / "package.json").is_file() and not ((repo / "pyproject.toml").is_file() or (repo / "setup.py").is_file()):
        a.ecosystem, a.tier = "node", 2
        _node(repo, a)
    elif (repo / "Cargo.toml").is_file():
        a.ecosystem, a.tier = "rust", 2
        _rust(repo, a)
    elif (repo / "go.mod").is_file():
        a.ecosystem, a.tier = "go", 2
        _go(repo, a, files)
    elif has_py:
        a.ecosystem, a.tier = "python", 1
        a.test_cmd = detect_test_cmd(repo)
        a.bench_cmd = detect_bench_cmd(repo)
        if a.bench_cmd and "hotpath" not in _read(repo / a.bench_cmd.split()[-1]):
            # A plain bench.py that does not print Hotpath JSON is still useful: time it as a command.
            a.bench_wrap_cmd, a.bench_cmd = a.bench_cmd, None
        a.profile_cmd = detect_profile_cmd(repo)
        a.editable, a.src_layout = _python_editable(repo, files)
        a.dependencies, a.requirement_files, a.python_requires, notes = python_dependencies(repo)
        a.notes += notes
        for script in (a.bench_cmd, a.bench_wrap_cmd, a.profile_cmd, a.test_cmd):
            if script and script.startswith("python ") and script.endswith(".py"):
                rel = script.split()[-1]
                if rel not in a.locked:
                    a.locked.append(rel)
        # A locked script is not editable, so do not list it as both.
        a.editable = [p for p in a.editable if p not in a.locked]
    else:
        a.blockers.append("no Python, Node, Rust, or Go project found; Hotpath's guided flow supports those")
        return a

    if a.tier == 2:
        a.notes.append(f"{a.ecosystem} support is Tier 2: Hotpath checks correctness and times an existing "
                       "command, but cannot profile functions or generate a benchmark yet")
        if not a.bench_wrap_cmd and a.test_cmd:
            a.notes.append("no benchmark command was found, so the test suite's own runtime is the benchmark")
    if not a.test_cmd:
        a.blockers.append("no test suite found: Hotpath can only keep a change when a correctness check proves "
                          "it; add tests (or pass --test-cmd)")
    if a.secret_files:
        a.notes.append(f"{len(a.secret_files)} secret-looking file(s) are tracked; Hotpath never stages them into "
                       "containers or model prompts, but consider removing them from git")
    if len(files) > 20_000:
        a.notes.append("large repository: narrow the editable patterns to the package you care about")
    return a
