> **Status: implementation design record.** The B1/B2 work is present in the current
> implementation and covered by the automated suite. This file records design rationale;
> deployment validation remains environment-specific. Docker is the default execution
> backend, while the bundled demo explicitly opts into trusted local execution. See
> `docs/DEMO.md` and `docs/ISOLATION.md` for the current validation boundary.
> Kept as the design record — it explains *why* each honesty rule exists, which the code comments
> reference. The two deferred items (B1.5 per-experiment profiles, true stack flame graphs) are in
> README's open roadmap.

# Implementation plan — B1 bottleneck diff, B2 metric-aware chart + tree fidelity

Scope: the agent/product side — `context.py`, `agent.py`, `providers/**`, `store.py`, `server/**`,
`observability.py`, `configs/**`. Two changes cross into shared/harness territory and are flagged
explicitly; nothing else touches a measurement path.

- **B1** Before/after "bottleneck shrinking" view from `baseline_profile` vs `head_profile`.
- **B2** Metric-aware chart (raw tokens/sec, not only speedup×) + honest rendering of retries
  (`retry_of`) and beam branches in the experiment tree.

---

## 0. Naming correction, up front

The stored profile is **flat**: `profilelib.run` discards `pstats`' `_callers` map, and `torch_run`
passes `with_stack=True` but emits only per-op rows. `ProfileSummary.hotspots` is a list of
functions with self/total time — there are no stacks, so **a true flame graph cannot be drawn from
what is stored today.** README already says as much in the roadmap.

What B1 delivers is a **paired before/after bottleneck diff** — ranked bars, aligned per function,
with signed deltas. That is the "bottleneck shrinking" story and it is the honest name for it. Call
it *Bottleneck diff* in the UI, not *flame graph*.

A real flame graph is reachable later (§6, B1.5) and is cheaper than it looks: `pstats` already
holds the caller edges that `profilelib` throws away. That is a harness-side change and is out of
scope here.

---

## 1. Data audit — five findings that shape the design

Everything below was read out of the current code, not assumed.

**F1 — Both profiles are already stored and already on the wire.**
`RunState.baseline_profile` and `RunState.head_profile` are `Optional[ProfileSummary]`.
`/api/state` returns `run.model_dump(mode="json")` in full, so both profiles reach the browser
today. `/api/runs` excludes them (`exclude={"logs","baseline_profile","head_profile"}`), which is
correct and should stay. **No new data collection is needed for B1.**

**F2 — Truncation at parse time is the one real blocker.**
`harness.run_profile` calls `parse_profile_output(res.stdout, self.cfg.context.max_hotspots, commit)`
and `max_hotspots` defaults to **12**. Rows past 12 are discarded before storage. For a diff this is
actively dangerous: a function ranked #3 at baseline that falls to #20 at head is *absent* from
`head_profile`, and a naive diff renders that as "eliminated, −100%". That is a fabricated claim of
the exact kind this repo exists to prevent.

Fix by decoupling **display depth** from **planner depth** (§3.1). The planner keeps its 12-row
budget; storage retains more.

**F3 — Hotspot identity is not stable across a patch.**
- cProfile rows carry `file` (repo-relative), `line`, `function`. **`line` shifts** whenever the
  patch adds or removes lines above the function, so any key containing `line` breaks.
- `torch.profiler` rows have `file == ""` and `function` = the op key (`aten::cat`) — stable.
- C builtins have `file == ""` and names like `<method 'count' of 'list' objects>` — stable.

Join key: **`(file, function)`**, `file` possibly empty. Collision risk is real — cProfile's `func`
is the bare name, so `Block.forward` and `Model.forward` in one file both key as `("model.py","forward")`.
Handle by summing colliding rows within a profile and setting an `ambiguous` flag the UI surfaces,
rather than silently picking one.

**F4 — `pct` is not comparable between profiles.**
`pct` is normalized to each profile's own total self time. If total time halves, a function whose
absolute cost is unchanged sees its `pct` **double**. Ranking by `pct` delta would report growth
where nothing grew. Absolute seconds must be the primary axis; `pct` is secondary text only.

**F5 — `tool` can differ between the two profiles.**
`profilelib.torch_run` labels itself `torch.profiler` or `torch.profiler (cpu-time fallback)`
depending on whether CUPTI initialized *at that moment*. If that flips mid-run, baseline and head
are measuring different quantities (device time vs CPU time) and a quantitative diff is meaningless.
Must be detected and refused, not smoothed over. Given AUDIT.md notes CUPTI fails on this box, this
is a live case, not hypothetical.

