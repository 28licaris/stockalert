"""Futures lake — Schwab CME futures OHLCV, separate from equities.

Mirrors `app/services/equities/` but simpler: futures have no splits /
dividends, so there's no adjustment tier (no polygon_adjusted, no Spark
adjustment job) — just one raw 1-minute table fed by the Schwab stream
(live) and Schwab REST (nightly). Stored by CONTINUOUS ROOT symbol
(/ES, /MES, …) as Schwab streams the front month.

Own Glue DB (`futures`) and S3 folder (`iceberg/futures/`).
"""
