# Baseten setup (workers on Baseten)

Hotpath routes its **worker** model calls (the high-volume patch generation) through Baseten's
OpenAI-compatible Model API, while the **planner** stays on OpenAI. That is the "big model plans,
fast model explores" pattern — and the workers are what spend Baseten credits.

## 1. Account + credits (once per team workspace)
1. Create a Baseten account at https://app.baseten.co.
2. Dashboard → **Billing and usage** → **Redeem promo credits** → paste the code from
   `#spons-baseten-2026`. Credits apply to the *workspace*; only the workspace creator redeems, once.

## 2. API key
Dashboard → **API keys** → create one. Do **not** commit it.

## 3. Put the key in `.env` (gitignored — never in chat or Git)
Add this line to `hotpath/.env`:
```
BASETEN_API_KEY=your-key-here
```
(`.env` is already gitignored. `OPENAI_API_KEY` stays there too for the planner.)

## 4. Verify the key + pick a worker model
List the live catalog and choose a strong instruct/coding model for the worker:
```bash
curl https://inference.baseten.co/v1/models -H "Authorization: Bearer $BASETEN_API_KEY"
```
Good worker candidates: a GLM / DeepSeek / Kimi / GPT-OSS / Qwen-Coder instruct model. Put the exact
slug in the config's `worker_model` (default in the configs is `zai-org/GLM-5.3` — confirm it exists).

Quick one-call smoke test:
```bash
python - <<'PY'
import os
from openai import OpenAI
c = OpenAI(api_key=os.environ["BASETEN_API_KEY"], base_url="https://inference.baseten.co/v1")
r = c.chat.completions.create(model="zai-org/GLM-5.3",
    messages=[{"role":"user","content":"reply with the word ok"}])
print(r.choices[0].message.content)
PY
```

## 5. Run Hotpath with Baseten workers
```bash
# fast CPU validation (no GPU needed):
hotpath run configs/demo_repo_baseten.yaml --autocommit --iterations 1
# full run:
hotpath run configs/demo_repo_baseten.yaml --autocommit
```
The run log shows workers as `compat:<model>` (planner stays `openai:gpt-4.1`).

## Structured-output note (the one thing that can break)
The provider asks for structured JSON via OpenAI's `beta.chat.completions.parse`. Most Baseten
models support JSON-schema structured outputs, but some don't. If workers come back as
`patch_failed` with a parse/`response_format` error:
- Pick a model that advertises **structured outputs** in the catalog, or
- Ask Claude to enable the JSON-mode fallback in `hotpath/providers/openai_provider.py`
  (try `.parse()`, then fall back to `response_format={"type":"json_object"}` + schema-in-prompt).

## Rate limits (HTTP 429)
Keep `search.max_parallel_workers` modest (4 is fine), retry with backoff, and submit the event
rate-limit form from the attendee channel if you hit workspace limits.

## Going full-Baseten (optional)
To route the *planner* through Baseten too (no OpenAI needed), the provider needs a `planner_base_url`
field — ask Claude to add it. Keep the planner on a strong model; it does one call per iteration.
