"""HTTP sending with retries, shared by provider adapters.

Extracted once a second adapter (Gemini) needed exactly the same logic as Sarvam.
The leading underscore in the file name marks it as internal to the llm package.
"""

import asyncio
import random
import time
from typing import Any

import httpx

from buildsocial.harness.llm.base import LLMError

# Statuses where trying again later may succeed:
# 429 = rate limited, 500/502/503/504 = server-side trouble.
# Others (400 bad request, 401/403 bad key, 402 no credits, 404 unknown model)
# won't fix themselves, so we fail immediately with a clear error instead.
RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# If the server asks us to wait longer than this, don't wait: fail now with its message.
# (A free-tier daily quota once answered "retry in 12h".)
MAX_RETRY_WAIT_S = 60.0


async def post_json(
    http: httpx.AsyncClient,
    path: str,
    body: dict[str, Any],
    *,
    max_retries: int,
    provider: str,
    model: str,
    retry_log: list[str] | None = None,
) -> dict[str, Any]:
    """POST JSON and return the decoded response, retrying transient failures.

    retry_log: if given, a note is appended for every retry (what failed, how long we
    waited), so callers can record retries in traces instead of them being invisible.
    """
    for attempt in range(max_retries + 1):
        is_last = attempt == max_retries
        started = time.perf_counter()
        try:
            resp = await http.post(path, json=body)
        except httpx.TransportError as e:  # connection failed, timeout, etc.
            if is_last:
                raise LLMError(f"network error calling {provider}: {e!r}", retryable=True) from e
            wait = backoff(attempt)
            _note(retry_log, attempt, f"network error {type(e).__name__}", started, wait)
            await asyncio.sleep(wait)
            continue

        if resp.status_code == 200:
            return resp.json()

        retryable = resp.status_code in RETRYABLE_STATUS
        wait = retry_after(resp) or backoff(attempt)
        if retryable and not is_last and wait <= MAX_RETRY_WAIT_S:
            _note(retry_log, attempt, f"HTTP {resp.status_code}", started, wait)
            await asyncio.sleep(wait)
            continue

        raise LLMError(
            f"{provider} returned {resp.status_code} for model {model}: {resp.text[:500]}",
            status_code=resp.status_code,
            retryable=retryable,
        )
    raise AssertionError("unreachable")


def _note(log: list[str] | None, attempt: int, what: str, started: float, wait: float) -> None:
    if log is not None:
        log.append(f"try {attempt + 1}: {what} after {time.perf_counter() - started:.1f}s; retrying in {wait:.1f}s")


def backoff(attempt: int) -> float:
    """Exponential backoff with jitter: ~1s, 2s, 4s ... (max 30s).

    The random jitter stops many clients from retrying at the exact same moment.
    """
    return min(2**attempt, 30) + random.uniform(0, 1)


def retry_after(resp: httpx.Response) -> float | None:
    """Honor the server's Retry-After header (seconds) if it sent one."""
    try:
        return float(resp.headers["retry-after"])
    except (KeyError, ValueError):
        return None
