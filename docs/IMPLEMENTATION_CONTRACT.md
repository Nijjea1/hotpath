# A/B implementation contract

This contract fixes the meaning of measurements and verdicts while the harness,
agent, and dashboard are developed in parallel. Update it when a shared schema
change lands, before changing the UI's claims.

## Candidate lifecycle

1. A worker patch must touch only configured editable files. Locked paths are
   rejected before any bytes are written.
2. A patch that cannot be applied is `patch_failed`; a locked-path attempt is
   `locked_file`.
3. A test timeout is `timeout`. A completed test with a nonzero exit code is
   `rejected_correctness`. Neither outcome gets a candidate benchmark.
4. A benchmark timeout is `timeout`. A nonzero exit, malformed output, or
   incompatible metric is `error` or `rejected_speed` as determined by the
   existing harness and comparison code, with the exact reason persisted.
5. A correct candidate is `accepted` only when its comparison with its parent
   clears the configured threshold and confidence interval. Beam selection may
   subsequently mark a correct, faster sibling `not_selected`.
6. A profile failure produces a `ProfileSummary` with an explanatory `note`.
   Profiling is descriptive; the locked tests and benchmark decide acceptance.

`accepted` means the candidate beat its parent. `shipped` means the candidate
is in the final exported head's ancestry. Headline numbers use the shipped
tree, not the count of all accepted candidates.

## Measurement contract

- The target's locked benchmark emits repeated positive finite samples and
  declares the metric and whether higher is better.
- The benchmark establishes a fixed workload set before search. GPU workloads
  vary both prompt length and batch size. `benchmark.required_workloads`
  declares every required ID. Output records one sample per shape per trial,
  plus a headline `tokens_per_s` sample calculated from total generated tokens
  divided by total synchronized elapsed time across those shapes in that trial.
- The parser rejects missing, extra, duplicate, malformed, or nonpositive
  workload samples. An aggregate win must also retain at least
  `benchmark.min_workload_retention` of its parent's median throughput on every
  required shape (default 0.98). This gate is fixed before search; it is not
  chosen after seeing a candidate.
- Warmups, fixed seeds, GPU synchronization, repeated trials, a noise-adjusted
  threshold, and a bootstrap interval remain mandatory for a speed verdict.
- A profile is one observation. Hotspot differences explain a result but do not
  replace the benchmark's statistical decision.

## Evidence labels

- The current before/after profile view is a *flat hotspot comparison*. A true
  flame graph requires stored stack or caller relationships.
- The bundled transformer demonstrates the Hotpath loop. A Dryft result needs
  the real target, its official correctness contract, hardware identity, and
  an independent benchmark of the exported source.
- Offline mock patches are recorded inputs; their test and benchmark verdicts
  are measured during each run.

## Shared data changes

`schema.py` and `orchestrator.py` are integration-owned. New stored fields must
have defaults or a migration so existing SQLite runs still load. Resuming a run
must refuse a changed measurement contract. Never store live credentials in
run snapshots, reports, logs, or test fixtures.
