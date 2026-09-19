# H100 results — 2026-09-19

`targets/torch_transformer` (the tiny stand-in, not Dryft's model) optimized by Hotpath on a Baseten
H100 workstation (NVIDIA H100 80GB HBM3, torch 2.11+cu128). Planner: OpenAI `gpt-4.1`. Worker:
Baseten `moonshotai/Kimi-K2.7-Code`. Config: `configs/dryft_local.yaml` (6 iterations, beam 2).

| Run | Baseline tok/s | Optimized tok/s | Speedup | Shipped | Accepted |
|-----|---------------:|----------------:|--------:|--------:|---------:|
| 1   | 965            | 1,409           | 1.460x  | 3       | 10 / 51  |
| 2   | 916            | 1,263           | 1.379x  | 2       | 4 / 57   |
| 3   | 966            | 1,417           | 1.468x  | 1       | 4 / 42   |

Independent re-check after the runs, outside Hotpath (each `optimized_src/` with its locked
`tests/check.py` and `bench.py`, byte-identical to the originals): baseline 904 tok/s; runs 1–3 at
1,376 / 1,237 / 1,381 tok/s, all passing the correctness gate.

- `submission/runN/REPORT.md` — accepted chain, every candidate with its verdict, ablation
- `submission/runN/changes.patch`, `optimized_src/` — the shipped code
- `runs/` — raw logs. `runs/old/` is a first attempt where every worker call failed (a CRLF in the
  copied `.env` put `\r` in the Baseten key); kept for the record, not a result.
- `.hotpath/hotpath.db` — the run store; `hotpath serve --db <path>` to browse it.
