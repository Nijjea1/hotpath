import json

from hotpath.cli import main
from hotpath.doctor import render


def report(backend="cpu"):
    return {
        "hotpath": "0.1.0",
        "platform": {"system": "TestOS", "release": "1", "machine": "test64"},
        "python": {"version": "3.12.0", "supported": True, "executable": "python"},
        "git": {"ok": True, "detail": "git version test"},
        "docker": {"installed": True, "running": False, "detail": "not running"},
        "accelerator": {"installed": True, "device": backend, "backend": backend, "detail": "Test GPU"},
        "credentials": {"OPENAI_API_KEY": False, "GITHUB_TOKEN": True},
    }


def test_doctor_render_never_prints_secret_values():
    text = render(report("rocm"))
    assert "rocm / rocm" in text and "GITHUB_TOKEN" in text
    assert "OPENAI_API_KEY" not in text


def test_doctor_json_and_required_gpu(monkeypatch, capsys):
    monkeypatch.setattr("hotpath.doctor.collect", lambda: report("mps"))
    assert main(["doctor", "--json", "--require-gpu"]) == 0
    assert json.loads(capsys.readouterr().out)["accelerator"]["backend"] == "mps"


def test_doctor_required_gpu_fails_on_cpu(monkeypatch, capsys):
    monkeypatch.setattr("hotpath.doctor.collect", lambda: report("cpu"))
    assert main(["doctor", "--require-gpu"]) == 1
    assert "GPU required" in capsys.readouterr().err
