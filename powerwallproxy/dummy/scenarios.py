"""Dummy scenarios. Each run() plays one episode (one task) in a lane and returns;
the simulator removes the lane's agents afterwards and starts the next episode.

Agent topologies differ on purpose: many agent frameworks have no orchestrator.
"""

import asyncio
from dataclasses import dataclass
from functools import partial
from typing import Awaitable, Callable, Optional

from . import corpus
from .engine import LEAD_TOOLS, SOLO_TOOLS, WORKER_TOOLS, Engine, SimAgent, handoff_tool


@dataclass(frozen=True)
class Scenario:
    name: str
    agents: str  # human-readable agent count
    description: str
    run: Callable[[Engine, int, str], Awaitable[None]]


# -- orchestrated / hierarchical ----------------------------------------------


async def _lead(sim: Engine, lane: int, scen: str, lead: SimAgent, task: str, context: list,
                n_children: int, sub_leads: bool, top: bool) -> str:
    """Plan, spawn children (workers or sub-leads), wait for their reports, summarize."""
    rng = sim.rng
    turn = await sim.begin_turn(lead, context)
    personas = [rng.choice(corpus.ROLES) for _ in range(n_children)]
    if sub_leads:
        personas = [("lead", "Coordinates a group of workers.") for _ in range(n_children)]
    subtasks = [f"{p.capitalize()}: {corpus.search_query(rng)}" for p, _ in personas]
    await sim.stream(lead, turn, "reasoning", corpus.reasoning(rng))
    await sim.stream(lead, turn, "text", corpus.plan(rng, subtasks))
    calls = [await sim.tool_call(lead, turn, "spawn_worker", {"role": p, "task": sub})
             for (p, _), sub in zip(personas, subtasks)]
    sim.end_turn(lead, turn, "tool_calls")

    children = []
    for (persona, desc), sub, call in zip(personas, subtasks, calls):
        child = sim.add_agent(lane, scen, persona, "orchestrator" if sub_leads else "worker", parent=lead,
                              tools=LEAD_TOOLS if sub_leads else WORKER_TOOLS, description=desc)
        sim.em.link(lead.agent_id, child.agent_id, "spawn", call.call_id, sub)
        children.append((child, sub))

    async def run_child(i: int, child: SimAgent, sub: str) -> str:
        await sim.sleep(i * rng.uniform(0.2, 0.8))
        ctx = [sim.system_prompt(child), sim.user_task(sub, lead)]
        if sub_leads:
            report = await _lead(sim, lane, scen, child, sub, ctx, rng.randint(2, 3), sub_leads=False, top=False)
        else:
            peers = [c for c, _ in children if c is not child] + [lead]
            ctx = await sim.work(child, ctx, rng.randint(2, 4), peers)
            report = corpus.report(rng, child.role)
            await sim.answer(child, ctx, report)
        sim.em.status(child.agent_id, "done")
        return report

    reports = await asyncio.gather(*(run_child(i, c, s) for i, (c, s) in enumerate(children)))
    results = [("tool", call.call_id, "spawn_worker", [sim.text(rep)], False) for call, rep in zip(calls, reports)]
    turn = await sim.begin_turn(lead, results)
    await sim.stream(lead, turn, "reasoning", corpus.reasoning(rng))
    summary = corpus.final_summary(rng, task, n_children) if top else corpus.report(rng, "lead")
    await sim.stream(lead, turn, "text", summary)
    if top and rng.random() < 0.35:
        block = sim.em.block_start(turn, "image")
        await sim.sleep(rng.uniform(1, 3))
        sim.em.block_end(block, parts=[sim.image("plasma")])
    sim.end_turn(lead, turn, "stop")
    return summary


async def orchestrated(sim: Engine, lane: int, scen: str, n: Optional[int] = None) -> None:
    orch = sim.add_agent(lane, scen, "orchestrator", "orchestrator", tools=LEAD_TOOLS)
    task = sim.rng.choice(corpus.TASKS)
    ctx = [sim.system_prompt(orch, "Break tasks into subtasks and delegate them to workers."),
           sim.user_task(task, image_p=0.3)]
    await _lead(sim, lane, scen, orch, task, ctx, n or sim.rng.randint(sim.min_workers, sim.max_workers),
                sub_leads=False, top=True)
    sim.em.status(orch.agent_id, "done")


async def hierarchical(sim: Engine, lane: int, scen: str) -> None:
    orch = sim.add_agent(lane, scen, "orchestrator", "orchestrator", tools=LEAD_TOOLS)
    task = sim.rng.choice(corpus.TASKS)
    ctx = [sim.system_prompt(orch, "Delegate to team leads, who delegate to workers."),
           sim.user_task(task, image_p=0.3)]
    await _lead(sim, lane, scen, orch, task, ctx, sim.rng.randint(2, 3), sub_leads=True, top=True)
    sim.em.status(orch.agent_id, "done")


# -- no orchestrator ------------------------------------------------------------


async def _solo_agent(sim: Engine, agent: SimAgent, task: str, steps: int) -> None:
    ctx = [sim.system_prompt(agent, "Use tools to complete the task, then answer."), sim.user_task(task)]
    ctx = await sim.work(agent, ctx, steps)
    await sim.answer(agent, ctx, corpus.report(sim.rng, agent.role))
    sim.em.status(agent.agent_id, "done")


async def solo(sim: Engine, lane: int, scen: str) -> None:
    agent = sim.add_agent(lane, scen, "assistant", tools=SOLO_TOOLS)
    await _solo_agent(sim, agent, sim.rng.choice(corpus.CODING_TASKS + corpus.TASKS), sim.rng.randint(4, 8))


