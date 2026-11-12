"""Smoke test: one real LLM call through the whole LLM layer.

Run from the repo root:
    uv run python -m buildsocial.harness.llm            # default model
    uv run python -m buildsocial.harness.llm research   # model used by the "research" agent
"""

import asyncio
import json
import sys
import time

from buildsocial.harness.llm.registry import get_client, resolve_model
from buildsocial.harness.llm.types import Message


async def main(name: str) -> None:
    alias, cfg = resolve_model(name)
    print(f"→ '{name}' resolves to model alias '{alias}' ({cfg.provider}: {cfg.model})\n")

    async with get_client(name) as llm:
        start = time.perf_counter()
        resp = await llm.complete([
            Message.system("You are a social media copywriter. Be concise."),
            Message.user("Write one scroll-stopping Instagram hook about morning coffee."),
        ])
        elapsed = time.perf_counter() - start

    print("TEXT:       ", resp.text)
    if resp.reasoning:
        print("REASONING:  ", resp.reasoning[:300].replace("\n", " "), "..." if len(resp.reasoning) > 300 else "")
    print("STOP:       ", resp.stop_reason)
    print("MODEL:      ", resp.model)
    print(f"TIME:        {elapsed:.1f}s")
    print("USAGE (ours):", resp.usage.model_dump())
    print("USAGE (raw): ", json.dumps(resp.raw_usage))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "default"))
