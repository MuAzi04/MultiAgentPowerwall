"""Dummy agent swarm: produces the exact event stream the real proxy will produce,
without any LLM or external software.

The simulator runs one or more *lanes* concurrently. Each lane plays episodes of
a scenario (see scenarios.py) back to back; "cycle" rotates through all of them.
Lanes = scenarios x teams, and can be changed at runtime via set_scenarios().
"""

import asyncio
import contextlib
import logging
from typing import Optional, Sequence

from ..emit import Emitter
from .engine import Engine
from .images import generate_pool
from .scenarios import SCENARIOS

log = logging.getLogger(__name__)
CYCLE = "cycle"


def _log_crash(task: asyncio.Task) -> None:
    if not task.cancelled() and task.exception():
        log.error("dummy lane crashed", exc_info=task.exception())


def validate(scenarios: Sequence[str]) -> list[str]:
    names = [s.strip() for s in scenarios if s.strip()]
    unknown = [s for s in names if s not in SCENARIOS and s != CYCLE]
    if unknown or not names:
        raise ValueError(f"unknown scenario(s) {unknown}; choose from {[*SCENARIOS, CYCLE]}")
    return names


class Simulator(Engine):
    def __init__(
        self,
        emitter: Emitter,
        scenarios: Sequence[str] = ("orchestrated",),
        teams: int = 1,
        min_workers: int = 2,
        max_workers: int = 5,
        speed: float = 1.0,
        seed: Optional[int] = None,
        error_rate: float = 0.04,
    ):
        super().__init__(emitter, speed, seed, error_rate, min_workers, max_workers)
        self.scenarios = validate(scenarios)
        self.teams = teams
        self._lanes: list[asyncio.Task] = []
        self._lock = asyncio.Lock()

    async def run(self) -> None:
        self.images = await asyncio.to_thread(generate_pool, self.rng.randint(0, 2**31))
        try:
            await self.set_scenarios(self.scenarios, self.teams)
            await asyncio.Event().wait()  # lanes run as their own tasks
        finally:
            await self._stop_lanes()

    async def set_scenarios(self, scenarios: Sequence[str], teams: Optional[int] = None) -> None:
        """Replace whatever is running: clears the world and starts new lanes."""
        names = validate(scenarios)
        async with self._lock:
            await self._stop_lanes()
            self.scenarios, self.teams = names, teams or self.teams
            self.agents.clear()
            self._lane_agents.clear()
            self.em.world_reset(f"dummy scenarios: {', '.join(names)} x{self.teams}")
            lanes = [name for _ in range(self.teams) for name in names]
            self._lanes = [asyncio.create_task(self._lane(i, name)) for i, name in enumerate(lanes)]
            for task in self._lanes:
                task.add_done_callback(_log_crash)

    def info(self) -> dict:
        return {
            "running": {"scenarios": self.scenarios, "teams": self.teams},
            "available": [{"name": s.name, "agents": s.agents, "description": s.description} for s in SCENARIOS.values()]
            + [{"name": CYCLE, "agents": "varies", "description": "Rotate through all scenarios, one episode each."}],
        }

    async def _stop_lanes(self) -> None:
        for task in self._lanes:
            task.cancel()
        for task in self._lanes:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._lanes = []

    async def _lane(self, lane: int, name: str) -> None:
        await self.sleep(lane * 1.5)  # stagger lanes
        order = list(SCENARIOS)
        episode = lane
        while True:
            scenario = SCENARIOS[order[episode % len(order)] if name == CYCLE else name]
            episode += 1
            await scenario.run(self, lane, scenario.name)
            await self.sleep(self.rng.uniform(5, 9))  # let the result sit on screen
            self.clear_lane(lane)
            await self.sleep(2)
