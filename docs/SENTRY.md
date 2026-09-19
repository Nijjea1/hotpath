# Sentry

Hotpath treats Sentry as optional observability. Leaving `SENTRY_DSN` empty disables
telemetry; the optimization loop does not depend on Sentry.

## Local verification

Copy `.env.example` to `.env`, set `SENTRY_DSN` to the DSN for the Python/FastAPI
project that should receive the events, and restart the server:

```powershell
Copy-Item .env.example .env
notepad .env
.\.venv\Scripts\Activate.ps1
hotpath serve configs/demo_repo_openai.yaml
```

The server reads `.env` from the launch directory and does not overwrite environment
variables that are already exported. Check
`http://127.0.0.1:8765/api/observability` for `"enabled": true`, then request
`http://127.0.0.1:8765/sentry-debug` from the same machine. The debug endpoint is
loopback-only in `HOTPATH_ENV=development` and intentionally returns HTTP 500 after
emitting an error, transaction, verification span, logs, and metrics. Without a DSN,
it returns 503.

The repository tests cover SDK setup and envelope construction with an in-memory
transport. They do not prove delivery or visibility in a Sentry project. Confirm those
two properties in the target Sentry project after adding a real DSN.

## Settings

```dotenv
SENTRY_DSN=
HOTPATH_ENV=development
SENTRY_TRACES_SAMPLE_RATE=1.0
SENTRY_PROFILE_SESSION_SAMPLE_RATE=1.0
SENTRY_SEND_DEFAULT_PII=false
# SENTRY_RELEASE=hotpath@<version>
```

Hotpath records run and experiment spans, model usage, logs, and benchmark metrics.
Request bodies and frame-local variables are excluded, and configured credentials are
filtered. Sentry profiles the Hotpath process; target profiling remains the configured
cProfile or PyTorch profiler.

## What a run looks like in Sentry

| Product | What Hotpath sends | Where to look |
|---|---|---|
| Tracing | One `hotpath.run <config>` transaction (baseline, profiling, planning) and **one `hotpath.experiment` transaction per experiment**, containing its `hotpath.generate`, `hotpath.patch`, `hotpath.test` and `hotpath.bench` spans | Traces. Filter `hotpath.status:rejected_correctness` to open only the experiments whose output changed; `hotpath.run_id` groups one run |
| AI agent monitoring | Every planner and worker call as a `gen_ai.chat` span: model requested and served, input/output/total tokens, latency, and `gen_ai.agent.name` (`planner` or `worker`). `gen_ai.system` is `openai`, `baseten`, or `openai-compatible` | AI Agents / Insights |
| Logs | Planner decisions and hypotheses, worker rationale, each experiment's final verdict and reason | Logs, filtered by `run_id` |
| Metrics | Runs, best speedup, baseline noise, per-stage durations, per-experiment speedups | Metrics |
| Profiling | Continuous profiles of the Hotpath process itself | Profiles |

Tags on every experiment transaction: `hotpath.run_id`, `hotpath.experiment_id`, `hotpath.iteration`,
`hotpath.retry`, `hotpath.status`. Errors and timeouts also set the transaction status.

The gen_ai spans are recorded by Hotpath, not by Sentry's OpenAI integration: the planner and worker
call `chat.completions.parse`, which posts directly and bypasses the `create` method that integration
patches, so the integration alone would record nothing. Prompts and completions are not attached,
because they contain the target's source code. The SDK sends gen_ai spans as standalone span items
linked to the experiment's trace.

Record real debugging moments in `docs/SENTRY_MOMENTS.md` as they happen.
