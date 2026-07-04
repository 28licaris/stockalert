"""
Export cash-dividend ex-dates from the lake for EXP-53 (H-44/H-45).

Scans `equities.market_corp_actions` (action_type == cash_dividend) and
writes symbol/ex_date/cash_amount to data/mcpt/dividends_ex_dates.parquet
— the input consumed by the dividend_runup / dividend_ex_drift families
in scripts/mcpt_insample.py. Regenerable any time; the parquet is not
committed (data/ is gitignored).

  poetry run python scripts/export_dividend_dates.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.config  # noqa: F401  (AWS env normalization side-effect)
from app.services.equities.schemas import equities_table_id  # noqa: E402
from app.services.iceberg_catalog import get_catalog  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "data" / "mcpt" / "dividends_ex_dates.parquet"


def main() -> int:
    table = get_catalog().load_table(equities_table_id("market_corp_actions"))
    df = table.scan(
        row_filter="action_type == 'cash_dividend'",
        selected_fields=("symbol", "ex_date", "action_type", "cash_amount"),
    ).to_arrow().to_pandas()
    if df.empty:
        raise SystemExit("no cash_dividend rows found — refusing to write an empty export")
    print(f"rows={len(df)} symbols={df['symbol'].nunique()} "
          f"span={df['ex_date'].min()}..{df['ex_date'].max()}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.sort_values(["symbol", "ex_date"]).to_parquet(OUT, index=False)
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
