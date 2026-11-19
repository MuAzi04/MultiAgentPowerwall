"""High-level API for producing events.

Both the dummy simulator and (later) the real proxy talk to the frontend only
through this class, which is what keeps the output format identical.
It tracks per-turn/per-block bookkeeping so callers don't have to: block
indices, accumulated block content, parsed tool arguments, timing, and agent
status transitions.
"""

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from .blobs import BlobStore
from .events import (
    AgentLink,
    AgentLinkEvent,
    AgentRemove,
    AgentRemoveEvent,
    AgentStatus,
    AgentStatusEvent,
    AgentStatusValue,
    AgentUpsert,
    AgentUpsertEvent,
    BlockDelta,
    BlockDeltaEvent,
    BlockEnd,
    BlockEndEvent,
    BlockKind,
    BlockStart,
    BlockStartEvent,
    ContextMessage,
    ContextMessageEvent,
    ErrorEvent,
    ErrorInfo,
    FinishReason,
    ImagePart,
    LinkKind,
    MessageRole,
    Part,
    SessionReset,
    SessionResetEvent,
    TextPart,
    Timing,
    ToolCallInfo,
    ToolDef,
    ToolResult,
    ToolResultEvent,
    TurnEnd,
    TurnEndEvent,
    TurnStart,
    TurnStartEvent,
    Usage,
    WorldReset,
    WorldResetEvent,
)
from .hub import Hub


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@dataclass
class _Turn:
    agent_id: str
    started: float
    first_token: Optional[float] = None
    deltas: int = 0
    reasoning_deltas: int = 0
    next_index: int = 0


@dataclass
class _Block:
    agent_id: str
    turn_id: str
    kind: BlockKind
    buf: list[str] = field(default_factory=list)


