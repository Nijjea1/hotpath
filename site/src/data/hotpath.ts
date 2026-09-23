// Hotpath site data.
//
// MEASURED: every run number in this file comes from one real Hotpath run,
// run_73124208c0, recorded in submission/h100_2026-09-19/.hotpath/hotpath.db and summarised in
// submission/h100_2026-09-19/submission/run1/REPORT.md. Target: targets/torch_transformer, a tiny
// stand-in transformer (not a production model), on an NVIDIA H100 80GB with torch 2.11+cu128.
// Planner OpenAI gpt-4.1; worker moonshotai/Kimi-K2.7-Code; config configs/dryft_local.yaml.
//
// Experiment ids, hypotheses, planner rationales, harness reasons, tokens/sec medians and CIs are
// copied from the store. Diffs are real lines from each stored diff, trimmed to the lines that
// matter. `idea` is a short label for the hypothesis; `hypothesis` holds the planner's words.
// If you replace the run, replace every value here — the page reads nothing else.

export const ILLUSTRATIVE = false;

export const RUN = {
  id: "run_73124208c0",
  date: "2026-09-19",
  target: "a tiny stand-in transformer",
  hardware: "NVIDIA H100 80GB",
  planner: "gpt-4.1",
  worker: "Kimi-K2.7-Code",
  // Re-measured afterwards outside Hotpath, with the same locked tests/check.py and bench.py.
  recheck: { baseline: 904, optimized: 1376 },
};

export const REPO_URL = "https://github.com/Nijjea1/hotpath";
export const README_URL = `${REPO_URL}/blob/main/README.md`;
export const HARNESS_URL = `${REPO_URL}/blob/main/hotpath/harness.py`;
export const BENCHMARK_URL = `${REPO_URL}/blob/main/hotpath/benchmark.py`;
export const DEMO_URL = `${REPO_URL}/blob/main/docs/DEMO.md`;
export const REPORT_URL = `${REPO_URL}/blob/main/submission/h100_2026-09-19/submission/run1/REPORT.md`;

// ---------------------------------------------------------------------------
// The loop. `kind` is who does the step: an AI model (allowed to be creative
// and wrong) or the harness (deterministic, never trusts the model).
// ---------------------------------------------------------------------------
export type LoopStep = {
  id: string;
  n: number;
  name: string;
  kind: "ai" | "harness";
  tool: string;
  question: string;
  detail: string;
  code: string;
};

export const LOOP: LoopStep[] = [
  {
    id: "profile",
    n: 1,
    name: "Profile",
    kind: "harness",
    tool: "torch.profiler · cProfile",
    question: "Where does the time actually go?",
    detail:
      "Runs the target under a profiler and summarises the widest bars. Amdahl's law: a function that takes 5% of runtime can only ever save 5%.",
    code: "top: gemv kernel             12.3%\n     aten::native_layer_norm   7.4%",
  },
  {
    id: "hypothesize",
    n: 2,
    name: "Hypothesize",
    kind: "ai",
    tool: "planner · OpenAI API",
    question: "What should we try next?",
    detail:
      "The planner reads the profile, the hottest source, and every past result, and returns structured experiments. It stops proposing what already failed.",
    code: '{"idea": "build the causal mask once",\n "target_file": "model.py", "risk": "low"}',
  },
  {
    id: "implement",
    n: 3,
    name: "Implement",
    kind: "ai",
    tool: "workers · git worktrees",
    question: "Can the idea be written as a patch?",
    detail:
      "Fast worker models write each idea as a patch in its own isolated worktree, so many experiments run in parallel without touching each other.",
    code: "worktree  .hotpath/worktrees/exp_5a3154d8e6\npatch     model.py",
  },
  {
    id: "verify",
    n: 4,
    name: "Verify correctness",
    kind: "harness",
    tool: "locked test suite",
    question: "Is the output still the same?",
    detail:
      "Generated tokens must match the frozen reference exactly and logits must stay within tolerance. Tests are locked: a patch that touches them is rejected before a byte is written.",
    code: "ok   tokens seed=4 plen=200 n_new=16\nok   logits seed=4 max abs diff 5.96e-07",
  },
  {
    id: "benchmark",
    n: 5,
    name: "Benchmark",
    kind: "harness",
    tool: "warmup · sync · bootstrap CI",
    question: "Is it faster than the noise?",
    detail:
      "Warmup runs, GPU sync, many trials, the median. The speedup must clear the machine-measured noise floor, and its 95% interval must exclude 1.0.",
    code: "threshold  max(1.03, 1 + 2·1.1%) = 1.03\nci95       [1.350, 1.372]",
  },
  {
    id: "compose",
    n: 6,
    name: "Keep & compose",
    kind: "harness",
    tool: "beam · re-profile · ablate",
    question: "Does it still help on top of the rest?",
    detail:
      "Accepted patches stack, and each new one is measured against the stack it builds on. The bottleneck moves, so Hotpath re-profiles. An ablation pass re-measures what each shipped change contributes.",
    code: "stack  sdpa → reshape → mask once\nnext   re-profile",
  },
];

