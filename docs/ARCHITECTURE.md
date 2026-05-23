# StockAlert — Architecture (redirect)

> **This file was deprecated 2026-05-23 during the v1→v2 cleanup.**
> The v1 bronze/silver/gold model it described was replaced by the v2
> `equities.*` Iceberg lake in CV1-CV29 (March-May 2026).

The system overview is now distributed across smaller, canonical docs:

| Topic | Canonical source |
|---|---|
| Lake schema, partitioning, S3 layout, ingestion paths | [`architecture_v2/`](architecture_v2/README.md) |
| Service folder map + commands + standards index | [`../CLAUDE.md`](../CLAUDE.md) |
| Operator procedures (cutover, restart, backfill) | [`architecture_v2/07_runbook.md`](architecture_v2/07_runbook.md) |
| Coding / engagement / testing rules | [`standards/`](standards/README.md) |
| Trading subsystem contract | [`trading_subsystem_design.md`](trading_subsystem_design.md) |
| AI services roadmap | [`trading-ai-build-plan.md`](trading-ai-build-plan.md) |

The original 618-line v1 overview is preserved in git history; recover
with `git log --all --diff-filter=D -- docs/ARCHITECTURE.md` if you need
the historical pipeline description.
