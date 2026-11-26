"""Command-line entry point.

    uv run buildsocial research --brand examples/mamaearth.yaml --topic "onion hair oil in winter" --platform instagram
    uv run buildsocial content  --brand examples/brew-house.yaml --topic "monsoon special" --platform instagram
    uv run buildsocial run      --brand examples/brew-house.yaml --topic "monsoon special" --platform instagram \
                                --research-file examples/research/brew-house-masala-chai.json
    uv run buildsocial run      --brand examples/mamaearth.yaml --topic "onion hair oil in winter" --platform instagram
    uv run buildsocial trace              # show the most recent run
    uv run buildsocial trace <file> --full

This file only wires things together: load inputs -> get an LLM client -> run an
agent -> print. No agent logic lives here, so a web API can later do the same
wiring from HTTP requests.
"""

import argparse
import asyncio
import json
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, get_args

import yaml
from pydantic import BaseModel, ValidationError

from buildsocial.agents.content import ContentAgent, ContentInput
from buildsocial.harness.agent import AgentError, AgentResult
from buildsocial.harness.llm.base import LLMError
from buildsocial.harness.llm.registry import Pricing, get_client, resolve_model
from buildsocial.harness.storage import Storage, get_storage
from buildsocial.harness.trace import (
    TRACES_PREFIX,
    Tracer,
    format_trace,
    latest_trace,
    parse_trace,
    summarize_spend,
    tracer_for_storage,
)
from buildsocial import evals
from buildsocial.output import save_post
from buildsocial.pipeline import (
    PipelineResult,
    StepRecord,
    StepRunner,
    default_pricing,
    gather_research,
    revise_with_feedback,
    run_pipeline,
)
from buildsocial.schemas import (
    BrandProfile,
    ContentPlan,
    Format,
    Goal,
    Platform,
    PostBrief,
    PostDraft,
    ResearchReport,
    Review,
)

_STORE: Storage | None = None


def store() -> Storage:
    """Where runtime data goes (local folder or GCS bucket), from .env. Created once."""
    global _STORE
    if _STORE is None:
        _STORE = get_storage()
    return _STORE


def new_tracer(args: argparse.Namespace, label: str, pricing: Pricing | None = None) -> Tracer:
    if getattr(args, "no_trace", False):
        return Tracer()  # records nothing
    if pricing:
        return tracer_for_storage(store(), label, pricing.cost, pricing.currency)
    return tracer_for_storage(store(), label)


def finish_tracer(tracer: Tracer) -> None:
    tracer.close()
    if tracer.location():
        print(f"trace: {tracer.location()}", file=sys.stderr)
