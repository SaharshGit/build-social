"""Command-line entry point.

    uv run buildsocial content --brand examples/brew-house.yaml --topic "monsoon special" --platform instagram
    uv run buildsocial trace              # show the most recent run
    uv run buildsocial trace <file> --full

This file only wires things together: load inputs -> get an LLM client -> run an
agent -> print. No agent logic lives here, so a web API can later do the same
wiring from HTTP requests.
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path
from typing import get_args

import yaml
from pydantic import ValidationError

from buildsocial.agents.content import ContentAgent, ContentInput
from buildsocial.harness.agent import AgentError, AgentResult
from buildsocial.harness.llm.base import LLMError
from buildsocial.harness.llm.registry import Pricing, get_client, resolve_model
from buildsocial.harness.trace import FileTracer, Tracer, format_trace, latest_trace, read_trace
from buildsocial.schemas import BrandProfile, Format, Goal, Platform, PostBrief, PostDraft

TRACES_DIR = Path("traces")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="buildsocial", description="BuildSocial agents from the command line")
    commands = parser.add_subparsers(dest="command", required=True)

    content = commands.add_parser("content", help="write one post draft for a brand")
    content.add_argument("--brand", required=True, type=Path, help="brand profile YAML file")
    content.add_argument("--topic", required=True, help="what the post is about")
    content.add_argument("--platform", required=True, choices=get_args(Platform))
    content.add_argument("--format", default="text", choices=get_args(Format))
    content.add_argument("--goal", default="engagement", choices=get_args(Goal))
    content.add_argument("--notes", help="extra instructions for this post")
    content.add_argument("--json", action="store_true", help="print the draft as raw JSON")
    content.add_argument("--no-trace", action="store_true", help="don't write a trace file for this run")

    trace = commands.add_parser("trace", help="show what happened in a run")
    trace.add_argument("file", nargs="?", type=Path, help="trace file (default: the most recent in traces/)")
    trace.add_argument("--full", action="store_true", help="show complete prompts and the run's input")

    args = parser.parse_args(argv)
    try:
        if args.command == "trace":
            return show_trace(args)
        return asyncio.run(run_content(args))
    except (FileNotFoundError, ValidationError, yaml.YAMLError, AgentError, LLMError, RuntimeError, KeyError) as e:
        # Expected failures (bad input file, API problem, agent gave up): short message, no traceback.
        print(f"error: {e}", file=sys.stderr)
        return 1


async def run_content(args: argparse.Namespace) -> int:
    brand = load_brand(args.brand)
    brief = PostBrief(
        topic=args.topic, platform=args.platform, format=args.format, goal=args.goal, notes=args.notes
    )
    agent = ContentAgent()
    _, model_cfg = resolve_model(agent.name)
    pricing = model_cfg.pricing

    tracer: Tracer
    if args.no_trace:
        tracer = Tracer()  # records nothing
    else:
        tracer = FileTracer(TRACES_DIR, agent.name, cost_fn=pricing.cost, currency=pricing.currency)

    print(f"Writing {brief.platform} post for {brand.name}...", file=sys.stderr)
    start = time.perf_counter()
    try:
        async with get_client(agent.name) as llm:
            result = await agent.run(ContentInput(brand=brand, brief=brief), llm, tracer)
    except AgentError as e:
        # Failed runs still cost money: report it before the error is printed.
        print(f"cost of failed run: {pricing.currency} {pricing.cost(e.usage):.4f}", file=sys.stderr)
        raise
    finally:
        if isinstance(tracer, FileTracer):
            print(f"trace: {tracer.path}", file=sys.stderr)
    elapsed = time.perf_counter() - start

    if args.json:
        print(result.output.model_dump_json(indent=2))
    else:
        print_draft(result.output)
    print_stats(result, elapsed, pricing)
    return 0


def show_trace(args: argparse.Namespace) -> int:
    path = args.file or latest_trace(TRACES_DIR)
    if path is None:
        raise FileNotFoundError(f"no traces in {TRACES_DIR}/ yet; run an agent first")
    if not path.exists():
        raise FileNotFoundError(f"trace file not found: {path}")
    print(f"{path}\n")
    print(format_trace(read_trace(path), full=args.full))
    return 0


def load_brand(path: Path) -> BrandProfile:
    if not path.exists():
        raise FileNotFoundError(f"brand file not found: {path}")
    return BrandProfile.model_validate(yaml.safe_load(path.read_text()))


def print_draft(draft: PostDraft) -> None:
    line = "─" * 60
    print(f"\n{line}\n{draft.platform.upper()} POST\n{line}")
    print(draft.full_text)  # exactly as it would be published
    print(line)
    print(f"Image idea: {draft.image_idea}")
    print(f"Rationale:  {draft.rationale}")
    print(f"Length:     hook {len(draft.hook)} chars, post {len(draft.full_text)} chars, {len(draft.hashtags)} hashtags")


def print_stats(result: AgentResult, elapsed: float, pricing: Pricing) -> None:
    u = result.usage
    print(
        f"\n[{result.model}] {result.attempts} attempt(s), {elapsed:.1f}s | "
        f"tokens: in {u.input_tokens}, cache read {u.cache_read_tokens}, cache write {u.cache_write_tokens}, "
        f"out {u.output_tokens} (reasoning {u.reasoning_tokens}) | "
        f"cost: {pricing.currency} {pricing.cost(u):.4f}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    sys.exit(main())
