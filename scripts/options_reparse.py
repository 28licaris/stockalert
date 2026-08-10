"""Rebuild parsed option data from the stored raw Schwab payloads.

    poetry run python scripts/options_reparse.py --target ch --dry-run
    poetry run python scripts/options_reparse.py --target ch
    poetry run python scripts/options_reparse.py --target lake --dry-run

## Why this exists

Until 2026-08-09 the chain parser looked for `bidPrice` / `askPrice` /
`lastPrice` / `markPrice` — TD Ameritrade's legacy field names. Schwab sends
`bid` / `ask` / `last` / `mark`, so all four resolved to None on every contract
ever ingested, and both the ClickHouse hot table and the Iceberg canonical table
carry 100% NULL quotes. Implied volatility and the greeks were unaffected.

Nothing was lost: `options.schwab_chain_raw` keeps the provider payload verbatim
precisely so a parser defect is recoverable. This replays those payloads through
the corrected parser.

## Two targets, deliberately separate

`ch`   `options_contracts_latest` — a `ReplacingMergeTree` keyed on
       `(underlying_symbol, option_symbol)`, i.e. **one row per contract**. Only
       the newest payload per symbol is relevant, so this is ~40 payloads and
       seconds of work. This is what the Vol Premium screen reads, so it is what
       unblocks the product.

`lake` `options.schwab_chain_contracts` — the full replay/backtest history,
       ~1.6M rows over every stored payload. Needed for the eventual
       "is this reading unusual for this symbol" feature, not for shipping.

### On the ClickHouse version column

Normal ingest sets `version = snapshot_ts_ms`. Re-inserting a corrected row for
the same snapshot would collide at the same version, and `ReplacingMergeTree`
does not promise which of two equal-version rows survives a merge. So reparsed
rows are written with `version = reparse wall-clock ms`, which is strictly
greater than any existing row's version and strictly less than any future
snapshot's — corrected data wins now, and a genuinely newer chain still wins
later. Readers already use `FINAL`, so the fix takes effect without an OPTIMIZE.

Gamma exposure (`options_gex_latest`) is **not** rewritten: it is derived from
gamma and open interest, both of which parsed correctly all along.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.options.hot_sink import (  # noqa: E402
    CONTRACT_COLUMNS,
    CONTRACTS_TABLE,
    _contract_row,
)
from app.services.options.parser import parse_schwab_option_chain  # noqa: E402
from app.services.options.tables import (  # noqa: E402
    CHAIN_CONTRACTS_TABLE_NAME,
    CHAIN_RAW_TABLE_NAME,
)


def _load_raw(symbols: list[str] | None, newest_only: bool) -> list[dict]:
    """Raw payloads from the lake, optionally just the newest per symbol."""
    from app.services.iceberg_catalog import get_catalog

    tbl = get_catalog().load_table(f"options.{CHAIN_RAW_TABLE_NAME}")
    rows = tbl.scan(
        selected_fields=(
            "underlying_symbol",
            "snapshot_ts",
            "raw_payload",
            "status",
            # Schwab omits the top-level `symbol` on a good third of payloads;
            # the parser recovers it from the request that produced them. The
            # live ingest passes this, so a replay that does not would drop 31%
            # of the archive as "missing underlying symbol".
            "request_params",
        )
    ).to_arrow().to_pylist()

    if symbols:
        want = {s.upper() for s in symbols}
        rows = [r for r in rows if r["underlying_symbol"] in want]

    if not newest_only:
        return sorted(rows, key=lambda r: r["snapshot_ts"])

    newest: dict[str, dict] = {}
    for r in rows:
        s = r["underlying_symbol"]
        if s not in newest or r["snapshot_ts"] > newest[s]["snapshot_ts"]:
            newest[s] = r
    return sorted(newest.values(), key=lambda r: r["underlying_symbol"])


def _request_params(row: dict) -> dict:
    """The stored request, so the parser can recover an absent symbol.

    Falls back to the row's own `underlying_symbol` column, which the raw table
    records independently — a payload whose request params were never stored is
    still perfectly replayable.
    """
    raw = row.get("request_params")
    params: dict = {}
    if raw:
        try:
            decoded = json.loads(raw)
            if isinstance(decoded, dict):
                params = decoded
        except (TypeError, ValueError):
            params = {}
    params.setdefault("symbol", row["underlying_symbol"])
    return params


def _parse(rows: list[dict]) -> tuple[list, dict[str, dict]]:
    """Parse every payload. Returns (contracts, per-symbol stats)."""
    contracts: list = []
    stats: dict[str, dict] = defaultdict(lambda: {"payloads": 0, "contracts": 0,
                                                  "with_bid": 0, "failed": 0})
    for r in rows:
        sym = r["underlying_symbol"]
        st = stats[sym]
        st["payloads"] += 1
        try:
            res = parse_schwab_option_chain(
                json.loads(r["raw_payload"]),
                snapshot_ts=r["snapshot_ts"],
                request_params=_request_params(r),
            )
        except Exception as exc:                     # a bad payload must be visible
            st["failed"] += 1
            print(f"  ! {sym} {r['snapshot_ts']}: unparseable — {exc}")
            continue
        contracts.extend(res.contracts)
        st["contracts"] += len(res.contracts)
        st["with_bid"] += sum(1 for c in res.contracts if c.bid is not None and c.bid > 0)
    return contracts, dict(stats)


def _ch_before_after(client) -> tuple[int, int]:
    n, withbid = client.query(
        f"SELECT count(), countIf(bid > 0) FROM {CONTRACTS_TABLE} FINAL"
    ).result_rows[0]
    return int(n), int(withbid)


def reparse_ch(symbols, dry_run: bool) -> int:
    from app.db.client import get_client

    client = get_client()
    before_n, before_bid = _ch_before_after(client)
    print(f"BEFORE  {before_n:,} contracts · {before_bid:,} with a bid "
          f"({100*before_bid/max(1,before_n):.1f}%)\n")

    rows = _load_raw(symbols, newest_only=True)
    print(f"replaying the newest payload for {len(rows)} symbol(s)")
    contracts, stats = _parse(rows)

    total_bid = sum(s["with_bid"] for s in stats.values())
    print(f"parsed {len(contracts):,} contracts · {total_bid:,} carry a bid "
          f"({100*total_bid/max(1,len(contracts)):.1f}%)\n")

    if not contracts:
        print("nothing parsed — refusing to write"); return 1
    if total_bid == 0:
        print("parsed rows still have no bids — the fix is not in effect, refusing")
        return 1

    if dry_run:
        print("DRY RUN — no write. Per symbol:")
        for sym in sorted(stats):
            s = stats[sym]
            print(f"  {sym:6} {s['contracts']:5,} contracts  {s['with_bid']:5,} with bid"
                  + (f"  ({s['failed']} payload(s) failed)" if s["failed"] else ""))
        return 0

    now = datetime.now(timezone.utc)
    version = int(now.timestamp() * 1000)
    batch = [_contract_row(c, now, f"reparse-{now:%Y%m%dT%H%M%S}", version)
             for c in contracts]

    client.insert(CONTRACTS_TABLE, batch, column_names=CONTRACT_COLUMNS)
    print(f"inserted {len(batch):,} rows at version {version}")

    after_n, after_bid = _ch_before_after(client)
    print(f"\nAFTER   {after_n:,} contracts · {after_bid:,} with a bid "
          f"({100*after_bid/max(1,after_n):.1f}%)")

    # Cross-side verification: the write is only a success if the read changed.
    if after_bid <= before_bid:
        print("FAILED — quote coverage did not improve after the insert")
        return 1
    print(f"recovered quotes on {after_bid - before_bid:,} contracts")
    return 0


def reparse_lake(symbols, dry_run: bool) -> int:
    from app.services.iceberg_catalog import get_catalog
    from app.services.options.sink import _table_from_contracts, _upsert_if_rows

    rows = _load_raw(symbols, newest_only=False)
    print(f"replaying {len(rows):,} stored payload(s)")
    contracts, stats = _parse(rows)
    total_bid = sum(s["with_bid"] for s in stats.values())
    print(f"parsed {len(contracts):,} contracts · {total_bid:,} carry a bid "
          f"({100*total_bid/max(1,len(contracts)):.1f}%)")

    if not contracts or total_bid == 0:
        print("nothing usable parsed — refusing to write"); return 1
    if dry_run:
        print("DRY RUN — no write.")
        return 0

    now = datetime.now(timezone.utc)
    run_id = f"reparse-{now:%Y%m%dT%H%M%S}"
    tbl = get_catalog().load_table(f"options.{CHAIN_CONTRACTS_TABLE_NAME}")
    written = _upsert_if_rows(
        tbl, _table_from_contracts(contracts, now, run_id), "options_reparse"
    )
    print(f"upserted {written:,} rows into {CHAIN_CONTRACTS_TABLE_NAME} (run {run_id})")

    # Cross-side verification — a write that did not change the read is a
    # failure, however cleanly the call returned.
    check = tbl.scan(selected_fields=("bid",)).to_arrow()
    got = sum(1 for x in check.column("bid").to_pylist() if x is not None)
    print(f"canonical table now has {got:,} of {check.num_rows:,} rows with a bid "
          f"({100*got/max(1, check.num_rows):.1f}%)")
    if got == 0:
        print("FAILED — the canonical table still has no quotes")
        return 1
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default="ch", choices=["ch", "lake"])
    ap.add_argument("--symbols", default="", help="comma list; default = all")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    symbols = [s.strip().upper() for s in a.symbols.split(",") if s.strip()] or None
    print(f"Options reparse · target={a.target}"
          f"{' · DRY RUN' if a.dry_run else ''}\n")

    return (reparse_ch if a.target == "ch" else reparse_lake)(symbols, a.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
