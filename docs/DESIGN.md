# powerwallproxy: design notes

Backend for rendering LLM agent activity on the large display. The visual frontend is built by
another part of the group; this backend turns agent traffic into a clean event stream for it
([EVENTS.md](EVENTS.md)).

## Decisions so far

- **Display-only.** The screen never feeds back into agent context or responses.
- **Python** (FastAPI + httpx + uvicorn). Frontend language still open (Rust / JS / Python).
  The WebSocket + JSON + JSON Schema contract works for all of them.
- **Upstreams:** OpenRouter, vLLM, llama.cpp server. All speak OpenAI Chat Completions, so no protocol
  translation is needed upstream at first.
- **External software:** mostly our own/custom agent software, so we can add identity hints
  (URL path, headers) wherever needed.
- **Dummy first.** The simulator emits the final event format through the same `Emitter` API the
  proxy will use, so the frontend can be built in parallel without LLMs.

## Architecture

```
agent software ──HTTP (OpenAI-compatible)──▶ powerwallproxy ──▶ OpenRouter / vLLM / llama.cpp
                                               │  (stream is tee'd, never buffered)
                                               ▼
                                       parser / differ ──▶ Emitter ──▶ Hub ──WebSocket──▶ frontend(s)
dummy simulator / replay ────────────────────────────────▶ Emitter/Hub          └─ /blobs/{sha}
```

- `events.py`: schema (the contract). `emit.py`: high-level event API. `hub.py`: sequencing,
  fan-out, catch-up snapshot for late joiners, recording. `blobs.py`: content-addressed media.
- `dummy/`: `engine.py` (fake agents, streaming, tools), `scenarios.py` (topologies: orchestrated,
  hierarchical, solo, parallel, pipeline, group_chat), `simulator.py` (lanes, runtime switching).
  New scenario = one async function + one registry entry in `scenarios.py`.
- `replay.py`: plays recordings. `server.py`: WebSocket/HTTP.

## Planned: the proxy (next phase)

### Ingress

- `POST /a/{agent_id}/v1/chat/completions`: per-agent base URL, agents are created on first request
  (variable agent count without config). Also `/v1/chat/completions` with identity from elsewhere.
- Identity fallbacks, in order: URL path → headers (`X-Powerwall-Agent`, `X-Powerwall-Role`, `X-Powerwall-Parent`,
  `X-Powerwall-Name`) → API key encoding (`pw:<agent>:<role>`) → hash of the system prompt.
- `GET /v1/models` passthrough (some software calls it on startup).
- Later, if needed: `/v1/responses` (OpenAI Agents SDK default), `/v1/messages` (Anthropic).
- Strip our extension fields/headers before forwarding upstream.

### Routing

- Config file mapping agent/role/model alias → upstream (base URL, key, real model name).
- Upstream keys live in the proxy, never in the agent software; never log `Authorization`.

### What we extract per request (mostly free from the protocol)

| Info | Source |
|---|---|
| Tools available | `tools[]` in the request → `agent.upsert.tools` |
| Tool kind (communication/spawn) | config patterns on tool names (`send_message`, `delegate_*`, `transfer_to_*`, `spawn_*`) or header |
| Tool calls | streamed `delta.tool_calls[]` (fragments by `index`, reassemble) |
| Tool results | `role: "tool"` messages in the **next** request, matched by `tool_call_id` |
| Reasoning | `delta.reasoning_content` (llama.cpp, vLLM, DeepSeek), `delta.reasoning` (OpenRouter) |
| Images/files/audio in context | `image_url` (data URI or URL), `input_audio`, `file` parts → BlobStore |
| Output images | OpenRouter `message.images` |
| Usage/cost | `stream_options.include_usage` (inject it), OpenRouter `usage.cost` |
| Orchestrator | explicit role (path/header) or inferred: agent that calls spawn tools |
| Agent links | spawn/communication tool calls → `agent.link`; match the spawned agent's first request |

### Conversation diffing (core logic)

Requests are stateless, so every request repeats the whole history. Per agent, keep the last
message list (hashed per message); new request = known prefix + new messages → emit only the new ones.
If the prefix doesn't match → `session.reset` (new task, truncation, compaction). One agent id may
have several parallel sessions, so key sessions by prefix hash.

### Streaming

- Forward every SSE chunk immediately; parse a copy. Added latency should be ~0.
- Non-streaming requests: emit the same events from the full response.
- Client disconnect → abort upstream → `turn.end(cancelled)`. Upstream 4xx/5xx/429 → `error` + `turn.end(error)`.

## Other ideas / backlog

- **Redaction filter** on the visual stream (API keys, tokens, emails, paths). It's a public screen.
- Per-client subscription filters (`/ws?agents=...`) if the frontend is split over several machines.
- Delta coalescing per client (e.g. 30 Hz) if message rate becomes a problem.
- Thumbnails / downscaled variants for huge images; PDF first-page render.
- Markdown pre-parsing (block boundaries, code languages) if the frontend wants structure.
- Recording library of real runs for demos when nothing is live (`--record` / `--source replay` exist already).
- Metrics endpoint (tokens/s, cost per agent and team) for an overview panel.
