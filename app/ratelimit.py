"""Sliding-window rate limiter for credential endpoints.

The widget quota (quota.py) counts *successful* use of a resource; this module
counts *attempts* against an endpoint, which is a different problem — its job
is to make brute-forcing a password (or resending verification mails) slow
enough to be pointless. Counters live in process memory: a restart clears
them, which merely widens the window an attacker has, never the ceiling.
"""
import threading
import time

_lock = threading.Lock()
_attempts: dict[str, list[float]] = {}
_MAX_KEYS = 20000


def _prune(now: float, window: float) -> None:
    if len(_attempts) > _MAX_KEYS:
        for key in [k for k, hits in _attempts.items() if hits[-1] < now - window]:
            _attempts.pop(key, None)


def hit(key: str, max_attempts: int, window_seconds: float) -> tuple[bool, int]:
    """Record one attempt. Returns (allowed, retry_after_seconds)."""
    now = time.time()
    with _lock:
        _prune(now, window_seconds)
        recent = [t for t in _attempts.get(key, []) if t > now - window_seconds]
        if len(recent) >= max_attempts:
            retry = int(recent[0] + window_seconds - now) + 1
            _attempts[key] = recent
            return False, max(retry, 1)
        recent.append(now)
        _attempts[key] = recent
        return True, 0


def clear(key: str) -> None:
    """A successful login forgives the failed attempts that preceded it."""
    with _lock:
        _attempts.pop(key, None)


def reset() -> None:
    """Test helper: forget every counter."""
    with _lock:
        _attempts.clear()
