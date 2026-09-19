"""Opening (or updating) a GitHub pull request for a branch Hotpath already pushed.

Three ways, tried in order, so the same command works on a laptop, a CI box, or a bare GPU host:

1. the `gh` CLI, when it is installed and logged in for the remote's host;
2. a token from `GITHUB_TOKEN` / `GH_TOKEN`, against the REST API;
3. no credentials at all: a pre-filled "compare" link that opens GitHub's PR form.

A PR is keyed on its head branch, so publishing the same run twice edits the existing PR
instead of opening a duplicate. No token is ever logged or embedded in an error.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlencode

#: GitHub rejects PR bodies over 65536 characters.
MAX_BODY_CHARS = 65000
#: Browsers and GitHub reject very long URLs; a body that would push the link past this is left for
#: the user to paste from the saved file instead.
MAX_LINK_CHARS = 7500


class GitHubError(RuntimeError):
    """GitHub refused or could not be reached. The message never contains a credential."""


@dataclass(frozen=True)
class RepoRef:
    host: str
    owner: str
    name: str

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.name}"

    @property
    def web_url(self) -> str:
        return f"https://{self.host}/{self.slug}"


@dataclass(frozen=True)
class PullRequest:
    url: str
    number: int
    created: bool


_REMOTE_PATTERNS = (
    re.compile(r"^https?://(?:[^@/]+@)?(?P<host>[^/:]+)(?::\d+)?/(?P<owner>[^/]+)/(?P<name>[^/]+?)(?:\.git)?/?$"),
    re.compile(r"^ssh://(?:[^@/]+@)?(?P<host>[^/:]+)(?::\d+)?/(?P<owner>[^/]+)/(?P<name>[^/]+?)(?:\.git)?/?$"),
    re.compile(r"^(?:[^@/]+@)?(?P<host>[^/:]+):(?P<owner>[^/]+)/(?P<name>[^/]+?)(?:\.git)?/?$"),
)


def parse_remote_url(url: str) -> Optional[RepoRef]:
    """owner/name from an https, ssh, or scp-style remote URL; None for anything else (e.g. a path)."""
    url = url.strip()
    if not url or url.startswith(("/", ".", "file:")) or re.match(r"^[A-Za-z]:[\\/]", url):
        return None
    for pattern in _REMOTE_PATTERNS:
        m = pattern.match(url)
        if m:
            return RepoRef(m["host"].lower(), m["owner"], m["name"])
    return None


def api_base(repo: RepoRef) -> str:
    override = os.environ.get("HOTPATH_GITHUB_API")
    if override:
        return override.rstrip("/")
    return "https://api.github.com" if repo.host == "github.com" else f"https://{repo.host}/api/v3"


def github_token() -> Optional[str]:
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or None


def compare_url(repo: RepoRef, base: str, branch: str, title: str, body: str) -> str:
    """GitHub's 'Open a pull request' form with the base, head, and title (and body, if short) filled in."""
    prefix = f"{repo.web_url}/compare/{quote(base, safe='')}...{quote(branch, safe='')}?"
    with_body = prefix + urlencode({"expand": "1", "title": title, "body": body})
    return with_body if len(with_body) <= MAX_LINK_CHARS else prefix + urlencode({"expand": "1", "title": title})


def clamp_body(body: str) -> str:
    if len(body) <= MAX_BODY_CHARS:
        return body
    note = "\n\n_(report truncated to fit GitHub's size limit; the full report is in the run's REPORT.md)_\n"
    return body[: MAX_BODY_CHARS - len(note)] + note


# --------------------------------------------------------------------------- #
# REST API with a token
# --------------------------------------------------------------------------- #

class TokenClient:
    method = "token"

    def __init__(self, repo: RepoRef, token: str, api: Optional[str] = None, timeout: float = 30.0):
        self.repo, self._token, self.api, self.timeout = repo, token, (api or api_base(repo)), timeout

    def _request(self, method: str, path: str, body: Optional[dict] = None):
        req = urllib.request.Request(
            self.api + path, method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {self._token}", "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "hotpath",
                     **({"Content-Type": "application/json"} if body is not None else {})})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                payload = json.loads(e.read() or b"{}")
                detail = payload.get("message", "")
                errors = payload.get("errors") or []
                detail += "".join(f"; {x.get('message') or x}" if isinstance(x, dict) else f"; {x}" for x in errors)
            except (ValueError, AttributeError):
                pass
            hint = {401: " (token invalid or expired)",
                    403: " (token lacks 'Pull requests: write' on this repository, or is rate limited)",
                    404: " (repository not found, or the token cannot see it)"}.get(e.code, "")
            raise GitHubError(f"GitHub API {method} {path} returned {e.code}{hint}: {detail}".rstrip(": ")) from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise GitHubError(f"could not reach GitHub API at {self.api}: {getattr(e, 'reason', e)}") from None
        return json.loads(raw) if raw else None

    def find_open(self, branch: str) -> Optional[PullRequest]:
        q = urlencode({"state": "open", "head": f"{self.repo.owner}:{branch}"})
        for pr in self._request("GET", f"/repos/{self.repo.slug}/pulls?{q}") or []:
            if pr.get("head", {}).get("ref") == branch:
                return PullRequest(pr["html_url"], int(pr["number"]), created=False)
        return None

    def create(self, base: str, branch: str, title: str, body: str, draft: bool) -> PullRequest:
        pr = self._request("POST", f"/repos/{self.repo.slug}/pulls",
                           {"title": title, "head": branch, "base": base, "body": body, "draft": draft})
        return PullRequest(pr["html_url"], int(pr["number"]), created=True)

    def update(self, number: int, title: str, body: str) -> PullRequest:
        pr = self._request("PATCH", f"/repos/{self.repo.slug}/pulls/{number}", {"title": title, "body": body})
        return PullRequest(pr["html_url"], int(pr["number"]), created=False)


