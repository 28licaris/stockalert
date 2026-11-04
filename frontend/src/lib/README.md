# lib/

Dependency-free helpers. Nothing here imports from `api/`, `auth/`,
`components/` or `routes/` — if a helper needs those, it belongs with them.

| File | Owns |
|---|---|
| [errors.ts](errors.ts) | `ApiError`, `readErrorEnvelope` — normalizes backend error bodies |
| [timezone.ts](timezone.ts) | Display-timezone resolution + `TZ_OPTIONS` |
| [fmt.ts](fmt.ts) | Number, price, percent and date formatting |
| [storage.ts](storage.ts) | Guarded `localStorage` access |
| [roundTrips.ts](roundTrips.ts) | Trade round-trip pairing for equity/trade charts |
| [utils.ts](utils.ts) | `cn()` class merging and small generics |

## Two rules worth stating

**Timezone is display-only.** Bar timestamps arrive as UTC instants (ISO-8601
with `Z`). One global per-user setting (`chart.timezone`) drives both the chart
axis and the Recent Bars table so they cannot drift apart; `"local"` resolves
to `undefined`, which tells `Intl` to use the viewer's browser zone.

Do not confuse this with the **trading day**, which is ET 04:00-20:00 on the
backend. After-hours bars cross midnight UTC, so a UTC date misclassifies them
— see
[`../../../docs/standards/data/timezone_et_vs_utc.md`](../../../docs/standards/data/timezone_et_vs_utc.md).

**Error parsing happens once.** Every API error flows through
`readErrorEnvelope`; a call site that reaches into an error body directly is a
bug waiting for the envelope shape to change.
