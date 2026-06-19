# Elliott Wave — As-Built Architecture

How the EWT system works today (EW-1 → EW-6, EW-8 shipped). This is the
implementation map; the design rationale lives in
[elliott_wave_system_spec.md](elliott_wave_system_spec.md) and the doctrine in
[`.claude/skills/elliott-wave/SKILL.md`](../.claude/skills/elliott-wave/SKILL.md).

## Pipeline

```
  Market bars (equities + futures lake · BarsGateway)
        │
        ▼
  PivotDetector            app/indicators/pivots.py
  (causal, multi-degree)   pivot at bar i is confirmed only at i+k
        │
        ▼
  WaveEngine               app/signals/elliott/ (pure, deterministic)
  rules + Fibonacci        primary + secondary counts, each with probability;
  + multi-degree synthesis leftover mass = honest "uncertainty"
        │   ▲
        │   └── compute (live edge bar)
        ▼
  elliott_wave_labels      app/services/elliott_store/ → Iceberg (equities.* / futures.*)
  (append-only store)      written by the nightly recompute job
        │
        ▼
  WaveReader               app/services/readers/wave_reader.py
  store · compute · auto   one Pydantic shape for every surface
        │
        ├──────────────┬───────────────┬──────────────┐
        ▼              ▼               ▼              ▼
   REST            MCP tools        Alerts          EWT page
  /api/v1/wave   get_wave_state   scan + R:R gates  chart overlay
                 evaluate_targets  (trade plans)    + count panel
                 list_wave_alerts      │            + scan list
                      │                 │                │
                      ▼                 ▼                ▼
                 Assistant (v2 prompt)            Operator browser
```

## Layer map

| Layer | Module | Role |
|---|---|---|
| Pivots | `app/indicators/pivots.py` | Causal multi-fractal `PivotDetector` (registered as `pivots`); `confirmed_at_index = i+k` is the no-look-ahead guarantee |
| Engine | `app/signals/elliott/` | `rules.py` (3 hard rules + zigzag), `fib.py` (anchor-free scoring + anchored targets), `engine.py` (`WaveEngine.label`). Pure — no I/O imports (purity-gated) |
| Store | `app/services/elliott_store/` | `elliott_wave_labels` Iceberg schema/sink + `recompute.py` (`compute_labeling`, `recompute_universe`, nightly loop) |
| Reader | `app/services/readers/wave_reader.py` | `WaveReader` — `store`/`compute`/`auto`; `get_state`, `get_history`, `list_latest` |
| HTTP | `app/api/routes_wave.py` | `GET /api/v1/wave/{symbol}`, `/history`, `/alerts` |
| MCP | `app/mcp/tools/wave.py` | `get_wave_state`, `evaluate_wave_targets`, `list_wave_alerts` |
| Alerts | `app/services/alerts/` | `WaveAlert` trade plans + `scan_alerts` (probability + R:R gates) |
| Frontend | `frontend/src/routes/ewt.tsx` + `OhlcvChart` overlay | Wave path + invalidation/target price lines + count panel + scan list |
| Agent | `app/services/assistant/prompts/v2.md` | Teaches the wave tools + doctrine (tools already allowed) |

## Invariants (enforced, tested)

- **No look-ahead** — a label at bar `t` uses only pivots confirmed by `t`;
  re-running with future bars yields a byte-identical label
  (`tests/test_elliott_no_lookahead.py`).
- **Deterministic** — same input → same output; ties break on pivot-index tuples.
- **Honest** — primary + secondary + an explicit uncertainty mass; the engine
  refuses to force a count (returns "no clear count") rather than fabricate.
- **Reproducible** — every stored row carries `engine_ver` + `git_sha`.
- **Pure engine** — `app/signals/elliott/` imports nothing from
  `app.db` / `app.providers` / `app.services` (`tests/test_elliott_purity.py`).

## Operations

- **Recompute (manual):** `poetry run python scripts/ewt_recompute.py AAPL NVDA TSLA`
- **Label one symbol (debug):** `poetry run python scripts/ewt_label.py AAPL 1d 400 4`
- **Nightly job:** registered in `main_api`, gated by `ELLIOTT_RECOMPUTE_ENABLED`
  (default OFF) + `ELLIOTT_RECOMPUTE_SYMBOLS` / `_RUN_HOUR_UTC` / `_INTERVALS`.

## Not yet built

EW-7 (intraday day-trade alerts via the live monitor), EW-9 (analyst-content
ingestion). Engine tuning deferred until nightly history accrues: confidence
calibration vs forward returns, cross-timeframe nesting, more structure types
(flats / triangles / diagonals).
