# slow_web_analytics

An offline, general-repository Hotpath demo target. It summarizes synthetic web-request
events by path. The implementation is deliberately slow: it rescans the full event list
for every path. The recorded winning patch changes that to one dictionary pass while
preserving sorted output and exact counts.

`analytics.py` is the only editable source. `tests/check.py`, `bench.py`, and
`hotprofile.py` are locked. The mock patch set contains a correct optimization, an
incorrect path-normalization attempt, and an attempt to edit the correctness gate.

Run it from the Hotpath repository root:

```powershell
python -m hotpath.cli run configs/slow_web_analytics.yaml
python scripts/reliability_slow_web_analytics.py
```

The reliability script starts each of ten runs with a fresh `.hotpath` workspace,
checks that the shipped hypothesis sequence and exact verdict counts are stable, verifies
that worktrees are empty after every run, and writes its JSON evidence under
`reports/slow_web_analytics_reliability.json` (generated locally and ignored by Git).
