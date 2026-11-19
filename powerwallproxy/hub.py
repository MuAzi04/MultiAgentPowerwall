"""Event hub: sequences events, fans them out to frontends, keeps catch-up state.

A frontend that connects mid-run receives a compacted replay of the current
world (agents, links, recent turns with merged deltas) before live events, so
it only needs one code path: "apply events".
"""

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Optional, Union

from .events import (
    AgentLinkEvent,
    AgentRemoveEvent,
    AgentStatusEvent,
    AgentUpsertEvent,
    BlockDelta,
    BlockDeltaEvent,
    BlockEndEvent,
    BlockStartEvent,
    ContextMessageEvent,
    ErrorEvent,
    Event,
    SessionResetEvent,
    ToolResultEvent,
    TurnEndEvent,
    TurnStartEvent,
    WorldResetEvent,
)

log = logging.getLogger(__name__)


@dataclass
class _BlockRec:
    start: BlockStartEvent
    text: list[str] = field(default_factory=list)
    last_delta: Optional[BlockDeltaEvent] = None
    end: Optional[BlockEndEvent] = None

    def events(self) -> list[Event]:
        out: list[Event] = [self.start]
        if self.last_delta is not None:
            merged = BlockDelta(turn_id=self.start.data.turn_id, block_id=self.start.data.block_id, text="".join(self.text))
            out.append(self.last_delta.model_copy(update={"data": merged}))
        if self.end is not None:
            out.append(self.end)
        return out


@dataclass
class _TurnRec:
    start: TurnStartEvent
    items: list[Union[Event, _BlockRec]] = field(default_factory=list)
    end: Optional[TurnEndEvent] = None

    def events(self) -> list[Event]:
        out: list[Event] = [self.start]
        for item in self.items:
            out.extend(item.events() if isinstance(item, _BlockRec) else [item])
        if self.end is not None:
            out.append(self.end)
        return out


@dataclass
class _AgentRec:
    upsert: AgentUpsertEvent
    status: Optional[AgentStatusEvent] = None
    session_reset: Optional[SessionResetEvent] = None
    turns: deque = field(default_factory=deque)  # of _TurnRec