// ---------------------------------------------------------------------------
// Verdict statuses — the real ExperimentStatus values from hotpath/schema.py.
// ---------------------------------------------------------------------------
export type Status =
  | "accepted"
  | "rejected_correctness"
  | "rejected_speed"
  | "patch_failed"
  | "locked_file"
  | "not_selected"
  | "timeout"
  | "error";

export const STATUS_LABEL: Record<Status, string> = {
  accepted: "accepted",
  rejected_correctness: "rejected · correctness",
  rejected_speed: "rejected · speed",
  patch_failed: "patch failed",
  locked_file: "locked file",
  not_selected: "not selected",
  timeout: "timeout",
  error: "error",
};

// ---------------------------------------------------------------------------
// Experiment log: a selection from run_73124208c0's 51 attempts, in run order.
// tokPerSec is the benchmark median for the candidate. It is null when the patch
// never reached the benchmark — Hotpath does not time code that failed its tests.
// ---------------------------------------------------------------------------
export type Experiment = {
  id: string;
  n: number; // position in the run (1..51)
  idea: string; // short label
  hypothesis: string; // the planner's hypothesis, verbatim
  status: Status;
  shipped: boolean; // in the final head's lineage
  tokPerSec: number | null;
  speedup: number | null; // vs baseline
  rationale: string; // planner's reasoning (first sentence)
  reason: string; // harness verdict, verbatim; failing tests add the last line of their output
  short: string; // one-line summary for the verdict pills
  diff: string;
};

export const BASELINE_TOK_S = 965;

