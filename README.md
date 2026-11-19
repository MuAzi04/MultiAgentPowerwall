# powerwallproxy

Backend that turns LLM agent activity into a render-friendly event stream for the big display.
Currently ships a **dummy agent swarm** so the frontend can be built before real agents are connected.

- Frontend team: read [docs/EVENTS.md](docs/EVENTS.md). That's the whole contract.
- Background and roadmap: [docs/DESIGN.md](docs/DESIGN.md).

## Run

```sh
pip install -r requirements.txt
python -m powerwallproxy serve                      # ws://localhost:8765/ws, debug viewer at http://localhost:8765/
```

Scenarios (different agent topologies, many without an orchestrator):

| Scenario | Agents | Pattern |
|---|---|---|
| `orchestrated` | 1 + min..max workers | orchestrator spawns workers, they report back (default) |
| `hierarchical` | 1 + 2–3 leads + 2–3 workers each | nested orchestrators |
| `solo` | 1 | single agent in a long tool loop |
| `parallel` | min..max | independent agents, no links |
| `pipeline` | 3–4 | sequential handoffs, no orchestrator |
| `group_chat` | 3–5 | peers discussing, messages broadcast, no orchestrator |
| `parallel_8` | 8 | full-size showcase: 8 independent agents |
| `orchestrated_8` | 1 + 8 workers | full-size showcase: orchestrator with 8 workers |
| `cycle` | varies | rotate through all of the above |

```sh
python -m powerwallproxy scenarios                                         # list them
python -m powerwallproxy serve --scenario solo,pipeline,group_chat         # run several side by side
python -m powerwallproxy serve --scenario cycle --teams 2                  # two lanes, rotating
curl -X POST localhost:8765/dummy/scenarios -H "Content-Type: application/json" \
     -d '{"scenarios": ["hierarchical"], "teams": 1}'                      # switch live (also in the debug viewer)
```

Other options:

```sh
python -m powerwallproxy serve --teams 3 --min-workers 3 --max-workers 8   # more agents
python -m powerwallproxy serve --speed 3                                   # faster (stress test)
python -m powerwallproxy serve --seed 42                                   # reproducible-ish runs
python -m powerwallproxy serve --record runs/demo.jsonl                    # save the stream (+ runs/demo.jsonl.blobs/)
python -m powerwallproxy serve --source replay --replay runs/demo.jsonl    # play it back in a loop
python -m powerwallproxy schema -o events.schema.json                      # JSON Schema for codegen
python examples/client.py                                                  # print the stream in a terminal
```
