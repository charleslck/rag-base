"""The usage meter: what a request costs, who asked, and what /api/usage shows."""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from app import database as db, providers, usage
from helpers import FAQ_TEXT, parse_sse, upload


def make_key(client, headers, label="website"):
    resp = client.post("/api/api-keys", headers=headers, json={"label": label})
    assert resp.status_code == 200, resp.text
    return resp.json()


def widget_chat(client, key, message="How long is the refund policy?"):
    return client.post("/api/widget/chat", headers={"X-API-Key": key}, json={"message": message})


def events(user_id: str):
    return db.get_conn().execute(
        "SELECT * FROM usage_events WHERE user_id = ? ORDER BY created_at", (user_id,)
    ).fetchall()


# ----------------------------------------------------------------- estimation

def test_estimate_counts_cjk_per_character_and_latin_per_four():
    assert usage.estimate_tokens("退款期限是多久") == 7  # every CJK char is a token
    assert usage.estimate_tokens("abcd" * 2) == 2       # 8 latin chars ≈ 2 tokens
    assert usage.estimate_tokens("") == 0


def test_estimate_messages_adds_a_per_message_overhead():
    one = usage.estimate_messages([{"role": "user", "content": "abcd"}])
    two = usage.estimate_messages([{"role": "user", "content": "abcd"}] * 2)
    assert one == 5 and two == 10


# ------------------------------------------------------------- web chat metering

def test_a_web_answer_writes_one_usage_event(client, user):
    upload(client, user["headers"], ("faq.txt", FAQ_TEXT.encode()))

    client.post("/api/chat", headers=user["headers"], json={"message": "refund policy"})

    rows = events(db.get_user_by_email(user["email"])["id"])
    assert len(rows) == 1
    assert rows[0]["source"] == "web"
    assert rows[0]["api_key_id"] is None
    assert rows[0]["prompt_tokens"] > 0
    assert rows[0]["completion_tokens"] > 0
    assert rows[0]["status"] == "ok"


def test_streaming_answer_is_metered_after_the_stream_ends(client, user):
    upload(client, user["headers"], ("faq.txt", FAQ_TEXT.encode()))

    resp = client.post("/api/chat", headers=user["headers"], json={"message": "refund policy"})
    assert parse_sse(resp.text)[-1]["done"] is True  # stream fully consumed

    rows = events(db.get_user_by_email(user["email"])["id"])
    assert len(rows) == 1
    assert rows[0]["completion_tokens"] > 0
    assert rows[0]["latency_ms"] >= 0


# ---------------------------------------------------------- widget metering

def test_a_widget_answer_is_billed_to_its_key(client, user):
    upload(client, user["headers"], ("faq.txt", FAQ_TEXT.encode()))
    key = make_key(client, user["headers"], label="acme site")

    widget_chat(client, key["key"])

    rows = events(db.get_user_by_email(user["email"])["id"])
    assert len(rows) == 1
    assert rows[0]["source"] == "widget"
    assert rows[0]["api_key_id"] == key["id"]

    summary = client.get("/api/usage", headers=user["headers"]).json()
    assert summary["totals"]["requests"] == 1
    assert summary["by_key"][0]["key_prefix"] == key["prefix"] + "…"
    assert summary["by_key"][0]["key_label"] == "acme site"


def test_a_streamed_widget_answer_is_metered_once(client, user):
    upload(client, user["headers"], ("faq.txt", FAQ_TEXT.encode()))
    key = make_key(client, user["headers"])

    resp = client.post(
        "/api/widget/chat",
        headers={"X-API-Key": key["key"]},
        json={"message": "refund policy", "stream": True},
    )
    assert parse_sse(resp.text)[-1]["done"] is True

    assert len(events(db.get_user_by_email(user["email"])["id"])) == 1


def test_the_canned_fallback_is_recorded_but_costs_no_tokens(client, user):
    key = make_key(client, user["headers"])
    client.patch(
        f"/api/api-keys/{key['id']}",
        headers=user["headers"],
        json={"config": {"behavior": {"fallbackMessage": "Please email support."}}},
    )

    resp = widget_chat(client, key["key"], "anything at all")
    assert resp.json()["answer"] == "Please email support."

    rows = events(db.get_user_by_email(user["email"])["id"])
    assert rows[0]["status"] == "fallback"
    assert rows[0]["completion_tokens"] == 0