async def parallel(sim: Engine, lane: int, scen: str, n: Optional[int] = None) -> None:
    rng = sim.rng
    n = n or rng.randint(sim.min_workers, sim.max_workers)
    agents = [sim.add_agent(lane, scen, rng.choice(corpus.ROLES)[0], tools=SOLO_TOOLS) for _ in range(n)]
    tasks = rng.sample(corpus.CODING_TASKS + corpus.TASKS, n)

    async def one(i: int, agent: SimAgent, task: str) -> None:
        await sim.sleep(i * rng.uniform(0.3, 1.5))
        await _solo_agent(sim, agent, task, rng.randint(2, 5))

    await asyncio.gather(*(one(i, a, t) for i, (a, t) in enumerate(zip(agents, tasks))))


PIPELINES = [
    ["researcher", "analyst", "writer"],
    ["researcher", "coder", "critic", "writer"],
    ["analyst", "designer", "writer"],
    ["coder", "critic", "coder"],
]


async def pipeline(sim: Engine, lane: int, scen: str) -> None:
    """Sequential handoffs: each stage works, then transfers control to the next."""
    rng = sim.rng
    stages = rng.choice(PIPELINES)
    base_tools = [t for t in WORKER_TOOLS if t != "send_message"]
    agents = []
    for i, persona in enumerate(stages):
        tools = base_tools + ([handoff_tool(stages[i + 1])] if i + 1 < len(stages) else [])
        agents.append(sim.add_agent(lane, scen, persona, tools=tools))  # all visible up front, idle until handed off
    task = rng.choice(corpus.TASKS)
    message, sender = task, None
    for i, agent in enumerate(agents):
        ctx = [sim.system_prompt(agent, "Do your part, then hand over to the next agent."),
               sim.user_task(message, sender, image_p=0.25 if i == 0 else 0)]
        ctx = await sim.work(agent, ctx, rng.randint(1, 3))
        if i + 1 == len(agents):
            await sim.answer(agent, ctx, corpus.final_summary(rng, task, len(agents)))
        else:
            nxt = agents[i + 1]
            turn = await sim.begin_turn(agent, ctx)
            if rng.random() < 0.5:
                await sim.stream(agent, turn, "reasoning", corpus.reasoning(rng))
            await sim.stream(agent, turn, "text", corpus.worker_text(rng))
            message = corpus.handoff_note(rng, nxt.role)
            call = await sim.tool_call(agent, turn, f"transfer_to_{nxt.role}", {"summary": message})
            sim.end_turn(agent, turn, "tool_calls")
            sim.em.link(agent.agent_id, nxt.agent_id, "handoff", call.call_id, message)
            sender = agent
        sim.em.status(agent.agent_id, "done")


async def group_chat(sim: Engine, lane: int, scen: str) -> None:
    """Peers take turns speaking; every message is broadcast to all others."""
    rng = sim.rng
    personas = rng.sample(corpus.ROLES, rng.randint(3, 5))
    peers = [sim.add_agent(lane, scen, p, tools=["web_search", "read_file"], description=d) for p, d in personas]
    task = rng.choice(corpus.TASKS)
    pending = {p.agent_id: [sim.system_prompt(p, "You are in a group discussion. Be concise."),
                            sim.user_task(task, image_p=0.2)] for p in peers}
    rounds = rng.randint(2, 3) * len(peers)
    last = None
    for r in range(rounds):
        speaker = peers[r % len(peers)]  # mostly round-robin, sometimes someone jumps in
        if speaker is last or rng.random() < 0.3:
            speaker = rng.choice([p for p in peers if p is not last])
        ctx, pending[speaker.agent_id] = pending[speaker.agent_id], []
        if rng.random() < 0.25:
            ctx = await sim.work(speaker, ctx, 1)
        final = r == rounds - 1
        text = corpus.chat_conclusion(rng) if final else corpus.chat_reply(rng, last.name if last else None)
        await sim.answer(speaker, ctx, text, reasoning_p=0.3)
        for other in peers:
            if other is not speaker:
                sim.message(speaker, other.agent_id, text)
        last = speaker
    for p in peers:
        sim.em.status(p.agent_id, "done")


SCENARIOS: dict[str, Scenario] = {s.name: s for s in [
    Scenario("orchestrated", "1 orchestrator + min..max workers",
             "Orchestrator spawns workers, they work with tools, report back, orchestrator summarizes.", orchestrated),
    Scenario("hierarchical", "1 orchestrator + 2-3 leads + 2-3 workers each",
             "Nested orchestration: orchestrator -> team leads -> workers.", hierarchical),
    Scenario("solo", "1", "Single agent in a long tool loop (coding-agent style). No orchestrator.", solo),
    Scenario("parallel", "min..max", "Independent agents on unrelated tasks, no links between them.", parallel),
    Scenario("pipeline", "3-4", "Sequential handoffs between specialists, no orchestrator.", pipeline),
    Scenario("group_chat", "3-5", "Peers take turns in a shared discussion, every message broadcast.", group_chat),
    # Full-size showcases: the display targets up to 8 agents (+ orchestrator).
    Scenario("parallel_8", "8", "8 independent agents working at once, no orchestrator.", partial(parallel, n=8)),
    Scenario("orchestrated_8", "1 orchestrator + 8 workers", "Orchestrator delegating to 8 workers.",
             partial(orchestrated, n=8)),
]}
