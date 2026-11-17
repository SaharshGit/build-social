"""Command-line entry point.

    uv run buildsocial research --brand examples/brew-house.yaml --topic "monsoon special" --platform instagram
    uv run buildsocial content  --brand examples/brew-house.yaml --topic "monsoon special" --platform instagram
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
from collections.abc import Callable
from pathlib import Path
from typing import Any, get_args

import yaml
from pydantic import BaseModel, ValidationError

from buildsocial.agents.content import ContentAgent, ContentInput
from buildsocial.agents.research import ResearchAgent, ResearchInput
from buildsocial.harness.agent import AgentError, AgentResult
from buildsocial.harness.llm.base import LLMError
from buildsocial.harness.llm.registry import Pricing, get_client, resolve_model
from buildsocial.harness.trace import FileTracer, Tracer, format_trace, latest_trace, read_trace
from buildsocial.schemas import BrandProfile, Format, Goal, Platform, PostBrief, PostDraft, ResearchReport

TRACES_DIR = Path("traces")
LINE = "─" * 60


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="buildsocial", description="BuildSocial agents from the command line")
    commands = parser.add_subparsers(dest="command", required=True)

    research = commands.add_parser("research", help="research a topic on the web for a brand's post")
    add_brief_args(research)

    content = commands.add_parser("content", help="write one post draft for a brand")
    add_brief_args(content)
    content.add_argument("--format", default="text", choices=get_args(Format))

    trace = commands.add_parser("trace", help="show what happened in a run")
    trace.add_argument("file", nargs="?", type=Path, help="trace file (default: the most recent in traces/)")
    trace.add_argument("--full", action="store_true", help="show complete prompts and the run's input")

    args = parser.parse_args(argv)
    try:
        if args.command == "trace":
            return show_trace(args)
        brand, brief = load_brand(args.brand), make_brief(args)
        if args.command == "research":
            print(f"Researching '{brief.topic}' for {brand.name}...", file=sys.stderr)
            agent = ResearchAgent()
            return asyncio.run(run_agent(args, agent, ResearchInput(brand=brand, brief=brief), print_report))
        print(f"Writing {brief.platform} post for {brand.name}...", file=sys.stderr)
        agent = ContentAgent()
        return asyncio.run(run_agent(args, agent, ContentInput(brand=brand, brief=brief), print_draft))
    except (FileNotFoundError, ValidationError, yaml.YAMLError, AgentError, LLMError, RuntimeError, KeyError) as e:
        # Expected failures (bad input file, API problem, agent gave up): short message, no traceback.
        print(f"error: {e}", file=sys.stderr)
        return 1


def add_brief_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--brand", required=True, type=Path, help="brand profile YAML file")
    p.add_argument("--topic", required=True, help="what the post is about")
    p.add_argument("--platform", required=True, choices=get_args(Platform))
    p.add_argument("--goal", default="engagement", choices=get_args(Goal))
    p.add_argument("--notes", help="extra instructions for this post")
    p.add_argument("--json", action="store_true", help="print the result as raw JSON")
    p.add_argument("--no-trace", action="store_true", help="don't write a trace file for this run")


def make_brief(args: argparse.Namespace) -> PostBrief:
    return PostBrief(
        topic=args.topic,
        platform=args.platform,
        format=getattr(args, "format", "text"),
        goal=args.goal,
        notes=args.notes,
    )


async def run_agent(
    args: argparse.Namespace, agent: Any, input: BaseModel, printer: Callable[[Any], None]
) -> int:
    """Run any agent with tracing and cost reporting, then print its output."""
    _, model_cfg = resolve_model(agent.name)
    pricing = model_cfg.pricing

    tracer: Tracer
    if args.no_trace:
        tracer = Tracer()  # records nothing
    else:
        tracer = FileTracer(TRACES_DIR, agent.name, cost_fn=pricing.cost, currency=pricing.currency)

    start = time.perf_counter()
    try:
        async with get_client(agent.name) as llm:
            result = await agent.run(input, llm, tracer)
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
        printer(result.output)
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
    print(f"\n{LINE}\n{draft.platform.upper()} POST\n{LINE}")
    print(draft.full_text)  # exactly as it would be published
    print(LINE)
    print(f"Image idea: {draft.image_idea}")
    print(f"Rationale:  {draft.rationale}")
    print(f"Length:     hook {len(draft.hook)} chars, post {len(draft.full_text)} chars, {len(draft.hashtags)} hashtags")


def print_report(report: ResearchReport) -> None:
    print(f"\n{LINE}\nRESEARCH REPORT\n{LINE}")
    print(report.summary)
    print(f"\nFindings ({len(report.findings)}):")
    for f in report.findings:
        refs = ", ".join(str(n) for n in f.source_ids)
        print(f"  - {f.fact}  [{refs}]")
    print("\nTrends:")
    for t in report.trends:
        print(f"  - {t}")
    print("\nAngles:")
    for a in report.angles:
        print(f"  - {a}")
    print(f"\nSources ({len(report.sources)}):")
    for s in report.sources:
        print(f"  [{s.id}] {s.title}")
    print(f"\nSearched for: {'; '.join(report.search_queries) or '(nothing)'}")
    print(LINE)


def print_stats(result: AgentResult, elapsed: float, pricing: Pricing) -> None:
    u = result.usage
    searches = f", web searches {u.web_searches}" if u.web_searches else ""
    print(
        f"\n[{result.model}] {result.attempts} attempt(s), {elapsed:.1f}s | "
        f"tokens: in {u.input_tokens}, cache read {u.cache_read_tokens}, cache write {u.cache_write_tokens}, "
        f"out {u.output_tokens} (reasoning {u.reasoning_tokens}){searches} | "
        f"cost: {pricing.currency} {pricing.cost(u):.4f}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    sys.exit(main())
