"""Per-key widget configuration: defaults, validation, and the public view.

Every value an integrator can change flows through `normalize_config`, which is
an allow-list rebuilder — unknown keys are dropped, enums are checked, colours
must be hex, URLs must be http(s). Nothing from a customer's JSON ever reaches
the DOM or a `style` attribute unvalidated, so a bad config degrades to the
default instead of breaking the widget or opening an injection path.
"""
import json
import re

# ---------------------------------------------------------------- defaults

DEFAULT_BRANDING = {
    "theme": "auto",            # auto | light | dark
    "primaryColor": "#c6f24e",  # launcher + accents
    "title": "",                # empty => localized default
    "subtitle": "",
    "greeting": "",
    "placeholder": "",
    "position": "right",        # right | left
    "icon": "chat",             # chat | spark | logo
    "logoUrl": "",
    "hideBranding": False,      # Pro only
}

DEFAULT_BEHAVIOR = {
    "autoOpen": "never",        # never | delay | always
    "autoOpenDelay": 5,         # seconds, used when autoOpen == delay
    "starterQuestions": [],     # up to 4 short prompts
    "showSources": True,
    "persistSession": True,
    "fallbackMessage": "",      # overrides the "nothing found" line
}

DEFAULT_CONTENT = {
    "docIds": [],               # empty => every document the owner has
    "quotaPerMin": 0,           # 0 => unlimited
    "quotaPerDay": 0,
}

DEFAULT_CONFIG = {
    "branding": DEFAULT_BRANDING,
    "behavior": DEFAULT_BEHAVIOR,
    "content": DEFAULT_CONTENT,
}

THEMES = ("auto", "light", "dark")
POSITIONS = ("right", "left")
ICONS = ("chat", "spark", "logo")
AUTO_OPEN = ("never", "delay", "always")

_HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_URL_RE = re.compile(r"^https?://[^\s\"'<>]{1,480}$")


def _text(value, limit: int) -> str:
    """Coerce to a trimmed string of bounded length (no control chars)."""
    if not isinstance(value, str):
        return ""
    clean = "".join(ch for ch in value if ch == "\n" or ch >= " ")
    return clean.strip()[:limit]


def _int(value, low: int, high: int, default: int = 0) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, n))


def _bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _enum(value, allowed, default: str) -> str:
    return value if isinstance(value, str) and value in allowed else default


def normalize_config(raw, plan: str = "free") -> dict:
    """Merge a customer payload over the defaults, keeping only known keys."""
    raw = raw if isinstance(raw, dict) else {}
    branding_in = raw.get("branding") if isinstance(raw.get("branding"), dict) else {}
    behavior_in = raw.get("behavior") if isinstance(raw.get("behavior"), dict) else {}
    content_in = raw.get("content") if isinstance(raw.get("content"), dict) else {}

    color = branding_in.get("primaryColor") or DEFAULT_BRANDING["primaryColor"]
    logo = _text(branding_in.get("logoUrl"), 500)
    icon = _enum(branding_in.get("icon"), ICONS, DEFAULT_BRANDING["icon"])
    if icon == "logo" and not _URL_RE.match(logo):
        icon = "chat"  # a logo with no usable image falls back to the default glyph

    questions = []
    if isinstance(behavior_in.get("starterQuestions"), list):
        for q in behavior_in["starterQuestions"][:4]:
            text = _text(q, 80)
            if text:
                questions.append(text)

    doc_ids = []
    if isinstance(content_in.get("docIds"), list):
        for d in content_in["docIds"][:500]:
            if isinstance(d, str) and d:
                doc_ids.append(d[:64])

    return {
        "branding": {
            "theme": _enum(branding_in.get("theme"), THEMES, DEFAULT_BRANDING["theme"]),
            "primaryColor": color if _HEX_RE.match(color) else DEFAULT_BRANDING["primaryColor"],
            "title": _text(branding_in.get("title"), 60),
            "subtitle": _text(branding_in.get("subtitle"), 80),
            "greeting": _text(branding_in.get("greeting"), 300),
            "placeholder": _text(branding_in.get("placeholder"), 60),
            "position": _enum(branding_in.get("position"), POSITIONS, DEFAULT_BRANDING["position"]),
            "icon": icon,
            "logoUrl": logo if _URL_RE.match(logo) else "",
            # white-label is a paid capability: the flag only survives on a Pro key
            "hideBranding": _bool(branding_in.get("hideBranding")) and plan == "pro",
        },
        "behavior": {
            "autoOpen": _enum(behavior_in.get("autoOpen"), AUTO_OPEN, DEFAULT_BEHAVIOR["autoOpen"]),
            "autoOpenDelay": _int(behavior_in.get("autoOpenDelay"), 0, 60, DEFAULT_BEHAVIOR["autoOpenDelay"]),
            "starterQuestions": questions,
            "showSources": _bool(behavior_in.get("showSources"), DEFAULT_BEHAVIOR["showSources"]),
            "persistSession": _bool(behavior_in.get("persistSession"), DEFAULT_BEHAVIOR["persistSession"]),
            "fallbackMessage": _text(behavior_in.get("fallbackMessage"), 300),
        },
        "content": {
            "docIds": doc_ids,
            "quotaPerMin": _int(content_in.get("quotaPerMin"), 0, 100000),
            "quotaPerDay": _int(content_in.get("quotaPerDay"), 0, 10000000),
        },
    }


