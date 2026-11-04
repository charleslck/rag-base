"""Static UI wiring: the bits of the front-end that no route test can see.

Everything here is a plain file check — no client, no browser. It exists because
these failures are silent: an untranslated key renders as the raw key name, and a
missing event binding only throws when a user clicks.
"""
from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"


def test_every_data_i18n_key_is_defined_in_both_languages():
    """A key added to the markup but not to the dictionaries shows up as raw text.

    Both dictionaries are checked, not just "defined somewhere": shipping the
    English string and forgetting the Chinese one is the easy mistake.
    """
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "i18n.js").read_text(encoding="utf-8")

    used = set(re.findall(r'data-i18n(?:-ph|-title|-html)?="([\w.]+)"', html))
    assert len(used) > 50, f"only found {len(used)} keys — the regex broke"

    missing = sorted(k for k in used if len(re.findall(rf'"{re.escape(k)}":', js)) != 2)
    assert not missing, f"not defined in both en and zh: {missing}"


def test_scope_toolbar_offers_select_all_and_invert():
    """Bulk selection for the document scope list.

    The handlers are wired at load time in app.js; a missing binding fails only
    on the first click, which is exactly the kind of thing that ships.
    """
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    for hook in ("ed-doc-head", "ed-doc-count", "ed-docs-all", "ed-docs-invert"):
        assert f'id="{hook}"' in html, hook

    # the toolbar belongs between the hint and the list, not inside the list
    assert html.index('id="ed-doc-head"') < html.index('id="ed-docs"')

    app = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "setDocChecks(() => true)" in app  # select all
    assert "setDocChecks((checked) => !checked)" in app  # invert
    # and a hand-ticked box has to move the counter too
    assert '$("#ed-docs").addEventListener("change", updateDocScope)' in app
    # once nothing is left to select the button goes inert — via aria-disabled,
    # not `disabled`, so pressing it does not throw focus away
    assert 'all.setAttribute("aria-disabled"' in app


def test_answer_renderer_handles_block_markdown():
    """Structured answers must not print their own markup.

    Retrieval can now return a whole listing, so the model answers with
    headings, lists and tables. The renderer used to inline-format only, which
    left a literal "# " and "| a | b |" in the transcript.
    """
    app = (STATIC / "app.js").read_text(encoding="utf-8")

    assert "function mdInline(" in app
    for marker in ('"<h" + h + ">"', '"<ul>"', '"<ol>"', '"<table><tbody>"'):
        assert marker in app, marker
    # the |---|---| separator row is markdown punctuation, not a table row
    assert "^:?-{2,}:?$" in app

    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    for selector in (".msg-text h3", ".msg-text ul", ".msg-text table", ".msg-text th, .msg-text td"):
        assert selector in css, selector

    # escaping must stay the first step, or a model reply becomes live markup
    block = app.split("function renderMarkdownLite")[1].split("\nfunction ")[0]
    assert "esc(text)" in block


def test_knowledge_base_shows_how_many_documents_it_holds():
    """After a hundred-file upload, the count is the only confirmation."""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    js = (STATIC / "i18n.js").read_text(encoding="utf-8")

    assert 'id="doc-count"' in html  # the rail summary
    assert 'id="lib-stat"' in html  # the same numbers above the gallery
    assert "renderDocCount()" in app
    assert "counter.hidden = total === 0" in app  # nothing to count on an empty base
    assert len(re.findall(r'"kb\.count":', js)) == 2
    assert len(re.findall(r'"kb\.countOne":', js)) == 2


def test_file_library_replaces_one_x_per_row_with_bulk_selection():
    """The gallery is the fix for "deleting a hundred files is a hundred clicks".

    What is asserted here is the contract the behaviour depends on — an id that
    goes missing, or a handler bound to a click on a div instead of a real
    checkbox, fails silently in the browser.
    """
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    css = (STATIC / "styles.css").read_text(encoding="utf-8")

    for hook in (
        "library", "lib-grid", "lib-stat", "lib-tools", "lib-sel",
        "lib-all", "lib-invert", "lib-delete", "lib-clear",
        "lib-q", "lib-sort", "lib-upload", "lib-empty",
        "up-panel", "up-fill", "up-fails", "confirm-modal",
    ):
        assert f'id="{hook}"' in html, hook

    # the gallery takes over the chat area, so it lives beside it in the shell
    assert html.index('id="chat-main"') < html.index('id="library"')
    assert 'data-lib-view="grid"' in html and 'data-lib-view="list"' in html

    # a real checkbox per tile: keyboard, screen readers and shift-ranges all
    # depend on it; a click handler on a div would give up all three
    assert 'class="tile-input"' in app
    assert 'libGrid.addEventListener("change"' in app
    assert "selectRange(state.libAnchor" in app
    assert "all.indeterminate =" in app  # header box shows the mixed state

    # one request for the whole selection, and never without a confirmation
    assert '"/api/documents/delete"' in app
    assert "await confirmDialog(" in app
    assert 'id="confirm-ok"' in html

    # uploads report movement instead of greying the drop zone out
    assert "function showUpload(" in app
    assert 't("lib.uploading"' in app
    assert "UPLOAD_BATCH" in app

    # and the whole area accepts a drop, not just the 264px box in the rail
    assert 'libEl.addEventListener("drop"' in app

    for selector in (".lib-grid", ".tile.is-sel", ".lib-grid.is-list", ".lib-drop"):
        assert selector in css, selector


