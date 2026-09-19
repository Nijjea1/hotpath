// Hotpath site data.
//
// ILLUSTRATIVE: every run number in this file (tokens/sec, speedups, funnel counts,
// experiment ids, flame-graph widths) comes from the design doc's illustrative
// transformer run, not from a measured H100/Dryft result. When the real run lands,
// replace the values here — the page reads nothing else. Keep `ILLUSTRATIVE` true
// until then; the page shows a disclosure wherever it is set.
//
// Not illustrative: the loop, the config (configs/dryft_h100.yaml), the verdict statuses
// (hotpath/schema.py), the reason formats (benchmark.py, workspace.py, harness.py), and the
// CLI (hotpath/cli.py). Reason strings below use the harness's real message formats; the
// text after " · " stands in for the test output tail the harness stores alongside.

export const ILLUSTRATIVE = true;

export const REPO_URL = "https://github.com/Nijjea1/hotpath";
export const README_URL = `${REPO_URL}/blob/main/README.md`;
export const HARNESS_URL = `${REPO_URL}/blob/main/hotpath/harness.py`;
export const BENCHMARK_URL = `${REPO_URL}/blob/main/hotpath/benchmark.py`;
export const DEMO_URL = `${REPO_URL}/blob/main/docs/DEMO.md`;

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
    code: "top: kv_cache torch.cat   22%\n     .item() sync          12%",
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
    code: '{"idea": "preallocate KV cache",\n "risk": "low"}',
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
    code: "worktree  .hotpath/worktrees/exp_0011\npatch     model.py",
  },
  {
    id: "verify",
    n: 4,
    name: "Verify correctness",
    kind: "harness",
    tool: "locked test suite",
    question: "Is the output still the same?",
    detail:
      "Generated tokens must match the original exactly and logits must stay within tolerance. Tests are locked: a patch that touches them is rejected before a byte is written.",
    code: "tokens_match    true\nmax_logit_diff  3.1e-06",
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
    code: "threshold  max(1.03, 1 + 2·noise)\nci95       [1.18, 1.24]",
  },
  {
    id: "compose",
    n: 6,
    name: "Keep & compose",
    kind: "harness",
    tool: "stack · re-profile · ablate",
    question: "Does it still help on top of the rest?",
    detail:
      "Accepted patches stack and the stack is re-tested. The bottleneck moves, so Hotpath re-profiles. An ablation pass drops any change that isn't pulling its weight.",
    code: "stack  compile → kv → rmsnorm\nnext   re-profile",
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
// Experiment log (illustrative). tokPerSec is what the benchmark measured for
// the candidate — including the rejected ones, which is the point: fast is not
// the same as kept.
// ---------------------------------------------------------------------------
export type Experiment = {
  id: string;
  n: number; // position in the run
  idea: string;
  status: Status;
  tokPerSec: number | null;
  speedup: number | null; // vs baseline
  rationale: string; // planner's reasoning
  reason: string; // harness verdict, in the harness's real message format
  short: string; // one-line summary for the verdict pills
  diff: string;
};

export const BASELINE_TOK_S = 100;

export const EXPERIMENTS: Experiment[] = [
  {
    id: "exp_0003",
    n: 3,
    idea: "torch.compile the decode step",
    status: "accepted",
    tokPerSec: 135,
    speedup: 1.35,
    rationale: "attention + MLP launch ~40 small kernels per token; let the compiler fuse them",
    reason: "1.350x vs parent clears point-estimate threshold 1.042x; CI [1.311, 1.389] excludes 1.0",
    short: "1.35× vs parent · CI excludes 1.0",
    diff: `- logits = self.decode_step(tok, cache)
+ self.decode_step = torch.compile(self.decode_step)
+ logits = self.decode_step(tok, cache)`,
  },
  {
    id: "exp_0004",
    n: 4,
    idea: "int8 weight quantization",
    status: "rejected_correctness",
    tokPerSec: 180,
    speedup: 1.8,
    rationale: "weights dominate memory traffic; int8 halves bandwidth per token",
    reason: "tests failed (exit 1) · tokens diverged at position 17 on prompt 3; max |Δlogit| 0.41 > tolerance 1e-3",
    short: "tokens diverged at position 17",
    diff: `  # model.py
- self.w = nn.Linear(d, 4 * d)
+ self.w = quantize_int8(nn.Linear(d, 4 * d))`,
  },
  {
    id: "exp_0006",
    n: 6,
    idea: "reorder sampling loop",
    status: "rejected_speed",
    tokPerSec: 136,
    speedup: 1.36,
    rationale: "top-k is computed before temperature; swap to skip one pass",
    reason: "1.008x but the 95% CI [0.982, 1.031] includes 1.0: not statistically distinguishable",
    short: "1.008× · CI includes 1.0",
    diff: `- probs = softmax(topk(logits) / t)
+ probs = topk(softmax(logits / t))`,
  },
  {
    id: "exp_0011",
    n: 11,
    idea: "preallocate the KV cache",
    status: "accepted",
    tokPerSec: 162,
    speedup: 1.62,
    rationale: "torch.cat on the cache is 22% of decode time — it copies the whole cache every step",
    reason: "1.200x vs parent clears point-estimate threshold 1.042x; CI [1.178, 1.236] excludes 1.0",
    short: "1.20× vs parent · CI excludes 1.0",
    diff: `- self.k = torch.cat([self.k, k], dim=1)
+ self.k[:, pos] = k      # allocated once, max_len`,
  },
  {
    id: "exp_0014",
    n: 14,
    idea: "capture decode in CUDA graphs",
    status: "rejected_correctness",
    tokPerSec: null,
    speedup: null,
    rationale: "one token at a time is CPU-bound; replaying a graph removes launch overhead",
    reason: "tests failed (exit 1) · RuntimeError: graph capture with a dynamic sequence length",
    short: "tests crashed during graph capture",
    diff: `+ g = torch.cuda.CUDAGraph()
+ with torch.cuda.graph(g):
+     out = self.decode_step(tok, cache)`,
  },
  {
    id: "exp_0017",
    n: 17,
    idea: "skip slow prompts in the test",
    status: "locked_file",
    tokPerSec: null,
    speedup: null,
    rationale: "the long-prompt case dominates test time",
    reason: "blocked: 'tests/check.py' matches locked pattern 'tests/*'",
    short: "edited the locked test · blocked before writing",
    diff: `  # tests/check.py   (locked)
- for prompt in PROMPTS:
+ for prompt in PROMPTS[:3]:`,
  },
  {
    id: "exp_0022",
    n: 22,
    idea: "fused RMSNorm kernel",
    status: "accepted",
    tokPerSec: 179,
    speedup: 1.79,
    rationale: "RMSNorm launches 4 kernels per layer; one Triton kernel does it in a pass",
    reason: "1.105x vs parent clears point-estimate threshold 1.042x; CI [1.071, 1.134] excludes 1.0",
    short: "1.10× vs parent · CI excludes 1.0",
    diff: `- x = x * torch.rsqrt(x.pow(2).mean(-1, keepdim=True) + eps)
+ x = fused_rmsnorm(x, self.weight, eps)   # kernels/rmsnorm.py`,
  },
  {
    id: "exp_0026",
    n: 26,
    idea: "fuse rotary embeddings",
    status: "patch_failed",
    tokPerSec: null,
    speedup: null,
    rationale: "rotary is applied separately to q and k; fuse into one kernel",
    reason: "search text not found in model.py: 'q = apply_rotary(q, cos, sin)'",
    short: "search text not found in model.py",
    diff: `  (no diff — the worker's search block did not match the file)`,
  },
  {
    id: "exp_0029",
    n: 29,
    idea: "fp16 attention scores",
    status: "rejected_correctness",
    tokPerSec: 191,
    speedup: 1.91,
    rationale: "attention scores in fp32 double the memory traffic of the softmax",
    reason: "tests failed (exit 1) · max |Δlogit| 2.3e-02 > tolerance 1e-3 (tokens still matched)",
    short: "logits out of tolerance",
    diff: `- scores = (q @ k.transpose(-1, -2)).float()
+ scores = (q @ k.transpose(-1, -2)).half()`,
  },
  {
    id: "exp_0034",
    n: 34,
    idea: "speculative decoding, greedy verification",
    status: "accepted",
    tokPerSec: 224,
    speedup: 2.24,
    rationale: "a tiny draft model proposes 4 tokens; the main model checks them in one pass",
    reason: "1.251x vs parent clears point-estimate threshold 1.042x; CI [1.212, 1.290] excludes 1.0",
    short: "1.25× vs parent · CI excludes 1.0",
    diff: `- tok = self.step(tok)
+ draft = self.draft.propose(tok, k=4)
+ tok = self.verify_greedy(draft)`,
  },
];

// the trust moment
export const TRUST_ID = "exp_0004";

// ---------------------------------------------------------------------------
// Funnel (illustrative) — every proposal ends in exactly one status.
// ---------------------------------------------------------------------------
export const FUNNEL = [
  { stage: "Ideas proposed", count: 40 },
  { stage: "Patches applied cleanly", count: 31 },
  { stage: "Passed correctness", count: 19 },
  { stage: "Faster than the noise", count: 8 },
  { stage: "Survived ablation, shipped", count: 5 },
];

export const OUTCOMES: { status: Status | "pruned"; label: string; count: number }[] = [
  { status: "accepted", label: "accepted", count: 5 },
  { status: "pruned", label: "pruned by ablation", count: 3 },
  { status: "rejected_speed", label: "rejected · speed", count: 11 },
  { status: "rejected_correctness", label: "rejected · correctness", count: 10 },
  { status: "patch_failed", label: "patch failed", count: 9 },
  { status: "locked_file", label: "locked file", count: 2 },
];

export const TOTALS = { proposed: 40, shipped: 5, brokeCorrectness: 10, withinNoise: 11 };

// ---------------------------------------------------------------------------
// Throughput over the run (illustrative): best verified tokens/sec after each
// experiment. Steps are accepted changes; flat stretches are rejections.
// ---------------------------------------------------------------------------
export const THROUGHPUT_STEPS = [
  { at: 0, tokPerSec: 100, label: "baseline" },
  { at: 3, tokPerSec: 135, label: "torch.compile" },
  { at: 11, tokPerSec: 162, label: "KV cache" },
  { at: 22, tokPerSec: 179, label: "fused RMSNorm" },
  { at: 34, tokPerSec: 224, label: "speculative decoding" },
];
export const RUN_LENGTH = 40;
export const NOISE_PCT = 2.1;

// ---------------------------------------------------------------------------
// Flame graphs (illustrative): widths are ms per decode step. Before = 100;
// after = 45 (0.45×). Children are laid left to right under their parent.
// ---------------------------------------------------------------------------
export type Frame = { name: string; ms: number; tone: "hot" | "warm" | "cool" | "gone"; children?: Frame[] };

export const FLAME_BEFORE: Frame = {
  name: "decode_step",
  ms: 100,
  tone: "cool",
  children: [
    {
      name: "attention",
      ms: 34,
      tone: "warm",
      children: [
        { name: "qkv_proj", ms: 10, tone: "cool" },
        { name: "sdpa", ms: 14, tone: "warm" },
        { name: "out_proj", ms: 10, tone: "cool" },
      ],
    },
    { name: "kv_cache torch.cat", ms: 22, tone: "hot" },
    {
      name: "mlp",
      ms: 22,
      tone: "warm",
      children: [
        { name: "up_proj", ms: 11, tone: "cool" },
        { name: "down_proj", ms: 11, tone: "cool" },
      ],
    },
    { name: ".item() sync", ms: 12, tone: "hot" },
    { name: "rmsnorm", ms: 6, tone: "warm" },
    { name: "sample", ms: 4, tone: "cool" },
  ],
};

export const FLAME_AFTER: Frame = {
  name: "decode_step",
  ms: 45,
  tone: "cool",
  children: [
    {
      name: "attention (compiled)",
      ms: 19,
      tone: "cool",
      children: [
        { name: "qkv_proj", ms: 6, tone: "cool" },
        { name: "sdpa", ms: 8, tone: "cool" },
        { name: "out_proj", ms: 5, tone: "cool" },
      ],
    },
    {
      name: "mlp (compiled)",
      ms: 15,
      tone: "cool",
      children: [
        { name: "up_proj", ms: 7, tone: "cool" },
        { name: "down_proj", ms: 8, tone: "cool" },
      ],
    },
    { name: "kv write", ms: 3, tone: "cool" },
    { name: "fused_rmsnorm", ms: 4, tone: "cool" },
    { name: "sample", ms: 4, tone: "cool" },
  ],
};

export const FLAME_NOTES = [
  { name: "kv_cache torch.cat", before: "22 ms", after: "3 ms", how: "preallocated" },
  { name: ".item() sync", before: "12 ms", after: "gone", how: "sync point removed" },
  { name: "attention + mlp", before: "56 ms", after: "34 ms", how: "torch.compile" },
];

// ---------------------------------------------------------------------------
// Thesis cards
// ---------------------------------------------------------------------------
export const THESIS_CARDS = [
  {
    eyebrow: "Fast but wrong",
    title: "A 1.8× speedup that changed the model's output.",
    body: "AI coding tools will happily propose it. Without a locked correctness check, nothing tells you the tokens quietly changed at position 17.",
    statLabel: "proposals that broke correctness",
  },
  {
    eyebrow: "Faster, but it's noise",
    title: "A 3% win on a ±2% benchmark is luck.",
    body: "GPU timings drift run to run. Without warmup, sync, many trials and a measured noise floor, you ship regressions that looked like wins.",
    statLabel: "proposals within measurement noise",
  },
];

// ---------------------------------------------------------------------------
// Quickstart — the real config shape (configs/*.yaml) and CLI (hotpath/cli.py).
// ---------------------------------------------------------------------------
export const CONFIG_YAML = `# configs/dryft_h100.yaml (excerpt)
target: ../targets/torch_transformer
test_cmd: python tests/check.py      # mirrors Dryft's correctness definition
bench_cmd: python bench.py           # tokens_per_s across varied workloads
profile_cmd: python hotprofile.py    # torch.profiler summary
editable: ["model.py", "kernels/*.py", "*.py"]
locked: ["tests/*", "bench.py", "hotprofile.py", "reference*.py"]
benchmark: {min_speedup: 1.03, noise_multiplier: 2.0, baseline_repeats: 5}`;

export const COMMANDS = [
  ["hotpath run configs/demo_repo.yaml", "The full loop on the bundled slow repo, offline with the mock provider — no API keys."],
  ["hotpath serve configs/demo_repo.yaml", "The live dashboard at 127.0.0.1:8765: experiment tree, throughput chart, diffs, rejection reasons."],
  ["hotpath ablate configs/demo_repo.yaml", "Leave-one-out re-measure of the accepted chain. --prune drops removable changes together, re-verifies, and keeps the result only if it isn't measurably slower."],
  ["hotpath export configs/demo_repo.yaml out/", "A PR bundle: the optimized tree, changes.patch, and a REPORT.md with the benchmark table. --ablate adds the ablation table; --prune ships the pruned stack."],
] as const;

export const MOVES = [
  "torch.compile",
  "KV cache preallocation",
  "remove sync points",
  "CUDA graphs",
  "fused Triton kernels",
  "speculative decoding",
  "quantization",
];
