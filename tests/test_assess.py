"""`hotpath assess`: static detection that decides what the guided flow will do."""
import json
import subprocess
from pathlib import Path

from hotpath.assess import GENERATED_FILES, assess, requirement_lines
from hotpath.cli import main


def write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


def test_python_package_layout(tmp_path):
    repo = write(tmp_path, {"pkg/__init__.py": "", "pkg/core.py": "def f(): pass\n", "helpers.py": "",
                            "tests/test_core.py": "def test_x(): pass\n",
                            "requirements.txt": "numpy>=1.20\n-e .\n.\n# comment\n-r extra.txt\n",
                            "extra.txt": "requests\n--index-url https://example.com\n"})
    a = assess(repo)
    assert (a.ecosystem, a.tier) == ("python", 1)
    assert a.test_cmd == "python -m pytest -q"
    assert a.editable == ["pkg/*.py", "helpers.py"]
    assert "tests/*" in a.locked and all(f in a.locked for f in GENERATED_FILES)
    # The project itself is never installed: an installed copy would shadow the worktree under test.
    assert a.dependencies == ["numpy>=1.20", "requests"]
    assert a.ok and "Tier 1" in a.to_markdown()


def test_src_layout_and_pyproject_dependencies(tmp_path):
    repo = write(tmp_path, {
        "src/lib/__init__.py": "", "src/lib/a.py": "",
        "tests/test_a.py": "def test_a(): pass\n",
        "pyproject.toml": '[project]\nname="lib"\ndependencies=["attrs"]\nrequires-python=">=3.10"\n'
                          '[project.optional-dependencies]\ntest=["pytest-mock"]\ndocs=["sphinx"]\n'})
    a = assess(repo)
    assert a.src_layout and a.editable == ["src/*.py"]
    assert a.dependencies == ["attrs", "pytest-mock"]           # test extras yes, docs no
    assert a.python_requires == ">=3.10"


def test_requirement_includes_do_not_loop(tmp_path):
    write(tmp_path, {"a.txt": "-r b.txt\nx\n", "b.txt": "-r a.txt\ny\n"})
    assert requirement_lines(tmp_path / "a.txt") == ["y", "x"]


def test_plain_bench_script_is_timed_as_a_command(tmp_path):
    repo = write(tmp_path, {"m.py": "", "test_m.py": "def test_m(): pass\n", "bench.py": "import time\n"})
    a = assess(repo)
    assert a.bench_cmd is None and a.bench_wrap_cmd == "python bench.py"
    assert "bench.py" in a.locked


def test_hotpath_bench_script_is_used_directly(tmp_path):
    repo = write(tmp_path, {"m.py": "", "test_m.py": "def test_m(): pass\n",
                            "bench.py": "from hotpath.benchlib import run\n"})
    assert assess(repo).bench_cmd == "python bench.py"


def test_node_is_tier_two(tmp_path):
    repo = write(tmp_path, {"package.json": json.dumps({"scripts": {"test": "node t.js", "bench": "node b.js"}}),
                            "package-lock.json": "{}", "src/a.js": ""})
    a = assess(repo)
    assert (a.ecosystem, a.tier) == ("node", 2)
    assert a.test_cmd == "npm test --silent" and a.bench_wrap_cmd == "npm run bench --silent"
    assert a.install_cmds == ["npm ci"] and "src/*.js" in a.editable and "*.test.*" in a.locked


def test_node_default_test_script_is_not_a_test_suite(tmp_path):
    repo = write(tmp_path, {"package.json": json.dumps({"scripts": {"test": "echo \"Error: no test specified\" && exit 1"}}),
                            "a.js": ""})
    a = assess(repo)
    assert not a.ok and any("no test suite" in b for b in a.blockers)


def test_unsupported_repo_is_blocked(tmp_path):
    a = assess(write(tmp_path, {"README.md": "hi"}))
    assert a.tier == 0 and not a.ok


def test_secret_looking_files_are_reported(tmp_path):
    repo = write(tmp_path, {"m.py": "", "test_m.py": "def test_m(): pass\n", ".env": "KEY=1", "deploy.pem": "x"})
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    a = assess(repo)
    assert set(a.secret_files) == {".env", "deploy.pem"}


def test_cli_assess_json(tmp_path, capsys):
    repo = write(tmp_path, {"m.py": "", "test_m.py": "def test_m(): pass\n"})
    assert main(["assess", str(repo), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ecosystem"] == "python"
