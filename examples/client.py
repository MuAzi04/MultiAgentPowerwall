"""Minimal frontend-side client: connects and prints a one-line summary per event.

    python examples/client.py [ws://localhost:8765/ws]
"""

import asyncio
import json
import sys

import websockets


async def main(url: str) -> None:
    async for ws in websockets.connect(url, max_size=16 * 1024 * 1024):  # reconnects automatically
        try:
            async for raw in ws:
                ev = json.loads(raw)
                d = ev["data"]
                if ev["type"] == "block.delta":
                    print(d["text"], end="", flush=True)
                    continue
                detail = {
                    "agent.upsert": lambda: f"{d['name']} role={d['role']} tools={[t['name'] for t in d['tools']]}",
                    "agent.status": lambda: d["status"],
                    "agent.link": lambda: f"{d['kind']} {d['source_agent']} -> {d['target_agent']}",
                    "block.start": lambda: f"{d['kind']} {d['tool_call']['name'] if d['tool_call'] else ''}",
                    "turn.end": lambda: f"{d['finish_reason']} {d['usage']}",
                    "error": lambda: f"{d['code']}: {d['message']}",
                }.get(ev["type"], lambda: "")()
                print(f"\n[{ev['seq']:>6}] {'R' if ev['replay'] else ' '} {ev['type']:<16} {ev['agent_id'] or '-':<22} {detail}")
        except websockets.ConnectionClosed:
            continue


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "ws://localhost:8765/ws"))
