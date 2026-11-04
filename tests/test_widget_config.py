"""Third-party integration surface.

Covers the three things that make an embed safe to hand to a customer: every
input an integrator sends is rebuilt from an allow-list, a key can be pinned to
the domains that may use it, and its quota is enforced.
"""
import pytest

from app import database as db, quota, rag, widget_config as wc
from helpers import seed_document

# ---------------------------------------------------------------- unit: config


def test_colour_accepts_hex_and_rejects_injection():
    assert wc.normalize_config({"branding": {"primaryColor": "#1a2b3c"}})["branding"]["primaryColor"] == "#1a2b3c"
    for bad in ("javascript:alert(1)", "red", "#12345", "url(x)", "expression(1)"):
        assert wc.normalize_config({"branding": {"primaryColor": bad}})["branding"]["primaryColor"] == "#c6f24e"


def test_unknown_keys_and_enum_values_fall_back():
    cfg = wc.normalize_config({
        "branding": {"theme": "neon", "position": "top", "icon": "skull", "evil": "<script>"},
        "behavior": {"autoOpen": "sometimes"},
        "surprise": {"nested": 1},
    })
    assert cfg["branding"]["theme"] == "auto"
    assert cfg["branding"]["position"] == "right"
    assert cfg["branding"]["icon"] == "chat"
    assert "evil" not in cfg["branding"]
    assert set(cfg) == {"branding", "behavior", "content"}


def test_text_is_trimmed_and_bounded():
    cfg = wc.normalize_config({"branding": {"title": "  " + "x" * 200 + "  ", "subtitle": "ok"}})
    assert len(cfg["branding"]["title"]) == 60
    assert cfg["branding"]["subtitle"] == "ok"


def test_logo_icon_without_a_usable_url_falls_back_to_chat():
    cfg = wc.normalize_config({"branding": {"icon": "logo", "logoUrl": "javascript:alert(1)"}})
    assert cfg["branding"]["icon"] == "chat"
    assert cfg["branding"]["logoUrl"] == ""
    assert wc.normalize_config({"branding": {"icon": "logo", "logoUrl": "https://x.test/a.png"}})["branding"]["icon"] == "logo"


def test_starter_questions_and_doc_ids_are_capped():
    cfg = wc.normalize_config({
        "behavior": {"starterQuestions": ["q%d" % i for i in range(9)]},
        "content": {"docIds": ["d%d" % i for i in range(600)]},
    })
    assert len(cfg["behavior"]["starterQuestions"]) == 4
    assert len(cfg["content"]["docIds"]) == 500


def test_numbers_are_clamped_not_trusted():
    cfg = wc.normalize_config({"content": {"quotaPerMin": -5, "quotaPerDay": "99999999999"},
                               "behavior": {"autoOpenDelay": 5000}})
    assert cfg["content"]["quotaPerMin"] == 0
    assert cfg["content"]["quotaPerDay"] == 10000000
    assert cfg["behavior"]["autoOpenDelay"] == 60


def test_white_label_only_survives_on_a_pro_key():
    payload = {"branding": {"hideBranding": True}}
    assert wc.normalize_config(payload, "free")["branding"]["hideBranding"] is False
    assert wc.normalize_config(payload, "pro")["branding"]["hideBranding"] is True


def test_merge_keeps_fields_the_patch_did_not_mention():
    base = {"branding": {"title": "A", "primaryColor": "#112233"},
            "content": {"quotaPerMin": 7}}
    merged = wc.merge_config(base, {"branding": {"title": "B"}})
    assert merged["branding"] == {"title": "B", "primaryColor": "#112233"}
    assert merged["content"]["quotaPerMin"] == 7


# ---------------------------------------------------------------- unit: origins


@pytest.mark.parametrize("raw,expected", [
    ("customer.com", ["customer.com"]),
    ("https://www.Customer.com/", ["customer.com"]),
    ("a.com, b.io", ["a.com", "b.io"]),
    ("a.com\nb.io\n", ["a.com", "b.io"]),
    ("*.a.com", ["*.a.com"]),
    ("localhost:3000", ["localhost:3000"]),
])
def test_parse_origins_normalises(raw, expected):
    assert wc.parse_origins(raw) == expected


@pytest.mark.parametrize("raw", ["", "not a domain", "!!!", "http://"])
def test_parse_origins_drops_garbage(raw):
    assert wc.parse_origins(raw) == []


def test_origin_allowed_exact_and_wildcard():
    allowed = ["customer.com", "*.acme.io"]
    assert wc.origin_allowed(allowed, "https://customer.com")
    assert wc.origin_allowed(allowed, "http://www.customer.com/page")
    assert wc.origin_allowed(allowed, "https://sub.acme.io")
    assert not wc.origin_allowed(allowed, "https://acme.io.evil.com")
    assert not wc.origin_allowed(allowed, "https://other.com")


