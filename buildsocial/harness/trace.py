"""Tracing: a record of everything that happened in a run.

A trace is a JSON Lines file (one JSON object per line), written as events
happen, so a run that crashes still leaves a trace up to the crash:

    {"type": "run_start",  "agent": "content", "input": {...}}
    {"type": "llm_call",   "attempt": 1, "messages": [...], "response": {...}, "usage": {...}, "cost": 0.035}
    {"type": "validation", "attempt": 1, "errors": [...]}
    {"type": "run_end",    "status": "ok", "output": {...}, "usage": {...}, "cost": 0.035}

Traces contain full prompts and outputs (that is what makes them useful), so the
traces/ folder is git-ignored. They never contain API keys: only messages and
responses are recorded, never request headers.

Generic: knows nothing about social media or about any LLM provider.
"""

import copy
import json
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from buildsocial.harness.llm.types import Usage

CostFn = Callable[[Usage], float]


class Tracer:
    """Base tracer. Builds event records; subclasses decide where they go.

    Used directly, it records nothing (a "null" tracer), so code that doesn't
    care about tracing can ignore it.
    """

    def __init__(self, cost_fn: CostFn | None = None, currency: str | None = None):
        self.run_id = uuid.uuid4().hex[:8]
        self._cost_fn = cost_fn
        self._currency = currency

    def event(self, type: str, **data: Any) -> None:
        record: dict[str, Any] = {
            "type": type,
            "run_id": self.run_id,
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        }
        # Any event that carries token usage also gets its cost.
        usage = data.get("usage")
        if isinstance(usage, Usage) and self._cost_fn is not None:
            record["cost"] = round(self._cost_fn(usage), 6)
            record["currency"] = self._currency
        # Convert now, not later: callers may keep changing the objects they passed in
        # (the agent loop appends to its message list after each call).
        record.update(_jsonable(data))
        self.write(record)

    def write(self, record: dict[str, Any]) -> None:
        """Override to store the record. The base tracer drops it."""

    def scoped(self, cost_fn: CostFn | None, currency: str | None) -> "Tracer":
        """Same destination and run id, different pricing.

        For pipelines where each step uses a different model (and maybe currency):
        every step's events go to one trace, each costed with its own model's prices.
        """
        child = copy.copy(self)  # shallow: shares the file path / record list
        child._cost_fn, child._currency = cost_fn, currency
        return child


class FileTracer(Tracer):
    """Writes one JSON line per event to <directory>/<time>_<label>_<run_id>.jsonl."""

    def __init__(
        self,
        directory: Path,
        label: str,
        cost_fn: CostFn | None = None,
        currency: str | None = None,
    ):
        super().__init__(cost_fn, currency)
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
        self.path = directory / f"{stamp}_{label}_{self.run_id}.jsonl"

    def write(self, record: dict[str, Any]) -> None:
        # Open, append, close for every event: slower than keeping the file open,
        # but each event is safely on disk even if the process dies right after.
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


class MemoryTracer(Tracer):
    """Keeps events in a list. For tests."""

    def __init__(self, cost_fn: CostFn | None = None, currency: str | None = None):
        super().__init__(cost_fn, currency)
        self.records: list[dict[str, Any]] = []

    def write(self, record: dict[str, Any]) -> None:
        self.records.append(record)


# ----------------------------------------------------------------------
# Reading and displaying traces
# ----------------------------------------------------------------------


