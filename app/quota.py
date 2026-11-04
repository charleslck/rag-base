"""Per-key request accounting.

A fixed-window counter kept in process memory: an embed's traffic is bursty and
a database round-trip per message is not worth it. Counters reset on restart,
which is acceptable for a limit meant to stop a runaway client, not to bill.
"""
import threading
import time

_lock = threading.Lock()
_buckets: dict[str, dict] = {}
_MAX_KEYS = 5000


def _snapshot(bucket: dict) -> dict:
    return {
        "minute": bucket["minute_count"],
        "day": bucket["day_count"],
        "total": bucket["total"],
    }


def check(key_id: str, per_min: int = 0, per_day: int = 0) -> tuple[bool, dict]:
    """Consume one request if the key is still inside its limits."""
    now = time.time()
    minute = int(now // 60)
    today = int(now // 86400)
    with _lock:
        if len(_buckets) > _MAX_KEYS:
            for k in [k for k, v in _buckets.items() if v["day"] != today]:
                _buckets.pop(k, None)
        bucket = _buckets.get(key_id)
        if bucket is None:
            bucket = {"minute": minute, "minute_count": 0, "day": today, "day_count": 0, "total": 0}
            _buckets[key_id] = bucket
        if bucket["minute"] != minute:
            bucket["minute"], bucket["minute_count"] = minute, 0
        if bucket["day"] != today:
            bucket["day"], bucket["day_count"] = today, 0

        snap = _snapshot(bucket)
        if per_min and bucket["minute_count"] >= per_min:
            return False, {**snap, "reason": "minute", "limit": per_min, "retryAfter": 60 - int(now % 60)}
        if per_day and bucket["day_count"] >= per_day:
            return False, {**snap, "reason": "day", "limit": per_day,
                           "retryAfter": 86400 - int(now % 86400)}

        bucket["minute_count"] += 1
        bucket["day_count"] += 1
        bucket["total"] += 1
        return True, _snapshot(bucket)


def usage(key_id: str) -> dict:
    """Current counters without consuming anything."""
    with _lock:
        bucket = _buckets.get(key_id)
        if not bucket:
            return {"minute": 0, "day": 0, "total": 0}
        today = int(time.time() // 86400)
        minute = int(time.time() // 60)
        return {
            "minute": bucket["minute_count"] if bucket["minute"] == minute else 0,
            "day": bucket["day_count"] if bucket["day"] == today else 0,
            "total": bucket["total"],
        }


def reset() -> None:
    """Test helper: forget every counter."""
    with _lock:
        _buckets.clear()
