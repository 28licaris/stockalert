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
from app.services.assistant.contract import AssistantService
from app.services.assistant.schemas import (
    AssistantStreamEvent,
    Conversation,
    ConversationTurn,
    ContinueRequest,
    Role,
    StreamEventType,
    ToolCall,
    ToolCallStatus,
)

__all__ = [
    "AssistantService",
    "AssistantStreamEvent",
    "Conversation",
    "ConversationTurn",
    "ContinueRequest",
    "Role",
    "StreamEventType",
    "ToolCall",
    "ToolCallStatus",
]