def read_trace(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def latest_trace(directory: Path) -> Path | None:
    # File names start with a timestamp, so the last name in sorted order is the newest.
    files = sorted(directory.glob("*.jsonl"))
    return files[-1] if files else None


def format_trace(records: list[dict[str, Any]], full: bool = False) -> str:
    """Human-readable view of a trace. full=False shortens long prompt text."""
    lines: list[str] = []
    rule = "─" * 60

    for r in records:
        kind = r["type"]
        if kind == "run_start":
            lines += [f"RUN {r['run_id']} | agent: {r.get('agent')} | started {r['ts']}", rule]
            if full:
                lines += ["INPUT:", json.dumps(r.get("input"), indent=2, ensure_ascii=False), rule]

        elif kind == "llm_call":
            resp = r.get("response", {})
            lines.append(
                f"ATTEMPT {r.get('attempt')} | {resp.get('model')} | {r.get('duration_s', 0):.1f}s | "
                f"{_usage_text(r.get('usage', {}))}{_cost_text(r)}"
            )
            for m in r.get("messages", []):
                lines.append(f"\n  [{m['role']}]")
                lines.append(_indent(_shorten(m.get("content") or "", full)))
            for note in resp.get("http_retries") or []:
                lines.append(f"  [http retry] {note}")
            for q in resp.get("search_queries") or []:
                lines.append(f"\n  [web search] {q}")
            lines.append("\n  [reply]")
            lines.append(_indent(resp.get("text") or "(no text)"))
            for c in resp.get("citations") or []:
                lines.append(f"  [source] {c.get('title')}: \"{(c.get('cited_text') or '')[:80]}\"")
            if resp.get("stop_reason") != "end":
                lines.append(f"  stop reason: {resp.get('stop_reason')}")

        elif kind == "validation":
            errors = r.get("errors") or []
            if errors:
                lines.append(f"\n  CHECKS FAILED ({len(errors)}):")
                lines += [f"    - {e}" for e in errors]
            else:
                lines.append("\n  CHECKS PASSED")
            lines.append(rule)

        elif kind == "run_end":
            lines.append(
                f"RESULT ({r.get('agent', '?')}): {r.get('status')} | attempts: {r.get('attempts', '?')} | "
                f"{r.get('duration_s', 0):.1f}s | {_usage_text(r.get('usage', {}))}{_cost_text(r)}"
            )
            if r.get("error"):
                lines.append(f"error: {r['error']}")

        elif kind == "pipeline_start":
            lines += [f"PIPELINE {r['run_id']} | brand: {r.get('brand')} | started {r['ts']}", "═" * 60]

        elif kind == "pipeline_end":
            lines.append("═" * 60)
            for s in r.get("steps") or []:
                lines.append(
                    f"  {s['agent']:<10} {s['attempts']} attempt(s) {s['seconds']:>6.1f}s   "
                    f"{s['currency']} {s['cost']:.4f}"
                )
            totals = ", ".join(f"{cur} {amount:.4f}" for cur, amount in (r.get("totals") or {}).items())
            lines.append(f"PIPELINE {r.get('status')} | total: {totals or 'n/a'}")
            if r.get("error"):
                lines.append(f"error: {r['error']}")

        else:  # event types added later (tools, pipeline steps) still show up
            lines.append(f"{kind}: {json.dumps({k: v for k, v in r.items() if k not in ('type', 'run_id', 'ts')}, ensure_ascii=False)[:300]}")

    return "\n".join(lines)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _jsonable(value: Any) -> Any:
    """Turn Pydantic models (anywhere inside dicts/lists) into plain JSON-ready data."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _usage_text(u: dict[str, Any]) -> str:
    text = (
        f"tokens: in {u.get('input_tokens', 0)}, cache read {u.get('cache_read_tokens', 0)}, "
        f"cache write {u.get('cache_write_tokens', 0)}, out {u.get('output_tokens', 0)} "
        f"(reasoning {u.get('reasoning_tokens', 0)})"
    )
    if u.get("web_searches"):
        text += f", web searches {u['web_searches']}"
    return text


def _cost_text(r: dict[str, Any]) -> str:
    return f" | cost: {r.get('currency') or ''} {r['cost']:.4f}" if "cost" in r else ""


def _shorten(text: str, full: bool, limit: int = 400) -> str:
    if full or len(text) <= limit:
        return text
    return f"{text[:limit]}... [{len(text) - limit} more characters; use --full to see all]"


def _indent(text: str, prefix: str = "  ") -> str:
    return "\n".join(prefix + line for line in text.splitlines())


# ----------------------------------------------------------------------
# Spend: total cost across trace files
# ----------------------------------------------------------------------


def summarize_spend(paths: list[Path], since: str | None = None) -> dict[tuple[str, str], dict[str, float]]:
    """Total cost per (currency, model) over llm_call events, optionally since a date (YYYY-MM-DD).

    Only CLI runs write trace files, so this is the recorded spend of real runs
    (tests use in-memory tracers and never appear here).
    """
    totals: dict[tuple[str, str], dict[str, float]] = {}
    for path in paths:
        for r in read_trace(path):
            if r.get("type") != "llm_call" or "cost" not in r:
                continue
            if since and r.get("ts", "") < since:
                continue
            key = (r.get("currency") or "?", (r.get("response") or {}).get("model") or "?")
            t = totals.setdefault(key, {"calls": 0, "cost": 0.0, "web_searches": 0})
            t["calls"] += 1
            t["cost"] += r["cost"]
            t["web_searches"] += (r.get("usage") or {}).get("web_searches", 0)
    return totals