**F6 — Beam membership per iteration is derivable; a retry's fed-back failure is not.**
- `accepted` ⟺ the node was in the beam when chosen; `not_selected` = accepted but dropped. But an
  older head that silently falls out of the beam at a later iteration **keeps** `accepted`, so
  per-iteration membership is not readable from status alone. It *is* derivable from parentage: a
  node was expanded at iteration `it` iff some experiment with `iteration == it` has
  `parent_id == node.id` (the orchestrator creates children with `parent_id=node.experiment_id` for
  every beam node it plans on). Only blind spot: a beam node the planner returned zero hypotheses
  for produces no children and looks unexpanded. Acceptable; note it in the UI copy.
- `retry_of` is stored, but the **failure text fed back to the worker is not** — `_failure_feedback`
  builds it transiently in `orchestrator.py`. Reconstructing it in JS would duplicate that logic and
  drift. Small schema addition recommended (§4).

---

## 2. Deliverables at a glance

| ID | Deliverable | Files | Cross-boundary? |
|---|---|---|---|
| B1.1 | Retain more hotspots for display | `profiler.py`, `schema.py`, `harness.py` | **yes — harness + shared** |
| B1.2 | `diff_profiles()` + `ProfileDiff` model | `hotpath/profilediff.py` (new), `schema.py` | shared (schema only) |
| B1.3 | `GET /api/runs/{id}/profile_diff` | `server/app.py` | no |
| B1.4 | Bottleneck diff UI card | `server/static/index.html` | no |
| B1.5 | *(optional)* persist per-accepted-experiment profiles | `schema.py`, `orchestrator.py` | shared |
| B2.1 | `GET /api/runs/{id}/tree` derived view model | `server/tree.py` (new), `server/app.py` | no |
| B2.2 | Metric-aware chart with mode toggle + noise band | `server/static/index.html` | no |
| B2.3 | Retry edges + badges in tree & table & detail | `server/static/index.html` | no |
| B2.4 | Beam-aware tidy layout | `server/tree.py`, `server/static/index.html` | no |
| B2.5 | Store `previous_failure` on retries | `schema.py`, `agent.py`/`orchestrator.py` | shared |

**The two cross-boundary edits needing the harness owner's sign-off:** B1.1's `retain` parameter on
`parse_profile_output` (a pure widening of what is kept — no measurement semantics change), and the
`ProfileSummary`/`Experiment` field additions in `schema.py`, which is explicitly "shared, change
together" per CLAUDE.md.

---

## 3. B1 — bottleneck diff

### 3.1 Retain more, show less (B1.1)

New config block, rather than overloading `ContextConfig` (which is the *planner's* budget and should
not acquire display concerns):

```python
class ProfileConfig(BaseModel):
    retain: int = Field(40, ge=1, description="Hotspot rows kept in storage for the before/after diff. The planner still sees only context.max_hotspots.")
```

Add `profile: ProfileConfig = ProfileConfig()` to `HotpathConfig`.

`parse_profile_output(stdout, retain, commit)` keeps `retain` rows and additionally records what it
threw away, so the UI can be honest about the cutoff:

```python
class ProfileSummary(BaseModel):
    tool: str = "none"
    total_time: float = 0.0
    hotspots: list[Hotspot] = Field(default_factory=list)
    commit: str = ""
    note: str = ""
    # new — all defaulted, so runs already in SQLite still deserialize
    n_functions_total: int = 0        # rows the profiler emitted before truncation
    retained: int = 0                 # len(hotspots) as stored
    residual_self_time: float = 0.0   # total_time - sum(self_time of retained rows)
    cutoff_self_time: float = 0.0     # smallest retained self_time == upper bound for anything omitted
```

`harness.run_profile` passes `cfg.profile.retain`. `render_profile` (planner-facing) slices to
`cfg.context.max_hotspots` so **prompt size and cost do not move.** Verify that explicitly in a test —
it is the one way this change could regress the agent side.

`cutoff_self_time` is what makes the diff honest: anything missing from a profile is provably
`≤ cutoff_self_time`, so the UI can say "≤ 0.004s (below top-40)" instead of "eliminated".

### 3.2 `diff_profiles()` (B1.2)

New module `hotpath/profilediff.py`. It reads `ProfileSummary` and writes no measurements, so it
belongs to the product side; it lives in `hotpath/` rather than `server/` so `hotpath export` can
reuse it for `REPORT.md` later.