# ------------------------------------------------------------------ reporting

def test_usage_requires_authentication(client):
    assert client.get("/api/usage").status_code == 401


def test_totals_cover_only_the_requested_window(client, user):
    uid = db.get_user_by_email(user["email"])["id"]
    db.add_usage_event(uid, prompt_tokens=10, completion_tokens=5)
    old = db.add_usage_event(uid, prompt_tokens=1000, completion_tokens=1000)
    db.get_conn().execute(
        "UPDATE usage_events SET created_at = ? WHERE id = ?", (time.time() - 10 * 86400, old)
    )
    db.get_conn().commit()

    recent = client.get("/api/usage?days=1", headers=user["headers"]).json()
    assert recent["totals"]["prompt_tokens"] == 10
    assert recent["totals"]["total_tokens"] == 15

    month = client.get("/api/usage?days=30", headers=user["headers"]).json()
    assert month["totals"]["total_tokens"] == 2015


def test_daily_series_has_one_row_per_day(client, user):
    uid = db.get_user_by_email(user["email"])["id"]
    db.add_usage_event(uid, prompt_tokens=3, completion_tokens=4)

    daily = client.get("/api/usage", headers=user["headers"]).json()["daily"]

    assert len(daily) == 1
    assert daily[0]["requests"] == 1
    assert daily[0]["prompt_tokens"] == 3
    assert len(daily[0]["day"]) == 10  # YYYY-MM-DD


def test_cost_follows_the_configured_prices(client, user, patch_settings):
    uid = db.get_user_by_email(user["email"])["id"]
    db.add_usage_event(uid, prompt_tokens=1_000_000, completion_tokens=500_000)

    with patch_settings(price_per_mtok_in=2.0, price_per_mtok_out=8.0):
        body = client.get("/api/usage", headers=user["headers"]).json()

    assert body["totals"]["cost"] == pytest.approx(2.0 + 4.0)
    assert body["pricing"] == {"per_mtok_in": 2.0, "per_mtok_out": 8.0, "currency": "USD"}


def test_the_days_parameter_is_clamped(client, user):
    assert client.get("/api/usage?days=0", headers=user["headers"]).json()["days"] == 1
    assert client.get("/api/usage?days=9999", headers=user["headers"]).json()["days"] == 365


def test_a_revoked_key_keeps_its_history(client, user):
    uid = db.get_user_by_email(user["email"])["id"]
    key = make_key(client, user["headers"])
    db.add_usage_event(uid, api_key_id=key["id"], prompt_tokens=5, completion_tokens=5)
    client.delete(f"/api/api-keys/{key['id']}", headers=user["headers"])

    body = client.get("/api/usage", headers=user["headers"]).json()

    assert body["totals"]["requests"] == 1            # billing history outlives the key
    assert body["by_key"][0]["revoked"] is True


# ----------------------------------------------------- provider-reported counts

def test_provider_reported_usage_wins_over_the_estimate(monkeypatch, patch_settings):
    class _Completions:
        def create(self, **kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="hello there"))],
                usage=SimpleNamespace(prompt_tokens=123, completion_tokens=45),
            )

    fake = SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))
    monkeypatch.setattr(providers, "_get_chat_client", lambda: fake)

    meter = usage.Usage()
    with patch_settings(llm_api_key="sk-test"):
        providers.chat_once([{"role": "user", "content": "hi"}], usage=meter)

    assert (meter.prompt_tokens, meter.completion_tokens) == (123, 45)
    assert meter.measured is True


def test_a_provider_without_usage_is_estimated(monkeypatch, patch_settings):
    class _Completions:
        def create(self, **kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="hello there"))],
                usage=None,
            )

    fake = SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))
    monkeypatch.setattr(providers, "_get_chat_client", lambda: fake)

    meter = usage.Usage()
    with patch_settings(llm_api_key="sk-test"):
        providers.chat_once([{"role": "user", "content": "hi"}], usage=meter)

    assert meter.measured is False
    assert meter.prompt_tokens > 0 and meter.completion_tokens > 0


