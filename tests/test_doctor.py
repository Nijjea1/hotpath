import json

import httpx
import pytest

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
    monkeypatch.setattr("hotpath.doctor.collect", lambda **_kw: report("mps"))
    assert main(["doctor", "--json", "--require-gpu"]) == 0
    assert json.loads(capsys.readouterr().out)["accelerator"]["backend"] == "mps"


def test_doctor_required_gpu_fails_on_cpu(monkeypatch, capsys):
    monkeypatch.setattr("hotpath.doctor.collect", lambda **_kw: report("cpu"))
    assert main(["doctor", "--require-gpu"]) == 1
    assert "GPU required" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# Key validity. Presence is not validity: a revoked OPENAI_API_KEY looked healthy here and only
# surfaced when a run started, which is the fail-late problem `hotpath check` exists to remove.

def test_a_missing_key_is_missing_without_asking_anyone(monkeypatch):
    from hotpath.doctor import key_status
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert key_status("OPENAI_API_KEY", None, verify=True) == "missing"


def test_presence_is_reported_as_present_not_valid(monkeypatch):
    from hotpath.doctor import key_status
    monkeypatch.setenv("OPENAI_API_KEY", "sk-whatever")
    assert key_status("OPENAI_API_KEY", None, verify=False) == "present"


@pytest.mark.parametrize("error, expected", [
    ("AuthenticationError", "rejected"),
    ("APIConnectionError", "unverified"),
])
def test_a_rejected_key_is_distinguished_from_an_unreachable_api(monkeypatch, error, expected):
    """A network problem must never be reported against the key, and a 401 must never be hidden."""
    import openai
    from hotpath import doctor

    monkeypatch.setenv("OPENAI_API_KEY", "sk-whatever")

    class Client:
        def __init__(self, **_kw):
            self.models = self

        def list(self):
            exc = getattr(openai, error)
            if error == "AuthenticationError":
                raise exc("nope", response=httpx.Response(401, request=httpx.Request("GET", "http://x")),
                          body=None)
            raise exc(request=httpx.Request("GET", "http://x"))

    monkeypatch.setattr(openai, "OpenAI", Client)
    assert doctor.key_status("OPENAI_API_KEY", None, verify=True) == expected


def test_a_valid_key_is_reported_valid(monkeypatch):
    import openai
    from hotpath import doctor

    monkeypatch.setenv("BASETEN_API_KEY", "x")

    class Client:
        def __init__(self, **_kw):
            self.models = self

        def list(self):
            return []

    monkeypatch.setattr(openai, "OpenAI", Client)
    assert doctor.key_status("BASETEN_API_KEY", "https://inference.baseten.co/v1", verify=True) == "valid"


def test_collect_does_not_touch_the_network_unless_asked(monkeypatch):
    from hotpath import doctor
    called = []
    monkeypatch.setattr(doctor, "key_status",
                        lambda name, base, verify, **kw: called.append(verify) or "present")
    doctor.collect()
    assert called and not any(called), "doctor must stay offline by default"


def test_the_rendered_line_shouts_about_a_rejected_key():
    from hotpath.doctor import _render_keys
    text = _render_keys({"OPENAI_API_KEY": "rejected", "BASETEN_API_KEY": "valid"})
    assert "REJECTED" in text and "valid" in text
