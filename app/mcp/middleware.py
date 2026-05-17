"""
Cross-cutting observability + error handling for MCP tools.

Tools wrap their core call in `with tool_call(tool_name)` so timing,
arg sizes, and error type get logged uniformly. The structural test
in `test_mcp_layering.py` checks that every tool file uses this
context manager — agents calling 100 tools shouldn't produce 100
different logging shapes.

Why not a decorator: FastMCP's `@mcp.tool()` introspects the wrapped
function's signature for the public schema. A naive `@observable`
decorator would clobber the signature unless we write a careful
`functools.wraps` + `inspect.signature` setup. A context manager
inside the body is simpler and more honest about the boundary.

Future hooks that will land here:
  - Bearer-token auth (deferred until deployment topology demands it)
  - Per-tool rate limiting (when an agent does runaway calls)
  - Cost accounting (for paid provider tools — Schwab options)
"""
from __future__ import annotations

import contextlib
import logging
import time
from typing import Iterator

logger = logging.getLogger("mcp.tools")


@contextlib.contextmanager
def tool_call(tool_name: str, **fields) -> Iterator[None]:
    """
    Context manager: log start, success-with-timing, or failure-with-
    type for one MCP tool invocation. Extra structured fields can be
    passed via kwargs (e.g. `symbol="AAPL"`, `count=5000`).

    On exit:
      - Success -> single INFO line `mcp.tool: <name> ok in X.YYYs`
      - ValueError -> WARNING (client-input problem, not server bug)
      - Other Exception -> ERROR with traceback, re-raised
    """
    start = time.monotonic()
    extras = " ".join(f"{k}={v}" for k, v in fields.items() if v is not None)
    try:
        yield
    except ValueError as exc:
        elapsed = time.monotonic() - start
        logger.warning(
            "mcp.tool: %s rejected after %.3fs (ValueError): %s | %s",
            tool_name, elapsed, exc, extras,
        )
        raise
    except Exception:
        elapsed = time.monotonic() - start
        logger.exception(
            "mcp.tool: %s failed after %.3fs | %s",
            tool_name, elapsed, extras,
        )
        raise
    else:
        elapsed = time.monotonic() - start
        logger.info("mcp.tool: %s ok in %.3fs | %s", tool_name, elapsed, extras)