def test_a_failed_model_call_is_recorded_as_an_error(client, user, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("upstream down")

    monkeypatch.setattr(providers, "chat_stream", boom)
    client.post("/api/chat", headers=user["headers"], json={"message": "hello"})

    rows = events(db.get_user_by_email(user["email"])["id"])
    assert rows[0]["status"] == "error"


def test_metering_failures_never_break_the_request(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(db, "add_usage_event", boom)
    assert usage.record("user-1", usage.Usage(prompt_tokens=1)) == ""


def test_total_tokens_adds_both_directions():
    assert usage.Usage(prompt_tokens=7, completion_tokens=3).total_tokens == 10


def test_totals_can_be_filtered_by_key(client, user):
    uid = db.get_user_by_email(user["email"])["id"]
    db.add_usage_event(uid, api_key_id="key-a", prompt_tokens=5, completion_tokens=5)
    db.add_usage_event(uid, prompt_tokens=100, completion_tokens=100)

    assert db.usage_totals(uid, api_key_id="key-a")["total_tokens"] == 10
    assert db.usage_totals(uid)["total_tokens"] == 210


# ------------------------------------------------- streamed responses

class _StreamChatClient:
    """Fake OpenAI client: yields `chunks`, then an optional usage-only frame."""

    def __init__(self, chunks=("hello ", "there"), usage=None, reject_options=False):
        self.calls: list[dict] = []
        self._chunks = chunks
        self._usage = usage
        self._reject = reject_options

        class _Completions:
            def create(_self, **kwargs):
                outer = self
                outer.calls.append(kwargs)
                if outer._reject and "stream_options" in kwargs:
                    raise TypeError("unexpected keyword argument 'stream_options'")

                def frames():
                    for text in outer._chunks:
                        yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text))])
                    if outer._usage is not None:
                        yield SimpleNamespace(choices=[], usage=outer._usage)

                return frames()

        self.chat = SimpleNamespace(completions=_Completions())


def test_a_stream_that_reports_usage_is_measured(monkeypatch, patch_settings):
    fake = _StreamChatClient(usage=SimpleNamespace(prompt_tokens=88, completion_tokens=12))
    monkeypatch.setattr(providers, "_get_chat_client", lambda: fake)

    meter = usage.Usage()
    with patch_settings(llm_api_key="sk-test"):
        text = "".join(providers.chat_stream([{"role": "user", "content": "hi"}], usage=meter))

    assert text == "hello there"
    assert (meter.prompt_tokens, meter.completion_tokens) == (88, 12)
    assert meter.measured is True
    assert fake.calls[0]["stream_options"] == {"include_usage": True}


def test_a_stream_without_usage_is_estimated(monkeypatch, patch_settings):
    fake = _StreamChatClient()
    monkeypatch.setattr(providers, "_get_chat_client", lambda: fake)

    meter = usage.Usage()
    with patch_settings(llm_api_key="sk-test"):
        "".join(providers.chat_stream([{"role": "user", "content": "hi"}], usage=meter))

    assert meter.measured is False
    assert meter.prompt_tokens > 0
    assert meter.completion_tokens == usage.estimate_tokens("hello there")


def test_a_provider_that_rejects_stream_options_is_retried_without_them(monkeypatch, patch_settings):
    fake = _StreamChatClient(reject_options=True)
    monkeypatch.setattr(providers, "_get_chat_client", lambda: fake)

    with patch_settings(llm_api_key="sk-test"):
        assert "".join(providers.chat_stream([{"role": "user", "content": "hi"}])) == "hello there"

    assert fake.calls[-1].get("stream_options") is None


def test_a_streamed_fallback_is_metered_as_such(client, user):
    key = make_key(client, user["headers"])
    client.patch(
        f"/api/api-keys/{key['id']}",
        headers=user["headers"],
        json={"config": {"behavior": {"fallbackMessage": "Please email support."}}},
    )

    resp = client.post(
        "/api/widget/chat",
        headers={"X-API-Key": key["key"]},
        json={"message": "anything at all", "stream": True},
    )
    assert "Please email support." in resp.text

    rows = events(db.get_user_by_email(user["email"])["id"])
    assert rows[0]["status"] == "fallback"
    assert rows[0]["completion_tokens"] == 0

