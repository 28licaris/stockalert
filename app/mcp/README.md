# mcp/

Model Context Protocol server — agent-facing surface over the same
read services that HTTP routes use.

Mounted on the existing FastAPI app at `/mcp` via
[`server.mount_on(app)`](server.py). Single process today; lift-out to
its own container later is a Dockerfile change, not a code change
(the tools depend on the readers, not on the FastAPI request lifecycle).

## Why MCP

An LLM agent needs to read from this platform — historical bars for
training, live signals for monitoring, quotes for valuation, watchlists
for universe definition. Building a custom integration per agent type
is wasted work. MCP is the standard wire format; ship the tools once,
every MCP-compatible agent can use them.

Same Pydantic shapes that flow out of `/api/lake/*` flow out of MCP
tools. An agent that consumes `BronzeBarsResponse` over HTTP can
consume it over MCP with zero shape-conversion code.

## Layout

```
app/mcp/
├── server.py         FastMCP instance + mount_on(app)
├── middleware.py     tool_call() context manager — observability
├── tools/
│   ├── lake.py       BronzeReader-backed historical reads
│   └── (future: live.py, signals.py, quotes.py, watchlist.py,
│        movers.py, instruments.py, market.py, coverage.py,
│        system.py, schwab_options.py, journal.py)
└── README.md         (this file)
```

Future write-side tools go in dedicated modules (`tools/writes.py`,
`tools/trading.py`). The structural test in
`tests/test_mcp_layering.py` enforces this — non-mutation tool files
must not import write-side service methods.

## Tool surface (current — 23 tools)

### Lake (Iceberg bronze — CH-independent)
| Tool | Backed by | When to use |
|---|---|---|
| `get_bronze_bars` | `BronzeReader.get_bars` | Historical OHLCV for training/backtests. |
| `list_bronze_symbols` | `BronzeReader.list_symbols` | Universe discovery. |
| `get_latest_trading_day` | `BronzeReader.latest_trading_day` | "Freshest available data" anchor. |

### Live tier (ClickHouse)
| Tool | When to use |
|---|---|
| `get_recent_bars` | Last N 1-minute bars for one symbol, ASC. |
| `get_bars_in_range` | Explicit window query at any supported interval. |
| `get_bars_for_chart` | Chart-style — lookback_days + auto-limit + source fallback. |
| `get_latest_bar_per_symbol` | Snapshot across many symbols at once. |

### Signals (CH detector output)
| Tool | When to use |
|---|---|
| `get_recent_signals` | "What just fired" across all symbols. |
| `get_signals_by_symbol` | Drill into one symbol's signal history. |

### Quotes (provider REST)
| Tool | When to use |
|---|---|
| `get_quote` | Single symbol current price. |
| `get_quotes` | Batched (chunked) for many symbols. |

### Watchlists (read-only — write-side in a future `tools/writes.py`)
| Tool | When to use |
|---|---|
| `list_watchlists` | All known watchlists with member counts. |
| `get_watchlist` | One watchlist with members + metadata. |
| `get_watchlist_members` | Just the symbol list. |

### Discovery + Schwab pass-through
| Tool | When to use |
|---|---|
| `get_movers` | Top market movers for an index ($SPX, $COMPX, etc.). |
| `search_instrument` | Fuzzy ticker / company-name lookup. |
| `get_instruments` | Metadata for known tickers. |
| `get_market_hours` | Session schedule (open/close, pre/post). |

### Data-quality observability
| Tool | When to use |
|---|---|
| `get_coverage` | actual vs expected bars in a window (validate training set). |
| `find_intraday_gaps` | Locate contiguous missing-bar ranges. |
| `get_bronze_table_stats` | Row count / file count / snapshot for a bronze table. |

### System health
| Tool | When to use |
|---|---|
| `get_health` | Pre-flight: is CH up? Is the lake reachable? |
| `get_lake_freshness` | Latest trading day per bronze table. |

### Strategy execution + history (trading subsystem)
| Tool | When to use |
|---|---|
| `run_backtest` | Run a strategy + config end-to-end; returns canonical RunMetrics. Supports `sma_crossover` and `llm_agent`. The agent-iteration tool. |
| `list_strategy_runs` | Recent runs from the `agent_runs` registry. Lets an agent self-evaluate over its own history. |

### Indicators (compute-on-read)
| Tool | When to use |
|---|---|
| `compute_indicator` | Single indicator series for a symbol over a window. Returns one `IndicatorSeries`. |
| `compute_indicators` | Multi-indicator bundle — bars + N series in one call. Multi-output indicators (Bollinger / Stochastic / MACD) decompose into per-component series. |
| `get_chart_data` | Like `compute_indicators` but anchored to a relative lookback (`lookback_days`) instead of explicit timestamps. The "show me a chart from N days back with these overlays" call. |

### Future slices

- **Slice 4 (Schwab pass-through):** options chain / expirations / option quote / journal (account + trade history).
- **Slice 5 (Gated writes):** watchlist mutation, trading. Allowlist-protected.

## Rules every tool obeys

1. **One adapter per service method.** Tool body parses args, calls
   one reader/service method, returns the typed response. No business
   logic in the tool layer — that lives in the reader.
2. **Pydantic in, Pydantic out.** FastMCP introspects type hints to
   build the JSON-Schema the LLM sees in `list_tools`. Use the
   models from `app/services/readers/schemas.py` directly so the HTTP
   surface and MCP surface stay byte-identical.
3. **`with tool_call("tool_name")` for observability.** Wraps every
   call body. Logs success-with-timing, distinguishes `ValueError`
   (client input problem) from `Exception` (server bug), and reraises
   so FastMCP's error envelope catches it.
4. **Docstrings are agent UI.** First sentence = what the tool does
   in 12 words. Then `USE WHEN:` (agent affordance), `Args:`,
   `Returns:`, and `Cost:`. The LLM picks the right tool from these.
5. **Limits on list returns.** Every tool that returns a list has a
   `limit` arg with a sensible default. Defaults bias toward "small,
   useful" not "comprehensive" — agents shouldn't accidentally fetch
   2B bars.
6. **Read-only by default.** Mutating tools live in dedicated modules
   (not yet created). The structural test enforces this.

## How an agent discovers the surface

MCP `list_tools` returns every registered tool with its full input
schema, output schema, and description. No out-of-band documentation
needed — the agent reads its own tools.

Local test (with the app running):

```bash
# Initialize an MCP session and list tools (uses curl + jq)
curl -X POST http://127.0.0.1:8000/mcp/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

For programmatic testing, use the helpers in `tests/test_mcp_lake.py`
which exercise the tools through the FastMCP API directly.

## How to add a new tool

1. Pick the right tool file (or create one — one file per domain).
2. Import the relevant reader/service + its Pydantic response type.
3. Write the function, decorate with `@mcp.tool()`.
4. Wrap the body in `with tool_call("name", **fields_to_log):`.
5. Add the module to `register_all_tools()` in `server.py`.
6. Add a row to the tool surface table above.
7. Write a test that calls the tool through `mcp.call_tool(...)`.
8. Run the structural gate test to confirm layering is preserved.
