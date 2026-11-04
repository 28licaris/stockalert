# hooks/

Cross-page React hooks. Page-specific hooks live next to their route;
auth hooks live in [`../auth/`](../auth/README.md).

| File | Owns |
|---|---|
| [useDebouncedValue.ts](useDebouncedValue.ts) | Debounces a changing value — symbol search, filter inputs |
| [useQuotaMutation.ts](useQuotaMutation.ts) | Wraps a mutation with plan-quota handling, surfacing quota errors as UI state rather than thrown errors |

Add a hook here only when **more than one route** uses it. A single-consumer
hook belongs beside its consumer, where it can be deleted with it.