LINE = "─" * 60


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="buildsocial", description="BuildSocial agents from the command line")
    commands = parser.add_subparsers(dest="command", required=True)

    research = commands.add_parser("research", help="market + topic web research for a brand's post")
    add_brief_args(research)
    add_research_args(research)

    content = commands.add_parser("content", help="write one post draft for a brand")
    add_brief_args(content)
    content.add_argument("--format", default="text", choices=get_args(Format))

    run = commands.add_parser("run", help="full pipeline: research -> strategy -> content")
    add_brief_args(run)
    run.add_argument("--format", default="text", choices=get_args(Format))
    add_research_args(run)
    run.add_argument(
        "--research-file", type=Path, help="use a saved research report (JSON) instead of researching"
    )
    run.add_argument(
        "--interactive", action="store_true",
        help="ask to approve/revise even when input isn't a terminal (e.g. answers piped in)",
    )

    trace = commands.add_parser("trace", help="show what happened in a run")
    trace.add_argument("file", nargs="?", help="trace: storage key (traces/...jsonl) or local path (default: newest)")
    trace.add_argument("--full", action="store_true", help="show complete prompts and the run's input")

    ev = commands.add_parser("eval", help="run the eval cases and score them (frozen research, no Gemini cost)")
    ev.add_argument("--cases", type=Path, default=evals.DEFAULT_CASES)
    ev.add_argument("--only", nargs="+", help="run only these case ids")
    ev.add_argument("--label", default="run", help="name for this run, e.g. baseline or new-reviewer-prompt")
    ev.add_argument("--concurrency", type=int, default=3)
    ev.add_argument("--max-revisions", type=int, default=1, help="automatic revise rounds per post")

    evr = commands.add_parser("eval-revisions", help="score revisions: does a revision do what the feedback asked?")
    evr.add_argument("--cases", type=Path, default=Path("evals/revision_cases.yaml"))
    evr.add_argument("--only", nargs="+", help="run only these case ids")
    evr.add_argument("--label", default="run")
    evr.add_argument("--concurrency", type=int, default=3)
    evr.add_argument("--make-starts", action="store_true",
                     help="(re)generate the fixed starting posts (about INR 0.15 each), then stop")

    cmp = commands.add_parser("eval-compare", help="compare two saved eval runs")
    cmp.add_argument("a", help="saved run: storage key (evals/results/...json) or local path")
    cmp.add_argument("b", help="saved run: storage key or local path")

    api = commands.add_parser("api", help="run the web API (then open http://127.0.0.1:8000/docs)")
    api.add_argument("--host", default="127.0.0.1", help="127.0.0.1 = this laptop only (default)")
    api.add_argument("--port", type=int, default=8000)
    api.add_argument("--reload", action="store_true", help="restart automatically when code changes")

    wk = commands.add_parser("worker", help="process queued pipeline runs (started via the API)")
    wk.add_argument("--once", action="store_true", help="process at most one queued run, then exit")
    wk.add_argument("--poll", type=float, default=2.0, help="seconds between queue checks when idle")

    imp = commands.add_parser("import-brands", help="load brand YAML files into the database (create or update)")
    imp.add_argument("files", nargs="+", type=Path)

    spend = commands.add_parser("spend", help="total recorded LLM spend, from trace files")
    spend.add_argument("--since", help="only count runs on or after this date (YYYY-MM-DD)")

    args = parser.parse_args(argv)
    try:
        if args.command == "trace":
            return show_trace(args)
        if args.command == "spend":
            return show_spend(args)
        if args.command == "api":
            import uvicorn

            uvicorn.run("buildsocial.api.app:app", host=args.host, port=args.port, reload=args.reload)
            return 0
        if args.command == "worker":
            return asyncio.run(run_worker(args))
        if args.command == "import-brands":
            return asyncio.run(import_brands(args.files))
        if args.command == "eval":
            return asyncio.run(run_evals(args))
        if args.command == "eval-revisions":
            return asyncio.run(run_revision_evals(args))
        if args.command == "eval-compare":
            print(evals.compare(read_saved(args.a), read_saved(args.b)))
            return 0
        brand, brief = load_brand(args.brand), make_brief(args)
        if args.command == "run":
            return asyncio.run(run_full_pipeline(args, brand, brief))
        if args.command == "research":
            return asyncio.run(run_research(args, brand, brief))
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


def add_research_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--fresh-research", action="store_true",
        help="ignore cached research and search the web again (costs Gemini credit; result is cached)",
    )
    p.add_argument("--no-cache", action="store_true", help="don't read or write the research cache")


def research_store(args: argparse.Namespace) -> Storage | None:
    return None if args.no_cache else store()


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

    tracer = new_tracer(args, agent.name, pricing)
    start = time.perf_counter()
    try:
        async with get_client(agent.name) as llm:
            result = await agent.run(input, llm, tracer)
    except AgentError as e:
        # Failed runs still cost money: report it before the error is printed.
        print(f"cost of failed run: {pricing.currency} {pricing.cost(e.usage):.4f}", file=sys.stderr)
        raise
    finally:
        finish_tracer(tracer)
    elapsed = time.perf_counter() - start

    if args.json:
        print(result.output.model_dump_json(indent=2))
    else:
        printer(result.output)
    print_stats(result, elapsed, pricing)
    return 0


