"""Watch a published pull request's CI, and tell the difference between our fault and theirs.

A verified speedup that turns someone's CI red is not a finished job. But a repository's CI can be
red for reasons that have nothing to do with the change — a flaky job, a broken main, a check that
needs secrets a fork does not have. Fixing those is not Hotpath's business and pretending to have
fixed them would be worse.

So every failing check is classified against the *base commit* before anything is attempted:

- ``introduced``  — passes on base, fails on the PR head. Ours. Worth fixing.
- ``pre_existing`` — fails on base too. Reported, never touched.
- ``unknown``     — no result on base to compare against (a check that only runs on PRs, or a fork
                    without the secrets). Reported as unknown; Hotpath does not claim it passed.

Only ``introduced`` failures are fed back to a worker, and any fix it writes goes through the same
harness as every other patch: the locked tests must pass and the benchmark must still be faster.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from hotpath.github import RepoRef, gh_binary

#: Conclusions that do not mean "this check failed".
OK_CONCLUSIONS = {"success", "neutral", "skipped"}
#: Conclusions we will not try to fix: a human cancelled it, or it needs someone to approve a run.
UNACTIONABLE = {"cancelled", "action_required", "stale"}

#: Lines worth showing a model, in the order a log tends to reveal them.
_SIGNAL = re.compile(
    r"(Traceback \(most recent call last\)|^E\s+\w+|"
    r"ERROR collecting|ModuleNotFoundError|ImportError|AttributeError|TypeError|ValueError|"
    r"SyntaxError|NameError|AssertionError|FAILED |error:|Error:|\d+ failed)",
    re.MULTILINE)


@dataclass
class Check:
    name: str
    conclusion: str          # success | failure | neutral | skipped | cancelled | timed_out | ""
    status: str              # queued | in_progress | completed
    url: str = ""
    run_id: str = ""

    @property
    def settled(self) -> bool:
        return self.status == "completed"

    @property
    def failed(self) -> bool:
        return self.settled and self.conclusion not in OK_CONCLUSIONS


@dataclass
class Verdict:
    """What CI says about a pull request, split by whose fault it is."""
    introduced: list[Check] = field(default_factory=list)
    pre_existing: list[Check] = field(default_factory=list)
    unknown: list[Check] = field(default_factory=list)
    passed: list[Check] = field(default_factory=list)
    timed_out: bool = False
    unavailable: str = ""        # why no verdict could be reached at all

    @property
    def green(self) -> bool:
        return not self.introduced and not self.unknown and not self.unavailable and not self.timed_out

    def summary(self) -> str:
        if self.unavailable:
            return self.unavailable
        bits = [f"{len(self.passed)} passed"]
        for label, checks in (("introduced by this PR", self.introduced),
                              ("already failing on the base", self.pre_existing),
                              ("no base result to compare", self.unknown)):
            if checks:
                bits.append(f"{len(checks)} {label}")
        return ", ".join(bits) + (" (still running when we stopped waiting)" if self.timed_out else "")


class Gh:
    """The `gh` CLI, used read-only. Returns None rather than raising when a query cannot be made."""

    def __init__(self, repo: RepoRef, binary: Optional[str] = None, timeout: float = 60.0):
        self.repo, self.binary, self.timeout = repo, binary or gh_binary(), timeout

    @property
    def available(self) -> bool:
        return bool(self.binary)

    def _json(self, args: list[str]) -> Optional[object]:
        if not self.binary:
            return None
        try:
            res = subprocess.run([self.binary, *args, "--repo", self.repo.slug()],
                                 capture_output=True, text=True, timeout=self.timeout)
        except (OSError, subprocess.SubprocessError):
            return None
        if res.returncode != 0 or not res.stdout.strip():
            return None
        try:
            return json.loads(res.stdout)
        except json.JSONDecodeError:
            return None

    def checks_for_ref(self, ref: str) -> Optional[list[Check]]:
        """Every check run recorded against a commit."""
        data = self._json(["api", f"repos/{self.repo.slug()}/commits/{ref}/check-runs",
                           "--paginate", "--jq", ".check_runs"])
        if data is None:
            return None
        rows = data if isinstance(data, list) else []
        out: list[Check] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            out.append(Check(name=str(row.get("name", "?")),
                             conclusion=str(row.get("conclusion") or ""),
                             status=str(row.get("status") or ""),
                             url=str(row.get("html_url") or ""),
                             run_id=str((row.get("check_suite") or {}).get("id") or "")))
        return out

    def failing_log(self, check: Check, max_chars: int = 4000) -> str:
        """The interesting lines of a failed job's log, newest signal first."""
        if not self.binary or not check.url:
            return ""
        m = re.search(r"/job/(\d+)", check.url)
        if not m:
            return ""
        try:
            res = subprocess.run([self.binary, "run", "view", "--job", m.group(1), "--log-failed",
                                  "--repo", self.repo.slug()],
                                 capture_output=True, text=True, timeout=self.timeout)
        except (OSError, subprocess.SubprocessError):
            return ""
        text = res.stdout or ""
        # Strip the leading "job<TAB>step<TAB>timestamp " that `gh` prefixes to every line.
        lines = [re.sub(r"^.*?\d{4}-\d{2}-\d{2}T[\d:.]+Z\s?", "", ln).rstrip() for ln in text.splitlines()]
        signal = [ln for ln in lines if ln.strip() and _SIGNAL.search(ln)]
        chosen = signal or [ln for ln in lines if ln.strip()]
        excerpt = "\n".join(chosen[-60:])
        return excerpt[-max_chars:]


