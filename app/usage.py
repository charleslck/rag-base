"""Usage accounting: how many tokens every answered question actually cost.

Three layers cooperate:

- ``providers`` fills a :class:`Usage` in place — exact counts when the API
  reports ``usage``, an estimate from characters otherwise (demo mode, or a
  provider that omits the field).
- ``rag`` threads the same object through so one question produces exactly one
  meter reading, whether it streamed or not.
- ``main`` persists it with :func:`record`, adding who asked (account or API
  key) and how long the answer took.

Everything downstream — per-plan quota, invoices, cost alarms — reads the
``usage_events`` table, so this module is the only place that decides what a
"request" costs.
"""
import re
import time
from dataclasses import dataclass

from . import database as db
from .config import settings

_CJK_CHAR_RE = re.compile(r"[\u4e00-\u9fff]")


def estimate_tokens(text: str) -> int:
    """Rough token count: a CJK character is one token, latin is ~4 chars each.

    Deliberately not a real tokenizer: the meter needs a stable, dependency-free
    number for providers that do not report usage, and being off by a few
    percent on a free-tier estimate is fine. When the provider does report
    usage, the exact number always wins.
    """
    if not text:
        return 0
    cjk = len(_CJK_CHAR_RE.findall(text))
    latin = len(text) - cjk
    return cjk + (latin + 3) // 4


def estimate_messages(messages: list[dict]) -> int:
    """Prompt size of a chat payload, including the per-message overhead."""
    return sum(estimate_tokens(m.get("content") or "") + 4 for m in messages)


@dataclass
class Usage:
    """Mutable meter reading for one request."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    contexts: int = 0          # retrieved passages that made it into the prompt
    status: str = "ok"         # ok | error | fallback
    measured: bool = False     # True when the provider reported exact counts

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def cost(prompt_tokens: int, completion_tokens: int) -> float:
    """Money for a token count, using the configured per-million prices."""
    return round(
        prompt_tokens / 1_000_000 * settings.price_per_mtok_in
        + completion_tokens / 1_000_000 * settings.price_per_mtok_out,
        6,
    )


def record(user_id: str, usage: Usage, *, source: str = "web", api_key_id: str | None = None,
           model: str = "", latency_ms: int = 0) -> str:
    """Persist one reading and return the event id.

    Never raises: a metering failure must not turn an answered question into a
    500 for the end user.
    """
    try:
        return db.add_usage_event(
            user_id,
            api_key_id=api_key_id,
            source=source,
            model=model or ("demo-extractive" if settings.chat_demo else settings.llm_model),
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            contexts=usage.contexts,
            latency_ms=latency_ms,
            status=usage.status,
        )
    except Exception:  # pragma: no cover - defensive: the meter is best-effort
        return ""


def summary(user_id: str, days: int = 30) -> dict:
    """Everything the billing screen needs, in one round trip."""
    days = max(1, min(int(days), 365))
    since = time.time() - days * 86400
    totals = db.usage_totals(user_id, since)
    return {
        "days": days,
        "since": since,
        "totals": {
            **totals,
            "cost": cost(totals["prompt_tokens"], totals["completion_tokens"]),
        },
        "daily": db.usage_daily(user_id, since),
        "by_key": db.usage_by_key(user_id, since),
        "pricing": {
            "per_mtok_in": settings.price_per_mtok_in,
            "per_mtok_out": settings.price_per_mtok_out,
            "currency": "USD",
        },
        "measured": not settings.chat_demo,
    }