def test_empty_allowlist_stays_permissive():
    assert wc.origin_allowed([], "https://anything.test")


# ------------------------------------------------------------------- API layer


@pytest.fixture()
def key(client, user):
    resp = client.post("/api/api-keys", headers=user["headers"], json={"label": "site"})
    assert resp.status_code == 200
    return resp.json()


def test_patch_updates_only_the_sections_it_sends(client, user, key):
    base = {"branding": {"title": "Acme", "primaryColor": "#4f8cff"},
            "content": {"quotaPerMin": 9}}
    client.patch(f"/api/api-keys/{key['id']}", headers=user["headers"], json={"config": base})
    client.patch(f"/api/api-keys/{key['id']}", headers=user["headers"],
                 json={"config": {"branding": {"title": "Acme 2"}}})

    cfg = client.get("/api/api-keys", headers=user["headers"]).json()["keys"][0]["config"]
    assert cfg["branding"]["title"] == "Acme 2"
    assert cfg["branding"]["primaryColor"] == "#4f8cff"  # untouched
    assert cfg["content"]["quotaPerMin"] == 9


def test_patch_rejects_an_unparseable_allowlist(client, user, key):
    resp = client.patch(f"/api/api-keys/{key['id']}", headers=user["headers"],
                        json={"allowed_origins": ["%%% not a domain"]})
    assert resp.status_code == 400
    stored = client.get("/api/api-keys", headers=user["headers"]).json()["keys"][0]
    assert stored["allowed_origins"] == []  # nothing half-applied


def test_public_config_never_exposes_scope(client, user, key):
    client.patch(f"/api/api-keys/{key['id']}", headers=user["headers"],
                 json={"config": {"content": {"docIds": ["secret-doc"], "quotaPerDay": 50}}})
    payload = client.get(f"/api/widget/config?key={key['key']}").json()
    assert set(payload["config"]) == {"branding", "behavior"}
    assert "secret-doc" not in str(payload)
    assert payload["whiteLabel"]["available"] is False


def test_allowlist_blocks_a_foreign_origin(client, user, key):
    client.patch(f"/api/api-keys/{key['id']}", headers=user["headers"],
                 json={"allowed_origins": ["acme.example"]})
    ok = client.post("/api/widget/chat", headers={"X-API-Key": key["key"], "Origin": "https://acme.example"},
                     json={"message": "hi"})
    blocked = client.post("/api/widget/chat", headers={"X-API-Key": key["key"], "Origin": "https://evil.example"},
                          json={"message": "hi"})
    assert ok.status_code == 200
    assert blocked.status_code == 403
    # a refused origin gets no CORS grant at all, so the browser blocks the read
    assert "access-control-allow-origin" not in blocked.headers


def test_server_to_server_calls_carry_no_origin_and_are_allowed(client, user, key):
    client.patch(f"/api/api-keys/{key['id']}", headers=user["headers"],
                 json={"allowed_origins": ["acme.example"]})
    resp = client.post("/api/widget/chat", headers={"X-API-Key": key["key"]}, json={"message": "hi"})
    assert resp.status_code == 200


def test_widget_page_pins_frame_ancestors(client, user, key):
    client.patch(f"/api/api-keys/{key['id']}", headers=user["headers"],
                 json={"allowed_origins": ["acme.example", "*.acme.io"]})
    html = client.get(f"/widget?key={key['key']}")
    assert html.status_code == 200
    csp = html.headers["content-security-policy"]
    assert "frame-ancestors" in csp and "acme.example" in csp and "*.acme.io" in csp
    assert "'self'" in csp  # the /demo preview keeps working
    # an unrestricted key gets no CSP at all
    assert "content-security-policy" not in client.get("/widget?key=unknown").headers