async def run_full_pipeline(args: argparse.Namespace, brand: BrandProfile, brief: PostBrief) -> int:
    research = None
    if args.research_file:
        if not args.research_file.exists():
            raise FileNotFoundError(f"research file not found: {args.research_file}")
        research = ResearchReport.model_validate_json(args.research_file.read_text(encoding="utf-8"))

    tracer = new_tracer(args, "pipeline")
    try:
        return await _pipeline_and_approval(args, brand, brief, research, tracer)
    finally:
        finish_tracer(tracer)  # after approval too, so feedback rounds are in the trace


async def _pipeline_and_approval(
    args: argparse.Namespace, brand: BrandProfile, brief: PostBrief, research: ResearchReport | None, tracer: Tracer
) -> int:
    print(f"Running pipeline for {brand.name} on '{brief.topic}' ({brief.platform})...", file=sys.stderr)
    result = await run_pipeline(
        brand, brief, research=research, cache=research_store(args), fresh_research=args.fresh_research,
        tracer=tracer,
    )

    if args.json:
        print(json.dumps({
            "research": result.research.model_dump(mode="json"),
            "plan": result.plan.model_dump(mode="json"),
            "draft": result.draft.model_dump(mode="json"),
            "review": result.review.model_dump(mode="json"),
            "earlier": [{"draft": d.model_dump(mode="json"), "review": r.model_dump(mode="json")}
                        for d, r in result.history],
        }, indent=2, ensure_ascii=False))
    else:
        print_plan(result.plan, result.research)
        for n, (_, earlier_review) in enumerate(result.history, 1):
            print_review(earlier_review, title=f"REVIEW OF DRAFT {n} (sent back for revision)")
        print_draft(result.draft)
        print_review(result.review, title="REVIEW OF FINAL DRAFT")

    # Human approval: ask when someone is at the terminal (or --interactive); otherwise save as pending.
    interactive = args.interactive or (sys.stdin.isatty() and not args.json)
    feedback: list[str] = []
    status: str | None = "pending"
    if interactive:
        result, status, feedback = await approval_loop(result, brand, brief, tracer)

    print_steps(result.steps, result.research_origin)
    if status is None:
        print("Not saved.", file=sys.stderr)
    else:
        md_key, _ = save_post(store(), brand, brief, result, status, feedback)
        print(f"Saved ({status.upper()}): {store().location(md_key)}", file=sys.stderr)
    return 0


MAX_FEEDBACK_ROUNDS = 3


async def approval_loop(
    result: PipelineResult,
    brand: BrandProfile,
    brief: PostBrief,
    tracer: Tracer,
    ask: Callable[[str], str] = input,
    revise: Callable[..., Any] = revise_with_feedback,
) -> tuple[PipelineResult, str | None, list[str]]:
    """Approve, revise with feedback, save for later, or quit. Returns (result, status or None, feedback)."""
    feedback: list[str] = []
    while True:
        try:
            choice = ask("\n[a] approve   [r] revise with feedback   [s] save for later   [q] quit without saving\n> ")
        except EOFError:  # input ran out (e.g. piped): keep the post, undecided
            return result, "pending", feedback
        choice = choice.strip().lower()[:1]
        if choice == "a":
            return result, "approved", feedback
        if choice == "s":
            return result, "pending", feedback
        if choice == "q":
            return result, None, feedback
        if choice != "r":
            print("Please type a, r, s or q.")
            continue
        if len(feedback) >= MAX_FEEDBACK_ROUNDS:
            print(f"Already revised {MAX_FEEDBACK_ROUNDS} times; approve, save or quit (or edit the saved file).")
            continue
        try:
            text = ask("What should change?\n> ").strip()
        except EOFError:
            return result, "pending", feedback
        if not text:
            continue
        print("Revising...", file=sys.stderr)
        result = await revise(result, brand, brief, text, tracer=tracer)
        feedback.append(text)
        print_draft(result.draft)
        print_review(result.review, title="REVIEW OF FINAL DRAFT")


