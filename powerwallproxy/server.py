"""HTTP/WebSocket server for frontends.

    WS   /ws              event stream (see docs/EVENTS.md)
    GET  /blobs/{sha256}  binary content referenced by events
    GET  /state           compact JSON summary of the current world (debugging)
    GET  /schema          JSON Schema of all events
    GET  /dummy/scenarios available/running dummy scenarios
    POST /dummy/scenarios switch scenarios: {"scenarios": ["solo", "pipeline"], "teams": 1}
    GET  /                minimal debug viewer
"""

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from .blobs import BlobStore
from .emit import Emitter
from .events import Hello, HelloEvent, ReplayEndEvent, json_schema
from .hub import Hub

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"


@dataclass
class Runtime:
    hub: Hub
    blobs: BlobStore
    emitter: Emitter
    simulator: Optional[Any] = None  # dummy.simulator.Simulator when --source dummy


class ScenarioRequest(BaseModel):
    scenarios: list[str]
    teams: Optional[int] = Field(default=None, ge=1, le=20)


def create_app(runtime: Runtime, source_task: Optional[Callable[[Runtime], Awaitable[None]]] = None) -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(source_task(runtime)) if source_task else None
        if task:
            task.add_done_callback(_log_crash)
        yield
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        runtime.hub.close()

    app = FastAPI(title="powerwallproxy", lifespan=lifespan)
    # Frontends may be served from anywhere (dev servers, file://, native apps).
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    hub, blobs = runtime.hub, runtime.blobs

    @app.websocket("/ws")
    async def ws(websocket: WebSocket, replay: int = 1):
        await websocket.accept()
        sub, snapshot, last_seq = hub.subscribe(replay=bool(replay))
        sender = None
        try:
            hello = HelloEvent(seq=last_seq, data=Hello(source=hub.source, last_seq=last_seq))
            await websocket.send_text(hello.model_dump_json())
            for ev in snapshot:
                await websocket.send_text(ev.model_dump_json())
            await websocket.send_text(ReplayEndEvent(seq=last_seq).model_dump_json())

            async def pump():
                while True:
                    await websocket.send_text(await sub.queue.get())

            sender = asyncio.create_task(pump())
            overflow = asyncio.create_task(sub.overflowed.wait())
            receiver = asyncio.create_task(websocket.receive_text())  # only to notice disconnects
            done, pending = await asyncio.wait({sender, overflow, receiver}, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
            for t in done:
                t.exception()  # mark retrieved; a disconnect here is the normal way out
            if overflow in done:
                await websocket.close(code=1013, reason="client too slow, reconnect")
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            hub.unsubscribe(sub)
            if sender:
                sender.cancel()

    @app.get("/blobs/{sha}")
    async def blob(sha: str):
        hit = blobs.get(sha)
        if not hit:
            raise HTTPException(404, "unknown blob")
        data, ref = hit
        return Response(data, media_type=ref.mime, headers={"Cache-Control": "public, max-age=31536000, immutable"})

    @app.get("/state")
    async def state():
        return {"source": hub.source, "seq": hub.seq, "clients": hub.client_count, **hub.state.summary()}

    @app.get("/schema")
    async def schema():
        return json_schema()

    def simulator():
        if runtime.simulator is None:
            raise HTTPException(404, "not running with --source dummy")
        return runtime.simulator

    @app.get("/dummy/scenarios")
    async def get_scenarios():
        return simulator().info()

    @app.post("/dummy/scenarios")
    async def set_scenarios(req: ScenarioRequest):
        sim = simulator()
        try:
            await sim.set_scenarios(req.scenarios, req.teams)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return sim.info()

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "viewer.html")

    return app


def _log_crash(task: asyncio.Task) -> None:
    if not task.cancelled() and task.exception():
        log.error("event source crashed", exc_info=task.exception())