def test_file_library_has_navigable_folders():
    """Folders are a tree the UI walks; every edge of that walk is asserted here.

    The failure mode without these checks is quiet: a folder tile that renders
    but cannot be opened, or a move that posts to a route that does not exist.
    """
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    js = (STATIC / "i18n.js").read_text(encoding="utf-8")

    for hook in ("lib-crumbs", "lib-crumb-list", "lib-new-folder", "lib-move",
                 "folder-modal", "folder-name", "folder-save", "move-modal", "move-list"):
        assert f'id="{hook}"' in html, hook

    # the trail sits between the toolbar and the grid, where a file manager puts it
    assert html.index('class="lib-head"') < html.index('id="lib-crumbs"') < html.index('id="lib-body"')

    # navigation is a route through the tree, not a filter on a flat list
    assert "function folderChain(" in app
    assert "function childFolders(" in app
    assert "state.libFolder" in app
    assert 'data-crumb=' in app or "data-crumb=" in app
    # a search spans the whole library, so the trail would be a lie while it runs
    assert "nav.classList.toggle(\"hidden\", searching)" in app

    # three write paths, all of them server-backed
    for route in ('"/api/folders"', '"/api/folders/" + encodeURIComponent', '"/api/documents/move"'):
        assert route in app, route

    # moving by drag and dropping onto a folder, without the library-wide
    # upload handler swallowing the drop
    assert "e.stopPropagation(); // the library-wide handler would treat it as an upload" in app
    assert ".tile--folder.is-drop" in css

    # uploads follow the user into the open folder
    assert 'fd.append("folder_id", state.libFolder)' in app

    # deleting a folder must promise to keep the files, in both languages
    assert len(re.findall(r'"lib\.folderDelPlain":', js)) == 2
    assert len(re.findall(r'"lib\.folderDelSub":', js)) == 2


def test_settings_dialog_wires_account_and_audit():
    """The on-premise settings dialog: audit + account, nothing else.

    Members, invitations, roles and multiple knowledge bases were removed;
    this keeps the load-time wiring of what is left from going missing.
    """
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    app = (STATIC / "app.js").read_text(encoding="utf-8")

    for hook in (
        "ws-modal", "ws-btn", "ws-close", "ws-head-org", "ws-tab-audit",
        "ws-tab-account", "ws-audit-list", "ws-pass-form", "ws-export",
        "ws-del-account", "notice-banner", "notice-text",
    ):
        assert f'id="{hook}"' in html, hook
    assert (html.count('data-ws-tab=')) == 2  # audit + account only

    for route in (
        '"/api/audit', '"/api/export"', '"/api/account"',
        '"/api/auth/change-password"', '"/api/auth/sessions/revoke"',
    ):
        assert route in app, route
    for removed in (
        '"/api/org"', '"/api/kbs"', '"/api/org/invites"',
        'ws-member-list', 'ws-invite-form', 'lib-kb', 'ws-del-org',
        'ws-tab-members', 'ws-tab-kbs',
    ):
        assert removed not in html and removed not in app, removed
    assert 'fetch("/api/export"' in app
    assert '$("#ws-head-org").textContent' in app


def test_settings_dialog_keeps_the_two_column_layout():
    """The rail/grid fix must survive the removal of two panes."""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    js = (STATIC / "i18n.js").read_text(encoding="utf-8")

    assert 'id="ws-main"' in html
    assert "#int-main.int-layout, #ws-main.int-layout" in css
    assert "#ws-main.int-layout { grid-template-columns: 176px minmax(0, 1fr)" in css
    assert "min-height: min(620px" in css
    assert '.ed-row input[type="password"], .ed-row input[type="email"]' in css
    assert "grid-template-columns: 128px minmax(0, 1fr) minmax(0, 1.4fr)" in css
    assert ".ws-danger-sec .btn-danger" in css
    assert len(re.findall(r'"st\.admin":', js)) == 2
    assert len(re.findall(r'"ws\.title":', js)) == 2


def test_auth_screen_has_no_hosted_account_links():
    """No verification mails, reset links or invitation hashes on-premise."""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    js = (STATIC / "i18n.js").read_text(encoding="utf-8")

    assert 'id="auth-forgot-btn"' not in html
    assert 'id="verify-banner"' not in html
    assert 'id="notice-banner"' in html
    for marker in (
        "handleHashRoute", 'route === "invite"', "/api/auth/verify-email",
        "/api/auth/forgot-password", "/api/auth/reset-password",
        "kb_id: state.libKb", 'fd.append("kb_id"',
    ):
        assert marker not in app, marker
    for key in ("auth.forgot", "auth.verifyHint", "ws.tab.members", "ws.tab.kbs"):
        assert len(re.findall(rf'"{re.escape(key)}":', js)) == 0, key
