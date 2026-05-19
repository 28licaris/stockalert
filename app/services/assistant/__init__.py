"""Assistant service — conversational copilot over the platform's MCP tools.

See [docs/assistant_plan.md](../../../docs/assistant_plan.md) for the
full spec. Phase AS-1 is the read-only loop: ask a question, the LLM
calls MCP read tools, the answer streams back grounded in real
platform data.

The user-facing interface lives in `contract.py` (the
`AssistantService` Protocol). The concrete implementation will land
in `service.py` in AS-1 slice 2.

Distinct from the trading `LLMAgent` in
`app/services/sim/strategies/llm_agent.py` — that one is the
autonomous per-bar trading agent. This service is interactive,
user-driven, and cannot route orders by allowlist.
"""
from app.services.assistant.cache import CacheKeyInputs, CachedResponse, ResponseCache
from app.services.assistant.contract import AssistantService, Principal
from app.services.assistant.models import ModelChoice, ModelRegistry
from app.services.assistant.policy import DevModeToolPolicy, ToolPolicy, WRITE_TOOLS
from app.services.assistant.runner import MCPToolRunner, ToolResult, ToolRunner
from app.services.assistant.schemas import (
    AssistantStreamEvent,
    ConfirmRequest,
    Conversation,
    ConversationTurn,
    ContinueRequest,
    Role,
    StreamEventType,
    ToolCall,
    ToolCallStatus,
)
from app.services.assistant.service import (
    AnthropicLLMClient,
    DefaultAssistantService,
    LLMClient,
    LLMResult,
    LLMStream,
    LLMToolUse,
    LLMUsage,
)

__all__ = [
    "AnthropicLLMClient",
    "AssistantService",
    "AssistantStreamEvent",
    "CacheKeyInputs",
    "CachedResponse",
    "ConfirmRequest",
    "Conversation",
    "ConversationTurn",
    "ContinueRequest",
    "DefaultAssistantService",
    "DevModeToolPolicy",
    "LLMClient",
    "LLMResult",
    "LLMStream",
    "LLMToolUse",
    "LLMUsage",
    "MCPToolRunner",
    "ModelChoice",
    "ModelRegistry",
    "Principal",
    "ResponseCache",
    "Role",
    "StreamEventType",
    "ToolCall",
    "ToolCallStatus",
    "ToolPolicy",
    "ToolResult",
    "ToolRunner",
    "WRITE_TOOLS",
]
