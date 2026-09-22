"""hotpath go | assess | init | run [--pr] | pr | serve | ablate | export. The config defaults to the nearest .hotpath.yaml."""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from hotpath import observability as obs
from hotpath.config import ConfigNotFound, find_config, load_config, load_env_files


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")


def _config(arg: str | None):
    """An explicit config path, or the nearest `.hotpath.yaml` above the current directory."""
    return load_config(arg if arg else find_config())


def _publish(cfg, store, run, ws, args, pruned=None, ablation_md=None) -> int:
    from hotpath.pr import PRError, publish
    push = not getattr(args, "no_push", False)
    try:
        rec = publish(cfg, store, run, ws, base=args.base, remote=args.remote, draft=args.draft, push=push,
                      allow_moved_base=args.allow_moved_base, method=args.pr_method, pruned=pruned,
                      ablation_md=ablation_md, say=lambda m: print("  " + m))
    except PRError as e:
        print(f"pull request not published: {e}")
        return 1
    if rec.url:
        print(f"PR: {rec.url}")
    elif rec.method == "link":
        print(f"branch {rec.branch} pushed; open the PR: {rec.compare_url}")
    else:
        print(f"branch {rec.branch} is ready" + (" and pushed" if push else " (local only)"))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from hotpath.orchestrator import Orchestrator, ResumeError

    cfg = _config(args.config)
    if args.iterations is not None:
        cfg.search.iterations = args.iterations
    if args.beam is not None:
        cfg.search.beam_width = args.beam
    if args.provider:
        cfg.provider.planner = cfg.provider.worker = args.provider
    from hotpath.schema import HotpathConfig
    cfg = HotpathConfig.model_validate(cfg.model_dump())
    obs.init_sentry()
    try:
        orch = Orchestrator(cfg, resume=args.resume, autocommit=args.autocommit)
        verb = "resume" if args.resume else "run"
        print(f"hotpath {verb} {orch.run.id} on {cfg.target} (planner={orch.planner.name}, worker={orch.worker.name})")
        run = asyncio.run(orch.execute())
    except ResumeError as e:
        print(f"cannot resume: {e}")
        return 1
    for line in run.logs:
        print("  " + line)
    print(f"status={run.status} best_speedup={run.best_speedup:.3f}x head={run.head_commit[:8]}")
    if args.export and run.head_commit != run.base_commit:
        dest = Path(args.export)
        orch.export_best(dest)
        print(f"exported optimized tree to {dest}")
    if run.status != "finished":
        return 1
    if args.pr:
        if run.head_commit == run.base_commit:
            print("no change was both correct and measurably faster, so no pull request was opened")
            return 0
        return _publish(cfg, orch.store, run, orch.ws, args)
    return 0


def cmd_pr(args: argparse.Namespace) -> int:
    from hotpath.store import Store
    from hotpath.workspace import Workspace

    cfg = _config(args.config)
    obs.init_sentry()
    ws = Workspace(Path(cfg.target).resolve(), Path(cfg.workdir))
    store = Store(cfg.db_path or (ws.workdir / "hotpath.db"))
    run = store.get_run(args.run_id) if args.run_id else store.latest_run()
    if run is None:
        print("no run found; start one with `hotpath run`")
        return 1
    pruned, ablation_md = None, None
    if args.prune:
        from hotpath.ablation import render
        report = asyncio.run(_ablate(cfg, store, run, True))
        ablation_md, pruned = render(report), report.prune
        print(f"prune: {pruned.status}. {pruned.reason}")
    print(f"publishing {run.id} ({run.best_speedup:.3f}x vs baseline)")
    return _publish(cfg, store, run, ws, args, pruned, ablation_md)


