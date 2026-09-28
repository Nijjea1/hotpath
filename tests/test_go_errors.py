"""When `hotpath go` cannot continue, it says where and what to do next — it never hangs or dumps a traceback."""
import os
import subprocess
import sys

import pytest

from hotpath import go as go_module
from hotpath.config import stdin_is_terminal
from hotpath.go import Go, GoOptions


def _run(tmp_path, monkeypatch, ask=None, **kw):
    # Run from a clean directory: observability loads `./.env`, and a developer's real key must not leak in.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOTPATH_HOME", str(tmp_path / "home"))
    o = GoOptions(target=str(tmp_path), open_browser=False, dashboard=False, workspaces=str(tmp_path / "ws"), **kw)
    lines: list[str] = []
    code = Go(o, out=lines.append, ask=ask).run()
    return code, "\n".join(lines)


def test_the_null_device_is_not_a_terminal():
    """On Windows `NUL` is a character device, so `isatty()` says True; a prompt there waits forever."""
    with open(os.devnull) as devnull:
        out = subprocess.run([sys.executable, "-c", "from hotpath.config import stdin_is_terminal as t; print(t())"],
                             stdin=devnull, capture_output=True, text=True, check=True).stdout.strip()
    assert out == "False"


def test_captured_stdin_is_not_a_terminal():
    assert stdin_is_terminal() is False  # pytest replaces stdin


def test_a_missing_key_without_a_terminal_stops_with_the_way_on(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(go_module.getpass, "getpass",
                        lambda *_a, **_k: pytest.fail("prompted for a key with nobody at the terminal"))
    code, out = _run(tmp_path, monkeypatch, provider="openai")
    assert code == 1
    assert "stopped at [1/9] Setup: OPENAI_API_KEY is not set" in out
    assert "--provider mock" in out and str(tmp_path / "home" / ".env") in out


def test_a_prompted_key_is_saved_to_the_user_home_never_the_working_directory(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(go_module.getpass, "getpass", lambda *_a, **_k: "sk-test")
    monkeypatch.setattr(Go, "_key_works", staticmethod(lambda *_a: True))
    monkeypatch.setattr(Go, "fetch", lambda self, i: (_ for _ in ()).throw(go_module.GoStop("stop here")))
    _run(tmp_path, monkeypatch, ask=lambda _q: "", provider="openai")
    assert (tmp_path / "home" / ".env").read_text(encoding="utf-8").strip() == "OPENAI_API_KEY=sk-test"
    assert not (tmp_path / ".env").exists(), "a key must never be written into the directory being optimized"


def test_an_unexpected_error_names_the_stage_instead_of_a_traceback(tmp_path, monkeypatch):
    def boom(self, i):
        raise RuntimeError("something nobody planned for")
    monkeypatch.setattr(Go, "fetch", boom)
    code, out = _run(tmp_path, monkeypatch, provider="mock")
    assert code == 1
    assert "unexpected error at [2/9] Fetch: RuntimeError: something nobody planned for" in out
    assert "hotpath -v go" in out and "--resume" in out