```python
class BottleneckDelta(BaseModel):
    key: str                      # "file::function", file may be empty
    function: str
    file: str
    before_self: float | None     # None = absent from that profile
    after_self: float | None
    before_total: float | None
    after_total: float | None
    before_pct: float | None
    after_pct: float | None
    delta_self: float | None      # after - before, None when either side is bounded-only
    delta_ratio: float | None     # after/before
    classification: Literal["shrank","grew","unchanged","new","below_cutoff_after","below_cutoff_before"]
    bound_note: str = ""          # e.g. "absent from head: ≤ 0.0041s (below top-40)"
    ambiguous: bool = False       # F3 collision: rows were summed
    editable: bool = True         # target code vs C builtin / library

class ProfileDiff(BaseModel):
    comparable: bool
    incomparable_reason: str = ""
    before_tool: str
    after_tool: str
    before_commit: str
    after_commit: str
    before_total: float
    after_total: float
    total_ratio: float | None
    unchanged_epsilon: float          # the display threshold used, echoed for honesty
    rows: list[BottleneckDelta]
    benchmark_speedup: float | None   # run.best_speedup, for the coherence check
    coherence_warning: str = ""
```

Algorithm:

1. **Guard comparability** and return `comparable=False` with a reason for any of:
   `before is None or after is None`; either has a non-empty `note`; `before.tool != after.tool`
   (F5); `before.commit == after.commit` → reason `"head is still the baseline — nothing accepted yet"`.
   The UI renders the reason instead of a chart. Never draw a quantitative diff in these cases.
2. **Index each side** by `(file, function)`, summing collisions and marking `ambiguous`.
3. **Union the keys**, then per key emit a row, using `cutoff_self_time` for the absent side to
   produce a *bounded* claim rather than a zero.
4. **Classify.** `unchanged` when `abs(delta_ratio - 1) <= epsilon`. Default `epsilon = 0.05`,
   **echoed in the response** — because profiles are single-shot, there is no measured profile noise
   floor, so this is a display threshold and must never be presented as statistical significance.
   This is the one place B1 could drift into the overclaiming the benchmark gate is designed to
   prevent; keep the wording in the UI explicit.
5. **Sort** by `before_self` desc (the "what used to hurt most" ordering), so the reader's eye starts
   at the original bottleneck. Secondary sort for `new` rows by `after_self` desc.
6. **Coherence check.** If `total_ratio` and `benchmark_speedup` disagree by more than ~2x
   (e.g. profile says 5x less time, benchmark says 1.05x), set `coherence_warning`. Causes worth
   naming in the message: cProfile's per-call overhead distorting a call-heavy workload, the torch
   CPU-time fallback measuring the wrong thing, or a profile script whose workload differs from the
   benchmark's. This turns a confusing display into a diagnostic.

### 3.3 Endpoint (B1.3)

```
GET /api/runs/{run_id}/profile_diff  ->  ProfileDiff
404 when the run is unknown
```

Reads `store.get_run(run_id)`, calls `diff_profiles(run.baseline_profile, run.head_profile, run.best_speedup)`.
`/api/state` stays untouched for backwards compatibility.

Server-side rather than in JS deliberately: the alignment, bounding and classification rules above
are exactly the kind of subtle logic that needs unit tests, and the dashboard is a single HTML file
with no JS test harness. Keep the JS a renderer.

### 3.4 UI (B1.4)

New card, "Bottleneck diff — before → after", placed under the existing profile card in the right
column (it is a narrative panel, not a control).

- **Header strip:** `total_time` before → after with the ratio, both commits short-sha'd, the tool
  name, and `benchmark_speedup` beside it so the reader can compare the two numbers themselves.
