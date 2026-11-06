# lake_integrity

Keeps the Iceberg lake a **complete source of truth** for ClickHouse, so a
ClickHouse that is wiped, replaced or rebuilt on a new machine loses nothing.

## Why it exists

ClickHouse is a disposable cache; the lake is the ground truth. That only holds
if every bar ClickHouse has also reached the lake. The 2026-11-05 audit found it
had not: a day the live writer missed (2026-07-01, ~91k bars) and a family it
never covered (futures, ~20k bars) existed **only** in a laptop ClickHouse, and
Schwab's 1-minute history reaches back ~48 days, so they could not be re-fetched.

## What it owns

| Piece | File | Role |
|---|---|---|
| `LakeArchive` | `service.py` | Finds minute bars ClickHouse holds and the lake lacks (exact `(symbol, timestamp)` set difference per UTC day); optionally appends them |
| Adapters | `adapters.py` | ClickHouse reader (`FINAL`, source-filtered), Iceberg key index (week-chunked scan), Iceberg appender (commit-conflict retry, never creates tables) |
| `StateSnapshot` | `snapshot.py` | Exports every ClickHouse table the lake cannot rebuild (plus `data/paper` files and per-symbol bar coverage) to S3; restores them |
| Bootstrap logic | `bootstrap.py` | Pure: which window each symbol needs when rebuilding bars, and verification against recorded coverage |
| Nightly jobs | `jobs.py` | `lake_completeness_guard` (23:00 UTC) and `ch_state_snapshot` (01:00 UTC), audited like the other nightlies |
| DTOs | `schemas.py` | `BarFamily` (`EQUITIES`, `FUTURES`), `Bar`, `GapReport`, `SnapshotManifest`, `CoverageRow` |
| Protocols | `contract.py` | `ChBarSource`, `LakeBarIndex`, `LakeBarWriter`, `ChStateClient`, `ObjectStore` |

Only bars whose ClickHouse `source` has **no other copy** are checked
(`BarFamily.sources`: `schwab-stream`, `schwab-tipfill`, `silver-schwab`).
Rows tagged `lake-*` came from the lake by definition.

## Guarantees

- **Exact, not a count.** A lake with extra bars cannot hide a missing one.
- **Dry run by default.** Nothing is written without `apply=True` / `--apply`.
- **Idempotent.** A second apply finds nothing missing.
- **Verified from the lake side.** Each append is re-read with `fresh=True`; a
  write that is not visible is reported as an error, never as done.
- **Isolated.** One failing day is recorded; the other days still run.

## Futures timestamps

Schwab's `CHART_FUTURES` stream stamps a bar with its **arrival** time: one minute
after the bar it describes, plus 1-3 s of delay (ClickHouse `15:01:03.228` carries
the prices of the lake's `15:00:00` bar; 98% price-match at -1 minute, measured
2026-11-05). `BarFamily.floor_to_minute` / `bar_minute_offset` normalise futures
bars before comparing and before writing, so the archive neither reports the
same bar as missing nor writes a duplicate. Equities streams are minute-aligned.
See `docs/ISSUES.md` (`futures-stream-bars-stamped-at-arrival`).

## State snapshot

`s3://$STOCK_LAKE_BUCKET/ch_state/<snapshot_id>/` holds one Parquet file per
table, the paper-trading files, and `manifest.json` (per table: rows, bytes,
sha256, `SHOW CREATE TABLE`; plus per-symbol coverage of the bar tables).
`ch_state/latest.json` is written **last**, and only for a complete snapshot.

- **Safe by default.** Every table is backed up except the explicit
  `EXCLUDED_TABLES` (`ohlcv_1m`, `futures_ohlcv_1m`, `ohlcv_1m_candidates`,
  `ohlcv_hourly`). A table added later is included automatically.
- **Shrink guard.** A snapshot whose table holds under 50% of the previous
  snapshot's rows is **not published** (an empty or half-built ClickHouse must
  not replace a good backup). `--force` overrides deliberately.
- **Restore never mixes.** A non-empty table is skipped unless `--mode replace`.
  Every object's sha256 is verified before insert.
- **Retention.** `prune` keeps the newest 14; the `latest` target is never deleted.

```bash
poetry run python scripts/ch_state_snapshot.py export
poetry run python scripts/ch_state_snapshot.py restore --target-database stocks_restore_test   # rehearsal
```

## Rebuild a ClickHouse from S3

```bash
poetry run python scripts/ch_bootstrap.py --dry-run             # the plan
poetry run python scripts/ch_bootstrap.py --state-mode replace  # schema -> state -> bars -> futures -> daily -> verify
```

Each symbol is rebuilt over the window its **recorded** history needs (the
2006-era names start before the lake loader's 2021 default). Verification asks
"at least what the source ClickHouse had" (the lake may hold more), and exits 2
on any shortfall.

## Roll back an archive write

`scripts/lake_archive_ch.py --apply` appends only. To undo it, roll the Iceberg
table back to the snapshot recorded before the write:

```python
from app.services.iceberg_catalog import get_catalog
t = get_catalog().load_table("equities.schwab_universe")
t.manage_snapshots().rollback_to_snapshot(<snapshot_id>).commit()
```

## Use

```bash
export AWS_PROFILE=stock-lake
poetry run python scripts/lake_archive_ch.py --family all            # what is missing (writes nothing)
poetry run python scripts/lake_archive_ch.py --family all --apply    # archive + verify
```

Exit codes: `0` complete, `3` dry run found gaps, `2` error / unverified write.
Run it on the machine whose ClickHouse holds the data, **before** wiping or
replacing that ClickHouse.

## Test

```bash
poetry run pytest app/services/lake_integrity -q
```

Every collaborator is a fake; no test touches ClickHouse, Glue or S3.