export const EXPERIMENTS: Experiment[] = [
  {
    id: "exp_fba5dd5729",
    n: 1,
    idea: "fused scaled-dot-product attention",
    hypothesis: "Replace manual attention math in Attention.forward with torch.nn.functional.scaled_dot_product_attention for the (q, k, v) attention computation.",
    status: "accepted",
    shipped: true,
    tokPerSec: 1058,
    speedup: 1.096,
    rationale: "The current Attention.forward does explicit (q @ k^T) matmul, masking, softmax, and then (att @ v), which launches multiple kernels.",
    reason: "1.125x vs parent clears point-estimate threshold 1.030x; CI [1.111, 1.134] excludes 1.0",
    short: "1.13× vs parent · CI excludes 1.0",
    diff: `- att = (q @ k.transpose(-2, -1)) / math.sqrt(k.shape[-1])
- att = att.masked_fill(mask == 0, float("-inf"))
- att = F.softmax(att, dim=-1)
+ y = F.scaled_dot_product_attention(q, k, v, attn_mask=mask, dropout_p=0.0, is_causal=False)`,
  },
  {
    id: "exp_aeb0317f6f",
    n: 2,
    idea: "preallocate the KV cache",
    hypothesis: "Preallocate the KV cache to max length and write new keys/values in place in Attention.forward, instead of growing with torch.cat each decode step.",
    status: "rejected_correctness",
    shipped: false,
    tokPerSec: null,
    speedup: null,
    rationale: "The torch.cat([pk, k], dim=2) pattern in Attention.forward is O(T^2) and incurs frequent memory allocations/copying.",
    reason: "tests failed (exit 1) · ValueError: too many values to unpack (expected 2)",
    short: "tests crashed: cache shape changed",
    diff: `- k = torch.cat([pk, k], dim=2)   # grows the cache every step: O(T^2) copying
+ pk = torch.empty(B, H, MAX_SEQ, D, device=x.device, dtype=k.dtype)
+ pv = torch.empty(B, H, MAX_SEQ, D, device=x.device, dtype=v.dtype)`,
  },
  {
    id: "exp_3df3ca135d",
    n: 3,
    idea: "drop the .item() sync",
    hypothesis: "Remove CPU-GPU sync (item()) in generate loop and keep next token on device as a tensor during greedy decoding.",
    status: "rejected_speed",
    shipped: false,
    tokPerSec: 989,
    speedup: 1.024,
    rationale: ".item() triggers a full CPU-GPU sync and blocks pipelines, slowing down CUDA token generation.",
    reason: "1.018x is below the noise-adjusted threshold of 1.030x (baseline noise CV 1.1%)",
    short: "1.02× · below the 1.03× bar",
    diff: `- nxt = int(logits[:, -1, :].argmax(dim=-1).item())     # CPU sync every token
+ nxt_t = logits[:, -1, :].argmax(dim=-1, keepdim=True)  # stay on device`,
  },
  {
    id: "exp_364b9d9ad5",
    n: 6,
    idea: "torch.compile with CUDA graphs",
    hypothesis: "Wrap the TinyGPT model's forward pass (or the relevant attention and block operations) in torch.compile using mode='reduce-overhead' for CUDA graphs.",
    status: "rejected_correctness",
    shipped: false,
    tokPerSec: null,
    speedup: null,
    rationale: "For small models with many single-token decode calls, much time is lost to kernel launch overhead.",
    reason: "tests failed (exit 1) · RuntimeError: accessing tensor output of CUDAGraphs that has been overwritten by a subsequent run",
    short: "tests crashed: CUDA graph output overwritten",
    diff: `+ if not self._compiled and args[0].is_cuda:
+     # Compile the single-token decode path with CUDA graphs.
+     self.blocks = nn.ModuleList([torch.compile(b, mode='reduce-overhead') for b in self.blocks])
+     self._compiled = True`,
  },
  {
    id: "exp_9b5769ac8d",
    n: 24,
    idea: "drop a redundant .contiguous()",
    hypothesis: "Remove redundant .contiguous() call in Attention.forward before reshape/permute after attention output.",
    status: "accepted",
    shipped: true,
    tokPerSec: 1059,
    speedup: 1.097,
    rationale: "The .contiguous() call creates a new tensor allocation and memory copy.",
    reason: "1.196x vs parent clears point-estimate threshold 1.030x; CI [1.192, 1.218] excludes 1.0",
    short: "1.20× vs parent · CI excludes 1.0",
    diff: `- y = y.transpose(1, 2).contiguous().view(B, T, C)
+ y = y.transpose(1, 2).reshape(B, T, C)`,
  },
  {
    id: "exp_9764890939",
    n: 26,
    idea: "mixed-precision autocast",
    hypothesis: "Use autocast (mixed precision) for attention and MLP layers when on CUDA for further acceleration, respecting correctness tolerance.",
    status: "rejected_correctness",
    shipped: false,
    tokPerSec: null,
    speedup: null,
    rationale: "Mixed precision arithmetic (autocast) is supported for transformer architectures and is known to provide speedups.",
    reason: "tests failed (exit 1) · TypeError: autocast.__new__() got an unexpected keyword argument 'device_type'",
    short: "tests crashed: wrong autocast API",
    diff: `+ from torch.cuda.amp import autocast
+ if x.is_cuda:
+     with autocast(device_type='cuda'):
+         x = x + self.attn(self.ln1(x), cache)`,
  },
  {
    id: "exp_9167bbeb1e",
    n: 27,
    idea: "in-place MLP residual add",
    hypothesis: "Batch the residual + MLP add operation in Block.forward as in-place (x += self.mlp(self.ln2(x))) if shapes match and x is not reused later.",
    status: "not_selected",
    shipped: false,
    tokPerSec: 1020,
    speedup: 1.057,
    rationale: "In-place addition reduces memory allocations and bandwidth.",
    reason: "correct and 1.137x faster, but not in the top-2 beam; may be re-proposed on a surviving head",
    short: "correct and faster · a sibling ranked higher",
    diff: `- return x + self.mlp(self.ln2(x))
+ x += self.mlp(self.ln2(x))
+ return x`,
  },
  {
    id: "exp_5a3154d8e6",
    n: 37,
    idea: "build the causal mask once",
    hypothesis: "Build the causal attention mask once, ahead of time, and reuse it rather than constructing it dynamically within every Attention.forward call (except for single-token path which is already optimized).",
    status: "accepted",
    shipped: true,
    tokPerSec: 1409,
    speedup: 1.46,
    rationale: "Currently, torch.tril is invoked every time Attention.forward is called for multi-token inputs, leading to unnecessary repeated computation and memory allocation.",
    reason: "1.359x vs parent clears point-estimate threshold 1.030x; CI [1.350, 1.372] excludes 1.0",
    short: "1.36× vs parent · CI excludes 1.0",
    diff: `+ self.register_buffer("causal_mask", torch.tril(torch.ones(MAX_SEQ, MAX_SEQ)), persistent=False)
- mask = torch.tril(torch.ones(Tk, Tk, device=x.device))[Tk - T:, :].bool()
+ mask = None if T == 1 and cache is not None else self.causal_mask[Tk - T:Tk, :Tk].bool()`,
  },
  {
    id: "exp_cde974a819",
    n: 41,
    idea: "preallocate the KV cache, again",
    hypothesis: "Preallocate key and value caches in Attention to maximum sequence length and write new keys/values in-place during decoding, rather than growing with torch.cat each step.",
    status: "patch_failed",
    shipped: false,
    tokPerSec: null,
    speedup: null,
    rationale: "At each decoding step, torch.cat is used to append to the cache, which is O(T^2) and causes frequent allocations and copies.",
    reason: "edit to model.py is a no-op (search equals replace): no change",
    short: "the patch changed nothing",
    diff: `  (no diff — the worker's replacement was identical to the original text)`,
  },
  {
    id: "exp_7c21754a1b",
    n: 43,
    idea: "TF32 matmuls",
    hypothesis: "Enable TF32 matrix multiplication (matmuls and attention) on supported GPUs by setting torch.backends.cuda.matmul.allow_tf32 = True at the start of model code execution.",
    status: "rejected_speed",
    shipped: false,
    tokPerSec: 1390,
    speedup: 1.441,
    rationale: "Matmuls (mm, addmm) and attention kernels are present in the profile, and enabling TF32 can deliver significant speedup on Ampere+ GPUs.",
    reason: "1.004x is below the noise-adjusted threshold of 1.030x (baseline noise CV 1.1%)",
    short: "1.004× vs parent · noise",
    diff: `+ # Enable TF32 for CUDA matmuls/attention on Ampere+ GPUs.
+ torch.backends.cuda.matmul.allow_tf32 = True`,
  },
];