class Emitter:
    def __init__(self, hub: Hub, blobs: BlobStore):
        self.hub = hub
        self.blobs = blobs
        self._turns: dict[str, _Turn] = {}
        self._blocks: dict[str, _Block] = {}
        self._status: dict[str, AgentStatusValue] = {}

    # -- world / agents ----------------------------------------------------

    def world_reset(self, reason: str = "") -> None:
        self._turns.clear()
        self._blocks.clear()
        self._status.clear()
        self.hub.publish(WorldResetEvent(data=WorldReset(reason=reason)))

    def agent_upsert(
        self,
        agent_id: str,
        name: str,
        role: str = "worker",
        model: Optional[str] = None,
        parent_id: Optional[str] = None,
        tools: Optional[list[ToolDef]] = None,
        meta: Optional[dict[str, Any]] = None,
    ) -> None:
        data = AgentUpsert(name=name, role=role, model=model, parent_id=parent_id, tools=tools or [], meta=meta or {})
        self.hub.publish(AgentUpsertEvent(agent_id=agent_id, data=data))
        self.status(agent_id, "idle")

    def agent_remove(self, agent_id: str, reason: str = "") -> None:
        self._status.pop(agent_id, None)
        self.hub.publish(AgentRemoveEvent(agent_id=agent_id, data=AgentRemove(reason=reason)))

    def status(self, agent_id: str, status: AgentStatusValue) -> None:
        if self._status.get(agent_id) == status:
            return
        self._status[agent_id] = status
        self.hub.publish(AgentStatusEvent(agent_id=agent_id, data=AgentStatus(status=status)))

    def link(
        self,
        source_agent: str,
        target_agent: str,
        kind: LinkKind,
        call_id: Optional[str] = None,
        text: Optional[str] = None,
    ) -> str:
        link_id = new_id("l")
        data = AgentLink(
            link_id=link_id, source_agent=source_agent, target_agent=target_agent, kind=kind, call_id=call_id, text=text
        )
        self.hub.publish(AgentLinkEvent(agent_id=source_agent, data=data))
        return link_id

    def session_reset(self, agent_id: str, reason: str = "") -> str:
        session_id = new_id("s")
        self.hub.publish(SessionResetEvent(agent_id=agent_id, data=SessionReset(session_id=session_id, reason=reason)))
        return session_id

    # -- turns -------------------------------------------------------------

    def turn_start(self, agent_id: str, session_id: str, model: Optional[str] = None, context_messages: int = 0) -> str:
        turn_id = new_id("t")
        self._turns[turn_id] = _Turn(agent_id=agent_id, started=time.monotonic())
        data = TurnStart(turn_id=turn_id, session_id=session_id, model=model, context_messages=context_messages)
        self.hub.publish(TurnStartEvent(agent_id=agent_id, data=data))
        self.status(agent_id, "requesting")
        return turn_id

    def context_message(
        self, agent_id: str, turn_id: str, role: MessageRole, parts: list[Part], name: Optional[str] = None
    ) -> None:
        data = ContextMessage(turn_id=turn_id, message_id=new_id("m"), role=role, name=name, parts=parts)
        self.hub.publish(ContextMessageEvent(agent_id=agent_id, data=data))

    def tool_result(
        self, agent_id: str, turn_id: str, call_id: str, tool_name: str, parts: list[Part], is_error: bool = False
    ) -> None:
        data = ToolResult(turn_id=turn_id, call_id=call_id, tool_name=tool_name, is_error=is_error, parts=parts)
        self.hub.publish(ToolResultEvent(agent_id=agent_id, data=data))

    def block_start(self, turn_id: str, kind: BlockKind, tool_call: Optional[ToolCallInfo] = None) -> str:
        turn = self._turns[turn_id]
        block_id = new_id("b")
        self._blocks[block_id] = _Block(agent_id=turn.agent_id, turn_id=turn_id, kind=kind)
        data = BlockStart(turn_id=turn_id, block_id=block_id, index=turn.next_index, kind=kind, tool_call=tool_call)
        turn.next_index += 1
        self.hub.publish(BlockStartEvent(agent_id=turn.agent_id, data=data))
        return block_id

    def block_delta(self, block_id: str, text: str) -> None:
        if not text:
            return
        block = self._blocks[block_id]
        turn = self._turns[block.turn_id]
        if turn.first_token is None:
            turn.first_token = time.monotonic()
        if block.kind == "reasoning":
            turn.reasoning_deltas += 1
        turn.deltas += 1
        block.buf.append(text)
        data = BlockDelta(turn_id=block.turn_id, block_id=block_id, text=text)
        self.hub.publish(BlockDeltaEvent(agent_id=block.agent_id, data=data))
        self.status(block.agent_id, "streaming")

    def block_end(self, block_id: str, parts: Optional[list[Part]] = None) -> str:
        block = self._blocks.pop(block_id)
        content = "".join(block.buf)
        arguments = None
        if block.kind == "tool_call":
            try:
                arguments = json.loads(content) if content.strip() else {}
            except json.JSONDecodeError:
                arguments = None
        data = BlockEnd(
            turn_id=block.turn_id, block_id=block_id, kind=block.kind, content=content, arguments=arguments,
            parts=parts or [],
        )
        self.hub.publish(BlockEndEvent(agent_id=block.agent_id, data=data))
        return content

    def turn_end(
        self,
        turn_id: str,
        finish_reason: FinishReason,
        usage: Optional[Usage] = None,
        timing: Optional[Timing] = None,
    ) -> None:
        turn = self._turns.pop(turn_id)
        for block_id in [b for b, blk in self._blocks.items() if blk.turn_id == turn_id]:
            self.block_end(block_id)  # close anything left open (e.g. cancelled stream)
        now = time.monotonic()
        if timing is None:
            duration = now - turn.started
            gen_time = now - turn.first_token if turn.first_token else 0
            timing = Timing(
                ttft_ms=round((turn.first_token - turn.started) * 1000, 1) if turn.first_token else None,
                duration_ms=round(duration * 1000, 1),
                tokens_per_s=round(turn.deltas / gen_time, 1) if gen_time > 0 else None,
            )
        # Streaming chunks are roughly one token each; fills whatever upstream didn't report.
        estimate = Usage(completion_tokens=turn.deltas, reasoning_tokens=turn.reasoning_deltas or None)
        if usage is None:
            usage = estimate
        else:
            usage = usage.model_copy(update={k: v for k, v in estimate if getattr(usage, k) is None})
        data = TurnEnd(turn_id=turn_id, finish_reason=finish_reason, usage=usage, timing=timing)
        self.hub.publish(TurnEndEvent(agent_id=turn.agent_id, data=data))
        next_status: AgentStatusValue = {
            "tool_calls": "waiting_tool",
            "error": "error",
        }.get(finish_reason, "idle")
        self.status(turn.agent_id, next_status)

    def error(
        self,
        agent_id: Optional[str],
        code: str,
        message: str,
        turn_id: Optional[str] = None,
        retry_in_ms: Optional[int] = None,
    ) -> None:
        data = ErrorInfo(turn_id=turn_id, code=code, message=message, retry_in_ms=retry_in_ms)
        self.hub.publish(ErrorEvent(agent_id=agent_id, data=data))
        if agent_id:
            self.status(agent_id, "error")

    # -- parts helpers -----------------------------------------------------

    @staticmethod
    def text(text: str) -> TextPart:
        return TextPart(text=text)

    def image(self, data: bytes, mime: str) -> ImagePart:
        return ImagePart(blob=self.blobs.put(data, mime))