def cmd_init(args: argparse.Namespace) -> int:
    from hotpath.init import InitError, gather_answers, init_repo

    repo = Path(args.path).resolve()

    def ask(prompt: str, default: str) -> str:
        return input(prompt + (f" [{default}]" if default else "") + ": ")

    given = {"test_cmd": args.test_cmd, "bench_cmd": args.bench_cmd, "profile_cmd": args.profile_cmd,
             "editable": [x.strip() for x in args.editable.split(",") if x.strip()] if args.editable else None,
             "locked": args.lock, "execution": args.execution, "planner": args.planner, "worker": args.worker,
             "mock_patches_dir": args.mock_patches, "name": args.name}
    interactive = not args.yes and sys.stdin.isatty()
    try:
        answers, notes = gather_answers(repo, ask=ask if interactive else None, **given)
        result = init_repo(repo, answers, force=args.force, workflow=not args.no_workflow)
    except InitError as e:
        print(f"hotpath init: {e}")
        return 1
    for p in result.written:
        print(f"  wrote  {p.relative_to(repo).as_posix()}")
    for p in result.skipped:
        print(f"  kept   {p.relative_to(repo).as_posix()} (exists; --force replaces it)")
    print(f"\ncorrectness  {answers.test_cmd}\nbenchmark    {answers.bench_cmd}\n"
          f"editable     {', '.join(answers.editable)}\nlocked       {', '.join(answers.locked)}")
    for n in notes + result.notes:
        print(f"note: {n}")
    print('\nnext:\n  git add .hotpath.yaml .gitignore .github && git commit -m "Set up Hotpath" && git push\n'
          "  hotpath run --pr")
    return 0


def cmd_go(args: argparse.Namespace) -> int:
    from hotpath.go import Go, GoOptions

    opts = GoOptions(target=args.target, yes=args.yes, provider=args.provider, mock_patches=args.mock_patches,
                     iterations=args.iterations, candidates=args.candidates, beam=args.beam,
                     max_tokens=args.max_tokens, max_minutes=args.max_minutes, sandbox=args.sandbox,
                     test_cmd=args.test_cmd, bench_cmd=args.bench_cmd, no_generate=args.no_generate,
                     test_runs=args.test_runs, test_timeout=args.test_timeout, no_pr=args.no_pr,
                     pr_method=args.pr_method, ready=args.ready, open_browser=not args.no_open,
                     dashboard=args.dashboard, verify_ci=args.verify_ci, ci_attempts=args.ci_attempts,
                     ci_timeout=args.ci_timeout, port=args.port, workspaces=args.workspaces, remote=args.remote,
                     resume=args.resume)
    return Go(opts).run()


def cmd_assess(args: argparse.Namespace) -> int:
    import json

    from hotpath.assess import assess

    a = assess(Path(args.path))
    print(json.dumps(a.to_dict(), indent=2) if args.json else a.to_markdown())
    return 0 if a.ok else 1


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn
    from server.app import create_app

    if args.host not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("dashboard requires a loopback host; use an authenticated reverse proxy for remote access")

    cfg = load_config(args.config) if args.config else None
    if cfg is None and not args.db:
        cfg = _config(None)
    obs.init_sentry()
    app = create_app(cfg, db_path=args.db)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


async def _ablate(cfg, store, run, with_prune: bool):
    from hotpath.ablation import ablate, prune
    report = await ablate(cfg, store, run)
    if with_prune:
        report.prune = await prune(cfg, store, run, report)
    return report


