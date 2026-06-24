# MCP tools

Agent-facing adapters grouped by domain. Each tool parses bounded input, calls
an existing service or reader, and returns its typed result. Business logic and
storage access do not belong here.

Tool docstrings are part of the agent interface: describe what the tool does,
when to use it, argument limits, return shape, and cost. Register new modules in
`app.mcp.server.register_all_tools()` and preserve read-only defaults.

Tests for registration and behavior live in [`../tests/`](../tests/):

```bash
poetry run pytest app/mcp/tests
```