async def run_research(args: argparse.Namespace, brand: BrandProfile, brief: PostBrief) -> int:
    tracer = new_tracer(args, "research")
    print(f"Researching '{brief.topic}' for {brand.name} ({brand.industry}, {brand.market})...", file=sys.stderr)
    runner = StepRunner(tracer, get_client, default_pricing)
    try:
        report, origin = await gather_research(
            brand, brief, runner, cache=research_store(args), fresh=args.fresh_research
        )
    finally:
        finish_tracer(tracer)
    if args.json:
        print(report.model_dump_json(indent=2))
    else:
        print_report(report)
    print_steps(runner.steps, origin)
    return 0


def print_steps(steps: list[StepRecord], research_origin: dict[str, str]) -> None:
    print("\nResearch: " + ", ".join(f"{layer} from {how}" for layer, how in research_origin.items()), file=sys.stderr)
    print("Steps:", file=sys.stderr)
    totals: dict[str, float] = {}
    for s in steps:
        print(
            f"  {s.agent:<16} [{s.result.model}] {s.result.attempts} attempt(s), {s.seconds:.1f}s, "
            f"{s.pricing.currency} {s.cost:.4f}" + (f", {s.result.usage.web_searches} web searches"
                                                   if s.result.usage.web_searches else ""),
            file=sys.stderr,
        )
        totals[s.pricing.currency] = totals.get(s.pricing.currency, 0.0) + s.cost
    print("Total: " + (", ".join(f"{cur} {amount:.4f}" for cur, amount in totals.items()) or "nothing (all cached)"),
          file=sys.stderr)


def print_review(review: Review, title: str) -> None:
    print(f"\n{LINE}\n{title}\n{LINE}")
    if not review.issues:
        print("✓ No issues found.")
        return
    for i, issue in enumerate(review.issues, 1):
        print(f"{i}. [{issue.type}] \"{issue.quote}\"")
        print(f"   problem: {issue.problem}")
        print(f"   fix:     {issue.fix}")
    if title.startswith("REVIEW OF FINAL"):
        print(f"\n⚠ {len(review.issues)} issue(s) still open: decide before publishing.")


def print_plan(plan: ContentPlan, research: ResearchReport) -> None:
    print(f"\n{LINE}\nPLAN\n{LINE}")
    if plan.brief_concerns:
        print("⚠ Concerns about the brief:")
        for c in plan.brief_concerns:
            print(f"  - {c}")
        print()
    print(f"Angle:       {plan.angle}")
    print(f"Key message: {plan.key_message}")
    print(f"CTA:         {plan.cta_direction}")
    print("Facts to use:")
    for n, f in zip(plan.finding_ids, plan.approved_facts(research)):
        sites = ", ".join(s.title or "?" for i in f.source_ids if (s := research.source(i)))
        print(f"  {n}. {f.fact}  ({sites})")
    print(f"Why:         {plan.rationale}")


async def run_evals(args: argparse.Namespace) -> int:
    cases = evals.load_cases(args.cases, args.only)
    print(f"Running {len(cases)} eval case(s), {args.concurrency} at a time...", file=sys.stderr)
    results = await evals.run_eval(
        cases,
        concurrency=args.concurrency,
        max_revisions=args.max_revisions,
        tracer_for=lambda case: new_tracer(args, f"eval_{case.id}"),
    )
    print(evals.format_results(results))
    key = evals.save_results(results, args.label, store())
    print(f"\nSaved: {store().location(key)}", file=sys.stderr)
    return 0


