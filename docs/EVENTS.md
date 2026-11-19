# Event stream protocol (v1)

This is the contract between the backend (powerwallproxy) and the visual frontend.
The dummy simulator and the real proxy produce **exactly this format**, so a
frontend built against the dummy keeps working unchanged once real agents are
connected.

- Machine-readable version: `GET /schema` or `python -m powerwallproxy schema -o events.schema.json`
  (JSON Schema, usable for codegen: `typify`/`schemars` for Rust, `json-schema-to-typescript`, `datamodel-code-generator` for Python).
- Source of truth in code: [`powerwallproxy/events.py`](../powerwallproxy/events.py).

## Connecting

```
ws://<host>:8765/ws            full catch-up, then live events
ws://<host>:8765/ws?replay=0   live events only
http://<host>:8765/blobs/<sha256>   images/files referenced by events
http://<host>:8765/state       JSON summary of current agents (debugging)
http://<host>:8765/            debug viewer (raw stream per agent)
```

Each WebSocket text frame is **one JSON event**. The server never sends binary frames.

### Connection sequence

1. `hello`: protocol version, source (`dummy`, `replay`, `proxy`), `last_seq`.
2. Catch-up: the current world as normal events with `"replay": true` (agents, links, recent
   turns; each block's deltas are merged into a single `block.delta`). Skip animations for these.
3. `replay.end`
4. Live events (`"replay": false`), with `seq` starting at `last_seq + 1`.

**On every (re)connect, clear your state when you receive `hello`.** The catch-up gives you everything back.
If your client is too slow to keep up, the server closes the socket (code 1013) instead of dropping
events. Just reconnect.

## Envelope

```jsonc
{
  "v": 1,                 // protocol version
  "seq": 1042,            // strictly increasing per server run
  "ts": 1763391000.123,   // unix seconds (float)
  "agent_id": "t0-coder-3",  // null for global events
  "replay": false,        // true during catch-up
  "type": "block.delta",
  "data": { ... }         // payload, depends on type
}
```

All IDs are opaque strings. Unknown fields may be added in a minor revision, so ignore them.
Unknown event types should be ignored too.

## Concepts

- **Agent**: one LLM "persona" (an endpoint on the proxy). Has a role; `orchestrator` agents
  coordinate others. The number of agents changes over time.
- **Session**: one conversation of an agent. `session.reset` means the context was replaced
  (new task, context compaction). Clear the agent's history view.
- **Turn**: one request → response cycle with the model. Contains new **context** (what was sent
  to the model that wasn't sent before) and the model's output as **blocks**.
- **Block**: one piece of output, streamed: `text`, `reasoning` (thinking), `tool_call`, or `image`.
- **Tool result**: what a tool returned. It arrives at the **start of the next turn** (that's when the
  agent software sends it back to the model). Pair it with its call via `call_id`.
- **Link**: a relationship between agents: `spawn` (A created B), `message` (A sent B a message),
  `handoff` (A passed control to B).

Typical order for one agent:

```
agent.upsert → agent.status(idle)
turn.start → context.message* / tool.result* → agent.status(requesting)
  block.start → block.delta* → block.end   (repeated, one block at a time per agent)
turn.end → agent.status(waiting_tool | idle)
...
agent.status(done) → agent.remove
```

Several agents stream concurrently and their events are interleaved. Always route by `agent_id` / `turn_id` / `block_id`.

## Event types

### `hello`
`{ protocol_version, server, source, last_seq }`. First frame on every connection.

### `replay.end`
`{}`. Catch-up finished; live events follow.

### `world.reset`
`{ reason }`. Drop all agents and state (simulator/replay restarted).

### `agent.upsert`
Agent appeared or its info changed.
```jsonc
{
  "name": "Coder 3",
  "role": "worker",            // "orchestrator" | "worker" | other → treat as worker
  "model": "vllm/qwen3-32b",
  "parent_id": "orch-0",       // who spawned it, or null
  "tools": [ { "name": "send_message", "description": "...", "kind": "communication", "parameters": { /* JSON Schema */ } } ],
  "meta": { "team": 0 }        // free-form extras
}
```
Tool `kind`: `regular` | `communication` (talks to other agents) | `spawn` (creates agents).

### `agent.status`
`{ status }`: `idle` | `requesting` (waiting for first token) | `streaming` | `waiting_tool` | `error` | `done`.

### `agent.remove`
`{ reason }`. The agent is gone; remove its window.

### `agent.link`
```jsonc
{ "link_id": "l_…", "source_agent": "orch-0", "target_agent": "t0-coder-3",
  "kind": "spawn",            // spawn | message | handoff
  "call_id": "call_…",        // tool call that caused it, if any
  "text": "Coder: how to reduce latency" }   // task / message content preview
```

### `session.reset`
`{ session_id, reason }`. Clear this agent's conversation view.

### `turn.start`
`{ turn_id, session_id, model, context_messages }`. `context_messages` = total size of the context sent.

### `context.message`
New non-tool input to the model in this turn.
```jsonc
{ "turn_id": "t_…", "message_id": "m_…",
  "role": "user",             // system | user | assistant
  "name": "t0-critic-4",      // sender, e.g. another agent's id for inter-agent messages; may be null
  "parts": [ { "type": "text", "text": "..." }, { "type": "image", "blob": { /* BlobRef */ } } ] }
```

### `tool.result`
`{ turn_id, call_id, tool_name, is_error, parts }`. Result of a previous `tool_call` block (match by `call_id`).

### `block.start`
```jsonc
{ "turn_id": "t_…", "block_id": "b_…", "index": 0,
  "kind": "tool_call",        // text | reasoning | tool_call | image
  "tool_call": { "call_id": "call_…", "name": "web_search", "kind": "regular" } }   // only for tool_call
```

### `block.delta`
`{ turn_id, block_id, text }`. Append `text` to the block. For `tool_call`, it's a fragment of the JSON arguments
(partial JSON, not parseable until the end). `image` blocks have no deltas.

### `block.end`
```jsonc
{ "turn_id": "t_…", "block_id": "b_…", "kind": "tool_call",
  "content": "{\"query\": \"latency vs throughput\"}",  // full block text
  "arguments": { "query": "latency vs throughput" },   // parsed, only for tool_call (null if invalid JSON)
  "parts": [] }                                         // output media, e.g. the image of an image block
```
`content` always equals the concatenation of the deltas, so you can ignore deltas and just use this.

### `turn.end`
```jsonc
{ "turn_id": "t_…",
  "finish_reason": "tool_calls",   // stop | tool_calls | length | content_filter | error | cancelled
  "usage":  { "prompt_tokens": 1830, "completion_tokens": 212, "reasoning_tokens": 40, "cost_usd": 0.0037 },
  "timing": { "ttft_ms": 640.2, "duration_ms": 4100.5, "tokens_per_s": 61.3 } }
```
Any usage/timing field may be null.

### `error`
`{ turn_id, code, message, retry_in_ms }`. E.g. `rate_limited` with a retry. A failed turn ends with
`turn.end(finish_reason: "error")`; the retry is a new turn.

## Topologies to expect

Don't assume there is an orchestrator. Agent software organizes agents in very different ways,
and the frontend should handle all of these (the dummy can produce each one, see below):

| Pattern | What you see |
|---|---|
| Orchestrator + workers | one `role: orchestrator`; workers with `parent_id` set; `spawn` links; occasional `message` links |
| Hierarchical | orchestrators whose `parent_id` is another orchestrator (team leads), each with their own workers |
| Single agent | one agent, no links, long chain of turns and tool calls |
| Independent agents | several agents, no parents, no links |
| Pipeline | agents without parents, connected by `handoff` links in sequence; each one is `idle` until handed the task |
| Group chat | peers without parents; every message is a `message` link to each other peer and shows up in their next turn as `context.message` with `name` = sender |

## Dummy source

With `--source dummy` the server simulates these patterns. Select them with
`--scenario orchestrated,pipeline,...` (run side by side), `--scenario cycle` (rotate), or at runtime:

```
GET  /dummy/scenarios     list available + running
POST /dummy/scenarios     {"scenarios": ["solo", "group_chat"], "teams": 2}   → world.reset, then the new ones
```

Dummy agents carry `meta.scenario` and `meta.team` (the lane). These are for debugging only; real agents won't have them.

## Media (BlobRef)

Images and other binary content are never sent inline. Parts reference a blob:

```jsonc
{ "type": "image",
  "blob": { "sha256": "9f2c…", "url": "/blobs/9f2c…", "mime": "image/png", "bytes": 260203, "width": 400, "height": 400 } }
```

Fetch `http://<host>:<port>` + `url`. Blobs are immutable and content-addressed: the same image
appearing in many turns has the same `sha256`, so cache by it. Part types: `text`, `image`, `file`
(`filename`), `audio`.

## Rendering hints (not enforced)

- Text and reasoning are usually Markdown (headings, lists, tables, fenced code).
- Colour by block kind, and for tool calls by `tool_call.kind` (`communication` / `spawn` are the interesting ones).
- Draw `agent.link` edges between windows; nest workers under `parent_id`.
- Expect bursts: 10–50 agents × 30–100 deltas/s each. Batch DOM/GPU updates per frame.

## Versioning

`v`/`protocol_version` changes only on breaking changes. Additive changes (new optional fields,
new event types, new enum values) don't bump it, so be tolerant.
