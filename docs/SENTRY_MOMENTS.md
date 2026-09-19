# Sentry moments

A running log of moments where Sentry data changed what we built or how we ran the search. The
Sentry prize is judged on how meaningfully Sentry data influenced the project, so write an entry
**when it happens**, with a link, and never reconstruct one afterward. An entry without a real
trace, log, or metric behind it does not belong here.

## Template

```
### <date> <time> — <one-line title>
- Saw:      <what Sentry showed; link to the trace / log query / metric>
- Meant:    <what it told us that we did not know>
- Changed:  <the code, config, or run decision it led to; commit hash if any>
- Evidence: <before/after numbers, or the follow-up trace that confirms the fix>
```

## Entries

_None yet. No run has sent data to a real Sentry project so far; see `docs/SENTRY.md` for setup._

## Instrumentation notes (not Sentry-data moments)

- 2026-09-18 — While adding AI agent monitoring we found that Sentry's OpenAI integration never saw
  the planner or worker calls: `chat.completions.parse` posts directly and bypasses the patched
  `create`. We now emit `gen_ai.chat` spans ourselves (`hotpath/observability.py: ai_chat`). Found
  by inspecting SDK envelopes in `tests/test_observability.py`, not from Sentry data, so it is
  listed here rather than as an entry.
