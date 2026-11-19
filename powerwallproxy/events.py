"""Event schema: the contract between the backend and the visual frontend.

Every message sent to a frontend is one of the `Event` models below, serialized
as JSON. docs/EVENTS.md describes the same thing for humans; `python -m powerwallproxy
schema` exports it as JSON Schema (usable for codegen in Rust/JS/Python).

Bump PROTOCOL_VERSION on any breaking change.
"""

from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, Field, TypeAdapter

PROTOCOL_VERSION = 1

# ---------------------------------------------------------------------------
# Shared building blocks
# ---------------------------------------------------------------------------

ToolKind = Literal["regular", "communication", "spawn"]
BlockKind = Literal["text", "reasoning", "tool_call", "image"]
AgentStatusValue = Literal["idle", "requesting", "streaming", "waiting_tool", "error", "done"]
LinkKind = Literal["spawn", "message", "handoff"]
MessageRole = Literal["system", "user", "assistant"]
FinishReason = Literal["stop", "tool_calls", "length", "content_filter", "error", "cancelled"]


class ToolDef(BaseModel):
    name: str
    description: str = ""
    kind: ToolKind = "regular"
    parameters: dict[str, Any] = Field(default_factory=dict)  # JSON Schema of the arguments


class BlobRef(BaseModel):
    """Binary content (image, audio, file) served at `url` (relative to the server)."""

    sha256: str
    url: str
    mime: str
    bytes: int
    width: Optional[int] = None
    height: Optional[int] = None


class TextPart(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ImagePart(BaseModel):
    type: Literal["image"] = "image"
    blob: BlobRef


class FilePart(BaseModel):
    type: Literal["file"] = "file"
    blob: BlobRef
    filename: Optional[str] = None


class AudioPart(BaseModel):
    type: Literal["audio"] = "audio"
    blob: BlobRef


Part = Annotated[Union[TextPart, ImagePart, FilePart, AudioPart], Field(discriminator="type")]


class ToolCallInfo(BaseModel):
    call_id: str
    name: str
    kind: ToolKind = "regular"


class Usage(BaseModel):
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    reasoning_tokens: Optional[int] = None
    cost_usd: Optional[float] = None


class Timing(BaseModel):
    ttft_ms: Optional[float] = None  # time to first token
    duration_ms: Optional[float] = None
    tokens_per_s: Optional[float] = None


# ---------------------------------------------------------------------------
# Event payloads
# ---------------------------------------------------------------------------


class Hello(BaseModel):
    protocol_version: int = PROTOCOL_VERSION
    server: str = "powerwallproxy"
    source: str  # "dummy" | "replay" | "proxy"
    last_seq: int  # live events after the catch-up start at last_seq + 1


class ReplayEnd(BaseModel):
    pass


class WorldReset(BaseModel):
    reason: str = ""


class AgentUpsert(BaseModel):
    name: str
    role: str = "worker"  # "orchestrator" | "worker" | anything else -> treat like worker
    model: Optional[str] = None
    parent_id: Optional[str] = None
    tools: list[ToolDef] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)


class AgentStatus(BaseModel):
    status: AgentStatusValue


class AgentRemove(BaseModel):
    reason: str = ""


class AgentLink(BaseModel):
    link_id: str
    source_agent: str
    target_agent: str
    kind: LinkKind
    call_id: Optional[str] = None
    text: Optional[str] = None


class SessionReset(BaseModel):
    session_id: str
    reason: str = ""


class TurnStart(BaseModel):
    turn_id: str
    session_id: str
    model: Optional[str] = None
    context_messages: int = 0  # total messages in the request context


class ContextMessage(BaseModel):
    turn_id: str
    message_id: str
    role: MessageRole
    name: Optional[str] = None  # e.g. sending agent for inter-agent messages
    parts: list[Part]


class BlockStart(BaseModel):
    turn_id: str
    block_id: str
    index: int
    kind: BlockKind
    tool_call: Optional[ToolCallInfo] = None


class BlockDelta(BaseModel):
    turn_id: str
    block_id: str
    text: str


