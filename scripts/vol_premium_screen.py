"""Run the Vol Premium screen and print the ranked table.

    poetry run python scripts/vol_premium_screen.py
    poetry run python scripts/vol_premium_screen.py --source lake --limit 15
    poetry run python scripts/vol_premium_screen.py --symbols SPY,QQQ,NVDA --json out.json

Two chain sources:

  ch    (default) `options_contracts_latest` — the production path.
  lake  re-parse `options.schwab_chain_raw` in memory with the current parser.

`lake` exists because option quotes were silently NULLed in ClickHouse until the
2026-08-09 parser fix (the `bidPrice` vs `bid` mapping). Until a fresh snapshot
or the reparse backfill lands, `ch` has no bids and the screen will correctly
refuse every symbol. `lake` proves the pipeline end-to-end on real stored
payloads without committing to a write.

Spec: `docs/vol_premium_screen_spec.md`.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from app.services.vol_premium.iv import ChainQuote  # noqa: E402
from app.services.vol_premium.schemas import ScreenParams  # noqa: E402
from app.services.vol_premium.service import VolPremiumService  # noqa: E402


def _lake_chains(symbols):
    """Re-parse the newest stored raw payload per symbol."""
    import json as _json

    from app.services.iceberg_catalog import get_catalog
    from app.services.options.parser import parse_schwab_option_chain
    from app.services.options.tables import CHAIN_RAW_TABLE_NAME

    tbl = get_catalog().load_table(f"options.{CHAIN_RAW_TABLE_NAME}")
    rows = tbl.scan(
        selected_fields=("underlying_symbol", "snapshot_ts", "raw_payload")
    ).to_arrow().to_pylist()

    newest: dict[str, dict] = {}
    for r in rows:
        s = r["underlying_symbol"]
        if s in symbols and (s not in newest or r["snapshot_ts"] > newest[s]["snapshot_ts"]):
            newest[s] = r

    out: dict[str, list[ChainQuote]] = defaultdict(list)
    for sym, r in newest.items():
        parsed = parse_schwab_option_chain(
            _json.loads(r["raw_payload"]), snapshot_ts=r["snapshot_ts"]
        )
        for c in parsed.contracts:
            if c.days_to_expiration is None:
                continue
            out[sym].append(
                ChainQuote(
                    expiration_date=c.expiration_date,
                    days_to_expiration=int(c.days_to_expiration),
                    strike=float(c.strike),
                    put_call=str(c.put_call),
                    implied_vol=c.volatility,
                    bid=c.bid, ask=c.ask, mark=c.mark,
                    open_interest=c.open_interest,
                    underlying_price=c.underlying_price,
                    snapshot_ts=r["snapshot_ts"],
                )
            )
    return dict(out)


def _lake_universe():
    from app.services.iceberg_catalog import get_catalog
    from app.services.options.tables import CHAIN_RAW_TABLE_NAME

    tbl = get_catalog().load_table(f"options.{CHAIN_RAW_TABLE_NAME}")
    arr = tbl.scan(selected_fields=("underlying_symbol",)).to_arrow()
    return sorted({s for s in arr.column("underlying_symbol").to_pylist()})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="ch", choices=["ch", "lake"])
    ap.add_argument("--symbols", default="", help="comma list; default = full universe")
    ap.add_argument("--target-dte", type=int, default=30)
    ap.add_argument("--min-oi", type=int, default=10)
    ap.add_argument("--max-spread", type=float, default=0.25)
    ap.add_argument("--freshness-hours", type=int, default=36)
    ap.add_argument("--limit", type=int, default=0, help="print only the top N")
    ap.add_argument("--json", default="", help="write the full screen here")
    a = ap.parse_args(argv)

    params = ScreenParams(
        target_dte=a.target_dte,
        min_open_interest=a.min_oi,
        max_rel_spread=a.max_spread,
        freshness_hours=a.freshness_hours,
    )

    if a.source == "lake":
        from app.services.vol_premium.sources import load_closes

        svc = VolPremiumService(
            chains_fn=_lake_chains, closes_fn=load_closes, universe_fn=_lake_universe
        )
    else:
        svc = VolPremiumService.from_settings()

    symbols = [s.strip().upper() for s in a.symbols.split(",") if s.strip()] or None
    screen = svc.build_screen(symbols, params)

    print(
        f"Vol Premium · source={a.source} · target {a.target_dte}d · "
        f"{len(screen.rows)}/{screen.universe_size} symbols priced · "
        f"{screen.as_of:%Y-%m-%d %H:%M} UTC\n"
    )
    print(f"  {'SYM':<6}{'SPOT':>9}  {'EXP':>4}  {'IV':>7}  {'FCAST':>7}  "
          f"{'RATIO':>7}  {'%ILE':>6}  {'GAP':>7}  {'N':>3} {'SPRD':>6}  AGE")
    print("  " + "─" * 84)

    rows = screen.rows[: a.limit] if a.limit else screen.rows
    for r in rows:
        # "—" not "50" when there is no baseline: no history and an
        # unremarkable reading are opposite claims.
        pct = f"{r.premium_percentile:5.0f}" if r.premium_percentile is not None else "    —"
        print(f"  {r.symbol:<6}{r.spot:9.2f}  {r.days_to_expiration:3}d  "
              f"{r.implied_vol:6.1f}%  {r.forecast_vol:6.1f}%  "
              f"{r.vrp_ratio:6.2f}x  {pct}  {r.vrp_points:+6.1f}  "
              f"{r.contracts_used:3} {r.median_rel_spread*100:5.1f}%  "
              f"{r.snapshot_age_hours:.0f}h")

    if screen.rows:
        rr = [r.vrp_ratio for r in screen.rows]
        print(f"\n  ratio: min {min(rr):.2f}x · median {sorted(rr)[len(rr)//2]:.2f}x "
              f"· max {max(rr):.2f}x")

    if screen.skipped:
        print(f"\n  skipped {len(screen.skipped)}:")
        by_status: dict[str, list[str]] = defaultdict(list)
        for s in screen.skipped:
            by_status[s.status].append(s.symbol)
        for st, syms in sorted(by_status.items()):
            print(f"    {st:12} {len(syms):3}  {', '.join(sorted(syms)[:12])}")
            example = next(s for s in screen.skipped if s.status == st)
            print(f"                    e.g. {example.symbol}: {example.reason[:96]}")

    withpct = [r for r in screen.rows if r.premium_percentile is not None]
    if withpct:
        print(f"\n  %ILE = where today's ratio sits in that symbol's OWN history "
              f"({min(r.baseline_days for r in withpct):,}-"
              f"{max(r.baseline_days for r in withpct):,} days).")
        print("  A positive premium is NORMAL, so the ratio alone cannot say whether")
        print("  today is unusual — the percentile is the reading that can.")
        rich = [r for r in withpct if r.premium_percentile >= 90]
        cheap = [r for r in withpct if r.premium_percentile <= 10]
        if rich:
            print(f"  >=90th (options rich vs own history): "
                  f"{', '.join(f'{r.symbol} {r.premium_percentile:.0f}' for r in rich[:8])}")
        if cheap:
            print(f"  <=10th (options cheap vs own history): "
                  f"{', '.join(f'{r.symbol} {r.premium_percentile:.0f}' for r in cheap[:8])}")
    else:
        print("\n  No baseline available — run scripts/build_vol_premium_history.py.")
        print("  Without it this ranks symbols against each other only; a positive")
        print("  premium is normal, so a bare ratio cannot say whether today is unusual.")

    if a.json:
        Path(a.json).write_text(screen.model_dump_json(indent=2))
        print(f"\n  wrote {a.json}")

    return 0 if screen.rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
