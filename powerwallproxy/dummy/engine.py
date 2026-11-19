"""Building blocks for dummy scenarios: fake agents, streaming, turns, tools.

Everything goes through the Emitter, so scenarios produce exactly the event
stream the real proxy will produce.
"""

import asyncio
import json
import random
import re
from dataclasses import dataclass, field
from typing import Optional, Sequence

from ..emit import Emitter
from ..events import Part, TextPart, ToolCallInfo, ToolDef, Usage
from . import corpus

_TOKEN = re.compile(r"\s*[^\s]{1,6}|\s+")


def _obj(**props: str) -> dict:
    return {"type": "object", "properties": {k: {"type": v} for k, v in props.items()}, "required": list(props)}


TOOLS = {
    "spawn_worker": ToolDef(name="spawn_worker", kind="spawn", parameters=_obj(role="string", task="string"),
                            description="Start a sub-agent with a role and a task; returns its report."),
    "send_message": ToolDef(name="send_message", kind="communication", parameters=_obj(to="string", message="string"),
                            description="Send a message to another agent."),
    "web_search": ToolDef(name="web_search", description="Search the web.", parameters=_obj(query="string")),
    "read_file": ToolDef(name="read_file", description="Read a file from the workspace.", parameters=_obj(path="string")),
    "edit_file": ToolDef(name="edit_file", description="Replace text in a file.",
                         parameters=_obj(path="string", old="string", new="string")),
    "run_python": ToolDef(name="run_python", description="Execute Python code and return stdout (and plots).",
                          parameters=_obj(code="string")),
    "take_screenshot": ToolDef(name="take_screenshot", description="Screenshot a URL.", parameters=_obj(url="string")),
}
LEAD_TOOLS = ["spawn_worker", "send_message", "web_search"]
WORKER_TOOLS = ["web_search", "read_file", "run_python", "take_screenshot", "send_message"]
SOLO_TOOLS = ["read_file", "edit_file", "run_python", "web_search", "take_screenshot"]


def handoff_tool(target_role: str) -> ToolDef:
    return ToolDef(name=f"transfer_to_{target_role}", kind="communication", parameters=_obj(summary="string"),
                   description=f"Hand the task over to the {target_role} agent.")


@dataclass
class SimAgent:
    agent_id: str
    name: str
    role: str  # persona, e.g. "coder"; the protocol role (orchestrator/worker) is in agent.upsert
    model: str
    tps: float  # tokens per second
    tools: dict[str, ToolDef]
    session_id: str = ""
    context_messages: int = 0
    context_tokens: int = 0
    inbox: list[tuple[str, str]] = field(default_factory=list)  # (sender id, text)


# New context items for a turn: ("msg", role, parts, name) or ("tool", call_id, tool_name, parts, is_error)
ContextItem = tuple


