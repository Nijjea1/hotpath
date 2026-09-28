# Contributing to Hotpath

Hotpath's one promise is that it never accepts a change because a model says it is faster. Every
contribution is judged against that: a new failure mode must become a *structured status*, a new
number must be *defensible*, and nothing a model writes may influence a measurement.

## Set up

```bash
git clone https://github.com/Nijjea1/hotpath && cd hotpath
python -m venv .venv
source .venv/bin/activate            # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"              # or: make install
hotpath doctor
```

Python 3.11+ and Git are required. Docker and a Chromium-family browser are optional; the tests
that need them skip themselves when they are missing.

## Run the tests — from the right interpreter

```bash
python -m pytest -q        # make test       — the full suite, about ten minutes
python -m pytest -q -m "not slow"   # make test-fast — skips the end-to-end loops
ruff check .               # make lint
```

**Pytest must run from the interpreter Hotpath is installed into, with that interpreter first on
`PATH`.** The bundled targets run `python bench.py` as a subprocess. If `python` resolves to a
different interpreter, about twenty tests fail with `ModuleNotFoundError: No module named 'hotpath'`.
That is a `PATH` problem, not a regression. So:

- Activate the venv (`source .venv/bin/activate` / `.venv\Scripts\Activate.ps1`) and use
  `python -m pytest`, rather than calling `.venv/bin/python` or bare `pytest` from elsewhere.
- Check with `python -c "import hotpath; print(hotpath.__file__)"` — it should point at this checkout.

Tests marked `slow` drive the whole loop, `go`, or real subprocess trees end to end. Mark a new test
`@pytest.mark.slow` if it takes more than a few seconds; CI always runs everything.

Optional suites:

| Suite | How to enable |
| --- | --- |
| Real Docker boundary | `docker build -f docker/Dockerfile -t hotpath-runner:local .` then `HOTPATH_TEST_DOCKER=1 python -m pytest tests/test_isolation.py` |
| Real-browser dashboard | `npm ci --prefix site`, then `HOTPATH_PLAYWRIGHT_MODULE=$PWD/site/node_modules/playwright-core python -m pytest tests/test_dashboard_browser.py`. Either download Playwright's Chromium (`site/node_modules/.bin/playwright-core install chromium`) or point `HOTPATH_BROWSER_EXECUTABLE` at an installed Chrome. |
| Site | `cd site && npm ci && npm run typecheck && npm run build` |

## Where things live

`hotpath/schema.py` is the contract between two halves that can be worked on independently:

- **Harness** — `workspace.py`, `runner.py`, `execution.py`, `benchmark.py`, `profiler.py`,
  `harness.py`, `ablation.py`, `benchlib.py`, `profilelib.py`, and the targets. Success means every
  failure mode becomes a structured status and every number is defensible.
- **Agent and product** — `context.py`, `agent.py`, `providers/`, `store.py`, `server/`,
  `observability.py`, `profilediff.py`, and the `go`/`init`/`pr` flow. Success means the planner gets
  compact, relevant context, and the dashboard shows exactly what happened without claiming more
  than was measured.
- **Change together, deliberately** — `schema.py`, `orchestrator.py`, `configs/`.

The mock provider stands in for models and `demo_repo/` stands in for a target, so neither half
waits on the other. Derivations the dashboard shows belong in `server/views.py` (unit-testable),
not in the inline script of `server/static/index.html`.

## Rules that are not negotiable

- Locked paths are enforced in `workspace.py`, in code, before a byte is written. Don't weaken that.
- The accept/reject decision lives in `benchmark.py`. A change to it needs a test that shows the
  decision on crafted samples.
- Never claim more than was measured — in code, dashboard copy, or docs. `docs/EVIDENCE_LEDGER.md`
  records what has been observed, including what is *not* yet claimed.
- `hotpath --help` is the source of truth for commands. If you add or rename one, update the README,
  `docs/`, and `site/src/data/commands.ts` (a test fails if the site's table drifts from the CLI).

## Pull requests

- One topic per PR, with tests. `python -m pytest -q` and `ruff check .` green locally.
- Commit messages follow the existing style: `feat(area): …`, `fix(area): …`, `docs: …`.
- Add a line to `CHANGELOG.md` under *Unreleased*.
- Security problems go through [`SECURITY.md`](SECURITY.md), not a public issue.