def test_quota_returns_429_once_exhausted(client, user, key):
    client.patch(f"/api/api-keys/{key['id']}", headers=user["headers"],
                 json={"config": {"content": {"quotaPerMin": 2}}})
    codes = [client.post("/api/widget/chat", headers={"X-API-Key": key["key"]},
                         json={"message": "hi"}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]

    blocked = client.post("/api/widget/chat", headers={"X-API-Key": key["key"]}, json={"message": "hi"})
    assert blocked.status_code == 429
    assert blocked.headers["retry-after"].isdigit()


def test_quota_is_per_key_not_per_user(client, user, key):
    client.patch(f"/api/api-keys/{key['id']}", headers=user["headers"],
                 json={"config": {"content": {"quotaPerMin": 1}}})
    other = client.post("/api/api-keys", headers=user["headers"], json={"label": "second"}).json()
    client.post("/api/widget/chat", headers={"X-API-Key": key["key"]}, json={"message": "hi"})
    assert client.post("/api/widget/chat", headers={"X-API-Key": key["key"]},
                       json={"message": "hi"}).status_code == 429
    assert client.post("/api/widget/chat", headers={"X-API-Key": other["key"]},
                       json={"message": "hi"}).status_code == 200


def test_custom_fallback_replaces_the_model_answer(client, user, key):
    client.patch(f"/api/api-keys/{key['id']}", headers=user["headers"],
                 json={"config": {"behavior": {"fallbackMessage": "Acme: please contact support."}}})
    body = client.post("/api/widget/chat", headers={"X-API-Key": key["key"]},
                       json={"message": "quantum chromodynamics"}).json()
    assert body["answer"] == "Acme: please contact support."
    assert body["sources"] == []


def test_usage_endpoint_reports_counts_and_limits(client, user, key):
    client.patch(f"/api/api-keys/{key['id']}", headers=user["headers"],
                 json={"config": {"content": {"quotaPerMin": 5}}})
    usage = quota.check(key["id"], 5, 0)[1]
    assert usage["minute"] >= 1
    payload = client.get(f"/api/api-keys/{key['id']}/usage", headers=user["headers"]).json()
    assert payload["limits"]["perMin"] == 5
    assert payload["usage"]["total"] >= 1


def test_usage_is_scoped_to_the_owner(client, user, admin, key):
    resp = client.get(f"/api/api-keys/{key['id']}/usage", headers=admin["headers"])
    assert resp.status_code == 404


def test_only_an_admin_can_grant_white_label(client, user, admin, key):
    assert client.patch(f"/api/admin/api-keys/{key['id']}/plan", headers=user["headers"],
                        json={"plan": "pro"}).status_code == 403
    assert client.patch(f"/api/admin/api-keys/{key['id']}/plan", headers=admin["headers"],
                        json={"plan": "pro"}).status_code == 200

    client.patch(f"/api/api-keys/{key['id']}", headers=user["headers"],
                 json={"config": {"branding": {"hideBranding": True}}})
    assert client.get(f"/api/widget/config?key={key['key']}").json()["config"]["branding"]["hideBranding"] is True


def test_downgrade_hides_white_label_without_losing_the_setting(client, user, admin, key):
    client.patch(f"/api/admin/api-keys/{key['id']}/plan", headers=admin["headers"], json={"plan": "pro"})
    client.patch(f"/api/api-keys/{key['id']}", headers=user["headers"],
                 json={"config": {"branding": {"hideBranding": True}}})
    client.patch(f"/api/admin/api-keys/{key['id']}/plan", headers=admin["headers"], json={"plan": "free"})

    payload = client.get(f"/api/widget/config?key={key['key']}").json()
    assert payload["config"]["branding"]["hideBranding"] is False
    assert payload["whiteLabel"]["available"] is False

    # re-upgrading restores the customer's original choice
    client.patch(f"/api/admin/api-keys/{key['id']}/plan", headers=admin["headers"], json={"plan": "pro"})
    assert client.get(f"/api/widget/config?key={key['key']}").json()["config"]["branding"]["hideBranding"] is True


# ------------------------------------------------------------- retrieval scope


def test_retrieval_scope_excludes_other_documents(client, user):
    user_id = db.get_user_by_email(user["email"])["id"]
    pricing = seed_document(user_id, "pricing.txt", ["The starter plan costs 19 USD per month."])
    shipping = seed_document(user_id, "shipping.txt", ["Express shipping is delivered within 24 hours."])

    question = "express shipping delivered within 24 hours"
    scoped = rag.retrieve(user_id, question, doc_ids=[pricing])
    allowed = rag.retrieve(user_id, question, doc_ids=[shipping])

    assert all(hit["doc_id"] == pricing for hit in scoped)
    assert any(hit["doc_id"] == shipping for hit in allowed)  # the scope, not the ranker, did the work


def test_scope_applies_to_the_bm25_side_too(client, user):
    """A scoped embed must not leak phrases through the lexical retriever."""
    from app import bm25

    user_id = db.get_user_by_email(user["email"])["id"]
    a = seed_document(user_id, "a.txt", ["alpha beta gamma"])
    b = seed_document(user_id, "b.txt", ["delta epsilon zeta"])

    assert bm25.get_index(user_id, [a]).N == 1
    assert bm25.get_index(user_id, [a, b]).N == 2
    assert bm25.get_index(user_id).N == 2
