// Hand-written usage guide for the docs page. Every value here describes behaviour that exists in
// hotpath/ today (config defaults are copied from hotpath/schema.py, env vars from the code that
// reads them). The CLI flag table itself is generated: see ./commands.ts.
// tests/test_site_commands.py fails if a CLI command has no entry in COMMAND_GUIDE.

export type Workflow = { id: string; title: string; when: string; code: string; notes: string[] };

export const WORKFLOWS: Workflow[] = [
  {
    id: "any-repo",
    title: "Any repository, one command",
    when: "You have a GitHub URL (or a local checkout) and want a verified pull request.",
    code: `hotpath doctor                                   # git, Docker, keys, accelerator
hotpath check https://github.com/you/your-repo   # seconds, read-only: blockers, cautions, cost
hotpath go    https://github.com/you/your-repo   # the nine stages, ending in a draft PR`,
    notes: [
      "Nine stages: setup, fetch, assess, baseline, benchmark, configure, optimize, publish, verify. Each one finishes or stops with the reason and a next step.",
      "The baseline must be green on 3 of 3 runs; a flaky or failing suite stops the run before anything is measured.",
      "No benchmark in the repo? Hotpath profiles the test suite, writes one over the hottest functions, and validates it by running it before trusting it.",
      "Nothing is pushed without your confirmation (or --yes), never to the default branch, and the PR opens as a draft.",
      "After the PR opens, Hotpath waits for the repository's own CI and only touches failures the PR introduced.",
    ],
  },
  {
    id: "own-repo",
    title: "Inside your own repository",
    when: "You maintain the code and want Hotpath as a repeatable tool with a config you control.",
    code: `cd my-repo
hotpath init                 # writes .hotpath.yaml + .github/workflows/hotpath-verify.yml
git add .hotpath.yaml .gitignore .github && git commit -m "Set up Hotpath" && git push
hotpath run --pr             # optimize, then push hotpath/<run id> and open a PR
hotpath serve                # the dashboard for this repo's runs
hotpath pr --prune           # later: ship only the changes that pull their weight`,
    notes: [
      "init detects your correctness check and benchmark, locks them, and asks what Hotpath may edit.",
      "Every command then finds the nearest .hotpath.yaml, so no config path is needed.",
      "The generated workflow re-runs the locked check on GitHub and fails any hotpath/* PR that touches a locked or non-editable file.",
      "Publishing refuses if your base branch moved since the run measured it; re-publishing the same run refreshes the same PR.",
    ],
  },
  {
    id: "offline",
    title: "Offline demo, no API keys",
    when: "You want to see the whole loop, including every rejection path, before spending anything.",
    code: `git clone https://github.com/Nijjea1/hotpath && cd hotpath
pip install -e .
hotpath run configs/demo_repo.yaml            # full loop with recorded patches
hotpath serve configs/demo_repo.yaml          # http://127.0.0.1:8765
hotpath ablate configs/demo_repo.yaml         # what each kept change contributed
hotpath export configs/demo_repo.yaml out/    # PR bundle: tree, changes.patch, REPORT.md
hotpath run configs/demo_repo_beam.yaml       # beam search + a retry chain`,
    notes: [
      "The mock provider replaces the model, not the verification: every recorded patch still goes through a real worktree, the locked tests, and the benchmark.",
      "Several recorded patches are deliberately wrong (a test edit, a behaviour change, a no-op), so you see locked_file, rejected_correctness, rejected_speed and patch_failed for real.",
    ],
  },
  {
    id: "byo-bench",
    title: "Bring your own benchmark",
    when: "You already know what 'faster' means for your code and want Hotpath to measure exactly that.",
    code: `# bench.py — lock this file in .hotpath.yaml
from hotpath.benchlib import run
from mylib import workload

run(lambda: workload(), warmup=2, trials=15, seed=0)
# prints one JSON line: {"hotpath_benchmark": 1, "samples": [...], "metric": "seconds"}`,
    notes: [
      "Any command works as bench_cmd if it prints one JSON line with at least two positive samples.",
      "Add \"higher_is_better\": true for throughput metrics such as tokens/sec.",
      "GPU targets can declare a fixed workload matrix (benchmark.required_workloads); a candidate must keep ≥98% of its parent's throughput on every shape, not just win on aggregate.",
    ],
  },
];

export type CommandGuide = { when: string; example: string };