// The trust moment: a reasonable idea whose patch broke the model, caught before any timing.
export const TRUST_ID = "exp_364b9d9ad5";

// ---------------------------------------------------------------------------
// Funnel — build_funnel() over run_73124208c0. Every attempt ends in exactly one status.
// ---------------------------------------------------------------------------
export const FUNNEL = [
  { stage: "Attempts (37 ideas + 14 retries)", count: 51 },
  { stage: "Patches applied cleanly", count: 50 },
  { stage: "Passed correctness", count: 28 },
  { stage: "Faster than the noise", count: 10 },
  { stage: "Shipped in the final stack", count: 3 },
];

export const OUTCOMES: { status: Status | "superseded"; label: string; count: number }[] = [
  { status: "accepted", label: "shipped", count: 3 },
  { status: "superseded", label: "passed, not in final stack", count: 7 },
  { status: "rejected_speed", label: "rejected · speed", count: 18 },
  { status: "rejected_correctness", label: "rejected · correctness", count: 22 },
  { status: "patch_failed", label: "patch failed", count: 1 },
];

export const TOTALS = { proposed: 51, shipped: 3, brokeCorrectness: 22, notFaster: 18 };

// ---------------------------------------------------------------------------
// Throughput over the run: best verified tokens/sec after each attempt (build_chart's running
// best over accepted experiments). Two steps were beam heads that a later, faster lineage replaced.
// ---------------------------------------------------------------------------
export const THROUGHPUT_STEPS = [
  { at: 0, tokPerSec: 965, label: "baseline" },
  { at: 1, tokPerSec: 1058, label: "fused attention" },
  { at: 24, tokPerSec: 1059, label: "no .contiguous()" },
  { at: 33, tokPerSec: 1205, label: "decode skips mask*" },
  { at: 35, tokPerSec: 1276, label: "no .item() sync*" },
  { at: 37, tokPerSec: 1409, label: "mask built once" },
];
export const RUN_LENGTH = 51;
export const NOISE_PCT = 1.1;

// ---------------------------------------------------------------------------
// Profiles: torch.profiler GPU self time per aten op, baseline (74d8af7f) vs the shipped head
// (bf7b3bde), from the run's stored profiles. Only the aten ops that moved or matter are shown.
// A `≤` value is an upper bound: the op fell below the retained rows.
// ---------------------------------------------------------------------------
export type Frame = { name: string; ms: number; tone: "hot" | "warm" | "cool" | "gone"; children?: Frame[] };

