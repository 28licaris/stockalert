# `app/services/clickhouse_query/` — Ad-hoc ClickHouse Query Service

Read-only CH query execution + schema introspection for the cockpit's
`/app/clickhouse` page. The "developer's cadillac" surface from
[docs/frontend_api_contracts.md §10.4](../../../docs/frontend_api_contracts.md).

## Files

```
clickhouse_query/
├── __init__.py            re-exports the singleton
├── query_service.py       execute / list_schema / invalidate_schema_cache
└── README.md              this file
```

## Safety contract

Five rails, listed in order of how robust they are:

| Rail | Where enforced | Resilient to |
|---|---|---|
| `readonly=1` CH setting | ClickHouse engine | Any DDL / DML / SET attempt |
| `max_bytes_to_read` (1 GiB) | ClickHouse engine | Unbounded full-table scan |
| `max_memory_usage` (4 GiB) | ClickHouse engine | Cartesian-join OOM |
| `max_execution_time` (≤120s, default 30s) | ClickHouse engine | Stuck / runaway queries |
| `max_result_rows` (≤30k, default 1000) | ClickHouse engine + defensive Python trim | Million-row dumps |

We do NOT rely on string-parsing the SQL to detect "evil keywords."
The CH engine itself rejects writes when `readonly=1` is set; that's
the security boundary. The Python ceilings are belt-and-suspenders.

## Schema cache

`list_schema()` caches its result in-process for 60 s. The cockpit hits
this on page load + every keystroke in the schema sidebar's filter, so
caching pays for itself. `invalidate_schema_cache()` is exposed for
tests and a future "refresh schema" button.

Hidden databases: `system`, `INFORMATION_SCHEMA`, `information_schema`.

## Truncation detection

CH's default `result_overflow_mode=break` silently truncates at
`max_result_rows`. To tell the cockpit when it happened, we ask for
`max_rows + 1` and trim if the extra came back. `truncated: True` in
the response signals to the UI that the user should narrow the query.

## What's deliberately NOT here

- **Separate `cockpit_readonly` CH user.** A dedicated user is a
  cleaner ops posture, but `readonly=1` at the query level gives the
  same engine-level safety with zero operator setup. We can add a
  dedicated user later without changing the service or the API.
- **SQL syntax highlighting / formatting.** Cockpit-side concern;
  service stays a thin client.
- **Query history persistence.** Cockpit's `useUserSetting` handles
  per-user recents on the frontend side; storing them server-side is a
  multi-tenant feature for the SaaS phase.