- **Rows:** per key, two stacked horizontal bars on a **shared absolute-seconds scale** (max of the
  two profiles' top row), baseline above in `--dim`, head below in `--hot`. Reuse the existing
  `.prof .flame` self-within-total nesting so the two cards read as one family.
- **Delta column:** signed percentage plus an arrow (`↓78%`, `↑12%`, `new`, `≤0.004s`). Arrow and
  text carry the meaning, so colour is never the only channel — `--ok`/`--bad` on a dark panel is
  not colourblind-safe on its own.
- **Bounded rows** render the bar as a hatched/outline-only stub with the `bound_note` in the
  tooltip. Visually distinct from a measured zero, which is the whole point.
- **`residual_self_time`** shown as a final "everything else" row so the column sums to `total_time`
  and the reader can see how much is outside the top-N.
- **`comparable == false`** → render `incomparable_reason` in `.hint`, no chart.
- **`coherence_warning`** → a `.verdict.wait` banner above the rows.
- Old runs (pre-B1.1, `retained == 0`) → treat `cutoff_self_time` as unknown and degrade every
  absent-side row to `"absent (cutoff unknown on this run)"`. No elimination claims on legacy data.

---

## 4. B2 — metric-aware chart, retries, beam branches

### 4.1 Derived tree endpoint (B2.1)

New `server/tree.py`, exposed as `GET /api/runs/{run_id}/tree`. Same reasoning as B1.3: the
derivations (F6) deserve Python tests, and the current `renderTree` is already at the limit of what
is reasonable to hand-maintain in an inline `<script>`.

```python
class TreeNode(BaseModel):
    id: str                      # "baseline" or exp id
    kind: Literal["baseline","accepted","not_selected","rejected","pending"]
    status: str
    iteration: int
    column: int                  # = iteration; baseline is 0
    row: int                     # assigned by layout, see B2.4
    parent_id: str | None
    retry_of: str | None
    retry_depth: int             # 0 = original attempt, 1 = first retry, ...
    expanded_at: list[int]       # iterations at which this node was a beam head (F6 derivation)
    in_beam_final: bool
    is_head: bool
    title: str
    speedup_vs_parent: float | None
    speedup_vs_baseline: float | None
    raw_median: float | None
    metric: str
    higher_is_better: bool

class TreeEdge(BaseModel):
    from_id: str
    to_id: str
    kind: Literal["lineage","retry"]
    on_head_chain: bool
    spans_iterations: int        # >1 when an older beam head was re-expanded

class RunTree(BaseModel):
    nodes: list[TreeNode]
    edges: list[TreeEdge]
    max_row: int
    beam_width: int
    metric: str
    higher_is_better: bool
    baseline_median: float | None
```

`expanded_at` derivation, per F6:
```python
expanded = defaultdict(set)
for e in exps:
    expanded[e.parent_id or "baseline"].add(e.iteration)
```

### 4.2 Metric-aware chart (B2.2)

The current chart plots `speedup_vs_baseline` only, hardcodes `Math.max` for the running best, and
labels ticks `"N.Nx"`. Three things change.

**Mode toggle** in the card header: `speedup ×` | `raw {metric}`, persisted in `localStorage`
(wrapped in try/catch — a private window throws) and defaulting to **raw when
`higher_is_better`** (tokens/sec climbing is the more legible story for GPU targets) and to speedup
otherwise.

**Raw mode specifics:**
- y in native units, adaptive precision: integers for values ≥ 100 (tokens/sec), 5 decimals for
  sub-second timings. Derive from magnitude, not from the metric string.
- Running-best staircase flips: `higher_is_better ? max : min`. The existing hardcoded `Math.max` is
  correct for speedup and **wrong** for raw seconds — this is the actual bug the feature fixes.
- **Do not invert the axis.** For `seconds`, let the line descend and annotate "lower is better ↓".
  Flipping the axis to make every chart go up would misrepresent direction.
- **Do not force a zero origin.** For a 1.2x tokens/sec gain a zero-based axis flattens the signal
  to nothing. Instead draw the baseline as an explicit labelled reference line at its exact value,
  so the reader can judge the scale honestly. (Zero-origin matters for bars; this is a line chart of
  a ratio-scale quantity with a visible reference.)
- **Noise band.** Shade `baseline_median` ± the acceptance threshold
  (`max(min_speedup, 1 + noise_multiplier × baseline_noise_cv)`, converted into metric units).
  This is the single highest-value addition in B2: `rejected_speed` verdicts become *visible* —
  a dot inside the band is self-explanatory. `/api/state` already ships `baseline_noise_cv`;
  `config.benchmark` carries the multipliers via `_cfg_summary`. Note `_cfg_summary` returns `None`
  when the server was started with `--db` and no config — fall back to the hardcoded 1.03/2.0 the
  stats card already uses, and label the band as derived.
- **Error bars:** whiskers from `comparison.ci_low/ci_high` **only in speedup mode**, where that
  interval is exactly what `benchmark.py` bootstrapped. In raw mode draw the individual
  `benchmark.samples` as faint dots instead — converting a ratio CI into metric units would invent
  an interval nobody computed.

**Both modes:**
- x currently indexes only experiments with a `comparison`, silently dropping `patch_failed` /
  `locked_file` — which are part of the story. Switch x to chronological index over **all**
  experiments, drawing unmeasured ones as small ticks on the axis with no y value.
- Iteration boundaries as faint vertical separators with `it N` labels.
- Guard mixed metrics: if `new Set(exps.map(e => e.benchmark?.metric))` has >1 entry, disable raw
  mode and say why.

### 4.3 Retries (B2.3)

Retries are currently invisible: `_apply_retries` gives a retry the *same* `parent_id` and
`iteration` as the original, so it renders as an indistinguishable sibling, and `retry_of` is never
read by the UI. Yet "failed → fed the error back → accepted" is one of the most compelling things
the system does.

- **Tree:** keep the faint `lineage` edge to the parent commit (it is the true provenance) and add a
  dashed `--hot` `retry` edge from original → retry. Place the retry immediately below its original
  in a sub-row (§4.4) so the pair reads as one unit. Badge with `↻`.
- **Table:** a `↻ retry of #N` line under the hypothesis, and the `#` column showing `4.1` style
  sub-numbering for retry chains.
- **Detail panel:** when the selected experiment has `retry_of`, show a "Fed back to the worker"
  section containing the original's failure. Requires B2.5 to be faithful.
- **Funnel:** retries inflate `proposed` (each retry is its own experiment), so "N of M kept" drifts
  from "M hypotheses". Add a toggle or a second line: *attempts* vs *distinct hypotheses*
  (`retry_of == null` count). Small, but the funnel is currently quietly wrong with retries on.

### 4.4 Beam-aware layout (B2.4)

Today: `cols[it].forEach((e, ri) => y = 20 + ri*CY)` — row index is arrival order within the
iteration, with no relation to parentage. At `beam_width=1` that happens to look fine. At
`beam_width≥2` edges cross badly and the branch structure is unreadable.

Layout in `server/tree.py` (testable), not in JS:

1. Column = `iteration` (0 for baseline). Note an edge may span **more than one column**, because an
   older head can survive in the beam and be re-expanded later — `spans_iterations` flags it and the
   existing bezier already handles arbitrary dx.
2. Rows by DFS over the lineage forest, in `(iteration, created_at)` order: each leaf takes the next
   free row; a parent is centred over its children's row span. Simplified Reingold–Tilford — full
   R-T is not warranted at these node counts.
3. Retry chains occupy sub-rows directly under their original, before the next sibling, so a retry
   never separates a parent from its other children.
4. Emit `max_row` so the SVG height is computed server-side and the client stops recomputing it.

Beam visibility:
- Nodes with non-empty `expanded_at` get a "beam head · it 2,3" chip — this is what makes branching
  legible rather than just wide.
- Keep the amber `.head` stroke for the global best. Add a third state for "in the final beam but
  not the global head" (currently indistinguishable from any other `accepted`).
- Legend in the card header covering: accepted / not selected / rejected / pending / retry edge /
  beam head. The tree has six visual states after this change and needs one.

### 4.5 `previous_failure` provenance (B2.5)

```python
class Experiment(BaseModel):
    ...
    previous_failure: str = Field("", description="The failure text fed back to the worker on a retry")
```

Set where `_failure_feedback` is already computed in `orchestrator._apply_retries`. Defaulted, so
existing rows deserialize untouched. The alternative — recomputing the feedback string in JS — would
duplicate `_failure_feedback` and drift from it. Since the point of the panel is to show *what the
worker was actually told*, reconstruction is the wrong answer.

---

## 5. Tests

`tests/test_profilediff.py` (new):
- align across a line-number shift → matched by `(file, function)`, not orphaned (F3)
- `pct` inversion trap: total halves, one function's absolute time constant → classified
  `unchanged`, **not** `grew` (F4)
- absent-from-head → `below_cutoff_after` with `bound_note` citing `cutoff_self_time`; **never**
  `delta_ratio == 0` (F2) — this is the regression test for the fabricated-elimination bug
- `tool` mismatch → `comparable=False` with reason (F5)
- `before.commit == after.commit` → `comparable=False`, "nothing accepted yet"
- name collision in one file → summed, `ambiguous=True`
- coherence: profile ratio 5x vs `best_speedup` 1.05 → `coherence_warning` set
- legacy summary (`retained == 0`) → no elimination claims
- `residual_self_time` + retained self times reconcile to `total_time` within float tolerance

`tests/test_profiler.py` (extend, harness-owned — coordinate):
- `retain=40` stores 40 rows while `render_profile` still emits `max_hotspots` rows → **prompt size
  unchanged** (guards the one agent-side regression risk in B1.1)

`tests/test_tree.py` (new):
- `expanded_at` correct for `beam_width=2` across three iterations
- a beam node with zero hypotheses is absent from `expanded_at` (documented blind spot)
- retry chain: `retry_depth`, retry edge present, lineage edge preserved
- no row collisions; parent row within children's span; retry sub-row adjacency
- edge spanning >1 iteration when an old head is re-expanded

`tests/test_server.py` (extend):
- `GET /api/runs/{id}/profile_diff` 200 on the mock run, 404 unknown run
- `GET /api/runs/{id}/tree` node/edge counts match the experiment count + baseline
- both endpoints on a run with **no** `profile_cmd` → `comparable=False`, no 500

Reuse the existing `cfg`/`tiny_repo` fixtures. The mock provider makes a beam+retry run
deterministic, so B2's tests need no models. Target: **+18 tests, 67 total.**

---

## 6. Risks, and the honesty line

1. **Fabricated elimination (F2)** — highest risk in the whole plan, and the one that would damage
   the project's credibility rather than merely look wrong. Mitigated by `cutoff_self_time` bounds,
   the dedicated regression test, and legacy-run degradation.
2. **`unchanged_epsilon` is not significance.** Profiles are single-shot; there is no profile noise
   floor. The threshold is echoed in the API response and must be labelled a display threshold in
   the UI. Do not let the bottleneck card borrow the benchmark gate's statistical authority.
3. **`pct` comparisons (F4)** — absolute seconds primary, always.
4. **Scale honesty in raw mode** — explicit baseline reference line, no axis inversion, no
   zero-forcing, noise band shaded. The chart should make a rejection obvious, not flattering.
5. **Prompt cost** — B1.1 widens storage, not the prompt. Test-enforced.
6. **Poll-time re-render** — `render()` rebuilds tree + chart + table innerHTML every 1.2s while
   live. Adding two fetches per poll is wasteful: fetch `/tree` and `/profile_diff` only when
   `run.updated_at` changes, and skip `/profile_diff` entirely unless `head_commit` moved.
7. **B1.5, cheap and worth considering.** `orchestrator._execute` already profiles surviving beam
   nodes (`nd.profile = await self.harness.run_profile(nd.commit)`) and **discards the result at run
   end.** Persisting it onto the accepted experiment (`Experiment.profile`) costs nothing extra in
   compute and would let the UI diff *any* node against baseline or against its own parent. Deferred
   only because it touches `schema.py` + `orchestrator.py`; flagged as the best follow-on.
8. **True flame graphs (future).** `pstats.Stats.stats` values already carry the `_callers` map that
   `profilelib.run` drops on the floor, and `torch_run` already passes `with_stack=True`. Emitting
   caller edges would make real stack-based flame graphs possible and would supersede the flat diff.
   Harness-side; out of scope.

---

## 7. Sequencing

Ordered so each step is independently shippable and testable.

| Step | Work | Depends on |
|---|---|---|
| 1 | B1.1 retain/cutoff fields + config + harness wiring + prompt-size test | — |
| 2 | B1.2 `diff_profiles` + full unit suite | 1 |
| 3 | B1.3 endpoint + server tests | 2 |
| 4 | B1.4 bottleneck diff card | 3 |
| 5 | B2.1 `/api/runs/{id}/tree` + derivation tests | — (parallel with 1–4) |
| 6 | B2.2 metric-aware chart + noise band | 5 |
| 7 | B2.3 retry rendering | 5 |
| 8 | B2.5 `previous_failure` (unblocks the retry detail panel) | 7 |
| 9 | B2.4 beam layout + legend | 5, 7 |
| 10 | Regression pass: `pytest -q`, offline `demo_repo` run at `beam_width=2`, `max_patch_retries=1` | all |

Steps 1–4 and 5–9 are independent; B1 touches the harness boundary and B2 does not, so B2 can
proceed while B1.1 waits on sign-off.

**Demo config for step 10** — exercises beam branching and a retry in one offline run, no API keys:

```yaml
# configs/demo_repo_beam.yaml
search: {iterations: 3, candidates_per_iteration: 3, beam_width: 2, max_patch_retries: 1}
profile: {retain: 40}
```

Verification is not "the page renders": it is that a `rejected_speed` dot lands visibly inside the
shaded noise band, a retry chain shows failed → accepted, two beam branches are traceable without
crossing edges, and the bottleneck diff's rows plus residual reconcile to `total_time`.
