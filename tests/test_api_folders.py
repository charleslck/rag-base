"""Folders: nesting, renaming, deleting (files survive) and moving documents."""
from __future__ import annotations

from helpers import FAQ_TEXT, register


def make_folder(client, headers, name: str, parent: str | None = None) -> str:
    resp = client.post(
        "/api/folders", headers=headers, json={"name": name, "parent_id": parent}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def folders(client, headers) -> list[dict]:
    return client.get("/api/folders", headers=headers).json()["folders"]


def docs(client, headers) -> list[dict]:
    return client.get("/api/documents", headers=headers).json()["documents"]


def upload_into(client, headers, folder_id: str | None, name: str = "faq.txt") -> str:
    data = {"folder_id": folder_id} if folder_id else {}
    resp = client.post(
        "/api/documents",
        headers=headers,
        files=[("files", (name, FAQ_TEXT.encode(), "application/octet-stream"))],
        data=data,
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()["results"][0]
    assert result["ok"] is True, result
    return result["id"]


# --------------------------------------------------------------- creating

def test_new_folder_lands_at_the_root(client, user):
    folder_id = make_folder(client, user["headers"], "Contracts")

    listed = folders(client, user["headers"])
    assert [f["id"] for f in listed] == [folder_id]
    assert listed[0]["name"] == "Contracts"
    assert listed[0]["parent_id"] is None
    assert listed[0]["docs"] == 0


def test_nested_folder_keeps_its_parent(client, user):
    parent = make_folder(client, user["headers"], "Contracts")
    child = make_folder(client, user["headers"], "2026", parent=parent)

    by_id = {f["id"]: f for f in folders(client, user["headers"])}
    assert by_id[child]["parent_id"] == parent


def test_parent_must_belong_to_the_caller(client, user):
    other = register(client, "other@example.com")
    stranger = make_folder(client, {"Authorization": f"Bearer {other['token']}"}, "Theirs")

    resp = client.post(
        "/api/folders",
        headers=user["headers"],
        json={"name": "Mine", "parent_id": stranger},
    )

    assert resp.status_code == 404


def test_blank_folder_name_is_rejected(client, user):
    resp = client.post("/api/folders", headers=user["headers"], json={"name": "   "})
    assert resp.status_code == 400


def test_rename_folder(client, user):
    folder_id = make_folder(client, user["headers"], "Old name")

    resp = client.patch(
        f"/api/folders/{folder_id}", headers=user["headers"], json={"name": "New name"}
    )

    assert resp.status_code == 200
    assert folders(client, user["headers"])[0]["name"] == "New name"


def test_rename_unknown_folder_is_404(client, user):
    resp = client.patch("/api/folders/nope", headers=user["headers"], json={"name": "x"})
    assert resp.status_code == 404


# --------------------------------------------------------------- uploading

def test_upload_into_a_folder_tags_the_document(client, user):
    folder_id = make_folder(client, user["headers"], "Contracts")

    doc_id = upload_into(client, user["headers"], folder_id)

    assert docs(client, user["headers"])[0]["id"] == doc_id
    assert docs(client, user["headers"])[0]["folder_id"] == folder_id
    assert folders(client, user["headers"])[0]["docs"] == 1


def test_upload_into_an_unknown_folder_is_404(client, user):
    resp = client.post(
        "/api/documents",
        headers=user["headers"],
        files=[("files", ("faq.txt", FAQ_TEXT.encode(), "text/plain"))],
        data={"folder_id": "nope"},
    )
    assert resp.status_code == 404
    assert docs(client, user["headers"]) == []


def test_upload_without_a_folder_stays_at_the_root(client, user):
    upload_into(client, user["headers"], None)
    assert docs(client, user["headers"])[0]["folder_id"] is None


# --------------------------------------------------------------- moving

def test_move_documents_into_a_folder_and_back(client, user):
    folder_id = make_folder(client, user["headers"], "Contracts")
    a = upload_into(client, user["headers"], None, "a.txt")
    b = upload_into(client, user["headers"], None, "b.txt")

    resp = client.post(
        "/api/documents/move",
        headers=user["headers"],
        json={"ids": [a, b], "folder_id": folder_id},
    )
    assert resp.json() == {"moved": 2}
    assert all(d["folder_id"] == folder_id for d in docs(client, user["headers"]))

    resp = client.post(
        "/api/documents/move", headers=user["headers"], json={"ids": [a], "folder_id": None}
    )
    assert resp.json() == {"moved": 1}
    by_id = {d["id"]: d for d in docs(client, user["headers"])}
    assert by_id[a]["folder_id"] is None
    assert by_id[b]["folder_id"] == folder_id


def test_move_into_an_unknown_folder_is_404(client, user):
    doc_id = upload_into(client, user["headers"], None)
    resp = client.post(
        "/api/documents/move",
        headers=user["headers"],
        json={"ids": [doc_id], "folder_id": "nope"},
    )
    assert resp.status_code == 404
    assert docs(client, user["headers"])[0]["folder_id"] is None


def test_move_cannot_touch_someone_elses_documents(client, user):
    other = register(client, "other@example.com")
    other_headers = {"Authorization": f"Bearer {other['token']}"}
    their_doc = upload_into(client, other_headers, None)
    my_folder = make_folder(client, user["headers"], "Mine")

    resp = client.post(
        "/api/documents/move",
        headers=user["headers"],
        json={"ids": [their_doc], "folder_id": my_folder},
    )

    # their id simply does not match, so nothing moves and nothing leaks
    assert resp.json() == {"moved": 0}
    assert docs(client, other_headers)[0]["folder_id"] is None


def test_move_without_ids_is_rejected(client, user):
    resp = client.post("/api/documents/move", headers=user["headers"], json={"ids": []})
    assert resp.status_code == 400


# --------------------------------------------------------------- deleting

def test_deleting_a_folder_keeps_its_documents(client, user):
    folder_id = make_folder(client, user["headers"], "Contracts")
    doc_id = upload_into(client, user["headers"], folder_id)

    resp = client.delete(f"/api/folders/{folder_id}", headers=user["headers"])

    assert resp.json() == {"folders": 1, "documents": 1}
    assert folders(client, user["headers"]) == []
    remaining = docs(client, user["headers"])
    assert [d["id"] for d in remaining] == [doc_id]
    assert remaining[0]["folder_id"] is None  # back at the root, chunks intact


def test_deleting_a_folder_takes_its_subfolders_with_it(client, user):
    parent = make_folder(client, user["headers"], "Contracts")
    child = make_folder(client, user["headers"], "2026", parent=parent)
    nested_doc = upload_into(client, user["headers"], child)

    resp = client.delete(f"/api/folders/{parent}", headers=user["headers"])

    assert resp.json() == {"folders": 2, "documents": 1}
    assert folders(client, user["headers"]) == []
    assert docs(client, user["headers"])[0]["id"] == nested_doc
    assert docs(client, user["headers"])[0]["folder_id"] is None


def test_deleting_an_unknown_folder_is_404(client, user):
    assert client.delete("/api/folders/nope", headers=user["headers"]).status_code == 404


# --------------------------------------------------------------- isolation

def test_folders_are_per_user(client, user):
    other = register(client, "other@example.com")
    make_folder(client, {"Authorization": f"Bearer {other['token']}"}, "Theirs")

    assert folders(client, user["headers"]) == []


def test_folder_endpoints_require_authentication(client):
    assert client.get("/api/folders").status_code == 401
    assert client.post("/api/folders", json={"name": "x"}).status_code == 401
    assert client.post("/api/documents/move", json={"ids": ["a"]}).status_code == 401
