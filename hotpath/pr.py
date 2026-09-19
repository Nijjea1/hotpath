"""Turn a finished run into a pull request on the repository it optimized.

The PR is rebuilt from what the harness recorded, never from what a model claimed:

- One commit per shipped change, each carrying the exact tree the harness tested and benchmarked
  (`git commit-tree` on the recorded commit's tree), so what a reviewer merges is byte-for-byte what
  was verified. The message states the measured speedup, its confidence interval, and the gate.
- Every shipped path is re-checked against the run's editable/locked rules before anything is
  written, so a PR can never carry an edit to a test or benchmark.
- The PR targets the branch the run measured. If that branch moved on the remote since, the
  measurement no longer describes the merge, and publishing is refused unless explicitly allowed.

Commit dates come from when each experiment was verified, so rebuilding the same run yields the same
commit SHAs and re-publishing is a no-op push plus a PR description refresh.

Local git steps (commit-tree, update-ref) are plumbing: they never touch the working tree or index.
The push uses the user's own git configuration so their credentials apply; repository hooks never run.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from hotpath.ablation import PruneResult
from hotpath.config import scrub_command
from hotpath.export import _accepted_chain, _md_cell
from hotpath.github import GitHubError, parse_remote_url, publish_pull_request
from hotpath.profilediff import diff_profiles, render as render_bottlenecks
from hotpath.schema import (REJECTED_STATUSES, Experiment, ExperimentStatus, HotpathConfig, PullRequestRecord,
                            RunState, now)
from hotpath.store import Store
from hotpath.workspace import Workspace, path_allowed

BRANCH_PREFIX = "hotpath/"
BODY_MARKER = "<!-- hotpath:run={run_id} -->"


class PRError(RuntimeError):
    """The run cannot be published as a PR; the message says why and what to do instead."""


# --------------------------------------------------------------------------- #
# git
# --------------------------------------------------------------------------- #

def _git(args: list[str], cwd: Path, env: Optional[dict] = None, timeout: float = 120) -> str:
    """The user's git: their identity and credential helpers apply. Hooks never run, and no prompt can
    block (a missing credential fails fast with git's own message)."""
    full_env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", **(env or {})}
    try:
        res = subprocess.run(["git", "-c", f"core.hooksPath={os.devnull}", *args], cwd=str(cwd),
                             capture_output=True, text=True, env=full_env, timeout=timeout)
    except FileNotFoundError:
        raise PRError("git is not installed or not on PATH") from None
    except subprocess.TimeoutExpired:
        raise PRError(f"git {args[0]} timed out after {timeout:.0f}s") from None
    if res.returncode != 0:
        raise PRError(f"git {' '.join(args[:2])} failed: {res.stderr.strip() or res.stdout.strip()}")
    return res.stdout.strip()


def _git_ok(args: list[str], cwd: Path) -> Optional[str]:
    try:
        return _git(args, cwd)
    except PRError:
        return None


def _identity(repo: Path) -> tuple[str, str]:
    name = _git_ok(["config", "user.name"], repo) or "Hotpath"
    email = _git_ok(["config", "user.email"], repo) or "hotpath@localhost"
    return name, email


def _git_date(ts: datetime) -> str:
    return f"{int(ts.timestamp())} +0000"


def remote_branch_tip(repo: Path, remote: str, branch: str) -> Optional[str]:
    out = _git(["ls-remote", "--heads", remote, f"refs/heads/{branch}"], repo)
    for line in out.splitlines():
        sha, _, ref = line.partition("\t")
        if ref == f"refs/heads/{branch}":
            return sha
    return None


def remote_default_branch(repo: Path, remote: str) -> Optional[str]:
    out = _git_ok(["ls-remote", "--symref", remote, "HEAD"], repo) or ""
    for line in out.splitlines():
        if line.startswith("ref: refs/heads/"):
            return line[len("ref: refs/heads/"):].split("\t")[0]
    return None


# --------------------------------------------------------------------------- #
# Messages
# --------------------------------------------------------------------------- #

def _metric(run: RunState) -> str:
    return run.baseline_benchmark.metric if run.baseline_benchmark else "seconds"


def _num(v: Optional[float]) -> str:
    return "–" if v is None else f"{v:.5g}"


def _run_config(cfg: HotpathConfig, run: RunState) -> HotpathConfig:
    """The settings the run was measured with. The server's current config must never be attributed
    to a historical run; fall back to it only for runs stored before snapshots existed."""
    if run.config_snapshot:
        try:
            return HotpathConfig.model_validate(run.config_snapshot)
        except ValueError:
            pass
    return cfg


def _subject(text: str, limit: int = 72) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def commit_message(e: Experiment, parent_median: Optional[float], metric: str, test_cmd: str, bench_cmd: str,
                   run_id: str) -> str:
    c = e.comparison
    lines = [_subject(f"perf: {e.hypothesis.idea}"), ""]
    if c:
        lines.append(f"{c.speedup_vs_parent:.3f}x faster than its parent (95% CI {c.ci_low:.3f}x-{c.ci_high:.3f}x; "
                     f"acceptance threshold {c.threshold:.3f}x).")
    if e.benchmark:
        lines.append(f"Median {metric}: {_num(parent_median)} -> {_num(e.benchmark.median)} "
                     f"over {e.benchmark.n} trials of `{bench_cmd}`.")
    lines.append(f"Correctness: the locked check `{test_cmd}` passed on exactly this tree.")
    if e.hypothesis.rationale.strip():
        lines += ["", "Why: " + " ".join(e.hypothesis.rationale.split())]
    if e.retry_of:
        lines += ["", f"Verified on a retry after the first attempt ({e.retry_of}) failed."]
    lines += ["", f"Hotpath-Run: {run_id}", f"Hotpath-Experiment: {e.id}"]
    return "\n".join(lines) + "\n"


def pr_title(run: RunState, n_changes: int, speedup: float) -> str:
    noun = "change" if n_changes == 1 else "changes"
    return f"perf: {speedup:.2f}x faster ({n_changes} verified {noun}) [hotpath]"


def pr_body(cfg: HotpathConfig, run: RunState, exps: list[Experiment], chain: list[Experiment],
            shipped: set[str], speedup: float, head_sha: str, branch: str, remote: str,
            pruned: Optional[PruneResult] = None, ablation_md: Optional[str] = None) -> str:
    rc = _run_config(cfg, run)
    metric = _metric(run)
    test_cmd, bench_cmd = scrub_command(rc.test_cmd), scrub_command(rc.bench_cmd)
    b, h = run.baseline_benchmark, run.head_benchmark
    shipped_chain = [e for e in chain if e.id in shipped]
    use_pruned = pruned is not None and pruned.status == "pruned"
    rejected = [e for e in exps if e.status in REJECTED_STATUSES]
    accepted_elsewhere = [e for e in exps if e.status in (ExperimentStatus.accepted, ExperimentStatus.not_selected)
                          and e.id not in shipped]

    out = [BODY_MARKER.format(run_id=run.id), "",
           f"## {speedup:.3f}x faster, {len(shipped_chain)} verified change{'s' if len(shipped_chain) != 1 else ''}", "",
           f"Hotpath measured this repository at `{run.base_commit[:8]}`"
           + (f" (`{run.base_branch}`)" if run.base_branch else "")
           + " and kept a change only if it passed the locked correctness check **and** beat its parent by more "
             "than the measured noise. Each commit below is the exact tree that was tested and benchmarked.", ""]
    if b:
        after_median = pruned.pruned_median if use_pruned and pruned.pruned_median is not None else (h.median if h else None)
        higher = " (higher is better)" if b.higher_is_better else ""
        out += [f"| | Median {metric}{higher} | Trials |", "|---|---:|---:|",
                f"| Baseline `{run.base_commit[:8]}` | {_num(b.median)} | {b.n} |",
                f"| This PR `{head_sha[:8]}` | {_num(after_median)} | {h.n if h and not use_pruned else '–'} |", "",
                f"Run-to-run noise (CV) {run.baseline_noise_cv * 100:.2f}%. "
                f"Acceptance needs at least {rc.benchmark.min_speedup:.2f}x and more than "
                f"{rc.benchmark.noise_multiplier:g}x the noise, at {rc.benchmark.confidence * 100:.0f}% bootstrap confidence.", ""]
    if use_pruned:
        out += [f"> This PR ships the **pruned** stack as a single commit: ablation found {len(pruned.dropped)} "
                "change(s) that did not pull their weight, and the smaller stack re-passed the locked tests without "
                "being measurably slower.", ""]

    out += ["### Changes", ""]
    for i, e in enumerate(chain, 1):
        c = e.comparison
        files = ", ".join(f"`{f}`" for f in e.files_changed or e.hypothesis.files)
        sp = f"{c.speedup_vs_parent:.3f}x vs parent (95% CI {c.ci_low:.3f}–{c.ci_high:.3f})" if c else "–"
        dropped = " — _dropped by pruning_" if e.id not in shipped else ""
        out.append(f"{i}. **{_md_cell(e.hypothesis.idea)}** — {sp} · {files}{dropped}")
        if e.hypothesis.rationale.strip():
            out.append(f"   <br><sub>{_md_cell(e.hypothesis.rationale)[:400]}</sub>")
    out.append("")

    out += ["### How this was verified", "",
            f"- **Correctness:** `{test_cmd}` passed on every shipped state. Hotpath cannot edit these paths: "
            + (", ".join(f"`{p}`" for p in rc.locked) or "_none configured_") + ".",
            f"- **Speed:** `{bench_cmd}`, compared against the parent with a bootstrap confidence interval.",
            f"- **Scope:** only files matching " + ", ".join(f"`{p}`" for p in rc.editable) + " were changed.",
            f"- **Attempts:** {len(exps)} candidates, {len(rejected)} rejected, "
            f"{sum(1 for e in exps if e.status == ExperimentStatus.accepted)} accepted.", ""]

    if rejected:
        out += [f"<details><summary>Rejected attempts ({len(rejected)})</summary>", "",
                "| Hypothesis | Verdict | Why |", "|---|---|---|"]
        for e in rejected[:60]:
            retry = " _(retry with the failure fed back)_" if e.retry_of else ""
            out.append(f"| {_md_cell(e.hypothesis.idea)[:90]}{retry} | `{e.status.value}` | {_md_cell(e.reject_reason or '')[:160]} |")
        if len(rejected) > 60:
            out.append(f"| _…and {len(rejected) - 60} more_ | | |")
        out += ["", "</details>", ""]
    if accepted_elsewhere:
        out += [f"<details><summary>Correct and faster, but not shipped ({len(accepted_elsewhere)})</summary>", "",
                "These passed both gates on another branch of the search but are not in the final stack.", ""]
        for e in accepted_elsewhere[:30]:
            sp = f"{e.comparison.speedup_vs_parent:.3f}x vs parent" if e.comparison else ""
            out.append(f"- {_md_cell(e.hypothesis.idea)} {sp}")
        out += ["", "</details>", ""]

    diff = diff_profiles(run.baseline_profile, run.head_profile, run.best_speedup)
    if diff.comparable:
        out += ["<details><summary>Where the time went (profile before → after)</summary>", "", "```",
                render_bottlenecks(diff).rstrip(), "```", "",
                "A profile is one observation; the benchmark above decided every verdict.", "</details>", ""]
    if ablation_md:
        out += ["<details><summary>Ablation (leave-one-out re-measurement)</summary>", "", "```",
                ablation_md.rstrip(), "```", "</details>", ""]

    out += ["### Reproduce", "", "```sh", f"git fetch {remote} {branch}", f"git checkout {branch}",
            test_cmd, bench_cmd, "```", ""]
    env = run.execution_environment or {}
    planner = f"{rc.provider.planner}:{rc.provider.planner_model}" if rc.provider.planner != "mock" else "mock"
    worker = f"{rc.provider.worker}:{rc.provider.worker_model}" if rc.provider.worker != "mock" else "mock"
    out.append(f"<sub>Generated by Hotpath · run `{run.id}` · planner `{planner}` · worker `{worker}` · "
               f"execution `{env.get('backend', rc.execution.backend)}`</sub>")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- #
# Building the branch
# --------------------------------------------------------------------------- #

@dataclass
class BranchPlan:
    branch: str
    head_sha: str
    title: str
    body: str
    commits: list[tuple[str, str]] = field(default_factory=list)   # (sha, subject)
    shipped: list[str] = field(default_factory=list)               # experiment ids
    speedup: float = 1.0
    pruned: bool = False


def branch_name(run: RunState) -> str:
    return f"{BRANCH_PREFIX}{run.id}"


def _check_publishable(run: RunState, chain: list[Experiment], ws: Workspace) -> None:
    if run.status not in ("finished", "stopped"):
        raise PRError(f"run {run.id} is '{run.status}'; only a finished or stopped run can become a PR")
    if not run.head_commit or run.head_commit == run.base_commit or not chain:
        raise PRError(f"run {run.id} accepted no changes, so there is nothing to open a PR for")
    expected_parent = run.base_commit
    for e in chain:
        if not e.commit or not ws.has_commit(e.commit):
            raise PRError(f"the verified commit for {e.id} is missing from {ws.target}; was the repository re-cloned?")
        if e.status != ExperimentStatus.accepted or e.comparison is None or not e.comparison.significant:
            raise PRError(f"{e.id} is in the head lineage but is not a verified accepted change ({e.status.value})")
        actual_parent = _git(["rev-parse", f"{e.commit}^"], ws.target)
        if actual_parent != expected_parent:
            raise PRError(f"{e.id} was not built on its recorded parent ({actual_parent[:8]} != {expected_parent[:8]})")
        expected_parent = e.commit
    if chain[-1].commit != run.head_commit:
        raise PRError("the run's head commit does not match the end of its accepted chain")


def _check_scope(rc: HotpathConfig, repo: Path, base: str, head: str) -> list[str]:
    changed = [p for p in _git(["diff", "--no-ext-diff", "--name-only", base, head], repo).splitlines() if p]
    bad = [f"{p}: {why}" for p in changed for ok, why in [path_allowed(p, rc.editable, rc.locked)] if not ok]
    if bad:
        raise PRError("refusing to publish: the shipped diff touches paths Hotpath may not change:\n  " + "\n  ".join(bad))
    return changed


def build_branch(cfg: HotpathConfig, store: Store, run: RunState, ws: Workspace,
                 pruned: Optional[PruneResult] = None, ablation_md: Optional[str] = None,
                 remote: str = "origin") -> BranchPlan:
    """Create or refresh the local branch `hotpath/<run id>` holding the verified changes."""
    exps = store.list_experiments(run.id)
    chain = _accepted_chain(run, exps)
    _check_publishable(run, chain, ws)
    rc = _run_config(cfg, run)
    repo = ws.target
    branch = branch_name(run)
    if ws.current_branch() == branch:
        raise PRError(f"{branch} is checked out in {repo}; switch to another branch before publishing it again")
    use_pruned = pruned is not None and pruned.status == "pruned" and pruned.commit
    ship_commit = pruned.commit if use_pruned else run.head_commit
    _check_scope(rc, repo, run.base_commit, ship_commit)

    name, email = _identity(repo)
    metric = _metric(run)
    test_cmd, bench_cmd = scrub_command(rc.test_cmd), scrub_command(rc.bench_cmd)
    parent, commits = run.base_commit, []

    def commit(tree_of: str, message: str, when: datetime) -> str:
        tree = _git(["rev-parse", f"{tree_of}^{{tree}}"], repo)
        date = _git_date(when)
        env = {"GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email, "GIT_AUTHOR_DATE": date,
               "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email, "GIT_COMMITTER_DATE": date}
        # The message goes through stdin so no length limit or shell quoting applies.
        full_env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", **env}
        res = subprocess.run(["git", "commit-tree", tree, "-p", parent], cwd=str(repo), input=message,
                             capture_output=True, text=True, env=full_env, encoding="utf-8")
        if res.returncode != 0:
            raise PRError(f"git commit-tree failed: {res.stderr.strip()}")
        return res.stdout.strip()

    if use_pruned:
        kept = [e for e in chain if e.id not in set(pruned.dropped)]
        shipped = {e.id for e in kept}
        lines = [_subject(f"perf: {len(kept)} verified optimization(s), pruned stack"), "",
                 f"{pruned.speedup_vs_baseline or 0:.3f}x vs baseline. Ablation removed {len(pruned.dropped)} change(s) "
                 f"that did not pull their weight; `{test_cmd}` passed on this exact tree.", ""]
        lines += [f"- {e.hypothesis.idea}" for e in kept]
        lines += ["", f"Hotpath-Run: {run.id}", *(f"Hotpath-Experiment: {e.id}" for e in kept)]
        sha = commit(pruned.commit, "\n".join(lines) + "\n", max((e.updated_at for e in kept), default=now()))
        commits.append((sha, lines[0]))
        parent = sha
        speedup = pruned.speedup_vs_baseline or run.best_speedup
    else:
        shipped = {e.id for e in chain}
        parent_median = run.baseline_benchmark.median if run.baseline_benchmark else None
        for e in chain:
            msg = commit_message(e, parent_median, metric, test_cmd, bench_cmd, run.id)
            sha = commit(e.commit, msg, e.updated_at)
            commits.append((sha, msg.splitlines()[0]))
            parent = sha
            parent_median = e.benchmark.median if e.benchmark else None
        speedup = run.best_speedup
    # The rebuilt head must carry exactly the verified tree.
    if _git(["rev-parse", f"{parent}^{{tree}}"], repo) != _git(["rev-parse", f"{ship_commit}^{{tree}}"], repo):
        raise PRError("internal error: the rebuilt branch does not match the verified tree")
    _git(["update-ref", f"refs/heads/{branch}", parent], repo)

    body = pr_body(cfg, run, exps, chain, shipped, speedup, parent, branch, remote,
                   pruned if use_pruned else None, ablation_md)
    return BranchPlan(branch=branch, head_sha=parent, title=pr_title(run, len(shipped), speedup), body=body,
                      commits=commits, shipped=[e.id for e in chain if e.id in shipped], speedup=speedup,
                      pruned=bool(use_pruned))


# --------------------------------------------------------------------------- #
# Publishing
# --------------------------------------------------------------------------- #

def publish(cfg: HotpathConfig, store: Store, run: RunState, ws: Workspace, *, base: Optional[str] = None,
            remote: str = "origin", draft: bool = False, push: bool = True, allow_moved_base: bool = False,
            method: str = "auto", pruned: Optional[PruneResult] = None, ablation_md: Optional[str] = None,
            say: Callable[[str], None] = lambda _msg: None) -> PullRequestRecord:
    """Build the branch, push it, and open or refresh the PR. Records the outcome on the run."""
    plan = build_branch(cfg, store, run, ws, pruned, ablation_md, remote)
    repo = ws.target
    say(f"built {plan.branch}: {len(plan.commits)} commit(s), {plan.speedup:.3f}x")
    for sha, subject in plan.commits:
        say(f"  {sha[:8]} {subject}")
    body_file = ws.workdir / "prs" / f"{run.id}.md"
    body_file.parent.mkdir(parents=True, exist_ok=True)
    body_file.write_text(plan.body, encoding="utf-8")

    previous = run.pull_request
    if previous and (previous.branch != plan.branch or previous.remote != remote):
        previous = None
    record = PullRequestRecord(branch=plan.branch, base=base or run.base_branch or "", head_sha=plan.head_sha,
                               remote=remote, method="local", pruned=plan.pruned,
                               created_at=previous.created_at if previous else now())
    if not push:
        record.base = record.base or (previous.base if previous else "")
        return _record(store, run, record, previous)

    if remote.startswith("-"):
        raise PRError(f"'{remote}' is not a valid remote name")
    # The configured URL, not `remote get-url`: that expands insteadOf rewrites, and the PR belongs to
    # the repository the user named, wherever git is told to fetch it from.
    remote_url = _git_ok(["config", "--get", f"remote.{remote}.url"], repo)
    if not remote_url:
        raise PRError(f"no git remote named '{remote}' in {repo}. Add one (git remote add {remote} <url>) "
                      "or keep the branch local with --no-push.")
    record.base = record.base or remote_default_branch(repo, remote) or ""
    if not record.base:
        raise PRError("could not tell which branch the PR should target; pass --base <branch>")
    if record.base.startswith("-") or _git_ok(["check-ref-format", "--branch", record.base], repo) is None:
        raise PRError(f"'{record.base}' is not a valid branch name")
    tip = remote_branch_tip(repo, remote, record.base)
    if tip is None:
        raise PRError(f"{remote}/{record.base} does not exist. Push the branch the run measured first "
                      f"(git push {remote} {record.base}), then publish again.")
    if tip != run.base_commit and not allow_moved_base:
        raise PRError(
            f"{remote}/{record.base} is at {tip[:8]}, but this run measured {run.base_commit[:8]}. "
            "The speedup and tests describe the old base, not the merge. Push the measured commit, re-run "
            "Hotpath on the current branch, or pass --allow-moved-base (CI will re-run the locked tests).")

    say(f"pushing {plan.branch} to {remote}")
    _git(["push", remote, f"+{plan.head_sha}:refs/heads/{plan.branch}"], repo, timeout=300)

    gh_repo = parse_remote_url(remote_url)
    if gh_repo is None:
        say(f"'{remote}' is not a GitHub URL; the branch is pushed, open the PR in your forge")
        return _record(store, run, record, previous)
    try:
        result = publish_pull_request(gh_repo, record.base, plan.branch, plan.title, plan.body, draft, method)
    except GitHubError as e:
        _record(store, run, record, previous)
        raise PRError(f"the branch is pushed, but opening the PR failed: {e}") from None
    record.method = result.method
    record.compare_url = result.compare_url
    if result.pr:
        record.url, record.number = result.pr.url, result.pr.number
        say(("opened " if result.pr.created else "updated ") + result.pr.url)
    else:
        say("no GitHub credentials (gh or GITHUB_TOKEN), so the PR form is pre-filled instead")
        if "body=" not in result.compare_url:
            say(f"the description is too long for a link; paste it from {body_file}")
    return _record(store, run, record, previous)


def _record(store: Store, run: RunState, record: PullRequestRecord,
            previous: Optional[PullRequestRecord]) -> PullRequestRecord:
    if (previous and not record.url and previous.url and previous.base == record.base
            and previous.remote == record.remote):
        # A later link-only or local publish does not forget the PR that already exists.
        record.url, record.number = previous.url, previous.number
    record.updated_at = now()
    run.pull_request = record
    store.save_run(run)
    return record
