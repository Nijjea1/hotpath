from __future__ import annotations

from pathlib import Path
import hashlib
import os
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


#: What `hotpath init` writes at a repository root, and what every command looks for when no config is given.
CONFIG_NAMES = (".hotpath.yaml", ".hotpath.yml")


class ConfigNotFound(FileNotFoundError):
    pass


def find_config(start: str | Path | None = None) -> Path:
    """The nearest `.hotpath.yaml`, searching from `start` (default: cwd) up to the filesystem root."""
    here = Path(start or Path.cwd()).resolve()
    for d in (here, *here.parents):
        for name in CONFIG_NAMES:
            if (d / name).is_file():
                return d / name
    raise ConfigNotFound(f"no {CONFIG_NAMES[0]} in {here} or any parent directory; run `hotpath init` in your repository first")


def hotpath_home() -> Path:
    return Path(os.environ.get("HOTPATH_HOME") or (Path.home() / ".hotpath"))


def load_env_files() -> list[Path]:
    """Load API keys without ever overriding the real environment: first `./.env`, then the user-level
    `~/.hotpath/.env`, which is where keys belong when Hotpath runs inside someone's own repository."""
    from dotenv import load_dotenv
    loaded = []
    for p in (Path.cwd() / ".env", hotpath_home() / ".env"):
        if p.is_file():
            load_dotenv(p, override=False)
            loaded.append(p)
    return loaded


def load_config(path: str | Path) -> HotpathConfig:
    p = Path(path)
    # utf-8-sig accepts ordinary UTF-8 and strips the BOM commonly written by
    # Windows editors/PowerShell. A BOM before an initial YAML comment is
    # otherwise parsed as scalar content and makes the following mapping fail.
    raw = yaml.safe_load(p.read_text(encoding="utf-8-sig")) or {}
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