def merge_config(base, patch) -> dict:
    """Deep-merge a partial patch so a PATCH never wipes fields it did not send.

    Without this, `{"branding": {"title": "X"}}` would silently reset the
    customer's colours, quota and scope back to the defaults.
    """
    out = json.loads(json.dumps(base or {}))
    if not isinstance(patch, dict):
        return out
    for section in ("branding", "behavior", "content"):
        incoming = patch.get(section)
        if isinstance(incoming, dict):
            out.setdefault(section, {}).update(incoming)
    return out


def public_config(config: dict) -> dict:
    """What the widget is allowed to see — branding and behaviour only.

    `content` stays server-side: exposing which document ids a key can read
    would leak the owner's knowledge-base structure to any visitor who reads
    the page source.
    """
    return {
        "branding": dict(config.get("branding", DEFAULT_BRANDING)),
        "behavior": dict(config.get("behavior", DEFAULT_BEHAVIOR)),
    }


# ---------------------------------------------------------------- origins

def parse_origins(raw) -> list[str]:
    """One origin per line or comma; scheme optional, `*` wildcards allowed."""
    if isinstance(raw, (list, tuple)):
        raw = "\n".join(str(x) for x in raw)
    out = []
    for part in re.split(r"[\s,]+", raw or ""):
        host = part.strip().lower().rstrip("/")
        if not host:
            continue
        host = re.sub(r"^https?://", "", host)
        host = re.sub(r"^www\.", "", host)
        # drop any path: "example.com/docs" is still just the origin
        host = host.split("/")[0]
        # require a dot (or localhost): otherwise a typo like "not a domain"
        # would be sliced into plausible-looking allow-list entries
        if "." in host or host.split(":")[0] == "localhost":
            if re.match(r"^[a-z0-9.*\-]+(:\d+)?$", host):
                out.append(host)
    return list(dict.fromkeys(out))  # de-duplicate, keep order


def origin_allowed(allowed: list[str], origin: str) -> bool:
    """True when a request from `origin` may use a key with this allow-list.

    An empty list means "not configured" and stays permissive so existing
    embeds keep working; a configured list is enforced strictly.
    """
    if not allowed:
        return True
    host = (origin or "").strip().lower()
    host = re.sub(r"^https?://", "", host).split("/")[0]
    host = re.sub(r"^www\.", "", host)
    if not host:
        return False
    for pattern in allowed:
        if pattern == "*":
            return True
        if pattern.startswith("*."):
            suffix = pattern[1:]  # ".example.com"
            if host.endswith(suffix) or host == pattern[2:]:
                return True
        elif host == pattern:
            return True
    return False
