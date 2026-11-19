"""CLI.

    python -m powerwallproxy serve                      # dummy swarm on ws://localhost:8765/ws
    python -m powerwallproxy serve --teams 3 --max-workers 8 --speed 2
    python -m powerwallproxy serve --scenario solo,pipeline,group_chat
    python -m powerwallproxy scenarios                  # list dummy scenarios
    python -m powerwallproxy serve --record runs/demo.jsonl
    python -m powerwallproxy serve --source replay --replay runs/demo.jsonl
    python -m powerwallproxy schema -o events.schema.json
"""

import argparse
import json
import logging
import sys
from pathlib import Path

import uvicorn

from .blobs import BlobStore
from .emit import Emitter
from .events import json_schema
from .hub import Hub
from .replay import blob_dir_for, replay
from .server import Runtime, create_app


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="powerwallproxy")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the event server")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--source", choices=["dummy", "replay", "none"], default="dummy")
    s.add_argument("--scenario", default="orchestrated",
                   help="dummy: comma-separated scenarios run side by side, or 'cycle' (see 'scenarios' command)")
    s.add_argument("--teams", type=int, default=1, help="dummy: run each scenario this many times in parallel")
    s.add_argument("--min-workers", type=int, default=2)
    s.add_argument("--max-workers", type=int, default=5)
    s.add_argument("--speed", type=float, default=1.0, help="time multiplier (2 = twice as fast)")
    s.add_argument("--seed", type=int, default=None)
    s.add_argument("--error-rate", type=float, default=0.04, help="dummy: chance of a simulated 429 per turn")
    s.add_argument("--record", type=Path, default=None, help="append all events to this JSONL file")
    s.add_argument("--replay", type=Path, default=None, help="recording to play with --source replay")
    s.add_argument("--turns-per-agent", type=int, default=30, help="turns kept for late-joining clients")

    sc = sub.add_parser("schema", help="print the event JSON Schema")
    sc.add_argument("-o", "--output", type=Path, default=None)

    sub.add_parser("scenarios", help="list dummy scenarios")

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.cmd == "scenarios":
        from .dummy.scenarios import SCENARIOS

        for s in SCENARIOS.values():
            print(f"{s.name:<14} {s.agents:<44} {s.description}")
        print(f"{'cycle':<14} {'varies':<44} Rotate through all scenarios, one episode each.")
        return

    if args.cmd == "schema":
        text = json.dumps(json_schema(), indent=2)
        if args.output:
            args.output.write_text(text, encoding="utf-8")
        else:
            print(text)
        return

    if args.source == "replay" and not args.replay:
        p.error("--source replay needs --replay FILE")

    hub = Hub(source=args.source, turns_per_agent=args.turns_per_agent)
    blobs = BlobStore(persist_dir=blob_dir_for(args.record) if args.record else None)
    if args.record:
        hub.record_to(args.record)
    runtime = Runtime(hub=hub, blobs=blobs, emitter=Emitter(hub, blobs))

    source_task = None
    if args.source == "dummy":
        from .dummy.simulator import Simulator

        try:
            runtime.simulator = Simulator(
                runtime.emitter, scenarios=args.scenario.split(","), teams=args.teams, min_workers=args.min_workers,
                max_workers=args.max_workers, speed=args.speed, seed=args.seed, error_rate=args.error_rate)
        except ValueError as e:
            p.error(str(e))

        async def source_task(rt: Runtime):
            await rt.simulator.run()
    elif args.source == "replay":
        async def source_task(rt: Runtime):
            await replay(rt.hub, rt.blobs, args.replay, speed=args.speed)

    uvicorn.run(create_app(runtime, source_task), host=args.host, port=args.port, log_level="info",
                ws_max_size=16 * 1024 * 1024)


if __name__ == "__main__":
    sys.exit(main())
