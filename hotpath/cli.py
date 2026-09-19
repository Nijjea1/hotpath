"""hotpath run <config> | hotpath serve <config> | hotpath export <config> <dest>"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from hotpath import observability as obs
from hotpath.config import load_config


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")


def cmd_run(args: argparse.Namespace) -> int:
    from hotpath.orchestrator import Orchestrator, ResumeError

    cfg = load_config(args.config)
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
    return 0 if run.status == "finished" else 1


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn
    from server.app import create_app

    if args.host not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("dashboard requires a loopback host; use an authenticated reverse proxy for remote access")

    cfg = load_config(args.config) if args.config else None
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

    cfg = load_config(args.config)
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

    cfg = load_config(args.config)
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
    print("To open a PR: review changes.patch, then in your repo:")
    print(f"  git checkout -b hotpath/optimize && git apply \"{dest / 'changes.patch'}\" && git commit -am 'hotpath: optimizations' && gh pr create")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="hotpath", description="AI proposes optimizations; Hotpath proves whether they work.")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run the optimization loop once")
    r.add_argument("config")
    r.add_argument("--iterations", type=int)
    r.add_argument("--beam", type=int, help="beam width: how many accepted heads to keep and expand each iteration (1 = greedy)")
    r.add_argument("--provider", choices=["mock", "openai"], help="override both planner and worker providers")
    r.add_argument("--export", help="directory to write the best accepted source tree into")
    r.add_argument("--autocommit", action="store_true",
                   help="snapshot uncommitted changes in the target into a commit before measuring (default: refuse)")
    r.add_argument("--resume", metavar="RUN_ID",
                   help="continue a stopped or failed run from its stored beam instead of re-measuring a baseline; "
                        "runs up to the larger of its original budget and --iterations")
    r.set_defaults(fn=cmd_run)
    s = sub.add_parser("serve", help="serve the dashboard (and allow starting runs from it)")
    s.add_argument("config", nargs="?")
    s.add_argument("--db", help="database path (defaults to the config's)")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.set_defaults(fn=cmd_serve)
    a = sub.add_parser("ablate", help="re-measure the accepted chain with each change removed")
    a.add_argument("config")
    a.add_argument("--run-id")
    a.add_argument("--json", help="write the report here")
    a.add_argument("--prune", action="store_true",
                   help="also try dropping every change that did not pull its weight, together; keep the pruned "
                        "stack only if it passes correctness and the full stack is not measurably faster")
    a.set_defaults(fn=cmd_ablate)
    x = sub.add_parser("export", help="write a PR-ready bundle (optimized tree, diff, benchmark table, ablation)")
    x.add_argument("config")
    x.add_argument("dest", help="directory to write the bundle into")
    x.add_argument("--run-id", help="which run to export (defaults to the latest)")
    x.add_argument("--ablate", action="store_true", help="also run leave-one-out ablation and include the table (re-runs benchmarks)")
    x.add_argument("--prune", action="store_true",
                   help="ablate, then export the pruned stack instead of the head if pruning is verified (implies --ablate)")
    x.set_defaults(fn=cmd_export)
    args = p.parse_args(argv)
    _setup_logging(args.verbose)
    try:
        return args.fn(args)
    finally:
        obs.flush()


if __name__ == "__main__":
    sys.exit(main())