class Engine:
    def __init__(self, emitter: Emitter, speed: float, seed: Optional[int], error_rate: float,
                 min_workers: int, max_workers: int):
        self.em = emitter
        self.speed = speed
        self.rng = random.Random(seed)
        self.error_rate = error_rate
        self.min_workers = min_workers
        self.max_workers = max(min_workers, max_workers)
        self.images: dict[str, list[bytes]] = {}
        self.agents: dict[str, SimAgent] = {}
        self._lane_agents: dict[int, list[str]] = {}
        self._counter = 0

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds / self.speed)

    def image(self, kind: Optional[str] = None) -> Part:
        kind = kind or self.rng.choice(list(self.images))
        return self.em.image(self.rng.choice(self.images[kind]), "image/png")

    def text(self, text: str) -> Part:
        return self.em.text(text)

    # -- agents ------------------------------------------------------------

    def add_agent(self, lane: int, scenario: str, persona: str, role: str = "worker",
                  parent: Optional[SimAgent] = None, tools: Sequence = (), description: str = "") -> SimAgent:
        self._counter += 1
        defs = {t.name: t for t in (TOOLS[t] if isinstance(t, str) else t for t in tools)}
        agent = SimAgent(agent_id=f"t{lane}-{persona}-{self._counter}", name=f"{persona.capitalize()} {self._counter}",
                         role=persona, model=self.rng.choice(corpus.MODELS), tps=self.rng.uniform(25, 90), tools=defs)
        self.agents[agent.agent_id] = agent
        self._lane_agents.setdefault(lane, []).append(agent.agent_id)
        meta = {"team": lane, "scenario": scenario, **({"description": description} if description else {})}
        self.em.agent_upsert(agent.agent_id, agent.name, role, agent.model,
                             parent_id=parent.agent_id if parent else None, tools=list(defs.values()), meta=meta)
        agent.session_id = self.em.session_reset(agent.agent_id, "started")
        return agent

    def clear_lane(self, lane: int) -> None:
        for agent_id in reversed(self._lane_agents.pop(lane, [])):  # children first
            self.em.agent_remove(agent_id, "task complete")
            self.agents.pop(agent_id, None)

    def system_prompt(self, agent: SimAgent, extra: str = "") -> ContextItem:
        return ("msg", "system", [self.text(f"You are {agent.name}, a {agent.role}. {extra}".strip())], None)

    def user_task(self, task: str, sender: Optional[SimAgent] = None, image_p: float = 0.25) -> ContextItem:
        parts = [self.text(task)]
        if self.rng.random() < image_p:
            parts.append(self.image())
        return ("msg", "user", parts, sender.agent_id if sender else None)

    # -- turns -------------------------------------------------------------

    async def stream(self, agent: SimAgent, turn_id: str, kind: str, text: str,
                     tool_call: Optional[ToolCallInfo] = None) -> None:
        block_id = self.em.block_start(turn_id, kind, tool_call)
        if kind == "tool_call":
            chunks, i = [], 0
            while i < len(text):
                n = self.rng.randint(2, 8)
                chunks.append(text[i:i + n])
                i += n
        else:
            chunks = _TOKEN.findall(text)
        for chunk in chunks:
            self.em.block_delta(block_id, chunk)
            # jitter so streams don't look mechanical
            await self.sleep(self.rng.uniform(0.5, 1.5) / agent.tps)
        self.em.block_end(block_id)

    async def tool_call(self, agent: SimAgent, turn_id: str, tool: str, args: dict) -> ToolCallInfo:
        call = ToolCallInfo(call_id=f"call_{self.rng.getrandbits(48):012x}", name=tool, kind=agent.tools[tool].kind)
        await self.stream(agent, turn_id, "tool_call", json.dumps(args, ensure_ascii=False), call)
        return call

    async def begin_turn(self, agent: SimAgent, new_context: list[ContextItem]) -> str:
        """turn.start + context, including simulated rate-limit errors and retries."""
        new_context = list(new_context) + [("msg", "user", [self.text(t)], s) for s, t in agent.inbox]
        agent.inbox.clear()
        while True:
            agent.context_messages += len(new_context)
            turn_id = self.em.turn_start(agent.agent_id, agent.session_id, agent.model, agent.context_messages)
            new_tokens = 0
            for item in new_context:
                if item[0] == "msg":
                    _, role, parts, name = item
                    self.em.context_message(agent.agent_id, turn_id, role, parts, name)
                else:
                    _, call_id, tool_name, parts, is_error = item
                    self.em.tool_result(agent.agent_id, turn_id, call_id, tool_name, parts, is_error)
                new_tokens += sum(len(p.text) // 4 if isinstance(p, TextPart) else 800 for p in parts)
            agent.context_tokens += new_tokens
            await self.sleep(self.rng.uniform(0.2, 1.2))  # time to first token
            if self.rng.random() >= self.error_rate:
                return turn_id
            retry_ms = self.rng.choice([1000, 2000, 4000])
            self.em.error(agent.agent_id, "rate_limited", "429 Too Many Requests from upstream", turn_id, retry_ms)
            self.end_turn(agent, turn_id, "error")
            # the retry resends the same context
            agent.context_messages -= len(new_context)
            agent.context_tokens -= new_tokens
            await self.sleep(retry_ms / 1000)

    def end_turn(self, agent: SimAgent, turn_id: str, finish: str) -> None:
        # completion_tokens is left for the emitter to estimate from the delta count
        prompt = agent.context_tokens
        self.em.turn_end(turn_id, finish, Usage(prompt_tokens=prompt, cost_usd=round(prompt * 0.000002, 6)))

    # -- composite behaviours ----------------------------------------------

    async def work(self, agent: SimAgent, context: list[ContextItem], steps: int,
                   peers: Sequence[SimAgent] = ()) -> list[ContextItem]:
        """`steps` tool-using turns. Returns the tool results, to be sent with the next turn."""
        usable = [t for t in agent.tools if t in TOOLS and TOOLS[t].kind == "regular"]
        if peers and "send_message" in agent.tools:
            usable.append("send_message")
        for _ in range(steps):
            turn = await self.begin_turn(agent, context)
            if self.rng.random() < 0.6:
                await self.stream(agent, turn, "reasoning", corpus.reasoning(self.rng))
            if self.rng.random() < 0.7:
                await self.stream(agent, turn, "text", corpus.worker_text(self.rng))
            calls = []
            for _ in range(self.rng.choice([1, 1, 1, 2])):
                tool = self.rng.choice(usable)
                if tool == "send_message" and self.rng.random() < 0.5:
                    tool = usable[0]  # keep messaging a bit rarer
                args = self._tool_args(tool, peers)
                calls.append((await self.tool_call(agent, turn, tool, args), args))
            self.end_turn(agent, turn, "tool_calls")
            await self.sleep(self.rng.uniform(0.5, 3))  # tools running
            context = [self._tool_output(agent, call, args) for call, args in calls]
        return context

    async def answer(self, agent: SimAgent, context: list[ContextItem], text: str, reasoning_p: float = 0.5) -> None:
        turn = await self.begin_turn(agent, context)
        if self.rng.random() < reasoning_p:
            await self.stream(agent, turn, "reasoning", corpus.reasoning(self.rng))
        await self.stream(agent, turn, "text", text)
        self.end_turn(agent, turn, "stop")

    def _tool_args(self, tool: str, peers: Sequence[SimAgent]) -> dict:
        rng = self.rng
        if tool == "web_search":
            return {"query": corpus.search_query(rng)}
        if tool == "read_file":
            return {"path": rng.choice(corpus.FILES)}
        if tool == "edit_file":
            return {"path": rng.choice(corpus.FILES), "old": "threshold = 0.5", "new": f"threshold = {rng.random():.2f}"}
        if tool == "run_python":
            return {"code": rng.choice([c for lang, c in corpus.CODE_SNIPPETS if lang == "python"])}
        if tool == "take_screenshot":
            return {"url": f"https://example.org/dashboard/{rng.randint(1, 99)}"}
        return {"to": rng.choice(peers).agent_id, "message": corpus.peer_message(rng)}

    def _tool_output(self, agent: SimAgent, call: ToolCallInfo, args: dict) -> ContextItem:
        rng = self.rng
        if rng.random() < 0.08:
            return ("tool", call.call_id, call.name, [self.text("Error: tool timed out after 30s")], True)
        if call.name == "web_search":
            parts = [self.text(corpus.search_results(rng, args["query"]))]
        elif call.name == "read_file":
            parts = [self.text(corpus.file_contents(rng, args["path"]))]
        elif call.name == "edit_file":
            parts = [self.text(f"Edited {args['path']}: 1 replacement.")]
        elif call.name == "run_python":
            parts = [self.text(corpus.run_output(rng))]
            if rng.random() < 0.5:
                parts.append(self.image("chart"))
        elif call.name == "take_screenshot":
            parts = [self.text(f"Screenshot of {args['url']}"), self.image("screenshot")]
        else:
            self.message(agent, args["to"], args["message"], call.call_id)
            parts = [self.text(f"Message delivered to {args['to']}.")]
        return ("tool", call.call_id, call.name, parts, False)

    def message(self, sender: SimAgent, target_id: str, text: str, call_id: Optional[str] = None) -> None:
        """Agent-to-agent message: link event now, appears in the target's next turn context."""
        self.em.link(sender.agent_id, target_id, "message", call_id, text)
        if target := self.agents.get(target_id):
            target.inbox.append((sender.agent_id, text))