export const FLAME_BEFORE: Frame = {
  name: "aten ops, GPU self time",
  ms: 9.42,
  tone: "cool",
  children: [
    {
      name: "attention: 4 ops",
      ms: 2.19,
      tone: "warm",
      children: [
        { name: "bmm", ms: 1.03, tone: "cool" },
        { name: "softmax", ms: 0.43, tone: "cool" },
        { name: "masked_fill", ms: 0.39, tone: "cool" },
        { name: "div", ms: 0.34, tone: "cool" },
      ],
    },
    { name: "mask build", ms: 0.8, tone: "hot" },
    { name: "layer_norm", ms: 1.52, tone: "cool" },
    { name: "mm", ms: 1.36, tone: "cool" },
    { name: "addmm", ms: 1.31, tone: "cool" },
    { name: "cat", ms: 0.82, tone: "cool" },
    { name: "add", ms: 0.73, tone: "cool" },
    { name: "gelu", ms: 0.36, tone: "cool" },
    { name: "copy_", ms: 0.33, tone: "hot" },
  ],
};

export const FLAME_AFTER: Frame = {
  name: "aten ops, GPU self time",
  ms: 9.14,
  tone: "cool",
  children: [
    {
      name: "attention: 1 fused kernel",
      ms: 2.99,
      tone: "warm",
      children: [{ name: "efficient_attention", ms: 2.99, tone: "cool" }],
    },
    { name: "layer_norm", ms: 1.52, tone: "cool" },
    { name: "mm", ms: 1.34, tone: "cool" },
    { name: "addmm", ms: 1.33, tone: "cool" },
    { name: "cat", ms: 0.81, tone: "cool" },
    { name: "add", ms: 0.73, tone: "cool" },
    { name: "gelu", ms: 0.36, tone: "cool" },
    { name: "copy_", ms: 0.06, tone: "cool" },
  ],
};

export const FLAME_NOTES = [
  { name: "mask build (tril, == 0)", before: "0.80 ms", after: "≤ 0.10 ms", how: "built once" },
  { name: "copy_", before: "0.33 ms", after: "0.06 ms", how: ".contiguous() removed" },
  { name: "attention", before: "4 ops · 2.19 ms", after: "1 kernel · 2.99 ms", how: "fused SDPA" },
];

// ---------------------------------------------------------------------------
// Thesis cards
// ---------------------------------------------------------------------------
export const THESIS_CARDS = [
  {
    eyebrow: "Plausible, but broken",
    title: "CUDA graphs, autocast, fused kernels. All reasonable. All broke the model.",
    body: "The patches crashed or changed the output. Hotpath never times a patch that fails the locked check, so a broken speedup can't be counted as a win.",
    statLabel: "attempts that broke correctness",
  },
  {
    eyebrow: "Faster, but it's noise",
    title: "TF32 measured 1.004× faster. That's noise.",
    body: "Its 95% interval ran from 0.99 to 1.02. On this H100 the run-to-run noise was 1.1%, so the bar was 1.03× and an interval that excludes 1.0.",
    statLabel: "attempts not measurably faster",
  },
];

// ---------------------------------------------------------------------------
// Quickstart — the real config (configs/dryft_local.yaml) and CLI (hotpath/cli.py).
// ---------------------------------------------------------------------------
export const CONFIG_YAML = `# configs/dryft_local.yaml (excerpt) — the config behind the run above
target: ../targets/torch_transformer
test_cmd: python tests/check.py      # greedy tokens must match the frozen reference
bench_cmd: python bench.py           # tokens_per_s across four prompt/batch shapes
profile_cmd: python hotprofile.py    # torch.profiler summary
editable: ["model.py", "kernels/*.py", "*.py"]
locked: ["tests/*", "bench.py", "hotprofile.py", "reference*.py"]
benchmark: {min_speedup: 1.03, noise_multiplier: 2.0, baseline_repeats: 5, rebenchmark_parent: true}
search: {iterations: 6, candidates_per_iteration: 4, beam_width: 2, max_patch_retries: 1}`;

export const COMMANDS = [
  ["hotpath run configs/demo_repo.yaml", "The full loop on the bundled slow repo, offline with the mock provider — no API keys."],
  ["hotpath serve configs/demo_repo.yaml", "The live dashboard at 127.0.0.1:8765: experiment tree, progress chart, diffs, rejection reasons."],
  ["hotpath ablate configs/demo_repo.yaml", "Leave-one-out re-measure of the accepted chain. --prune drops removable changes together, re-verifies, and keeps the result only if it isn't measurably slower."],
  ["hotpath export configs/demo_repo.yaml out/", "A PR bundle: the optimized tree, changes.patch, and a REPORT.md with the benchmark table. --ablate adds the ablation table; --prune ships the pruned stack."],
] as const;

// The strategy seeds in configs/dryft_local.yaml; the planner may also invent its own.
export const MOVES = [
  "fused SDPA attention",
  "KV cache preallocation",
  "remove sync points",
  "build the causal mask once",
  "torch.compile",
  "CUDA graphs",
  "fused norm kernels",
  "TF32",
  "speculative decoding",
];