def cmd_ablate(args: argparse.Namespace) -> int:
    from hotpath.ablation import render
    from hotpath.orchestrator import Orchestrator

    cfg = _config(args.config)
    obs.init_sentry()
    orch = Orchestrator(cfg)  # only for its store path resolution
    run = orch.store.get_run(args.run_id) if args.run_id else orch.store.latest_run()
    if run is None:
        print("no run found"); return 1
    report = asyncio.run(_ablate(cfg, orch.store, run, args.prune))
    print(render(report))
    if args.json:
        Path(args.json).write_text(report.model_dump_json(indent=2))
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    from hotpath.export import build_bundle
    from hotpath.store import Store
    from hotpath.workspace import Workspace

    cfg = _config(args.config)
    obs.init_sentry()
    target = Path(cfg.target).resolve()
    ws = Workspace(target, Path(cfg.workdir))
    store = Store(cfg.db_path or (ws.workdir / "hotpath.db"))
    run = store.get_run(args.run_id) if args.run_id else store.latest_run()
    if run is None:
        print("no run found"); return 1
    if not run.head_commit or run.head_commit == run.base_commit:
        print(f"run {run.id} has no accepted changes to export"); return 1
    ablation_md, pruned = None, None
    if args.ablate or args.prune:
        from hotpath.ablation import render
        report = asyncio.run(_ablate(cfg, store, run, args.prune))
        ablation_md, pruned = render(report), report.prune
    dest = Path(args.dest)
    build_bundle(cfg, store, run, dest, ws, ablation_md, pruned)
    if pruned and pruned.status == "pruned":
        print(f"exported the PRUNED stack for {run.id} ({pruned.speedup_vs_baseline:.3f}x vs baseline; "
              f"dropped {len(pruned.dropped)} change(s)) to {dest}")
    else:
        if pruned:
            print(f"prune: {pruned.status}. {pruned.reason}")
        print(f"exported PR bundle for {run.id} ({run.best_speedup:.3f}x vs baseline) to {dest}")
    print(f"  {dest / 'REPORT.md'}\n  {dest / 'changes.patch'}\n  {dest / 'optimized_src'}/")
    print(f"To open a pull request with one verified commit per change: hotpath pr --run-id {run.id}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="hotpath", description="AI proposes optimizations; Hotpath proves whether they work.")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("init", help="set a repository up for Hotpath (.hotpath.yaml + CI check)")
    i.add_argument("path", nargs="?", default=".")
    i.add_argument("--test-cmd", help="command that checks correctness (exit 0 = correct)")
    i.add_argument("--bench-cmd", help="command that prints Hotpath benchmark JSON")
    i.add_argument("--profile-cmd", help="command that prints a Hotpath profile (optional)")
    i.add_argument("--editable", help="comma-separated globs Hotpath may edit (default: *.py, or src/*.py)")
    i.add_argument("--lock", action="append", default=[], help="an extra glob Hotpath must never edit (repeatable)")
    i.add_argument("--execution", choices=["local", "docker"], help="where candidate code runs")
    i.add_argument("--planner", choices=["openai", "mock"])
    i.add_argument("--worker", choices=["openai", "baseten", "mock"])
    i.add_argument("--mock-patches", help="directory of recorded patches for offline `--provider mock` runs")
    i.add_argument("--name", help="name shown in reports (default: the directory name)")
    i.add_argument("-y", "--yes", action="store_true", help="accept detected defaults without prompting")
    i.add_argument("--force", action="store_true", help="overwrite an existing .hotpath.yaml and workflow")
    i.add_argument("--no-workflow", action="store_true", help="do not write the GitHub Actions check")
    i.set_defaults(fn=cmd_init)

    def pr_flags(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--base", help="branch the PR targets (default: the branch the run measured)")
        sp.add_argument("--remote", default="origin")
        sp.add_argument("--draft", action="store_true", help="open the PR as a draft")
        sp.add_argument("--pr-method", choices=["auto", "gh", "token", "link"], default="auto",
                        help="how to open the PR: gh CLI, GITHUB_TOKEN, or a pre-filled link (auto tries them in order)")
        sp.add_argument("--allow-moved-base", action="store_true",
                        help="publish even though the base branch moved since the run measured it")

    g = sub.add_parser("go", help="one command: assess a repo, build and test it, find a benchmark, optimize, "
                                  "and open a draft PR")
    g.add_argument("target", nargs="?", default=".",
                   help="GitHub URL, owner/repo, any git URL, or a local checkout (default: the current directory)")
    g.add_argument("-y", "--yes", action="store_true",
                   help="accept every default: local execution consent, the benchmark, and pushing the PR branch")
    g.add_argument("--provider", choices=["openai", "mock"], help="model provider (default: openai; mock is offline)")
    g.add_argument("--mock-patches", help="recorded patches for --provider mock")
    g.add_argument("--iterations", type=int, default=3)
    g.add_argument("--candidates", type=int, default=3, help="candidates per iteration")
    g.add_argument("--beam", type=int, default=1)
    g.add_argument("--max-tokens", type=int, help="stop the search once model calls have used this many tokens")
    g.add_argument("--max-minutes", type=float, help="stop the search after this many minutes")
    g.add_argument("--sandbox", choices=["auto", "local", "docker"], default="auto",
                   help="where the target's code runs (auto: Docker if it is running, else local with consent)")
    g.add_argument("--test-cmd", help="override the detected correctness check")
    g.add_argument("--bench-cmd", help="use this benchmark (prints Hotpath JSON) instead of finding or generating one")
    g.add_argument("--no-generate", action="store_true", help="never ask a model to write a benchmark")
    g.add_argument("--test-runs", type=int, default=3, help="baseline test runs used to detect flaky tests")
    g.add_argument("--test-timeout", type=float, default=900.0, help="seconds per test-suite run")
    g.add_argument("--no-pr", action="store_true", help="build the PR branch locally but do not push")
    g.add_argument("--pr-method", choices=["auto", "gh", "token", "link"], default="auto")
    g.add_argument("--ready", action="store_true", help="open the PR ready for review instead of as a draft")
    g.add_argument("--no-open", action="store_true", help="do not open the PR or dashboard in a browser")
    g.add_argument("--dashboard", action=argparse.BooleanOptionalAction, default=True,
                   help="serve the live dashboard during the search and keep it up afterwards (default: on)")
    g.add_argument("--verify-ci", action=argparse.BooleanOptionalAction, default=True,
                   help="after opening the PR, wait for the repository's CI and fix what the PR "
                        "broke (default: on; failures already present on the base are never touched)")
    g.add_argument("--ci-attempts", type=int, default=2, help="how many times to try repairing CI")
    g.add_argument("--ci-timeout", type=float, default=900.0,
                   help="seconds to wait for CI checks to settle")
    g.add_argument("--port", type=int, default=8765)
    g.add_argument("--workspaces", help="where clones and per-repo environments live (default: ./workspaces)")
    g.add_argument("--remote", default="origin")
    g.add_argument("--resume", action="store_true", help="continue the last `go` run on this repository")
    g.set_defaults(fn=cmd_go)
    s0 = sub.add_parser("assess", help="read-only report: ecosystem, tests, benchmark, editable and locked files")
    s0.add_argument("path", nargs="?", default=".")
    s0.add_argument("--json", action="store_true")
    s0.set_defaults(fn=cmd_assess)

    r = sub.add_parser("run", help="run the optimization loop once")
    r.add_argument("config", nargs="?", help="config file (default: the nearest .hotpath.yaml)")
    r.add_argument("--iterations", type=int)
    r.add_argument("--beam", type=int, help="beam width: how many accepted heads to keep and expand each iteration (1 = greedy)")
    r.add_argument("--provider", choices=["mock", "openai"], help="override both planner and worker providers")
    r.add_argument("--export", help="directory to write the best accepted source tree into")
    r.add_argument("--autocommit", action="store_true",
                   help="snapshot uncommitted changes in the target into a commit before measuring (default: refuse)")
    r.add_argument("--resume", metavar="RUN_ID",
                   help="continue a stopped or failed run from its stored beam instead of re-measuring a baseline; "
                        "runs up to the larger of its original budget and --iterations")
    r.add_argument("--pr", action="store_true", help="when the run finishes with a win, push a branch and open a pull request")
    pr_flags(r)
    r.set_defaults(fn=cmd_run)
    q = sub.add_parser("pr", help="publish a finished run as a pull request (one verified commit per change)")
    q.add_argument("config", nargs="?", help="config file (default: the nearest .hotpath.yaml)")
    q.add_argument("--run-id", help="which run to publish (defaults to the latest)")
    q.add_argument("--prune", action="store_true", help="ablate first and ship the verified pruned stack if there is one")
    q.add_argument("--no-push", action="store_true", help="only build the local branch hotpath/<run id>")
    pr_flags(q)
    q.set_defaults(fn=cmd_pr)
    s = sub.add_parser("serve", help="serve the dashboard (and allow starting runs from it)")
    s.add_argument("config", nargs="?")
    s.add_argument("--db", help="database path (defaults to the config's)")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.set_defaults(fn=cmd_serve)
    a = sub.add_parser("ablate", help="re-measure the accepted chain with each change removed")
    a.add_argument("config", nargs="?")
    a.add_argument("--run-id")
    a.add_argument("--json", help="write the report here")
    a.add_argument("--prune", action="store_true",
                   help="also try dropping every change that did not pull its weight, together; keep the pruned "
                        "stack only if it passes correctness and the full stack is not measurably faster")
    a.set_defaults(fn=cmd_ablate)
    x = sub.add_parser("export", help="write a PR-ready bundle (optimized tree, diff, benchmark table, ablation)")
    x.add_argument("config", help="config file (use .hotpath.yaml for the current repository)")
    x.add_argument("dest", help="directory to write the bundle into")
    x.add_argument("--run-id", help="which run to export (defaults to the latest)")
    x.add_argument("--ablate", action="store_true", help="also run leave-one-out ablation and include the table (re-runs benchmarks)")
    x.add_argument("--prune", action="store_true",
                   help="ablate, then export the pruned stack instead of the head if pruning is verified (implies --ablate)")
    x.set_defaults(fn=cmd_export)
    args = p.parse_args(argv)
    _setup_logging(args.verbose)
    load_env_files()
    try:
        return args.fn(args)
    except ConfigNotFound as e:
        print(f"hotpath: {e}")
        return 1
    finally:
        obs.flush()


if __name__ == "__main__":
    sys.exit(main())