// One entry per CLI command, in the order the CLI lists them.
export const COMMAND_GUIDE: Record<string, CommandGuide> = {
  doctor: {
    when: "First thing on a new machine, and whenever a run fails for an environmental reason.",
    example: "hotpath doctor\nhotpath doctor --verify-keys      # ask each endpoint whether the key still works\nhotpath doctor --require-gpu      # exit non-zero unless PyTorch sees an accelerator",
  },
  init: {
    when: "Setting up a repository you own so that run, pr and serve need no config path.",
    example: "hotpath init\nhotpath init --yes --test-cmd \"python -m pytest -q\" --bench-cmd \"python bench.py\" --editable \"src/*.py\"",
  },
  go: {
    when: "One command from a URL to a verified draft pull request.",
    example: "hotpath go https://github.com/you/your-repo\nhotpath go owner/repo --iterations 4 --beam 2 --max-minutes 30\nhotpath go . --provider mock --mock-patches DIR --sandbox local --no-pr   # offline",
  },
  check: {
    when: "Before spending anything: what would stop a run, and what it would cost. Exit 0 go, 1 caution, 2 stop.",
    example: "hotpath check https://github.com/you/your-repo\nhotpath check . --json",
  },
  assess: {
    when: "A read-only look at what Hotpath detects: ecosystem, tier, test and benchmark commands, editable and locked files.",
    example: "hotpath assess path/to/repo\nhotpath assess . --json",
  },
  run: {
    when: "One optimization loop from a config you control.",
    example: "hotpath run                                   # nearest .hotpath.yaml\nhotpath run configs/demo_repo.yaml --iterations 5 --beam 2\nhotpath run --pr                              # publish if something was proved\nhotpath run --resume run_ab12cd34             # continue a stopped run",
  },
  pr: {
    when: "Publishing a finished run as a pull request, one verified commit per change.",
    example: "hotpath pr\nhotpath pr --run-id run_ab12cd34 --prune --draft\nhotpath pr --no-push                          # build the branch locally only",
  },
  serve: {
    when: "Reading a run: the experiment tree, the metric chart with its noise band, and every rejection reason.",
    example: "hotpath serve                                  # http://127.0.0.1:8765\nhotpath serve --db path/to/hotpath.db          # read-only",
  },
  ablate: {
    when: "Finding out what each accepted change actually contributed.",
    example: "hotpath ablate\nhotpath ablate --prune --json ablation.json",
  },
  export: {
    when: "A reviewable bundle without touching git remotes: optimized tree, changes.patch, REPORT.md.",
    example: "hotpath export .hotpath.yaml out/\nhotpath export .hotpath.yaml out/ --ablate --prune",
  },
};

export type ConfigKey = { key: string; default: string; meaning: string };
export type ConfigGroup = { group: string; keys: ConfigKey[] };