class BlockEnd(BaseModel):
    turn_id: str
    block_id: str
    kind: BlockKind
    content: str  # full text of the block (raw JSON arguments for tool_call)
    arguments: Optional[Any] = None  # parsed tool_call arguments, null if not parseable
    parts: list[Part] = Field(default_factory=list)  # output media, e.g. for kind == "image"


class ToolResult(BaseModel):
    turn_id: str
    call_id: str
    tool_name: str
    is_error: bool = False
    parts: list[Part]


class TurnEnd(BaseModel):
    turn_id: str
    finish_reason: FinishReason
    usage: Usage = Field(default_factory=Usage)
    timing: Timing = Field(default_factory=Timing)


class ErrorInfo(BaseModel):
    turn_id: Optional[str] = None
    code: str
    message: str
    retry_in_ms: Optional[int] = None


# ---------------------------------------------------------------------------
# Envelopes
# ---------------------------------------------------------------------------


class _Envelope(BaseModel):
    v: int = PROTOCOL_VERSION
    seq: int = 0
    ts: float = 0.0  # unix seconds
    agent_id: Optional[str] = None
    replay: bool = False  # true while catching up a freshly connected client


class HelloEvent(_Envelope):
    type: Literal["hello"] = "hello"
    data: Hello


class ReplayEndEvent(_Envelope):
    type: Literal["replay.end"] = "replay.end"
    data: ReplayEnd = Field(default_factory=ReplayEnd)


class WorldResetEvent(_Envelope):
    type: Literal["world.reset"] = "world.reset"
    data: WorldReset = Field(default_factory=WorldReset)


class AgentUpsertEvent(_Envelope):
    type: Literal["agent.upsert"] = "agent.upsert"
    data: AgentUpsert


class AgentStatusEvent(_Envelope):
    type: Literal["agent.status"] = "agent.status"
    data: AgentStatus


class AgentRemoveEvent(_Envelope):
    type: Literal["agent.remove"] = "agent.remove"
    data: AgentRemove = Field(default_factory=AgentRemove)


class AgentLinkEvent(_Envelope):
    type: Literal["agent.link"] = "agent.link"
    data: AgentLink


class SessionResetEvent(_Envelope):
    type: Literal["session.reset"] = "session.reset"
    data: SessionReset


class TurnStartEvent(_Envelope):
    type: Literal["turn.start"] = "turn.start"
    data: TurnStart


class ContextMessageEvent(_Envelope):
    type: Literal["context.message"] = "context.message"
    data: ContextMessage


class BlockStartEvent(_Envelope):
    type: Literal["block.start"] = "block.start"
    data: BlockStart


class BlockDeltaEvent(_Envelope):
    type: Literal["block.delta"] = "block.delta"
    data: BlockDelta


class BlockEndEvent(_Envelope):
    type: Literal["block.end"] = "block.end"
    data: BlockEnd


class ToolResultEvent(_Envelope):
    type: Literal["tool.result"] = "tool.result"
    data: ToolResult


class TurnEndEvent(_Envelope):
    type: Literal["turn.end"] = "turn.end"
    data: TurnEnd


class ErrorEvent(_Envelope):
    type: Literal["error"] = "error"
    data: ErrorInfo


Event = Annotated[
    Union[
        HelloEvent,
        ReplayEndEvent,
        WorldResetEvent,
        AgentUpsertEvent,
        AgentStatusEvent,
        AgentRemoveEvent,
        AgentLinkEvent,
        SessionResetEvent,
        TurnStartEvent,
        ContextMessageEvent,
        BlockStartEvent,
        BlockDeltaEvent,
        BlockEndEvent,
        ToolResultEvent,
        TurnEndEvent,
        ErrorEvent,
    ],
    Field(discriminator="type"),
]

EventAdapter: TypeAdapter[Event] = TypeAdapter(Event)


def json_schema() -> dict[str, Any]:
    schema = EventAdapter.json_schema()
    schema["title"] = f"powerwallproxy event (protocol v{PROTOCOL_VERSION})"
    return schema
