"""`hotpath init`: detection, the files it writes, and the CI check it generates actually working."""
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from hotpath.cli import main
from hotpath.config import find_config, load_config
from hotpath.init import (BENCH_SCAFFOLD, WORKFLOW_PATH, InitError, gather_answers, init_repo,
                          render_workflow)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo,
                          capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tiny_repo: Path) -> Path:
    git(tiny_repo, "init", "-q", "-b", "main")
    git(tiny_repo, "add", "-A")
    git(tiny_repo, "commit", "-q", "-m", "initial")
    return tiny_repo


def test_detects_commands_and_locks_what_defines_correct_and_fast(repo):
    answers, _ = gather_answers(repo, execution="local", planner="openai", worker="openai")
    assert answers.test_cmd == "python tests/check.py"
    assert answers.bench_cmd == "python bench.py"
    assert answers.editable == ["*.py"]
    for pattern in ("tests/*", "bench.py", "tests/check.py", ".github/*", ".hotpath.yaml", "conftest.py"):
        assert pattern in answers.locked


def test_pytest_projects_are_detected(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("def test_x(): pass\n")
    (tmp_path / "src").mkdir()
    answers, _ = gather_answers(tmp_path, execution="local")
    assert answers.test_cmd == "python -m pytest -q"
    assert answers.editable == ["src/*.py"]
    assert "pip install pytest" in render_workflow(answers)


def test_init_writes_a_valid_config_workflow_and_gitignore(repo):
    answers, _ = gather_answers(repo, execution="local", planner="openai", worker="baseten")
    result = init_repo(repo, answers)
    cfg = load_config(repo / ".hotpath.yaml")
    assert Path(cfg.target) == repo.resolve()
    assert cfg.test_cmd == "python tests/check.py" and cfg.execution.backend == "local"
    assert cfg.provider.planner == "openai" and cfg.provider.worker_base_url == "https://inference.baseten.co/v1"
    assert cfg.provider.worker_api_key_env == "BASETEN_API_KEY"
    wf = yaml.safe_load((repo / WORKFLOW_PATH).read_text())
    assert (wf.get(True) or wf.get("on")) == {"pull_request": None}  # PyYAML reads the `on` key as True
    steps = wf["jobs"]["correctness"]["steps"]
    assert any(s.get("run") == "python tests/check.py" for s in steps)
    assert wf["jobs"]["scope"]["if"] == "startsWith(github.head_ref, 'hotpath/')"
    assert (repo / ".gitignore").read_text().splitlines().count(".hotpath/") == 1
    assert {p.name for p in result.written} >= {".hotpath.yaml", "hotpath-verify.yml", ".gitignore"}
    assert find_config(repo / "tests") == repo / ".hotpath.yaml"


def test_init_refuses_to_overwrite_without_force(repo):
    answers, _ = gather_answers(repo, execution="local")
    init_repo(repo, answers)
    with pytest.raises(InitError, match="already exists"):
        init_repo(repo, answers)
    (repo / WORKFLOW_PATH).write_text("# customized\n")
    init_repo(repo, answers, force=True)
    assert "hotpath-verify" in (repo / WORKFLOW_PATH).read_text()
    assert (repo / ".gitignore").read_text().splitlines().count(".hotpath/") == 1


def test_a_repo_without_a_benchmark_gets_a_scaffold(tmp_path):
    git(tmp_path, "init", "-q")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "check.py").write_text("print('ok')\n")
    answers, _ = gather_answers(tmp_path, execution="local")
    result = init_repo(tmp_path, answers)
    assert answers.bench_cmd == f"python {BENCH_SCAFFOLD}" and BENCH_SCAFFOLD in answers.locked
    assert (tmp_path / BENCH_SCAFFOLD).exists() and any(BENCH_SCAFFOLD in n for n in result.notes)


def test_init_needs_git_and_a_correctness_check(tmp_path):
    with pytest.raises(InitError, match="no correctness check"):
        gather_answers(tmp_path)
    answers, _ = gather_answers(tmp_path, test_cmd="python -m pytest", execution="local")
    with pytest.raises(InitError, match="not a git repository"):
        init_repo(tmp_path, answers)


def _scope_script(repo: Path) -> str:
    """The Python the generated workflow runs, exactly as GitHub Actions would see it."""
    run = next(s["run"] for s in yaml.safe_load((repo / WORKFLOW_PATH).read_text())["jobs"]["scope"]["steps"]
               if "run" in s)
    body = run.split("<<'EOF'\n", 1)[1].rsplit("EOF", 1)[0]
    return body


@pytest.mark.parametrize("change,ok", [("mod.py", True), ("tests/check.py", False), ("bench.py", False),
                                       ("notes.txt", False), (".github/workflows/hotpath-verify.yml", False)])
def test_generated_scope_check_accepts_only_editable_unlocked_paths(repo, change, ok):
    answers, _ = gather_answers(repo, execution="local")
    init_repo(repo, answers)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "set up hotpath")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(repo, "checkout", "-q", "-b", "hotpath/run_x")
    target = repo / change
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text((target.read_text() if target.exists() else "") + "\n# changed\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "candidate")
    res = subprocess.run([sys.executable, "-c", _scope_script(repo)], cwd=repo, capture_output=True, text=True,
                         env={**os.environ, "BASE_REF": "main"})
    assert (res.returncode == 0) == ok, res.stdout + res.stderr


def test_cli_init_is_non_interactive_with_yes(repo, capsys):
    assert main(["init", str(repo), "--yes", "--execution", "local", "--worker", "openai"]) == 0
    out = capsys.readouterr().out
    assert "wrote  .hotpath.yaml" in out and "hotpath run --pr" in out
    assert load_config(repo / ".hotpath.yaml").provider.worker_model == "gpt-4.1-mini"


def test_commands_find_the_config_or_say_how_to_create_one(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["pr"]) == 1
    assert "run `hotpath init`" in capsys.readouterr().out
