from __future__ import annotations

from pathlib import Path
import hashlib
import re

import yaml
from urllib.parse import urlsplit, urlunsplit

from hotpath.schema import HotpathConfig

COMMAND_KEYS = ("test_cmd", "bench_cmd", "profile_cmd")
# The secret word must end the name: `--api-key` and `HF_TOKEN` are credentials, `--max-new-tokens` is not.
_SECRET_NAME = r"[A-Za-z0-9_-]*(?:key|token|secret|password|passwd|auth|credentials?)(?![A-Za-z0-9_-])"
_SECRET_ASSIGNMENT = re.compile(rf"(?i)\b({_SECRET_NAME})=(\"[^\"]*\"|'[^']*'|\S+)")
_SECRET_FLAG = re.compile(rf"(?i)(--?{_SECRET_NAME})(=|\s+)(\"[^\"]*\"|'[^']*'|\S+)")
_URL_CREDENTIALS = re.compile(r"(://)[^/\s:@]+(?::[^/\s@]*)?@")


def scrub_command(command: str) -> str:
    """A command as it can be shown: credentials in inline assignments (`HF_TOKEN=...`), secret-named
    flags (`--api-key ...`), URL userinfo, and values of secret environment variables are filtered."""
    from hotpath.observability import _scrub
    command = _SECRET_ASSIGNMENT.sub(r"\1=[Filtered]", command)
    command = _SECRET_FLAG.sub(r"\1\2[Filtered]", command)
    command = _URL_CREDENTIALS.sub(r"\1[Filtered]@", command)
    return _scrub(command)


def command_digest(command: str) -> str:
    return hashlib.sha256(command.encode()).hexdigest()[:16]


def legacy_redacted_command(command: str) -> str:
    """How snapshots written before commands were shown stored them: a digest of the raw text."""
    return f"[redacted; sha256:{command_digest(command)}]"


def config_snapshot(cfg: HotpathConfig) -> dict:
    """Persist decision settings, never credentials embedded in a command or endpoint URL."""
    from hotpath.observability import _scrub
    data = _scrub(cfg.model_dump(mode="json"))
    for key in COMMAND_KEYS:
        if data.get(key):
            data[key] = scrub_command(data[key])
    url = data["provider"].get("worker_base_url")
    if url:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        if parsed.port:
            host += f":{parsed.port}"
        data["provider"]["worker_base_url"] = urlunsplit((parsed.scheme, host, parsed.path, "", ""))
    return data


def load_config(path: str | Path) -> HotpathConfig:
    p = Path(path)
    raw = yaml.safe_load(p.read_text()) or {}
    cfg = HotpathConfig.model_validate(raw)
    # Resolve relative paths against the config file's directory.
    base = p.parent
    if not Path(cfg.target).is_absolute():
        cfg.target = str((base / cfg.target).resolve())
    if cfg.provider.mock_patches_dir and not Path(cfg.provider.mock_patches_dir).is_absolute():
        cfg.provider.mock_patches_dir = str((base / cfg.provider.mock_patches_dir).resolve())
    if cfg.db_path and not Path(cfg.db_path).is_absolute():
        cfg.db_path = str((base / cfg.db_path).resolve())
    return cfg