async def run_revision_evals(args: argparse.Namespace) -> int:
    from buildsocial import revision_evals as rev

    starts, cases = rev.load(args.cases, args.only)
    if args.make_starts:
        for start in starts.values():
            tracer = new_tracer(args, f"revstart_{start.id}")
            try:
                path = await rev.make_start(start, tracer=tracer)
            finally:
                tracer.close()
            print(f"saved {path}", file=sys.stderr)
        return 0
    missing = [s for s in {c.start for c in cases} if not (rev.STARTS_DIR / f"{s}.json").exists()]
    if missing:
        print(f"Missing starting posts {missing}: run with --make-starts first.", file=sys.stderr)
        return 1
    print(f"Running {len(cases)} revision case(s), {args.concurrency} at a time...", file=sys.stderr)
    results = await rev.run_eval(cases, starts, concurrency=args.concurrency,
                                 tracer_for=lambda case: new_tracer(args, f"evalrev_{case.id}"))
    print(rev.format_results(results))
    key = rev.save_results(results, args.label, store())
    print(f"\nSaved: {store().location(key)}", file=sys.stderr)
    return 0


async def run_worker(args: argparse.Namespace) -> int:
    from buildsocial.db.session import engine
    from buildsocial.worker import WorkerDeps, process_one, run_forever

    no_trace = argparse.Namespace(no_trace=False)
    deps = WorkerDeps(cache=store(), tracer_for=lambda run_id: new_tracer(no_trace, f"run_{str(run_id)[:8]}"))
    try:
        if args.once:
            if await process_one(deps) is None:
                print("no queued runs")
        else:
            await run_forever(deps, poll_seconds=args.poll)
    finally:
        await engine().dispose()
    return 0


async def import_brands(files: list[Path]) -> int:
    from buildsocial.db import brands as brand_ops
    from buildsocial.db.session import engine, session

    profiles = [load_brand(f) for f in files]  # validate every file before writing anything
    async with session() as db:
        org = await brand_ops.default_org(db)
        for f, profile in zip(files, profiles):
            brand, created = await brand_ops.upsert_brand(db, org, profile)
            print(f"{'created' if created else 'updated'}: {brand.name} ({brand.id})  <- {f}")
    await engine().dispose()
    return 0


def show_spend(args: argparse.Namespace) -> int:
    totals = summarize_spend(store(), since=args.since)
    if not totals:
        print("No recorded spend yet." + (f" (since {args.since})" if args.since else ""))
        return 0
    print(f"Recorded LLM spend{' since ' + args.since if args.since else ''} (from {store().location(TRACES_PREFIX)}/):\n")
    by_currency: dict[str, float] = {}
    for (currency, model), t in sorted(totals.items()):
        searches = f", {int(t['web_searches'])} web searches" if t["web_searches"] else ""
        print(f"  {model:<26} {int(t['calls']):>4} calls{searches:<20}  {currency} {t['cost']:.4f}")
        by_currency[currency] = by_currency.get(currency, 0.0) + t["cost"]
    print("\n  Total: " + ", ".join(f"{cur} {amount:.4f}" for cur, amount in by_currency.items()))
    print("\n  Note: Gemini searches are priced at the paid rate ($14/1,000), but the first 5,000 a month are free,")
    print("  so Gemini spend is overstated while you're inside that allowance.")
    return 0


def show_trace(args: argparse.Namespace) -> int:
    if args.file:
        text, where = read_saved(args.file), str(args.file)
    else:
        key = latest_trace(store())
        if key is None:
            raise FileNotFoundError(f"no traces in {store().location(TRACES_PREFIX)} yet; run an agent first")
        text, where = store().read_text(key), store().location(key)
    print(f"{where}\n")
    print(format_trace(parse_trace(text), full=args.full))
    return 0


def read_saved(name: str | Path) -> str:
    """A saved file: a storage key (e.g. traces/x.jsonl), or a local path."""
    if Path(name).is_file():
        return Path(name).read_text(encoding="utf-8")
    return store().read_text(str(name))


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