def wait_for_checks(gh: Gh, ref: str, *, timeout_s: float = 900, poll_s: float = 20,
                    settle_s: float = 45, say: Callable[[str], None] = lambda _m: None
                    ) -> tuple[list[Check], bool]:
    """Poll until every check on `ref` has completed. Returns (checks, timed_out).

    CI does not register all of its jobs at once, so "zero checks" and "all checks passed" look
    identical for the first minute. `settle_s` is how long an empty or all-green result must hold
    before it is believed.
    """
    deadline = time.monotonic() + timeout_s
    stable_since: Optional[float] = None
    last_note = 0.0
    checks: list[Check] = []
    while time.monotonic() < deadline:
        fetched = gh.checks_for_ref(ref)
        if fetched is None:
            return [], False
        checks = fetched
        pending = [c for c in checks if not c.settled]
        if pending:
            stable_since = None
            if time.monotonic() - last_note >= 60:
                last_note = time.monotonic()
                say(f"waiting on {len(pending)} of {len(checks)} checks")
        else:
            stable_since = stable_since or time.monotonic()
            if time.monotonic() - stable_since >= settle_s:
                return checks, False
        time.sleep(poll_s)
    return checks, True


def classify(head_checks: list[Check], base_checks: Optional[list[Check]]) -> Verdict:
    """Split failures into ones this PR introduced and ones the base already had."""
    verdict = Verdict()
    base_by_name = {c.name: c for c in (base_checks or [])}
    for check in head_checks:
        if not check.failed:
            if check.settled:
                verdict.passed.append(check)
            continue
        if check.conclusion in UNACTIONABLE:
            verdict.unknown.append(check)
            continue
        base = base_by_name.get(check.name)
        if base is None or not base.settled:
            verdict.unknown.append(check)
        elif base.failed:
            verdict.pre_existing.append(check)
        else:
            verdict.introduced.append(check)
    return verdict


def inspect(gh: Gh, head_sha: str, base_sha: str, *, timeout_s: float = 900,
            say: Callable[[str], None] = lambda _m: None) -> Verdict:
    """Wait for the head's checks, then judge them against the base commit's."""
    if not gh.available:
        return Verdict(unavailable="the gh CLI is not available, so CI could not be read")
    head_checks, timed_out = wait_for_checks(gh, head_sha, timeout_s=timeout_s, say=say)
    if not head_checks:
        return Verdict(unavailable="no CI checks are configured on this repository" if not timed_out
                       else "no CI checks had reported before the wait expired")
    base_checks = gh.checks_for_ref(base_sha)
    verdict = classify(head_checks, base_checks)
    verdict.timed_out = timed_out
    return verdict