# --------------------------------------------------------------------------- #
# gh CLI
# --------------------------------------------------------------------------- #

def gh_binary() -> Optional[str]:
    return os.environ.get("HOTPATH_GH") or shutil.which("gh")


class GhClient:
    method = "gh"

    def __init__(self, repo: RepoRef, binary: str, timeout: float = 60.0):
        self.repo, self.binary, self.timeout = repo, binary, timeout

    def _gh(self, args: list[str], check: bool = True) -> subprocess.CompletedProcess:
        env = {**os.environ, "GH_PROMPT_DISABLED": "1", "NO_COLOR": "1"}
        try:
            res = subprocess.run([self.binary, *args], capture_output=True, text=True, timeout=self.timeout, env=env)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise GitHubError(f"gh {args[0]} failed to run: {e}") from None
        if check and res.returncode != 0:
            raise GitHubError(f"gh {' '.join(args[:2])} failed: {(res.stderr or res.stdout).strip()[-500:]}")
        return res

    def available(self) -> bool:
        try:
            return self._gh(["auth", "status", "--hostname", self.repo.host], check=False).returncode == 0
        except GitHubError:
            return False

    def find_open(self, branch: str) -> Optional[PullRequest]:
        res = self._gh(["pr", "list", "--repo", self.repo.slug, "--head", branch, "--state", "open",
                        "--json", "number,url"])
        items = json.loads(res.stdout or "[]")
        return PullRequest(items[0]["url"], int(items[0]["number"]), created=False) if items else None

    def _with_body_file(self, body: str, args: list[str]) -> str:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "body.md"
            path.write_text(body, encoding="utf-8")
            return self._gh([*args, "--body-file", str(path)]).stdout.strip()

    def create(self, base: str, branch: str, title: str, body: str, draft: bool) -> PullRequest:
        out = self._with_body_file(body, ["pr", "create", "--repo", self.repo.slug, "--base", base,
                                          "--head", branch, "--title", title, *(["--draft"] if draft else [])])
        url = next((line for line in reversed(out.splitlines()) if "/pull/" in line), "").strip()
        m = re.search(r"/pull/(\d+)", url)
        if not m:
            raise GitHubError(f"gh pr create did not print a PR URL: {out[-300:]}")
        return PullRequest(url, int(m.group(1)), created=True)

    def update(self, number: int, title: str, body: str) -> PullRequest:
        self._with_body_file(body, ["pr", "edit", str(number), "--repo", self.repo.slug, "--title", title])
        return PullRequest(f"{self.repo.web_url}/pull/{number}", number, created=False)


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class PublishResult:
    method: str                    # "gh" | "token" | "link"
    pr: Optional[PullRequest]      # None for "link"
    compare_url: str


def choose_client(repo: RepoRef, method: str = "auto"):
    """The client for `method`, or None when only the link fallback is possible."""
    if method not in ("auto", "gh", "token", "link"):
        raise ValueError(f"unknown PR method {method!r}")
    if method in ("auto", "gh"):
        binary = gh_binary()
        client = GhClient(repo, binary) if binary else None
        if client and client.available():
            return client
        if method == "gh":
            raise GitHubError("gh is not installed or not logged in (run `gh auth login`)")
    if method in ("auto", "token"):
        token = github_token()
        if token:
            return TokenClient(repo, token)
        if method == "token":
            raise GitHubError("no GITHUB_TOKEN or GH_TOKEN in the environment")
    return None


def publish_pull_request(repo: RepoRef, base: str, branch: str, title: str, body: str,
                         draft: bool = False, method: str = "auto") -> PublishResult:
    body = clamp_body(body)
    link = compare_url(repo, base, branch, title, body)
    client = choose_client(repo, method)
    if client is None:
        return PublishResult("link", None, link)
    existing = client.find_open(branch)
    pr = client.update(existing.number, title, body) if existing else client.create(base, branch, title, body, draft)
    return PublishResult(client.method, pr, link)
