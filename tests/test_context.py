import pytest

from hotpath.context import build_source_context, read_target_file
from hotpath.schema import ContextConfig, Hotspot, ProfileSummary


@pytest.mark.parametrize("rel", ["../secret.py", "..\\secret.py", "C:\\secret.py", "C:secret.py", "//server/share/a.py", "/secret.py"])
def test_source_rejects_escape_paths(tmp_path, rel):
    with pytest.raises(ValueError, match="unsafe source path"):
        read_target_file(tmp_path, rel, 100)


def test_target_source_is_complete_or_explicitly_rejected(tmp_path):
    source = "x = 'é'\n" * 10
    (tmp_path / "mod.py").write_text(source, encoding="utf-8")
    assert read_target_file(tmp_path, "mod.py", len(source)) == source
    with pytest.raises(ValueError, match="increase context.max_source_chars"):
        read_target_file(tmp_path, "mod.py", 5)
    with pytest.raises(FileNotFoundError):
        read_target_file(tmp_path, "missing.py", 100)


def test_symlink_source_cannot_leave_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    secret = tmp_path / "secret.py"
    secret.write_text("SECRET")
    try:
        (repo / "mod.py").symlink_to(secret)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(ValueError, match="escapes repository"):
        read_target_file(repo, "mod.py", 100)
    assert "SECRET" not in build_source_context(repo, ProfileSummary(), ContextConfig(), ["*.py"], [])


def test_locked_source_and_internal_symlink_are_not_sent_to_models(tmp_path):
    (tmp_path / "private.py").write_text("def private():\n    return 'SECRET'\n")
    (tmp_path / "public.py").write_text("def public():\n    return 1\n")
    try:
        (tmp_path / "alias.py").symlink_to(tmp_path / "private.py")
    except OSError:
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(ValueError, match="symlink"):
        read_target_file(tmp_path, "alias.py", 100)
    profile = ProfileSummary(hotspots=[
        Hotspot(function="private", file="private.py", line=1, self_time=2, total_time=2, pct=50),
        Hotspot(function="private", file="alias.py", line=1, self_time=1, total_time=1, pct=25),
        Hotspot(function="public", file="public.py", line=1, self_time=1, total_time=1, pct=25),
    ])
    context = build_source_context(tmp_path, profile, ContextConfig(), ["*.py"], ["private.py"])
    assert "SECRET" not in context
    assert "def public" in context


def test_profile_cannot_read_outside_repository(tmp_path):
    (tmp_path / "mod.py").write_text("def local():\n    return 1\n")
    profile = ProfileSummary(hotspots=[Hotspot(function="secret", file="../secret.py", line=1,
                                             self_time=1, total_time=1, pct=100)])
    result = build_source_context(tmp_path, profile, ContextConfig(max_source_chars=5), ["*.py"], [])
    assert "INCOMPLETE SOURCE" in result
