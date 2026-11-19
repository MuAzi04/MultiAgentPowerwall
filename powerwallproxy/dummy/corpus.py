"""Canned text for the dummy agents. Content is nonsense-but-plausible on purpose:
it exercises markdown, code blocks, lists, tables, long and short outputs."""

import random

TASKS = [
    "Design a cooling system for a small research satellite and estimate its mass budget",
    "Compare three approaches to real-time path planning for warehouse robots",
    "Write a short report on the energy usage of the campus library and propose savings",
    "Plan a data pipeline that ingests weather station data and detects sensor faults",
    "Investigate why the image classifier underperforms on night-time photos",
    "Draft a tutorial explaining Fourier transforms to first-year students",
    "Evaluate whether the lab should migrate its simulations from CPU to GPU clusters",
    "Analyze survey results about student commuting habits and visualize the key findings",
]

CODING_TASKS = [
    "Fix the failing unit tests in the data loader and explain the root cause",
    "Add retry logic with exponential backoff to the HTTP client module",
    "Profile the image preprocessing step and make it at least 2x faster",
    "Refactor the config parser to support environment variable overrides",
    "Find out why the nightly export job silently skips some records",
]

ROLES = [
    ("researcher", "Finds and summarizes sources."),
    ("coder", "Writes and runs code."),
    ("analyst", "Crunches numbers and builds charts."),
    ("critic", "Reviews other agents' work for mistakes."),
    ("designer", "Produces diagrams and visual material."),
    ("writer", "Turns findings into readable prose."),
]

MODELS = ["openrouter/anthropic/claude-sonnet", "vllm/qwen3-32b", "llama.cpp/llama-3.3-70b", "openrouter/openai/gpt-oss-120b"]

TOPIC_WORDS = [
    "thermal budget", "latency", "sensor drift", "throughput", "baseline", "error margin", "edge cases",
    "dataset split", "cost model", "failure modes", "power draw", "heuristic", "confidence interval",
]

REASONING = [
    "Let me think about what the task actually requires. The key constraint seems to be the {t}, "
    "so I should check that first before committing to an approach.",
    "Hmm, the previous result doesn't quite add up. If the {t} is that high, then the {t2} would also have to "
    "change. Maybe I misread the units. Let me re-derive it step by step.",
    "I have enough information to start. Plan: 1) gather numbers on the {t}, 2) compare against the {t2}, "
    "3) write it up. I'll delegate the parts that can run in parallel.",
    "The user probably cares more about the {t} than the exact {t2}. I'll keep the answer short and focus on that.",
    "Two options here. Option A is simpler but ignores the {t}. Option B handles it but needs more data. "
    "I'll go with B and ask for the missing data via a tool call.",
]

PLAN_INTROS = [
    "I'll split this into parallel subtasks and assign specialists.",
    "This is big enough to parallelize. Here's the plan:",
    "Breaking the task down so the team can work on it concurrently:",
]

SENTENCES = [
    "The {t} is the dominant factor here, accounting for most of the variance.",
    "Looking at the data, the {t} improves by roughly {n}% when we change the {t2}.",
    "One caveat: the {t} was measured under ideal conditions, so real numbers will be worse.",
    "I could not find a reliable source for the {t}, so I'm using a conservative estimate.",
    "This matches what we expected from the {t2}, which is reassuring.",
    "The main risk is that the {t} interacts with the {t2} in ways we haven't modeled.",
    "After re-running the numbers, the result holds within a {n}% error margin.",
    "A simpler approach would be to fix the {t} first and revisit the {t2} later.",
]

CODE_SNIPPETS = [
    ("python", "import numpy as np\n\ndef estimate(samples: np.ndarray) -> float:\n"
               "    mean = samples.mean()\n    std = samples.std(ddof=1)\n"
               "    return mean + 1.96 * std / np.sqrt(len(samples))\n\nprint(estimate(np.random.rand(500)))"),
    ("python", "results = {}\nfor name, cfg in configs.items():\n    model = build(cfg)\n"
               "    results[name] = evaluate(model, val_set)\n\nbest = max(results, key=results.get)"),
    ("rust", "fn moving_average(xs: &[f64], w: usize) -> Vec<f64> {\n"
             "    xs.windows(w).map(|s| s.iter().sum::<f64>() / w as f64).collect()\n}"),
    ("bash", "python simulate.py --config configs/gpu.yaml --runs 20 | tee logs/gpu_run.txt"),
]

SEARCH_QUERIES = [
    "{t} typical values", "how to reduce {t}", "{t} vs {t2} tradeoff", "{t} measurement methods",
]

FILES = ["data/measurements.csv", "src/pipeline.py", "notes/meeting_2026-11-10.md", "results/summary.json"]


def _fill(rng: random.Random, template: str) -> str:
    t, t2 = rng.sample(TOPIC_WORDS, 2)
    return template.format(t=t, t2=t2, n=rng.randint(3, 40))


def reasoning(rng: random.Random) -> str:
    return " ".join(_fill(rng, r) for r in rng.sample(REASONING, rng.randint(1, 2)))


