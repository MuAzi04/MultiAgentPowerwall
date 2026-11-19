"""Replays a recording (JSONL written by `--record`) with its original timing, in a loop."""

import asyncio
import logging
from pathlib import Path

from .blobs import BlobStore
from .events import EventAdapter, HelloEvent, ReplayEndEvent, WorldReset, WorldResetEvent
from .hub import Hub

log = logging.getLogger(__name__)


def blob_dir_for(recording: Path) -> Path:
    return recording.with_name(recording.name + ".blobs")


async def replay(hub: Hub, blobs: BlobStore, path: Path, speed: float = 1.0, loop: bool = True) -> None:
    blob_dir = blob_dir_for(path)
    if blob_dir.is_dir():
        log.info("loaded %d blobs from %s", blobs.load_dir(blob_dir), blob_dir)
    lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    while True:
        hub.publish(WorldResetEvent(data=WorldReset(reason=f"replay of {path.name}")))
        prev_ts = None
        for line in lines:
            ev = EventAdapter.validate_json(line)
            if isinstance(ev, (HelloEvent, ReplayEndEvent, WorldResetEvent)):
                continue
            if prev_ts is not None:
                await asyncio.sleep(max(0.0, ev.ts - prev_ts) / speed)
            prev_ts = ev.ts
            ev.ts = 0.0  # re-stamped by the hub
            ev.replay = False
            hub.publish(ev)
        if not loop:
            return
        await asyncio.sleep(3)
