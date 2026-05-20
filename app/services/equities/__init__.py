"""Architecture-v2 equities namespace (Iceberg tables at `lake.equities.*`).

See docs/architecture_v2/ for the full design. Module scope:
  - schemas.py — Iceberg Schema / PartitionSpec / SortOrder definitions
  - tables.py  — idempotent `ensure_*()` table creators
"""