export const CONFIG_REFERENCE: ConfigGroup[] = [
  {
    group: "Target",
    keys: [
      { key: "name", default: "required", meaning: "Name shown in reports and the dashboard." },
      { key: "target", default: "required", meaning: "Path to the repository, relative to the config file." },
      { key: "test_cmd", default: "required", meaning: "Correctness check. Exit 0 means correct; it is the only definition Hotpath uses." },
      { key: "bench_cmd", default: "required", meaning: "Prints one JSON line with samples (see Bring your own benchmark)." },
      { key: "profile_cmd", default: "none", meaning: "Prints hotspots for the planner (cProfile or torch.profiler via hotpath.profilelib)." },
      { key: "editable", default: "required", meaning: "Globs the agent may change." },
      { key: "locked", default: "[]", meaning: "Globs it must never touch, enforced in code before a byte is written." },
      { key: "correctness_contract", default: "preserve outputs, ordering, exceptions…", meaning: "Plain-language contract shown to the models." },
      { key: "workdir", default: ".hotpath", meaning: "Where worktrees and the SQLite store live, relative to the target." },
    ],
  },
  {
    group: "benchmark",
    keys: [
      { key: "min_speedup", default: "1.03", meaning: "Absolute floor: below this a change is never accepted." },
      { key: "noise_multiplier", default: "2.0", meaning: "Threshold = max(min_speedup, 1 + multiplier × baseline noise)." },
      { key: "baseline_repeats", default: "3", meaning: "Baseline re-runs used to measure noise." },
      { key: "bootstrap_samples", default: "2000", meaning: "Resamples for the confidence interval." },
      { key: "confidence", default: "0.95", meaning: "The interval of the median ratio must exclude 1.0 at this level." },
      { key: "exclusive", default: "true", meaning: "Benchmarks run alone. Set false only when tests and benchmarks use different resources." },
      { key: "rebenchmark_parent", default: "false", meaning: "Re-measure the parent before each candidate to cancel machine drift (doubles benchmark cost)." },
      { key: "required_workloads", default: "[]", meaning: "Workload IDs every benchmark must report." },
      { key: "min_workload_retention", default: "0.98", meaning: "Per-workload floor versus the parent." },
    ],
  },
  {
    group: "search",
    keys: [
      { key: "iterations", default: "3", meaning: "Plan → generate → verify rounds." },
      { key: "candidates_per_iteration", default: "3", meaning: "Hypotheses per round." },
      { key: "beam_width", default: "1", meaning: "Accepted heads kept and expanded each round; 1 is greedy." },
      { key: "max_patch_retries", default: "1", meaning: "Re-ask the worker once with the failure fed back; 0 disables." },
      { key: "max_parallel_workers", default: "4", meaning: "Concurrent worker model calls." },
      { key: "max_parallel_tests", default: "2", meaning: "Concurrent correctness runs." },
      { key: "planner_retries", default: "2", meaning: "Extra planner attempts after a transient API failure." },
    ],
  },
  {
    group: "provider",
    keys: [
      { key: "planner / worker", default: "mock", meaning: "openai (any OpenAI-compatible endpoint) or mock (recorded patches)." },
      { key: "planner_model", default: "gpt-4.1", meaning: "One call per iteration; keep it strong." },
      { key: "worker_model", default: "gpt-4.1-mini", meaning: "Many parallel calls; fast and cheap is the point." },
      { key: "worker_base_url", default: "none", meaning: "OpenAI-compatible endpoint for workers, e.g. https://inference.baseten.co/v1." },
      { key: "planner_api_key_env / worker_api_key_env", default: "OPENAI_API_KEY", meaning: "Which environment variable holds each key." },
      { key: "mock_patches_dir", default: "none", meaning: "Recorded patches for offline runs." },
    ],
  },
  {
    group: "execution",
    keys: [
      { key: "backend", default: "docker", meaning: "docker (untrusted code, fails closed) or local (trusted code only)." },
      { key: "image", default: "hotpath-runner:local", meaning: "Reviewed runner image; pin a digest for real use." },
      { key: "cpus / memory_mb / pids_limit", default: "2 / 4096 / 128", meaning: "Container quotas." },
      { key: "gpu", default: "none", meaning: "e.g. device=0 for NVIDIA." },
      { key: "devices / group_add", default: "[]", meaning: "Device passthrough, e.g. /dev/kfd and /dev/dri for ROCm." },
    ],
  },
  {
    group: "timeouts, profile, context",
    keys: [
      { key: "timeouts.test / bench / profile / model", default: "300 / 900 / 300 / 180 s", meaning: "Hard limits; a timeout becomes a structured verdict, never a crash." },
      { key: "profile.retain", default: "40", meaning: "Hotspot rows stored for the before/after diff." },
      { key: "context.max_hotspots", default: "12", meaning: "Hotspot rows shown to the planner." },
      { key: "context.max_source_chars", default: "14000", meaning: "Source budget per worker (go sizes it to the repository)." },
    ],
  },
];

export type EnvVar = { name: string; meaning: string };

export const ENV_VARS: EnvVar[] = [
  { name: "OPENAI_API_KEY", meaning: "Planner key (and worker key unless workers use Baseten)." },
  { name: "BASETEN_API_KEY", meaning: "When set, hotpath go and init put workers on Baseten (moonshotai/Kimi-K2.7-Code); the planner stays on OpenAI." },
  { name: "GITHUB_TOKEN / GH_TOKEN", meaning: "Opens PRs when the gh CLI is not logged in. Without either, Hotpath prints a pre-filled PR link." },
  { name: "HOTPATH_HOME", meaning: "Where user-level keys live (default ~/.hotpath; keys go in its .env)." },
  { name: "HOTPATH_TORCH_DEVICE", meaning: "Force cuda, xpu, mps or cpu for the bundled PyTorch helpers." },
  { name: "HOTPATH_AUTOCOMMIT", meaning: "Same as run --autocommit: snapshot uncommitted target changes instead of refusing." },
  { name: "SENTRY_DSN", meaning: "Optional tracing, logs and metrics. Empty means telemetry is off." },
  { name: "HOTPATH_DISABLE_SENTRY", meaning: "Force telemetry off even if a DSN is present." },
];

