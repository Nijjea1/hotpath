## What and why

<!-- One topic per PR. What changes, and what problem it solves. -->

## Evidence

<!-- Tests added or changed. For anything touching measurement or verdicts, the crafted case that
     shows the decision. For a performance claim, the run and where its evidence is recorded. -->

## Checklist

- [ ] `python -m pytest -q` passes, run from the interpreter Hotpath is installed into
- [ ] `ruff check .` passes
- [ ] A new failure mode is a structured status, not an exception
- [ ] No model-authored value can influence a measurement or a locked path
- [ ] Docs match `hotpath --help` (README, `docs/`, `site/src/data/commands.ts`) if a command changed
- [ ] `CHANGELOG.md` has a line under *Unreleased*
