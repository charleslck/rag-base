"""Audit trail: who did what, when, from where.

Instance-wide and append-only, for the operator of an on-premise install.
Recording must never break the request it describes — a failed audit write is
a lost log line, not a failed login.
"""
from . import database as db


def record(actor: dict | None, action: str, *,
           target_type: str = "", target_id: str = "",
           detail: str | None = None, ip: str = "") -> None:
    """Append one event. `actor` is the session's user dict (None = system)."""
    try:
        db.add_audit_event(
            (actor or {}).get("id") or None,
            (actor or {}).get("email") or "system",
            action,
            target_type=target_type,
            target_id=target_id,
            detail=detail,
            ip=ip,
        )
    except Exception:  # pragma: no cover - defensive: auditing is best-effort
        pass