def paragraph(rng: random.Random, sentences: int = 3) -> str:
    return " ".join(_fill(rng, s) for s in rng.sample(SENTENCES, min(sentences, len(SENTENCES))))


def plan(rng: random.Random, subtasks: list[str]) -> str:
    lines = [rng.choice(PLAN_INTROS), ""]
    lines += [f"{i}. **{s}**" for i, s in enumerate(subtasks, 1)]
    lines += ["", "I'll merge the results once everyone reports back."]
    return "\n".join(lines)


def worker_text(rng: random.Random) -> str:
    """Intermediate output: sometimes plain, sometimes markdown-heavy."""
    choice = rng.random()
    if choice < 0.35:
        return paragraph(rng, rng.randint(1, 3))
    if choice < 0.6:
        lang, code = rng.choice(CODE_SNIPPETS)
        return f"{paragraph(rng, 1)}\n\n```{lang}\n{code}\n```\n\n{paragraph(rng, 1)}"
    if choice < 0.8:
        items = "\n".join(f"- {_fill(rng, s)}" for s in rng.sample(SENTENCES, 3))
        return f"Key findings so far:\n\n{items}"
    rows = "\n".join(f"| {w} | {rng.randint(1, 99)} | {rng.choice(['ok', 'warn', 'fail'])} |"
                     for w in rng.sample(TOPIC_WORDS, 4))
    return f"{paragraph(rng, 1)}\n\n| metric | value | status |\n|---|---|---|\n{rows}"


def report(rng: random.Random, role: str) -> str:
    return f"## {role.capitalize()} report\n\n{paragraph(rng, 3)}\n\n**Recommendation:** {paragraph(rng, 1)}"


def final_summary(rng: random.Random, task: str, n_workers: int) -> str:
    return (
        f"# Summary\n\n**Task:** {task}\n\n{paragraph(rng, 3)}\n\n"
        f"All {n_workers} subtasks completed. {paragraph(rng, 2)}\n\n"
        "## Next steps\n\n" + "\n".join(f"- {_fill(rng, s)}" for s in rng.sample(SENTENCES, 3))
    )


def search_query(rng: random.Random) -> str:
    return _fill(rng, rng.choice(SEARCH_QUERIES))


def search_results(rng: random.Random, query: str) -> str:
    hits = [f"{i}. \"{_fill(rng, s)}\" — example.org/{rng.randint(100, 999)}" for i, s in
            enumerate(rng.sample(SENTENCES, 3), 1)]
    return f"Results for '{query}':\n" + "\n".join(hits)


def file_contents(rng: random.Random, path: str) -> str:
    if path.endswith(".csv"):
        return "timestamp,sensor,value\n" + "\n".join(
            f"2026-11-{rng.randint(1, 16):02d}T{rng.randint(0, 23):02d}:00,s{rng.randint(1, 9)},{rng.uniform(0, 100):.2f}"
            for _ in range(6))
    if path.endswith(".py"):
        return rng.choice([c for lang, c in CODE_SNIPPETS if lang == "python"])
    if path.endswith(".json"):
        return '{"runs": %d, "mean": %.3f, "status": "ok"}' % (rng.randint(5, 50), rng.random())
    return paragraph(rng, 3)


def run_output(rng: random.Random) -> str:
    return f"$ python analysis.py\nloaded {rng.randint(100, 5000)} rows\nmean={rng.random():.4f} std={rng.random():.4f}\ndone in {rng.uniform(0.2, 9):.2f}s"


def peer_message(rng: random.Random) -> str:
    return rng.choice([
        "Heads up: I found a source that contradicts our assumption about the {t}.",
        "Can you double-check my numbers on the {t}? Something looks off.",
        "I've finished the {t} part, you can use my results for the {t2}.",
        "FYI the {t} data in the shared folder is outdated, use the new file.",
    ]).format(t=rng.choice(TOPIC_WORDS), t2=rng.choice(TOPIC_WORDS))


def handoff_note(rng: random.Random, next_role: str) -> str:
    return (f"Handing over to the {next_role}. Done so far: {paragraph(rng, 2)} "
            f"Open question: {_fill(rng, rng.choice(SENTENCES))}")


CHAT_OPENERS = [
    "I agree with {who} on the main point, but", "Building on what {who} said:", "I'd push back a little on {who} here.",
    "Good point, {who}.", "Not sure that's right, {who}.",
]


def chat_reply(rng: random.Random, previous_speaker: str | None) -> str:
    if previous_speaker is None:
        return f"Let me start. {paragraph(rng, 2)}"
    opener = rng.choice(CHAT_OPENERS).format(who=previous_speaker)
    return f"{opener} {paragraph(rng, rng.randint(1, 2))}"


def chat_conclusion(rng: random.Random) -> str:
    points = "\n".join(f"- {_fill(rng, s)}" for s in rng.sample(SENTENCES, 3))
    return f"Let's wrap up. We agree on:\n\n{points}\n\nTERMINATE"
