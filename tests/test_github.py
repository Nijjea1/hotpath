"""The GitHub client: remote parsing, the method ladder, and the gh CLI adapter."""
import subprocess

import pytest

from hotpath import github
from hotpath.github import (MAX_BODY_CHARS, GhClient, GitHubError, RepoRef, clamp_body, compare_url,
                            parse_remote_url, publish_pull_request)


@pytest.mark.parametrize("url,expected", [
    ("https://github.com/acme/widgets.git", ("github.com", "acme", "widgets")),
    ("https://github.com/acme/widgets", ("github.com", "acme", "widgets")),
    ("https://x-access-token:abc@github.com/acme/widgets.git", ("github.com", "acme", "widgets")),
    ("git@github.com:acme/widgets.git", ("github.com", "acme", "widgets")),
    ("ssh://git@github.com:22/acme/widgets.git", ("github.com", "acme", "widgets")),
    ("https://GHE.example.com/team/repo.name.git", ("ghe.example.com", "team", "repo.name")),
])
def test_parse_remote_url(url, expected):
    ref = parse_remote_url(url)
    assert (ref.host, ref.owner, ref.name) == expected


@pytest.mark.parametrize("url", ["", "/srv/git/repo.git", "../repo", "C:/repos/x.git", "C:\\repos\\x.git",
                                 "file:///srv/repo.git", "https://github.com/only-owner"])
def test_non_forge_remotes_are_not_parsed(url):
    assert parse_remote_url(url) is None


def test_compare_url_prefills_the_form_and_drops_an_oversized_body():
    repo = RepoRef("github.com", "acme", "widgets")
    short = compare_url(repo, "main", "hotpath/run_1", "perf: 2x", "body text")
    assert short.startswith("https://github.com/acme/widgets/compare/main...hotpath%2Frun_1?expand=1&title=perf%3A+2x")
    assert "body=body+text" in short
    long_link = compare_url(repo, "main", "hotpath/run_1", "t", "x" * 10000)
    assert "body=" not in long_link and long_link.endswith("expand=1&title=t")
    assert len(compare_url(repo, "main", "hotpath/run_1", "t", "\u00e9" * 3000)) <= github.MAX_LINK_CHARS


def test_clamp_body_respects_githubs_limit():
    assert clamp_body("short") == "short"
    clamped = clamp_body("x" * (MAX_BODY_CHARS + 5000))
    assert len(clamped) <= MAX_BODY_CHARS and "truncated" in clamped


def test_auto_without_credentials_falls_back_to_a_link(monkeypatch):
    monkeypatch.setattr(github, "gh_binary", lambda: None)
    for k in ("GITHUB_TOKEN", "GH_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    result = publish_pull_request(RepoRef("github.com", "a", "b"), "main", "hotpath/r", "t", "body")
    assert result.method == "link" and result.pr is None and "/a/b/compare/main...hotpath%2Fr" in result.compare_url


def test_explicit_methods_fail_loudly_when_unavailable(monkeypatch):
    monkeypatch.setattr(github, "gh_binary", lambda: None)
    for k in ("GITHUB_TOKEN", "GH_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    repo = RepoRef("github.com", "a", "b")
    with pytest.raises(GitHubError, match="gh is not installed"):
        publish_pull_request(repo, "main", "h", "t", "b", method="gh")
    with pytest.raises(GitHubError, match="GITHUB_TOKEN"):
        publish_pull_request(repo, "main", "h", "t", "b", method="token")


class FakeGh:
    """Records gh invocations and answers like gh 2.x does."""

    def __init__(self, open_prs=None):
        self.calls, self.open_prs = [], open_prs or []

    def __call__(self, args, check=True):
        self.calls.append(args)
        if args[:2] == ["auth", "status"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:2] == ["pr", "list"]:
            import json
            return subprocess.CompletedProcess(args, 0, json.dumps(self.open_prs), "")
        if args[:2] == ["pr", "create"]:
            return subprocess.CompletedProcess(args, 0, "Creating pull request\nhttps://github.com/a/b/pull/7\n", "")
        if args[:2] == ["pr", "edit"]:
            return subprocess.CompletedProcess(args, 0, "https://github.com/a/b/pull/7\n", "")
        raise AssertionError(args)


def test_gh_is_preferred_and_creates_a_pr(monkeypatch):
    fake = FakeGh()
    monkeypatch.setattr(github, "gh_binary", lambda: "gh")
    monkeypatch.setattr(GhClient, "_gh", fake)
    monkeypatch.setenv("GITHUB_TOKEN", "unused")
    result = publish_pull_request(RepoRef("github.com", "a", "b"), "main", "hotpath/r", "title", "body", draft=True)
    assert result.method == "gh" and result.pr.number == 7 and result.pr.created
    create = next(c for c in fake.calls if c[:2] == ["pr", "create"])
    assert create[create.index("--base") + 1] == "main" and create[create.index("--head") + 1] == "hotpath/r"
    assert "--draft" in create and "--body-file" in create and "--repo" in create


def test_gh_updates_an_existing_pr_instead_of_duplicating(monkeypatch):
    fake = FakeGh(open_prs=[{"number": 7, "url": "https://github.com/a/b/pull/7"}])
    monkeypatch.setattr(github, "gh_binary", lambda: "gh")
    monkeypatch.setattr(GhClient, "_gh", fake)
    result = publish_pull_request(RepoRef("github.com", "a", "b"), "main", "hotpath/r", "title", "body")
    assert result.pr.number == 7 and not result.pr.created
    assert not any(c[:2] == ["pr", "create"] for c in fake.calls)
    assert any(c[:3] == ["pr", "edit", "7"] for c in fake.calls)
