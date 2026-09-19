import pytest
from pydantic import ValidationError

from hotpath.config import config_snapshot
from hotpath.schema import HotpathConfig


@pytest.mark.parametrize("field,value", [
    ("search", {"max_parallel_workers": 0}),
    ("search", {"max_parallel_tests": -1}),
    ("search", {"max_parallel_benchmarks": 2}),
    ("search", {"iterations": 0}),
    ("benchmark", {"bootstrap_samples": 0}),
    ("benchmark", {"confidence": 2}),
    ("benchmark", {"min_speedup": float("nan")}),
    ("timeouts", {"test": 0}),
    ("execution", {"memory_mb": 0}),
    ("execution", {"network": True}),
])
def test_invalid_config_rejected(cfg, field, value):
    raw = cfg.model_dump()
    raw[field].update(value)
    with pytest.raises(ValidationError):
        HotpathConfig.model_validate(raw)


def test_execution_is_isolated_by_default(cfg):
    raw = cfg.model_dump()
    del raw["execution"]
    assert HotpathConfig.model_validate(raw).execution.backend == "docker"


def test_gpu_requires_exclusive_benchmarks(cfg):
    raw = cfg.model_dump()
    raw["execution"]["gpu"] = "device=0"
    raw["benchmark"]["exclusive"] = False
    with pytest.raises(ValidationError, match="exclusive"):
        HotpathConfig.model_validate(raw)


def test_snapshot_omits_url_credentials_and_known_secrets(cfg, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "private-test-credential")
    cfg.provider.worker_base_url = "https://user:password@example.test/v1?token=secret#secret"
    cfg.correctness_contract = "private-test-credential"
    saved = config_snapshot(cfg)
    assert saved["provider"]["worker_base_url"] == "https://example.test/v1"
    assert saved["correctness_contract"] == "[Filtered]"
    assert saved["test_cmd"] == "python tests/check.py", "commands are shown, not hashed"


@pytest.mark.parametrize("command, shown", [
    ("python tests/check.py", "python tests/check.py"),
    ("HF_TOKEN=abc123 python bench.py", "HF_TOKEN=[Filtered] python bench.py"),
    ("python b.py --api-key sk-live-1 --max-new-tokens 256", "python b.py --api-key [Filtered] --max-new-tokens 256"),
    ("python b.py --password='a b' --n 3", "python b.py --password=[Filtered] --n 3"),
    ("curl https://user:pw@host/x", "curl https://[Filtered]@host/x"),
])
def test_commands_are_shown_with_inline_credentials_filtered(command, shown):
    from hotpath.config import scrub_command
    assert scrub_command(command) == shown


def test_command_filter_also_catches_secret_environment_values(monkeypatch):
    from hotpath.config import scrub_command
    monkeypatch.setenv("BASETEN_API_KEY", "private-baseten-key")
    assert scrub_command("python b.py private-baseten-key") == "python b.py [Filtered]"


def test_dryft_h100_config_is_isolated_on_one_gpu_and_exclusive():
    from hotpath.config import load_config
    cfg = load_config("configs/dryft_h100.yaml")
    assert cfg.execution.backend == "docker"
    assert cfg.execution.gpu == "device=0"
    assert cfg.benchmark.exclusive and cfg.benchmark.rebenchmark_parent
    assert cfg.search.max_parallel_benchmarks == 1
    assert {"tests/*", "bench.py", "hotprofile.py"} <= set(cfg.locked)
    assert cfg.provider.planner == cfg.provider.worker == "openai"


def test_dryft_local_differs_from_the_docker_config_only_in_execution():
    """The local config is a faster path to the same search, not a different experiment."""
    from hotpath.config import load_config
    docker, local = load_config("configs/dryft_h100.yaml"), load_config("configs/dryft_local.yaml")
    assert local.execution.backend == "local"
    a, b = docker.model_dump(), local.model_dump()
    for d in (a, b):
        d.pop("name"), d.pop("execution")
    assert a == b
