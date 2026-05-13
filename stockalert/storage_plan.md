# Stock Lake — Storage Plan

## Bucket

- **One bucket:** `stock-lake` (or `stock-lake-gtg` if the name is taken globally)
- **Region:** `us-east-1` (cheapest, most services). Pick once, stick with it.
- **Block all public access:** ON
- **Versioning:** ON (protects against bad overwrites)
- **Default encryption:** SSE-S3 (default, free)

One bucket is the right call. Separate by *prefix*, not by bucket. Multiple buckets only matter when you need different regions, encryption keys, or access boundaries — none apply here.

## Layout

```
s3://stock-lake/
├── raw/                           ← immutable, provider-specific, source of truth
│   ├── provider=polygon/
│   │   └── asset_class=equities/
│   │       └── year=2025/month=05/
│   │           └── data.parquet   ← all tickers for the month
│   ├── provider=databento/
│   │   └── ...
│   └── provider=alpaca/
│       └── ...
│
├── curated/                       ← cleaned, gap-filled, primary version for analysis
│   └── asset_class=equities/
│       └── year=2025/month=05/
│           └── data.parquet
│
└── staging/                       ← daily drops before monthly compaction
    └── provider=polygon/year=2025/month=05/day=13/
        └── data.parquet
```

**Rules:**
- `raw/` is **write-once, never edited**. Keeps each provider's data exactly as delivered.
- `curated/` is your **source of truth for backtests**. Merge providers here deliberately, with a `source_provider` column tracking origin per bar.
- `staging/` holds daily ingests; a weekly job compacts them into monthly files in `raw/`.

## Partitioning

- **Partition by:** `year/month/` (Hive-style)
- **Symbol:** stored as a *column inside* the Parquet file, not as a partition
- **File format:** Parquet with Snappy compression
- **Why monthly, not daily:** daily files are ~12 KB per ticker → too small, hurts query performance. Monthly bundles 100 tickers into ~25 MB files → ideal size, ~240 partitions over 20 years.

## Provider strategy

- **Keep raw data separated by provider.** Different providers disagree on minute bars (consolidation rules, timestamp conventions, SIP vs direct feeds).
- **Mixing providers is fine — but do it in `curated/`, not `raw/`.** Pick a primary; fill gaps from a secondary; tag every bar with its source.
- Never blend silently — you'll lose the ability to reproduce or debug.

## Cost estimate (20 years × 100 tickers × 1-min bars)

| Item | Volume | Cost/month |
|---|---|---|
| Storage (Parquet, ~6–10 GB) | S3 Standard | $0.15–$0.25 |
| After lifecycle → IA (30d) | | $0.08–$0.13 |
| After lifecycle → Glacier IR (90d) | | $0.02–$0.04 |
| One-time PUTs (~500K) | $0.005/1K | ~$2.50 |

**Storage is essentially free at this scale.** Don't optimize for bytes — optimize for query speed and data quality.

## Lifecycle rules

1. Transition `raw/` and `curated/` to **Standard-IA after 30 days**
2. Transition to **Glacier Instant Retrieval after 180 days**
3. Expire incomplete multipart uploads after **7 days**
4. (Optional) Expire old versions after 90 days to control versioning costs

## IAM policy (scoped to this bucket)

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:GetObject"],
      "Resource": "arn:aws:s3:::stock-lake/*"
    },
    {
      "Effect": "Allow",
      "Action": ["s3:ListBucket", "s3:GetBucketLocation"],
      "Resource": "arn:aws:s3:::stock-lake"
    }
  ]
}
```

Add `s3:DeleteObject` only if your ingestion needs to overwrite. Safer to omit.

## Ingestion pattern

1. Daily script pulls bars from each provider → writes to `staging/provider=X/year=YYYY/month=MM/day=DD/`
2. Weekly (or monthly) compaction job reads `staging/` for the closed month, writes a single Parquet file to `raw/provider=X/year=YYYY/month=MM/`, deletes staging files
3. Curation job reads `raw/`, applies provider selection + gap-filling, writes to `curated/`

## Query layer (when ready)

- **Athena** — SQL over S3 directly, $5/TB scanned. Partitioning + Parquet keeps scans tiny.
- **DuckDB** — free, runs locally, reads Parquet from S3 natively. Great for backtesting.
- Register the bucket as a Glue table once, query from either.