class WorldState:
    def __init__(self, turns_per_agent: int = 30):
        self.turns_per_agent = turns_per_agent
        self.clear()

    def clear(self) -> None:
        self.agents: dict[str, _AgentRec] = {}
        self.links: deque[AgentLinkEvent] = deque(maxlen=1000)
        self.turns: dict[str, _TurnRec] = {}
        self.blocks: dict[str, _BlockRec] = {}

    def apply(self, ev: Event) -> None:
        if isinstance(ev, WorldResetEvent):
            self.clear()
        elif isinstance(ev, AgentUpsertEvent):
            rec = self.agents.get(ev.agent_id)
            if rec:
                rec.upsert = ev
            else:
                self.agents[ev.agent_id] = _AgentRec(upsert=ev)
        elif isinstance(ev, AgentRemoveEvent):
            rec = self.agents.pop(ev.agent_id, None)
            if rec:
                for turn in rec.turns:
                    self._forget_turn(turn)
            self.links = deque(
                (l for l in self.links if ev.agent_id not in (l.data.source_agent, l.data.target_agent)),
                maxlen=self.links.maxlen,
            )
        elif isinstance(ev, AgentStatusEvent):
            if rec := self.agents.get(ev.agent_id):
                rec.status = ev
        elif isinstance(ev, AgentLinkEvent):
            self.links.append(ev)
        elif isinstance(ev, SessionResetEvent):
            if rec := self.agents.get(ev.agent_id):
                for turn in rec.turns:
                    self._forget_turn(turn)
                rec.turns.clear()
                rec.session_reset = ev
        elif isinstance(ev, TurnStartEvent):
            if rec := self.agents.get(ev.agent_id):
                turn = _TurnRec(start=ev)
                rec.turns.append(turn)
                self.turns[ev.data.turn_id] = turn
                while len(rec.turns) > self.turns_per_agent:
                    self._forget_turn(rec.turns.popleft())
        elif isinstance(ev, BlockStartEvent):
            if turn := self.turns.get(ev.data.turn_id):
                block = _BlockRec(start=ev)
                turn.items.append(block)
                self.blocks[ev.data.block_id] = block
        elif isinstance(ev, BlockDeltaEvent):
            if block := self.blocks.get(ev.data.block_id):
                block.text.append(ev.data.text)
                block.last_delta = ev
        elif isinstance(ev, BlockEndEvent):
            if block := self.blocks.get(ev.data.block_id):
                block.end = ev
        elif isinstance(ev, (ContextMessageEvent, ToolResultEvent, ErrorEvent)):
            turn_id = ev.data.turn_id
            if turn_id and (turn := self.turns.get(turn_id)):
                turn.items.append(ev)
        elif isinstance(ev, TurnEndEvent):
            if turn := self.turns.get(ev.data.turn_id):
                turn.end = ev

    def _forget_turn(self, turn: _TurnRec) -> None:
        self.turns.pop(turn.start.data.turn_id, None)
        for item in turn.items:
            if isinstance(item, _BlockRec):
                self.blocks.pop(item.start.data.block_id, None)

    def snapshot(self) -> list[Event]:
        out: list[Event] = [rec.upsert for rec in self.agents.values()]
        out.extend(self.links)
        for rec in self.agents.values():
            if rec.session_reset:
                out.append(rec.session_reset)
            for turn in rec.turns:
                out.extend(turn.events())
        out.extend(rec.status for rec in self.agents.values() if rec.status)
        return [ev.model_copy(update={"replay": True}) for ev in out]

    def summary(self) -> dict:
        return {
            "agents": [
                {
                    "agent_id": agent_id,
                    **rec.upsert.data.model_dump(exclude={"tools"}),
                    "tools": [t.name for t in rec.upsert.data.tools],
                    "status": rec.status.data.status if rec.status else None,
                    "turns": len(rec.turns),
                }
                for agent_id, rec in self.agents.items()
            ],
            "links": [l.data.model_dump() for l in self.links],
        }


class Subscriber:
    def __init__(self, maxsize: int):
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=maxsize)
        self.overflowed = asyncio.Event()


class Hub:
    def __init__(self, source: str, turns_per_agent: int = 30, client_queue: int = 20000):
        self.source = source
        self.seq = 0
        self.state = WorldState(turns_per_agent)
        self.client_queue = client_queue
        self._subs: set[Subscriber] = set()
        self._record: Optional[IO[str]] = None

    def record_to(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._record = path.open("a", encoding="utf-8")

    def close(self) -> None:
        if self._record:
            self._record.close()
            self._record = None

    def publish(self, ev: Event) -> Event:
        """Assign seq/ts, update state, fan out. Synchronous on purpose: no awaits,
        so subscribe() always sees a consistent snapshot."""
        self.seq += 1
        ev.seq = self.seq
        if not ev.ts:
            ev.ts = time.time()
        self.state.apply(ev)
        line = ev.model_dump_json()
        if self._record:
            self._record.write(line + "\n")
            self._record.flush()
        for sub in list(self._subs):
            try:
                sub.queue.put_nowait(line)
            except asyncio.QueueFull:
                # Slow client: drop it rather than skip events; it reconnects and catches up.
                log.warning("frontend too slow, disconnecting it")
                self._subs.discard(sub)
                sub.overflowed.set()
        return ev

    def subscribe(self, replay: bool = True) -> tuple[Subscriber, list[Event], int]:
        sub = Subscriber(self.client_queue)
        self._subs.add(sub)
        return sub, (self.state.snapshot() if replay else []), self.seq

    def unsubscribe(self, sub: Subscriber) -> None:
        self._subs.discard(sub)

    @property
    def client_count(self) -> int:
        return len(self._subs)
