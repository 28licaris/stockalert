"""
MCP server scaffold — mounts on the existing FastAPI app at `/mcp`.

Tool modules in `app/mcp/tools/` register their tools onto the global
`mcp` instance exported here via `@mcp.tool()`. Each tool is a thin
adapter — it parses args, calls one reader/service method, and
returns the typed Pydantic response from `app.services.readers.schemas`.

Design intent (see `feedback_platform_design_intent`):

  - Same Pydantic contract over HTTP routes AND MCP tools. Adding a
    second surface is a wiring change, not a contract change.
  - Read-only by default. Mutation tools live in dedicated modules
    (`tools/writes.py`, `tools/trading.py` — neither built yet) with
    explicit allowlists. The structural test in
    `test_mcp_layering.py` enforces this — non-mutation tool files
    must not import write-side services.
  - Lift-out friendly. The MCP server is a Starlette app today, so a
    future split into its own container is a Dockerfile + a reverse
    proxy entry, not a refactor. The tools call the same readers
    routes call — flip the import path to a thin HTTP client when
    the service split happens.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger(__name__)

# Single global instance. Tool modules import this to register.
# Server name surfaces in MCP `list_tools` / agent UIs.
mcp = FastMCP("stockalert")


def register_all_tools() -> None:
    """
    Import every tool module so their `@mcp.tool()` decorations run.

    New tool modules get added here and ONLY here — keeps the
    discoverability one-stop. The structural test asserts that every
    module under `app/mcp/tools/` is registered.
    """
    # noqa: F401 — imports are for side effect (tool registration)
    from app.mcp.tools import coverage  # noqa: F401
    from app.mcp.tools import indicators  # noqa: F401
    from app.mcp.tools import instruments  # noqa: F401
    from app.mcp.tools import lake  # noqa: F401
    from app.mcp.tools import live  # noqa: F401
    from app.mcp.tools import market  # noqa: F401
    from app.mcp.tools import movers  # noqa: F401
    from app.mcp.tools import quotes  # noqa: F401
    from app.mcp.tools import screener  # noqa: F401
    from app.mcp.tools import signals  # noqa: F401
    from app.mcp.tools import sim  # noqa: F401
    from app.mcp.tools import system  # noqa: F401
    from app.mcp.tools import watchlist  # noqa: F401

    logger.info(
        "MCP tools registered (lake + live + quotes + signals + "
        "watchlist + movers + instruments + market + coverage + "
        "system + sim + indicators + screener)"
    )


def mount_on(app: "FastAPI", path: str = "/mcp") -> None:
    """
    Mount the MCP streamable-HTTP transport at `path` on the FastAPI
    app. Composes the MCP session-manager lifespan onto the app's
    existing lifespan so initialization is automatic.

    Idempotent: calling twice is a no-op (registration handled by
    `register_all_tools`, which is also idempotent).

    Usage in `app/main_api.py`:
        from app.mcp.server import mount_on as mount_mcp
        mount_mcp(app)
    """
    register_all_tools()

    mcp_app = mcp.streamable_http_app()

    # FastAPI's router has a `lifespan_context` callable. Compose so
    # the MCP session manager's lifespan runs inside the API's lifespan.
    existing_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def composed_lifespan(api):
        async with existing_lifespan(api):
            async with mcp_app.router.lifespan_context(mcp_app):
                yield

    app.router.lifespan_context = composed_lifespan
    app.mount(path, mcp_app)
    logger.info("✅ MCP server mounted at %s", path)