export type Platform = { host: string; local: string; docker: string };

export const PLATFORMS: Platform[] = [
  { host: "Windows / Linux / macOS CPU", local: "Supported", docker: "Supported (Linux containers)" },
  { host: "NVIDIA (Windows, Linux)", local: "PyTorch CUDA", docker: "execution.gpu: device=0 with the NVIDIA container toolkit" },
  { host: "AMD (Linux)", local: "PyTorch ROCm", docker: "devices: [/dev/kfd, /dev/dri], group_add: [video, render]" },
  { host: "Intel GPU (Linux)", local: "PyTorch XPU", docker: "devices: [/dev/dri]" },
  { host: "Apple GPU (macOS)", local: "PyTorch MPS", docker: "Not available (Docker cannot expose Metal)" },
];

export type Trouble = { symptom: string; fix: string };

export const TROUBLESHOOTING: Trouble[] = [
  { symptom: "stopped at [1/9] Setup: OPENAI_API_KEY is not set", fix: "Set the key in your shell, or add it to ~/.hotpath/.env. To try Hotpath with no key at all: --provider mock." },
  { symptom: "OPENAI_API_KEY was rejected by the API", fix: "The key is revoked or mistyped. hotpath doctor --verify-keys checks every configured key." },
  { symptom: "stopped at [4/9] Baseline: … Docker is not reachable", fix: "Start Docker Desktop (or the daemon) until docker info succeeds. For a repository you trust, --sandbox local runs it on the host." },
  { symptom: "no consent to run the repository's code on this machine", fix: "Docker was not available and nobody could answer the prompt. Start Docker, or pass --sandbox local / --yes for trusted code." },
  { symptom: "The baseline tests fail or are flaky", fix: "Hotpath refuses to measure against a suite that is not green 3/3. Narrow it with --test-cmd (e.g. deselect network or timing-dependent tests)." },
  { symptom: "Hypothesis deadline errors in the baseline", fix: "Property tests with a deadline fail under load. Deselect them with --test-cmd, or add a conftest profile with deadline=None." },
  { symptom: "Every candidate is patch_failed", fix: "The worker could not write exact search/replace text. Try a stronger worker_model; hotpath go already sizes the source budget to the repository." },
  { symptom: "Nothing accepted: all rejected_speed", fix: "That is a result, not an error. A noisy benchmark raises the bar; a narrower benchmark over the hot function (or rebenchmark_parent: true) measures smaller wins." },
  { symptom: "no pull request will be opened: no 'origin' remote", fix: "Add a remote, or use --no-pr / hotpath export for a local bundle." },
  { symptom: "Hotpath hit an unexpected error at [n/9] …", fix: "Rerun with hotpath -v go … for the traceback, add --resume to continue, and please open an issue." },
  { symptom: "~20 tests fail with No module named 'hotpath'", fix: "Contributors: run python -m pytest from the interpreter Hotpath is installed into (activate the venv first)." },
];

export type Faq = { q: string; a: string };

export const FAQ: Faq[] = [
  { q: "Can a model's claim ever get a change accepted?", a: "No. Correctness is the exit code of your locked test command; speed is a bootstrap confidence interval over repeated benchmark trials against a measured noise floor. The models only propose." },
  { q: "What code is sent to the model providers?", a: "The profile summary, run history, and the source of editable files. Locked files and symlinks are never sent. Keys never enter containers, commits or prompts." },
  { q: "What does a run cost?", a: "hotpath check prices it before you start (tokens and an approximate dollar figure). --max-tokens and --max-minutes stop the search cleanly and keep what has been proved." },
  { q: "What if nothing gets faster?", a: "Then nothing is shipped and no PR is opened. The dashboard keeps every candidate with the reason it was rejected, because that is the honest result." },
  { q: "Will it push to my main branch?", a: "Never. It pushes a hotpath/* branch after you confirm and opens a draft PR. Your checkout is never modified; commits are rebuilt from the exact trees the harness tested." },
  { q: "Does it work on GPU code?", a: "Yes, through the bundled PyTorch helpers (CUDA events, torch.profiler) on CUDA, ROCm, XPU and MPS. A GPU result is evidence only for the device, driver and workload it was measured on." },
];
