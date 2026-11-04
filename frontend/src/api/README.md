# api/

The **only** sanctioned path from components to the backend. No component
should call `fetch` directly.

## Layout

| File | Owns |
|---|---|
| [types.gen.ts](types.gen.ts) | **Auto-generated** from the FastAPI OpenAPI schema. Never hand-edit |
| [client.ts](client.ts) | `openapi-fetch` client typed by `types.gen.ts`, plus the auth and error middleware |
| [queryClient.ts](queryClient.ts) | TanStack Query client config (retry, stale times) |
| [queries.ts](queries.ts) | Shared query hooks + query-key factories |
| [assistant.ts](assistant.ts) | Assistant SSE stream client |
| [backtest.ts](backtest.ts) | Backtest run submission + polling |
| [library.ts](library.ts) | Strategy library |
| [wave.ts](wave.ts) | Elliott Wave state reads |

## The closed type chain

This is the point of the module: endpoint shapes are **never** hand-declared.

```
FastAPI route + Pydantic response_model
  → /openapi.json
    → openapi-typescript  (npm run codegen)
      → src/api/types.gen.ts
        → openapi-fetch client  (compile-time checked paths + payloads)
```

A backend shape change that breaks the SPA becomes a **TypeScript error**, not
a runtime surprise. Hand-rolled interfaces defeat this entirely.

### Regenerating

```bash
npm run codegen        # needs the API running on :8000
npm run codegen:watch
```

Run this after **any** route or `response_model` change and commit the result —
a stale `types.gen.ts` is how a contract drift ships silently. Contract
reference: [`../../../docs/product/api_contracts.md`](../../../docs/product/api_contracts.md).

## Conventions

- **Relative paths, no env wiring.** Same-origin in production (FastAPI serves
  the SPA at `/app` and the API at `/`); in dev, Vite proxies `/api/v1`, `/mcp`,
  `/ws` and `/openapi.json` to the FastAPI process.
- **`/api/v1/*` only.** Legacy `/api/*` paths still 307-redirect for the old
  static pages; new code must never use them.
- **Errors are envelopes.** `readErrorEnvelope` / `ApiError` in
  [`../lib/errors.ts`](../lib/errors.ts) normalize backend error bodies — do
  not parse error JSON at a call site.
- **Server state belongs to TanStack Query**, never to a component `useState`.
