/* RAGbase frontend — vanilla JS, no build step. */
"use strict";

const $ = (sel) => document.querySelector(sel);
const state = {
  token: localStorage.getItem("ragbase_token") || null,
  mode: "login", // login | register
  conversationId: null,
  conversations: [],
  sources: [],
  streaming: false,
  view: "welcome", // welcome | chat | auth
  banner: null, // "demo" | "embedding" | null
  bannerModel: "",
  lastFocus: null, // element to restore focus to after closing the modal
  apiTab: "curl", // which API snippet is showing
  apiMode: "json", // json | stream
  embedTab: "iframe", // iframe | sdk
  lastKey: null, // most recently created API key, used in the snippets
  editingKey: null, // key whose customizer is open
  editorCfg: null, // working copy of that key's config
  intTab: "embed", // embed | api | keys — which rail tab is showing
  pane: "chats", // chats | kb — which tab the sidebar rail has selected
  libDocs: [], // every document as the server returned it
  libFolders: [], // flat folder list; parent_id builds the tree
  libFolder: null, // folder currently open, null = root
  libShown: [], // the subset the grid is showing, after search and sort
  libSel: new Set(), // ids ticked in the file library
  libAnchor: null, // index of the last clicked tile, for shift-ranges
  libQuery: "",
  libSort: "new",
  libView: localStorage.getItem("ragbase_lib_view") === "list" ? "list" : "grid",
  dragging: null, // ids being dragged onto a folder
  folderEdit: null, // folder being renamed, null = creating a new one
  moving: null, // target folder picked in the move dialog
  uploading: false,
  uploadTimer: null,
  libPoll: null, // timer that re-reads the library while uploads index
  me: null, // /api/me payload
  wsTab: "audit", // which rail tab the settings dialog shows
};

/* ---------------- helpers ---------------- */
function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (state.token) headers["Authorization"] = "Bearer " + state.token;
  let body = options.body;
  if (body && !(body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(body);
  }
  const res = await fetch(path, { ...options, headers, body });
  if (res.status === 401 && state.token) {
    logout();
    throw new Error(t("err.sessionExpired"));
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    // A 405 means the path exists only under another verb — in practice the
    // backend predates this page (static files reload from disk, routes do not).
    // "Method Not Allowed" tells the user nothing; name the likely cause instead.
    throw new Error(res.status === 405 ? t("err.method")
      : data.detail || t("err.request", { status: res.status }));
  }
  return data;
}

function fmtTime(ts) {
  // date formatting must follow the UI language, not the OS locale
  return new Date(ts * 1000).toLocaleString(currentLang() === "zh" ? "zh-CN" : "en-US", {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

/* ---------------- auth view ---------------- */
function showAuth() {
  state.view = "auth";
  $("#app-view").classList.add("hidden");
  $("#auth-view").classList.remove("hidden");
}

function showApp() {
  state.view = "app";
  $("#auth-view").classList.add("hidden");
  $("#app-view").classList.remove("hidden");
}

function setAuthMode(mode) {
  state.mode = mode;
  const isLogin = mode === "login";
  $("#auth-title").textContent = t(isLogin ? "auth.login.title" : "auth.register.title");
  $("#auth-sub").textContent = t(isLogin ? "auth.login.sub" : "auth.register.sub");
  $("#auth-submit").textContent = t(isLogin ? "auth.login.submit" : "auth.register.submit");
  $("#auth-switch-text").textContent = t(isLogin ? "auth.noAccount" : "auth.haveAccount");
  $("#auth-switch-btn").textContent = t(isLogin ? "auth.createOne" : "auth.toLogin");
  $("#auth-password").autocomplete = isLogin ? "current-password" : "new-password";
  $("#auth-error").classList.add("hidden");
}

$("#auth-switch-btn").addEventListener("click", () =>
  setAuthMode(state.mode === "login" ? "register" : "login"));

$("#auth-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const err = $("#auth-error");
  err.classList.add("hidden");
  const email = $("#auth-email").value.trim();
  const password = $("#auth-password").value;
  if (!email || !password) {
    err.textContent = t("auth.missingFields");
    err.classList.remove("hidden");
    return;
  }
  $("#auth-submit").disabled = true;
  try {
    const path = state.mode === "login" ? "/api/auth/login" : "/api/auth/register";
    const data = await api(path, { method: "POST", body: { email, password } });
    state.token = data.token;
    localStorage.setItem("ragbase_token", data.token);
    await boot();
  } catch (ex) {
    err.textContent = ex.message;
    err.classList.remove("hidden");
  } finally {
    $("#auth-submit").disabled = false;
  }
});

/* A transient message strip above everything (password changed, etc.). */
function showNotice(text) {
  $("#notice-text").textContent = text;
  $("#notice-banner").classList.remove("hidden");
}

function logout() {
  state.token = null;
  state.conversationId = null;
  localStorage.removeItem("ragbase_token");
  showAuth();
}
$("#logout-btn").addEventListener("click", logout);

/* ---------------- boot ---------------- */
function renderBanner() {
  const banner = $("#demo-banner");
  if (state.banner === "demo") {
    banner.innerHTML = t("banner.demo");
    banner.classList.remove("hidden");
  } else if (state.banner === "embedding") {
    banner.innerHTML = t("banner.embeddingLocal", { model: esc(state.bannerModel || "") });
    banner.classList.remove("hidden");
  } else {
    banner.classList.add("hidden");
  }
}

async function boot() {
  if (!state.token) return showAuth();
  try {
    const me = await api("/api/me");
    state.me = me;
    $("#user-email").textContent = me.email;
    state.banner = me.demo_mode ? "demo" : me.embedding_local ? "embedding" : null;
    state.bannerModel = me.model;
    renderBanner();
    showApp();
    await Promise.all([loadConversations(), loadLibrary()]);
    showWelcome();
  } catch (ex) {
    showAuth();
  }
}

/* ---------------- sidebar / tabs ---------------- */
$("#menu-btn").addEventListener("click", () => {
  $("#sidebar").classList.toggle("open");
  $("#sidebar-backdrop").classList.toggle("show", $("#sidebar").classList.contains("open"));
});
$("#sidebar-backdrop").addEventListener("click", () => {
  $("#sidebar").classList.remove("open");
  $("#sidebar-backdrop").classList.remove("show");
});

function switchTab(which) {
  const library = which === "kb";
  $("#tab-chats").classList.toggle("active", !library);
  $("#tab-kb").classList.toggle("active", library);
  $("#tab-chats").setAttribute("aria-selected", String(!library));
  $("#tab-kb").setAttribute("aria-selected", String(library));
  $("#pane-chats").classList.toggle("hidden", library);
  $("#pane-kb").classList.toggle("hidden", !library);
  // the tab swaps the main area, not just the rail: CSS does the show/hide so
  // the sources panel's own open/closed state survives a round trip
  $("#app-view").classList.toggle("lib-open", library);
  state.pane = which;
  closeIntegrate();
  if (library) {
    renderLibrary(); // paints immediately, in case the fetch below is slow
    loadLibrary().catch(() => {}); // the API or the widget may have added files
    closeSidebarMobile();
  }
}

/* integrate opens as a modal: the sidebar keeps its current tab underneath */
function integrateOpen() {
  return !$("#int-modal").classList.contains("hidden");
}

function openIntegrate() {
  const modal = $("#int-modal");
  state.lastFocus = document.activeElement;
  closeEditor(); // always land on the list, never on a stale editor
  modal.classList.remove("hidden");
  document.body.classList.add("modal-open");
  $("#settings-btn").classList.add("active");
  closeSidebarMobile();
  showIntTab(state.intTab);
  loadKeys();
  renderEmbedCode();
  renderApiDocs();
  $("#int-close").focus();
}

function closeIntegrate() {
  const modal = $("#int-modal");
  if (modal.classList.contains("hidden")) return;
  modal.classList.add("hidden");
  document.body.classList.remove("modal-open");
  $("#settings-btn").classList.remove("active");
  if (state.lastFocus && state.lastFocus.isConnected) state.lastFocus.focus();
  state.lastFocus = null;
}

$("#tab-chats").addEventListener("click", () => switchTab("chats"));
$("#tab-kb").addEventListener("click", () => switchTab("kb"));
$("#settings-btn").addEventListener("click", openIntegrate);
$("#int-close").addEventListener("click", closeIntegrate);
$("#int-modal").addEventListener("click", (e) => {
  if (e.target === $("#int-modal")) closeIntegrate(); // click the dimmed backdrop
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    if (integrateOpen()) {
      e.preventDefault();
      if (state.editingKey) {
        closeEditor();
        loadKeys();
      } else {
        closeIntegrate();
      }
    } else if (wsOpen()) {
      e.preventDefault();
      closeWorkspace();
    }
  }
});

/* ---------------- integrate: left rail tabs ---------------- */
/* Embed / API / keys used to be three stacked sections with headings; the
   headings are now a vertical tab rail, so only one pane is on screen. */
function showIntTab(name) {
  const tabs = document.querySelectorAll("#int-main [data-int-tab]");
  if (!tabs.length) return;
  let known = false;
  tabs.forEach((tab) => {
    const on = tab.dataset.intTab === name;
    if (on) known = true;
    tab.classList.toggle("active", on);
    tab.setAttribute("aria-selected", on ? "true" : "false");
    tab.tabIndex = on ? 0 : -1; // roving tabindex: one stop for the whole rail
  });
  if (!known) return showIntTab("embed");
  document.querySelectorAll("#int-main .int-pane").forEach((pane) => {
    pane.classList.toggle("active", pane.id === "int-pane-" + name);
  });
  state.intTab = name;
}

document.querySelectorAll("#int-main [data-int-tab]").forEach((tab) => {
  tab.addEventListener("click", () => showIntTab(tab.dataset.intTab));
});
document.querySelectorAll("#int-main .int-nav").forEach((nav) => {
  nav.addEventListener("keydown", (e) => {
    const step = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 }[e.key];
    if (step === undefined) return;
    e.preventDefault();
    const tabs = [...nav.querySelectorAll("[data-int-tab]")];
    const i = tabs.findIndex((t) => t.classList.contains("active"));
    const next = tabs[(i + step + tabs.length) % tabs.length];
    showIntTab(next.dataset.intTab);
    next.focus();
  });
});

/* ---------------- integrate: embed code + API keys ---------------- */
const EMBED_SNIPPETS = {
  /* One attribute per line, and the 170-char style attribute broken at its
     semicolons: both are legal HTML and produce the same thing when pasted,
     but they keep every line inside the ~540px the dialog gives the block —
     the only line that still overflows is the src, and that is just the key
     being 43 characters long. */
  iframe: (origin, key) =>
    "<!-- RAGbase chat widget -->\n" +
    "<iframe\n" +
    '  src="' + origin + "/widget?key=" + key + '"\n' +
    '  style="position:fixed;bottom:20px;right:20px;\n' +
    "         width:380px;height:600px;max-height:80vh;\n" +
    "         border:none;border-radius:16px;z-index:9999;\n" +
    '         box-shadow:0 8px 30px rgba(0,0,0,.35);"\n' +
    '  title="Knowledge base chat"></iframe>',
  sdk: (origin, key) =>
    "<!-- RAGbase embed SDK: the launcher and panel are built in -->\n" +
    '<script src="' + origin + '/sdk.js"\n' +
    '        data-key="' + key + '"\n' +
    '        data-lang="' + currentLang() + '"><' + "/script>",
};

function renderEmbedCode(key) {
  const apiKey = key || state.lastKey || "YOUR_API_KEY";
  const code = EMBED_SNIPPETS[state.embedTab](location.origin, apiKey);
  $("#embed-code").innerHTML = RAGhl.highlight(code, "html");
}

document.querySelectorAll("[data-embed-tab]").forEach((btn) => {
  btn.addEventListener("click", () => {
    state.embedTab = btn.dataset.embedTab;
    document.querySelectorAll("[data-embed-tab]").forEach((b) => b.classList.toggle("active", b === btn));
    renderEmbedCode();
  });
});

$("#copy-embed").addEventListener("click", async () => {
  await copyText($("#embed-code").textContent);
  $("#copy-embed").textContent = t("int.embed.copied");
  setTimeout(() => ($("#copy-embed").textContent = t("int.embed.copy")), 1500);
});

/* "Open demo" jumps to the real embeddable page (/widget), the exact thing the
   snippet above would inject. That page authenticates with an API key, so reuse
   the key minted in this session or create a demo one on the spot. The blank
   window is opened synchronously inside the click: doing it after the await
   would make popup blockers swallow it. */
async function ensureDemoKey() {
  if (state.lastKey) return state.lastKey;
  const data = await api("/api/api-keys", {
    method: "POST",
    body: { label: t("int.embed.demoKeyLabel") },
  });
  state.lastKey = data.key;
  renderEmbedCode(data.key);
  renderApiDocs();
  await loadKeys();
  return data.key;
}

$("#open-widget-demo").addEventListener("click", async () => {
  const btn = $("#open-widget-demo");
  if (btn.disabled) return;
  const win = window.open("about:blank", "_blank");
  if (win) win.opener = null;
  btn.disabled = true;
  try {
    const key = await ensureDemoKey();
    const url = location.origin + "/demo?key=" + encodeURIComponent(key) + "&lang=" + currentLang();
    if (win) win.location.replace(url);
    else location.href = url; // popup blocked: navigate this tab instead
  } catch (ex) {
    if (win) win.close();
    alert(t("err.keyCreate", { msg: ex.message }));
  } finally {
    btn.disabled = false;
  }
});

/* ---------------- integrate: API access ----------------
   One endpoint, one header, three languages, two response modes. The snippet
   is rebuilt whenever the tab, the response mode or the language changes. */
const API_SNIPPETS = {
  json: {
    curl: (url, key, q) =>
      `curl -X POST ${url} \\
  -H "Content-Type: application/json" \\
  -H "X-API-Key: ${key}" \\
  -d '{"message": "${q}"}'`,

    python: (url, key, q) =>
      `import requests

API_KEY = "${key}"
url = "${url}"

r = requests.post(
    url,
    headers={"X-API-Key": API_KEY},
    json={"message": "${q}"},
    timeout=60,
)
data = r.json()
print(data["answer"])
for s in data["sources"]:
    print(s["doc_name"], round(s["score"], 3))`,

    node: (url, key, q) =>
      `const res = await fetch("${url}", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "X-API-Key": "${key}",
  },
  body: JSON.stringify({ message: "${q}" }),
});
const { conversation_id, answer, sources } = await res.json();
console.log(answer, sources);`,
  },

  stream: {
    curl: (url, key, q) =>
      `# -N keeps curl from buffering: frames print as they arrive
curl -N -X POST ${url} \\
  -H "Content-Type: application/json" \\
  -H "X-API-Key: ${key}" \\
  -d '{"message": "${q}", "stream": true}'`,

    python: (url, key, q) =>
      `import json
import requests

API_KEY = "${key}"
url = "${url}"

with requests.post(
    url,
    headers={"X-API-Key": API_KEY},
    json={"message": "${q}", "stream": True},
    stream=True,
    timeout=60,
) as r:
    r.raise_for_status()
    for line in r.iter_lines():
        if not line or not line.startswith(b"data: "):
            continue
        event = json.loads(line[6:])
        if "delta" in event:
            print(event["delta"], end="", flush=True)
        elif event.get("done"):
            print("\\n", event["conversation_id"], event["sources"])`,

    node: (url, key, q) =>
      `const res = await fetch("${url}", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "X-API-Key": "${key}",
  },
  body: JSON.stringify({ message: "${q}", stream: true }),
});

const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
let buf = "";
for (;;) {
  const { value, done } = await reader.read();
  if (done) break;
  buf += value;
  let cut;
  while ((cut = buf.indexOf("\\n\\n")) !== -1) {
    const frame = buf.slice(0, cut);
    buf = buf.slice(cut + 2);
    if (!frame.startsWith("data: ")) continue;
    const event = JSON.parse(frame.slice(6));
    if ("delta" in event) process.stdout.write(event.delta);
    else if (event.done) {
      console.log("\\n", event.conversation_id, event.sources);
    }
  }
}`,
  },
};

const API_SNIPPET_LANG = { curl: "bash", python: "python", node: "js" };

function renderApiDocs() {
  const url = location.origin + "/api/widget/chat";
  const key = state.lastKey || "YOUR_API_KEY";
  const code = API_SNIPPETS[state.apiMode][state.apiTab](url, key, t("int.api.sample"));
  $("#api-endpoint").textContent = url;
  $("#api-key-hint").textContent = key;
  // the highlighter escapes every token, so the key cannot inject markup here
  $("#api-snippet").innerHTML = RAGhl.highlight(code, API_SNIPPET_LANG[state.apiTab]);
  $("#api-note").textContent = t(state.apiMode === "stream" ? "int.api.noteStream" : "int.api.note");
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    return ok;
  }
}

document.querySelectorAll(".code-tab[data-api-tab]").forEach((btn) => {
  btn.addEventListener("click", () => {
    state.apiTab = btn.dataset.apiTab;
    document.querySelectorAll(".code-tab[data-api-tab]").forEach((b) => b.classList.toggle("active", b === btn));
    renderApiDocs();
  });
});

document.querySelectorAll(".mode-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    state.apiMode = btn.dataset.apiMode;
    document.querySelectorAll(".mode-btn").forEach((b) => b.classList.toggle("active", b === btn));
    renderApiDocs();
  });
});

$("#copy-api").addEventListener("click", async () => {
  await copyText($("#api-snippet").textContent);
  $("#copy-api").textContent = t("int.api.copied");
  setTimeout(() => ($("#copy-api").textContent = t("int.api.copy")), 1500);
});

async function loadKeys() {
  const data = await api("/api/api-keys");
  const list = $("#key-list");
  list.innerHTML = "";
  if (!data.keys.length) {
    list.innerHTML = '<p class="pane-empty">' + esc(t("int.keys.empty")) + "</p>";
    return;
  }
  for (const k of data.keys) {
    const item = document.createElement("div");
    item.className = "key-item";
    const customised = k.allowed_origins.length > 0;
    item.innerHTML =
      '<span class="key-prefix">' + esc(k.prefix) + "…</span>" +
      '<span class="key-label">' + esc(k.label || "—") + "</span>" +
      (customised ? '<span class="key-lock" title="' + esc(t("key.locked")) + '">●</span>' : "") +
      '<button class="key-edit" aria-label="' + esc(t("key.edit")) + '" title="' + esc(t("key.edit")) + '">' +
      '<svg width="13" height="13" viewBox="0 0 16 16" fill="none"><path d="M11.2 2.3a1.7 1.7 0 0 1 2.4 2.4L5.7 12.6l-3.1.7.7-3.1 7.9-7.9z" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/></svg></button>' +
      '<button class="key-del" aria-label="' + esc(t("int.keys.revoke")) + '" title="' + esc(t("int.keys.revoke")) + '">' +
      '<svg width="12" height="12" viewBox="0 0 14 14" fill="none"><path d="M3 3l8 8M11 3l-8 8" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg></button>';
    item.querySelector(".key-edit").addEventListener("click", () => openEditor(k));
    item.querySelector(".key-del").addEventListener("click", async () => {
      await api("/api/api-keys/" + k.id, { method: "DELETE" });
      loadKeys();
    });
    list.appendChild(item);
  }
}

$("#key-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const label = $("#key-label").value.trim();
  try {
    const data = await api("/api/api-keys", { method: "POST", body: { label } });
    $("#new-key-value").textContent = data.key;
    $("#new-key-box").classList.remove("hidden");
    $("#key-label").value = "";
    state.lastKey = data.key;
    renderEmbedCode(data.key);
    renderApiDocs();
    loadKeys();
  } catch (ex) {
    alert(t("err.keyCreate", { msg: ex.message }));
  }
});

/* ---------------- per-key customizer ----------------
   One editor serves both audiences: operators fill in the form and watch the
   preview, developers copy the resulting values straight out of the API. */
const ICON_SVG = {
  chat: '<svg viewBox="0 0 24 24" fill="none"><path d="M20.5 11.6a8.3 8.3 0 0 1-8.9 8.3 9.4 9.4 0 0 1-4.1-.9L3.5 20.3l1.2-3.9a8.1 8.1 0 0 1-1.2-4.4 8.3 8.3 0 0 1 8.5-8A8.3 8.3 0 0 1 20.5 11.6z" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  spark: '<svg viewBox="0 0 24 24" fill="none"><path d="M12 3l1.9 5.3L19 10l-5.1 1.7L12 17l-1.9-5.3L5 10l5.1-1.7L12 3z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/></svg>',
};

function getPath(obj, path) {
  return path.split(".").reduce((a, k) => (a == null ? undefined : a[k]), obj);
}
function setPath(obj, path, value) {
  const parts = path.split(".");
  let cur = obj;
  for (let i = 0; i < parts.length - 1; i++) {
    if (typeof cur[parts[i]] !== "object" || cur[parts[i]] === null) cur[parts[i]] = {};
    cur = cur[parts[i]];
  }
  cur[parts[parts.length - 1]] = value;
}
function normalizeHex(c) {
  if (!c) return "#c6f24e";
  if (/^#[0-9a-f]{3}$/i.test(c)) return "#" + c[1] + c[1] + c[2] + c[2] + c[3] + c[3];
  return /^#[0-9a-f]{6}$/i.test(c) ? c : "#c6f24e";
}

function openEditor(key) {
  state.editingKey = key;
  state.editorCfg = JSON.parse(JSON.stringify(key.config || {}));
  state.editorCfg.content = state.editorCfg.content || { docIds: [], quotaPerMin: 0, quotaPerDay: 0 };
  $("#int-modal").classList.add("editing");
  $("#int-main").classList.add("hidden");
  $("#int-editor").classList.remove("hidden");
  $("#editor-prefix").textContent = key.prefix + "…";
  $("#editor-plan").classList.toggle("hidden", key.plan !== "pro");
  $("#ed-origins").value = (key.allowed_origins || []).join("\n");
  fillEditor();
  renderPreview();
  loadScopeDocs();
  loadUsage(key.id);
  $("#int-editor").scrollTop = 0;
}

function closeEditor() {
  state.editingKey = null;
  $("#int-modal").classList.remove("editing");
  $("#int-editor").classList.add("hidden");
  $("#int-main").classList.remove("hidden");
}

function syncConditionalRows() {
  const cfg = state.editorCfg;
  $("#row-logo").classList.toggle("hidden", getPath(cfg, "branding.icon") !== "logo");
  $("#row-delay").classList.toggle("hidden", getPath(cfg, "behavior.autoOpen") !== "delay");
}

function fillEditor() {
  const cfg = state.editorCfg;
  document.querySelectorAll(".seg[data-seg]").forEach((seg) => {
    const value = getPath(cfg, seg.dataset.seg);
    seg.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.v === value));
  });
  document.querySelectorAll("[data-cfg]").forEach((el) => {
    const value = getPath(cfg, el.dataset.cfg);
    if (el.type === "checkbox") el.checked = !!value;
    else el.value = value == null ? "" : value;
  });
  document.querySelectorAll("[data-cfg-lines]").forEach((el) => {
    el.value = (getPath(cfg, el.dataset.cfgLines) || []).join("\n");
  });
  const color = normalizeHex(getPath(cfg, "branding.primaryColor"));
  $("#ed-color").value = color;
  document.querySelectorAll(".sw").forEach((s) =>
    s.classList.toggle("active", s.dataset.color.toLowerCase() === color.toLowerCase()));

  const pro = state.editingKey && state.editingKey.plan === "pro";
  $("#ed-branding-hide").checked = !!getPath(cfg, "branding.hideBranding");
  $("#ed-branding-hide").disabled = !pro;
  syncConditionalRows();
}

async function loadScopeDocs() {
  const box = $("#ed-docs");
  box.innerHTML = '<span class="empty">' + esc(t("key.loading")) + "</span>";
  let docs = [];
  try {
    docs = (await api("/api/documents")).documents || [];
  } catch {
    docs = [];
  }
  if (!docs.length) {
    box.innerHTML = '<span class="empty">' + esc(t("key.noDocs")) + "</span>";
    updateDocScope();
    return;
  }
  const selected = new Set(getPath(state.editorCfg, "content.docIds") || []);
  box.innerHTML = "";
  for (const doc of docs) {
    const label = document.createElement("label");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = doc.id;
    input.checked = selected.has(doc.id);
    const span = document.createElement("span");
    span.textContent = doc.filename;
    label.append(input, span);
    box.appendChild(label);
  }
  updateDocScope();
}

/* Scope list toolbar. "Nothing checked" is not "nothing allowed" — it means the
   whole knowledge base — so the counter says that outright instead of printing
   0/12, which reads like a key that can reach nothing. */
function docBoxes() {
  return [...document.querySelectorAll("#ed-docs input[type=checkbox]")];
}

function updateDocScope() {
  const boxes = docBoxes();
  const head = $("#ed-doc-head");
  head.classList.toggle("hidden", boxes.length === 0);
  if (!boxes.length) {
    $("#ed-doc-count").textContent = "";
    return;
  }
  const picked = boxes.filter((b) => b.checked).length;
  $("#ed-doc-count").textContent = picked
    ? t("key.docsCount", { n: picked, total: boxes.length })
    : t("key.docsUnrestricted");
  // select-all is a no-op once everything is already on; invert never is.
  // aria-disabled rather than the disabled attribute: pressing this button can
  // be the thing that makes it unavailable, and `disabled` would drop focus.
  const all = $("#ed-docs-all");
  const settled = picked === boxes.length;
  all.setAttribute("aria-disabled", settled ? "true" : "false");
  all.title = settled ? t("key.docsAllDone") : "";
}

function setDocChecks(pick) {
  for (const box of docBoxes()) box.checked = pick(box.checked);
  updateDocScope();
}

$("#ed-docs-all").addEventListener("click", (e) => {
  if (e.currentTarget.getAttribute("aria-disabled") === "true") return;
  setDocChecks(() => true);
});
$("#ed-docs-invert").addEventListener("click", () => setDocChecks((checked) => !checked));
$("#ed-docs").addEventListener("change", updateDocScope);

async function loadUsage(keyId) {
  const box = $("#ed-usage");
  try {
    const data = await api("/api/api-keys/" + keyId + "/usage");
    box.innerHTML =
      "<span>" + esc(t("key.usageMin")) + " <b>" + data.usage.minute + "</b></span>" +
      "<span>" + esc(t("key.usageDay")) + " <b>" + data.usage.day + "</b></span>" +
      "<span>" + esc(t("key.usageTotal")) + " <b>" + data.usage.total + "</b></span>";
  } catch {
    box.textContent = "";
  }
}

function renderPreview() {
  const cfg = state.editorCfg;
  if (!cfg) return;
  const b = cfg.branding || {};
  const beh = cfg.behavior || {};
  const pv = $("#pv");
  const prefersDark = !window.matchMedia || window.matchMedia("(prefers-color-scheme: dark)").matches;
  pv.dataset.theme = b.theme === "auto" ? (prefersDark ? "dark" : "light") : (b.theme || "dark");

  const accent = normalizeHex(b.primaryColor);
  pv.style.setProperty("--pv-accent", accent);
  $("#pv-title").textContent = b.title || t("key.pvTitle");
  $("#pv-sub").textContent = b.subtitle || t("key.pvSub");
  $("#pv-greeting").textContent = b.greeting || t("key.pvGreeting");
  $("#pv-ph").textContent = b.placeholder || t("key.pvPh");
  $("#pv-brand").style.display = b.hideBranding ? "none" : "";

  const chips = $("#pv-chips");
  const questions = (beh.starterQuestions || []).slice(0, 4);
  chips.innerHTML = "";
  chips.classList.toggle("hidden", !questions.length);
  for (const q of questions) {
    const s = document.createElement("span");
    s.textContent = q;
    chips.appendChild(s);
  }

  const launcher = $("#pv-launcher");
  launcher.style.background = accent;
  launcher.innerHTML = b.icon === "logo" && b.logoUrl
    ? '<img src="' + esc(b.logoUrl) + '" alt="" style="width:100%;height:100%;border-radius:50%;object-fit:cover">'
    : (ICON_SVG[b.icon] || ICON_SVG.chat);
  launcher.style.color = "#131a08";
}

document.querySelectorAll(".seg[data-seg]").forEach((seg) => {
  seg.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-v]");
    if (!btn) return;
    setPath(state.editorCfg, seg.dataset.seg, btn.dataset.v);
    seg.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b === btn));
    syncConditionalRows();
    renderPreview();
  });
});

document.querySelectorAll("[data-cfg]").forEach((el) => {
  el.addEventListener("input", () => {
    setPath(state.editorCfg, el.dataset.cfg, el.type === "checkbox" ? el.checked : el.value);
    renderPreview();
  });
});

document.querySelectorAll("[data-cfg-lines]").forEach((el) => {
  el.addEventListener("input", () => {
    const lines = el.value.split("\n").map((s) => s.trim()).filter(Boolean).slice(0, 4);
    setPath(state.editorCfg, el.dataset.cfgLines, lines);
    renderPreview();
  });
});

document.querySelectorAll(".sw").forEach((sw) => {
  sw.addEventListener("click", () => {
    state.editorCfg.branding.primaryColor = sw.dataset.color;
    $("#ed-color").value = sw.dataset.color;
    document.querySelectorAll(".sw").forEach((s) => s.classList.toggle("active", s === sw));
    renderPreview();
  });
});

$("#ed-color").addEventListener("input", (e) => {
  const hex = normalizeHex(e.target.value);
  state.editorCfg.branding.primaryColor = hex;
  document.querySelectorAll(".sw").forEach((s) =>
    s.classList.toggle("active", s.dataset.color.toLowerCase() === hex.toLowerCase()));
  renderPreview();
});

$("#ed-branding-hide").addEventListener("change", (e) => {
  state.editorCfg.branding.hideBranding = e.target.checked;
  renderPreview();
});

$("#editor-back").addEventListener("click", () => {
  closeEditor();
  loadKeys();
});

$("#editor-save").addEventListener("click", async () => {
  if (!state.editingKey) return;
  const btn = $("#editor-save");
  const cfg = state.editorCfg;
  cfg.content.docIds = [...document.querySelectorAll("#ed-docs input:checked")].map((i) => i.value);

  btn.disabled = true;
  try {
    await api("/api/api-keys/" + state.editingKey.id, {
      method: "PATCH",
      body: {
        label: state.editingKey.label || "",
        config: cfg,
        allowed_origins: $("#ed-origins").value.split("\n").map((s) => s.trim()).filter(Boolean),
      },
    });
    btn.textContent = t("key.saved");
    closeEditor();
    await loadKeys();
    setTimeout(() => (btn.textContent = t("key.save")), 1200);
  } catch (ex) {
    alert(t("err.keySave", { msg: ex.message }));
  } finally {
    btn.disabled = false;
  }
});

/* ---------------- conversations ---------------- */
async function loadConversations() {
  const data = await api("/api/conversations");
  state.conversations = data.conversations;
  const list = $("#conv-list");
  list.innerHTML = "";
  if (!state.conversations.length) {
    list.innerHTML = '<p class="pane-empty">' + esc(t("conv.empty")) + "</p>";
    return;
  }
  for (const conv of state.conversations) {
    const btn = document.createElement("button");
    btn.className = "conv-item" + (conv.id === state.conversationId ? " active" : "");
    btn.innerHTML =
      '<span class="conv-title">' + esc(conv.title) + "</span>" +
      '<span class="conv-del" role="button" tabindex="0" aria-label="' + esc(t("conv.delete")) + '" title="' + esc(t("conv.delete")) + '">' +
      '<svg width="13" height="13" viewBox="0 0 14 14" fill="none"><path d="M3 3l8 8M11 3l-8 8" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg></span>';
    btn.addEventListener("click", (e) => {
      if (e.target.closest(".conv-del")) return;
      openConversation(conv.id);
      closeSidebarMobile();
    });
    btn.querySelector(".conv-del").addEventListener("click", async (e) => {
      e.stopPropagation();
      await api("/api/conversations/" + conv.id, { method: "DELETE" });
      if (state.conversationId === conv.id) showWelcome();
      await loadConversations();
    });
    list.appendChild(btn);
  }
}

async function openConversation(id) {
  state.conversationId = id;
  const data = await api("/api/conversations/" + id + "/messages");
  renderMessages(data.messages);
  await loadConversations();
}

$("#new-chat-btn").addEventListener("click", () => {
  state.conversationId = null;
  showWelcome();
  loadConversations();
  closeSidebarMobile();
});

function closeSidebarMobile() {
  $("#sidebar").classList.remove("open");
  $("#sidebar-backdrop").classList.remove("show");
}

/* ---------------- file library ----------------
 * The knowledge base used to be a list inside a 264px rail, which works up to
 * about ten files and then stops: one X button per row means ten clicks and ten
 * dialogs to clear a batch. It is a gallery in the main area now — one click
 * selects, shift-click selects a range, and a batch delete is a single request.
 */
const FILE_GLYPH =
  '<svg width="26" height="26" viewBox="0 0 24 24" fill="none"><path d="M6 2.5h7.5L19 8v13.5H6z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/><path d="M13.5 2.5V8H19" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg>';
const FOLDER_GLYPH =
  '<svg width="26" height="26" viewBox="0 0 24 24" fill="none"><path d="M2.5 6.4A1.9 1.9 0 014.4 4.5h4.3l2.1 2.4h8.8a1.9 1.9 0 011.9 1.9v9.3a1.9 1.9 0 01-1.9 1.9H4.4a1.9 1.9 0 01-1.9-1.9z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg>';
const WHERE_GLYPH =
  '<svg width="12" height="12" viewBox="0 0 16 16" fill="none"><path d="M1.8 4.4A1.3 1.3 0 013.1 3.1h3l1.4 1.6h5.4a1.3 1.3 0 011.3 1.3v6.2a1.3 1.3 0 01-1.3 1.3H3.1a1.3 1.3 0 01-1.3-1.3z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>';
const PEN_GLYPH =
  '<svg width="13" height="13" viewBox="0 0 16 16" fill="none"><path d="M11.2 2.3l2.5 2.5-8 8-3.2.7.7-3.2z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg>';
const TRASH_GLYPH =
  '<svg width="13" height="13" viewBox="0 0 16 16" fill="none"><path d="M3 4.5h10M6.5 4.5V3h3v1.5M4.5 4.5l.6 8.2h5.8l.6-8.2" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>';

const EXT_LABEL = {
  pdf: "PDF", docx: "DOC", doc: "DOC", txt: "TXT",
  md: "MD", markdown: "MD", html: "HTML", htm: "HTML",
};
const LIB_VIEW_KEY = "ragbase_lib_view";
const UPLOAD_BATCH = 8;

function extOf(name) {
  const m = /\.([a-z0-9]+)$/i.exec(name || "");
  return m ? m[1].toLowerCase() : "txt";
}

/* ---------------- folders ----------------
 * A per-user tree held as a flat list with parent_id. Everything the UI needs
 * (breadcrumbs, a folder's children, the label of a search hit) is derived
 * from that list, so there is exactly one copy of the truth.
 */
function folderById(id) {
  return state.libFolders.find((f) => f.id === id) || null;
}

function folderChain(id) {
  const chain = [];
  let f = folderById(id);
  // the guard is for data that was already broken (a stray cycle), not logic
  for (let i = 0; f && i < 64; i++) {
    chain.unshift(f);
    f = f.parent_id ? folderById(f.parent_id) : null;
  }
  return chain;
}

function folderLabel(id) {
  return folderChain(id).map((f) => f.name).join(" / ");
}

function childFolders() {
  const here = state.libFolder || null;
  const collator = currentLang() === "zh" ? "zh-CN" : "en";
  return state.libFolders
    .filter((f) => (f.parent_id || null) === here)
    .sort((a, b) => a.name.localeCompare(b.name, collator, { numeric: true }));
}

/* The server returns documents newest first. Filtering and sorting happen here:
   a few hundred rows sort in memory faster than they round-trip. */
function libVisible() {
  const q = state.libQuery.trim().toLowerCase();
  const here = state.libFolder || null;
  // a query searches the whole library — that is what the box is for; without
  // one the grid shows exactly one folder's own files
  const docs = q
    ? state.libDocs.filter((d) => d.filename.toLowerCase().includes(q))
    : state.libDocs.filter((d) => (d.folder_id || null) === here);
  const collator = currentLang() === "zh" ? "zh-CN" : "en";
  const byName = (a, b) =>
    a.filename.localeCompare(b.filename, collator, { numeric: true, sensitivity: "base" });
  const order = {
    new: (a, b) => b.created_at - a.created_at,
    old: (a, b) => a.created_at - b.created_at,
    az: byName,
    za: (a, b) => byName(b, a),
    chunks: (a, b) => b.num_chunks - a.num_chunks || byName(a, b),
  };
  return docs.sort(order[state.libSort] || order.new);
}

function folderTileHTML(folder) {
  const kids = state.libFolders.filter((x) => x.parent_id === folder.id).length;
  const parts = [t(folder.docs === 1 ? "lib.oneFile" : "lib.nFiles", { n: folder.docs || 0 })];
  if (kids) parts.push(t(kids === 1 ? "lib.oneFolder" : "lib.nFolders", { n: kids }));
  return (
    '<div class="tile tile--folder" data-folder="' + esc(folder.id) + '" role="button" tabindex="0"' +
      ' aria-label="' + esc(t("lib.openFolder", { name: folder.name })) + '">' +
      '<span class="tile-top"><span class="tile-glyph tile-glyph--folder" aria-hidden="true">' +
        FOLDER_GLYPH + "</span></span>" +
      '<span class="tile-name" title="' + esc(folder.name) + '">' + esc(folder.name) + "</span>" +
      '<span class="tile-meta">' + esc(parts.join(" · ")) + "</span>" +
      '<span class="tile-acts">' +
        '<button class="tile-act" type="button" data-folder-rename="' + esc(folder.id) + '"' +
          ' data-i18n-title="lib.folderRename" aria-label="' + esc(t("lib.folderRename")) + '">' +
          PEN_GLYPH + "</button>" +
        '<button class="tile-act tile-act--danger" type="button" data-folder-del="' + esc(folder.id) + '"' +
          ' data-i18n-title="lib.folderDelete" aria-label="' + esc(t("lib.folderDelete")) + '">' +
          TRASH_GLYPH + "</button>" +
      "</span>" +
    "</div>"
  );
}

function tileHTML(doc, idx) {
  const ext = extOf(doc.filename);
  const on = state.libSel.has(doc.id);
  const meta = doc.status === "ready"
    ? esc(t(doc.num_chunks === 1 ? "kb.chunkOne" : "kb.chunks", { n: doc.num_chunks })) +
      " · " + esc(fmtTime(doc.created_at))
    : doc.status === "processing"
      ? '<span class="tile-work"><span class="tile-spin" aria-hidden="true"></span>' +
        esc(t("kb.processing")) + "</span>"
      : '<span class="tile-err">' + esc(doc.error || t("err.failed")) + "</span>";
  // a search crosses folders, so say which one a hit came from. It gets its own
  // line: appended to the meta line it would always be the part that gets cut.
  const where = state.libQuery.trim() && doc.folder_id && folderById(doc.folder_id)
    ? '<span class="tile-where" title="' + esc(folderLabel(doc.folder_id)) + '">' +
      WHERE_GLYPH + "<span>" + esc(folderLabel(doc.folder_id)) + "</span></span>"
    : "";
  return (
    '<label class="tile tile--doc' + (on ? " is-sel" : "") + '" data-id="' + esc(doc.id) +
      '" data-idx="' + idx + '" draggable="true">' +
      // a real checkbox keeps the tile keyboard-operable and screen-reader legible
      '<input type="checkbox" class="tile-input" data-id="' + esc(doc.id) + '"' +
        (on ? " checked" : "") + ' aria-label="' + esc(t("lib.pick", { name: doc.filename })) + '">' +
      '<span class="tile-mark" aria-hidden="true"></span>' +
      '<span class="tile-top"><span class="tile-glyph" aria-hidden="true">' + FILE_GLYPH + "</span>" +
        '<span class="tile-ext">' + esc(EXT_LABEL[ext] || ext.slice(0, 4).toUpperCase()) + "</span></span>" +
      '<span class="tile-name" title="' + esc(doc.filename) + '">' + esc(doc.filename) + "</span>" +
      '<span class="tile-meta">' + meta + "</span>" + where +
    "</label>"
  );
}

async function loadLibrary() {
  // one round trip each, in parallel: the grid needs both to paint at all
  const [docs, folders] = await Promise.all([api("/api/documents"), api("/api/folders")]);
  state.libDocs = docs.documents || [];
  state.libFolders = folders.folders || [];
  // the open folder may have been deleted from another tab
  if (state.libFolder && !folderById(state.libFolder)) state.libFolder = null;
  renderDocCount();
  renderLibrary();
  scheduleLibraryPoll();
}

/* Uploads index in the background, so a fresh tile says "indexing…" and the
   grid re-reads itself until every tile is ready. One shared timer: a second
   upload, a folder switch or a search never stacks a second poller. */
function scheduleLibraryPoll() {
  if (state.libPoll) { clearTimeout(state.libPoll); state.libPoll = null; }
  if (!state.token || !state.libDocs.some((d) => d.status === "processing")) return;
  state.libPoll = setTimeout(async () => {
    state.libPoll = null;
    try {
      await loadLibrary();
    } catch {
      // offline or logged out: the next visit or upload starts polling again
    }
  }, 1500);
}

function renderDocCount() {
  const counter = $("#doc-count");
  const total = state.libDocs.length;
  counter.hidden = total === 0;
  if (!total) return;
  const chunks = state.libDocs.reduce((n, d) => n + (d.num_chunks || 0), 0);
  counter.textContent = t(total === 1 ? "kb.countOne" : "kb.count", { n: total, c: chunks });
}

function renderCrumbs(searching) {
  const nav = $("#lib-crumbs");
  nav.classList.toggle("hidden", searching); // a search spans folders; the trail would lie
  if (searching) return;
  const chain = state.libFolder ? folderChain(state.libFolder) : [];
  const parts = [{ id: "", name: t("lib.root") }].concat(chain);
  $("#lib-crumb-list").innerHTML = parts
    .map((f, i) => {
      const last = i === parts.length - 1;
      return (i ? '<li class="lib-crumb-sep" aria-hidden="true">/</li>' : "") +
        '<li><button type="button" class="lib-crumb' + (last ? " is-here" : "") +
        '" data-crumb="' + esc(f.id) + '"' + (last ? ' aria-current="page"' : "") + ">" +
        esc(f.name) + "</button></li>";
    })
    .join("");
}

function renderLibrary() {
  const docs = libVisible();
  state.libShown = docs;

  // Selection only ever covers what is on screen: a search that hides half the
  // grid must not leave a delete aimed at files you can no longer see.
  const alive = new Set(docs.map((d) => d.id));
  for (const id of [...state.libSel]) if (!alive.has(id)) state.libSel.delete(id);
  if (state.libAnchor !== null && state.libAnchor >= docs.length) state.libAnchor = null;

  const searching = !!state.libQuery.trim();
  const folders = searching ? [] : childFolders();

  const grid = $("#lib-grid");
  grid.className = "lib-grid" + (state.libView === "list" ? " is-list" : "");
  grid.innerHTML = folders.map(folderTileHTML).join("") + docs.map(tileHTML).join("");

  renderCrumbs(searching);

  const empty = $("#lib-empty");
  const nothing = !docs.length && !folders.length;
  empty.classList.toggle("hidden", !nothing);
  if (nothing) {
    // three different nothings: no match, an empty folder, an empty library
    empty.innerHTML = searching
      ? "<strong>" + esc(t("lib.noMatchTitle", { q: state.libQuery.trim() })) + "</strong>" +
        esc(t("lib.noMatchHint"))
      : state.libFolder
        ? "<strong>" + esc(t("lib.emptyFolderTitle")) + "</strong>" + esc(t("lib.emptyFolderHint"))
        : "<strong>" + esc(t("lib.emptyTitle")) + "</strong>" + esc(t("lib.emptyHint"));
  }

  const total = state.libDocs.length;
  const stat = $("#lib-stat");
  if (!total) stat.textContent = "";
  else if (searching) stat.textContent = t("lib.found", { n: docs.length, total });
  else if (state.libFolder) {
    stat.textContent = t(docs.length === 1 ? "lib.inFolderOne" : "lib.inFolder", { n: docs.length });
  }
  else {
    const chunks = state.libDocs.reduce((n, d) => n + (d.num_chunks || 0), 0);
    stat.textContent = t(total === 1 ? "kb.countOne" : "kb.count", { n: total, c: chunks });
  }

  syncSelection();
}

function syncSelection() {
  const n = state.libSel.size;
  $("#lib-sel").classList.toggle("hidden", n === 0);
  $("#lib-tools").classList.toggle("hidden", n > 0);
  // nowhere to move them to: no folders exist yet
  $("#lib-move").disabled = state.libFolders.length === 0;
  $("#lib-sel-n").textContent = n ? t("lib.selected", { n }) : "";
  if (!n) state.libAnchor = null;

  // tri-state: every visible file ticked reads as "checked", a part as "mixed"
  const shown = state.libShown || [];
  const picked = shown.filter((d) => state.libSel.has(d.id)).length;
  const all = $("#lib-all");
  all.checked = shown.length > 0 && picked === shown.length;
  all.indeterminate = picked > 0 && picked < shown.length;

  $("#lib-grid").querySelectorAll(".tile--doc").forEach((tile) => {
    const on = state.libSel.has(tile.dataset.id);
    tile.classList.toggle("is-sel", on);
    const box = tile.querySelector(".tile-input");
    if (box) box.checked = on;
  });
}

function setLibView(view, opts) {
  state.libView = view === "list" ? "list" : "grid";
  localStorage.setItem(LIB_VIEW_KEY, state.libView);
  document.querySelectorAll("[data-lib-view]").forEach((btn) => {
    const on = btn.dataset.libView === state.libView;
    btn.classList.toggle("active", on);
    btn.setAttribute("aria-pressed", String(on));
  });
  if (!opts || opts.render !== false) renderLibrary();
}

/* ---------------- folder navigation ---------------- */
function openFolder(id) {
  state.libFolder = id || null;
  state.libSel.clear();
  // a folder is a place, not a filter: carrying the query in would show other
  // folders' hits under a breadcrumb that says you are somewhere else
  state.libQuery = "";
  $("#lib-q").value = "";
  $("#lib-q-clear").classList.add("hidden");
  renderLibrary();
}

function openFolderDialog(folder) {
  state.folderEdit = folder ? folder.id : null;
  $("#folder-title").textContent = folder ? t("lib.folderRename") : t("lib.folderNew");
  $("#folder-save").textContent = folder ? t("lib.save") : t("lib.create");
  const input = $("#folder-name");
  input.value = folder ? folder.name : "";
  // say where it will land, or the trail has to be read behind the dialog
  const hint = $("#folder-hint");
  hint.hidden = !!folder;
  hint.textContent = folder ? "" : t("lib.folderWhere", {
    path: state.libFolder ? folderLabel(state.libFolder) : t("lib.root"),
  });
  $("#folder-modal").classList.remove("hidden");
  document.body.classList.add("modal-open");
  input.focus();
  input.select();
}

function closeFolderDialog() {
  $("#folder-modal").classList.add("hidden");
  document.body.classList.remove("modal-open");
  state.folderEdit = null;
}

async function saveFolder() {
  const input = $("#folder-name");
  const name = input.value.trim();
  if (!name) { input.focus(); return; }
  try {
    if (state.folderEdit) {
      await api("/api/folders/" + encodeURIComponent(state.folderEdit), {
        method: "PATCH", body: { name },
      });
    } else {
      await api("/api/folders", {
        method: "POST", body: { name, parent_id: state.libFolder || null },
      });
    }
    closeFolderDialog();
    await loadLibrary();
  } catch (ex) {
    alert(ex.message);
  }
}

async function removeFolder(id) {
  const folder = folderById(id);
  if (!folder) return;
  // direct files, not the whole subtree: the confirmation should quote what
  // the user can actually see when they click the bin
  const docs = state.libDocs.filter((d) => d.folder_id === id).length;
  const subs = state.libFolders.filter((f) => f.parent_id === id).length;
  const ok = await confirmDialog(
    t("lib.folderDelTitle", { name: folder.name }),
    subs
      ? t("lib.folderDelSub", { n: docs, sub: subs })
      : t("lib.folderDelPlain", { n: docs }),
    t("lib.delete"),
  );
  if (!ok) return;
  try {
    await api("/api/folders/" + encodeURIComponent(id), { method: "DELETE" });
    // loadLibrary drops the open folder if it was inside the deleted subtree
    await loadLibrary();
  } catch (ex) {
    alert(ex.message);
  }
}

/* ---------------- move dialog ---------------- */
function openMoveDialog() {
  if (!state.libSel.size) return;
  const collator = currentLang() === "zh" ? "zh-CN" : "en";
  // a flat list of paths: indentation carries the tree, and every row is a
  // single click at any depth — faster than opening folders one by one
  const rows = [{ id: "", label: t("lib.root"), depth: 0 }];
  const walk = (parent, depth) => {
    state.libFolders
      .filter((f) => (f.parent_id || null) === parent)
      .sort((a, b) => a.name.localeCompare(b.name, collator, { numeric: true }))
      .forEach((f) => {
        rows.push({ id: f.id, label: f.name, depth });
        walk(f.id, depth + 1);
      });
  };
  walk(null, 1);
  const here = state.libFolder || "";
  const pickable = rows.filter((r) => r.id !== here);
  // pre-select the parent, so the default action is "take it up one level"
  // instead of a silently pointless "move into the folder it is already in"
  const preferred = state.libFolder ? (folderById(state.libFolder)?.parent_id || "") : "";
  $("#move-list").innerHTML = rows
    .map((r) => '<label class="move-row' + (r.id === here ? " is-here" : "") +
      '" style="--d:' + r.depth + '">' +
      '<input type="radio" name="move-dest" value="' + esc(r.id) + '"' +
      (r.id === here ? " disabled" : "") +
      (r.id === (preferred || pickable[0]?.id || "") && r.id !== here ? " checked" : "") +
      "><span>" + esc(r.label) + "</span></label>")
    .join("");
  $("#move-modal").classList.remove("hidden");
  document.body.classList.add("modal-open");
  const picked = $("#move-list input:checked") || $("#move-list input:not(:disabled)");
  if (picked) picked.focus();
}

function closeMoveDialog() {
  $("#move-modal").classList.add("hidden");
  document.body.classList.remove("modal-open");
}

async function moveSelected() {
  const picked = document.querySelector('input[name="move-dest"]:checked');
  if (!picked) return;
  const ids = [...state.libSel];
  try {
    await api("/api/documents/move", {
      method: "POST", body: { ids, folder_id: picked.value || null },
    });
    closeMoveDialog();
    state.libSel.clear();
    await loadLibrary();
  } catch (ex) {
    alert(ex.message);
  }
}

async function moveInto(ids, folderId) {
  if (!ids || !ids.length) return;
  if ((folderId || null) === (state.libFolder || null)) return; // already there
  try {
    await api("/api/documents/move", {
      method: "POST", body: { ids, folder_id: folderId || null },
    });
    state.libSel.clear();
    await loadLibrary();
  } catch (ex) {
    alert(ex.message);
  }
}

$("#lib-new-folder").addEventListener("click", () => openFolderDialog(null));
$("#folder-cancel").addEventListener("click", closeFolderDialog);
$("#folder-save").addEventListener("click", saveFolder);
$("#folder-name").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); saveFolder(); }
});
$("#folder-modal").addEventListener("click", (e) => {
  if (e.target === $("#folder-modal")) closeFolderDialog();
});

$("#lib-move").addEventListener("click", openMoveDialog);
$("#move-cancel").addEventListener("click", closeMoveDialog);
$("#move-ok").addEventListener("click", moveSelected);
$("#move-modal").addEventListener("click", (e) => {
  if (e.target === $("#move-modal")) closeMoveDialog();
});

$("#lib-crumb-list").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-crumb]");
  if (btn) openFolder(btn.dataset.crumb || null);
});

/* ---------------- selection ---------------- */
function selectAllVisible(on) {
  const shown = state.libShown || [];
  if (on) shown.forEach((d) => state.libSel.add(d.id));
  else state.libSel.clear();
  state.libAnchor = null;
  syncSelection();
}

function selectRange(from, to) {
  const shown = state.libShown || [];
  if (from === null || from === undefined || !shown[from]) {
    // no anchor yet (first shift-click): behave like a plain click on the target
    const doc = shown[to];
    if (doc) state.libSel.add(doc.id);
    state.libAnchor = to;
    syncSelection();
    return;
  }
  const [a, b] = from <= to ? [from, to] : [to, from];
  for (let i = a; i <= b; i++) state.libSel.add(shown[i].id);
  state.libAnchor = to;
  syncSelection();
}

const libGrid = $("#lib-grid");
libGrid.addEventListener("click", (e) => {
  const rename = e.target.closest("[data-folder-rename]");
  if (rename) { openFolderDialog(folderById(rename.dataset.folderRename)); return; }
  const bin = e.target.closest("[data-folder-del]");
  if (bin) { removeFolder(bin.dataset.folderDel); return; }
  const folder = e.target.closest(".tile--folder");
  if (folder) { openFolder(folder.dataset.folder); return; }
  const tile = e.target.closest(".tile--doc");
  if (!tile || !e.shiftKey) return;
  e.preventDefault(); // the label would toggle just this one box
  selectRange(state.libAnchor, Number(tile.dataset.idx));
});
libGrid.addEventListener("keydown", (e) => {
  const folder = e.target.closest(".tile--folder");
  if (!folder || (e.key !== "Enter" && e.key !== " ")) return;
  e.preventDefault();
  openFolder(folder.dataset.folder);
});
libGrid.addEventListener("change", (e) => {
  const box = e.target.closest(".tile-input");
  if (!box) return;
  if (box.checked) state.libSel.add(box.dataset.id); else state.libSel.delete(box.dataset.id);
  state.libAnchor = Number(box.closest(".tile").dataset.idx);
  syncSelection();
});

/* dragging files onto a folder moves them — the shortest path for "this one
   belongs in there" without a dialog */
libGrid.addEventListener("dragstart", (e) => {
  const tile = e.target.closest(".tile--doc");
  if (!tile) return;
  state.dragging = state.libSel.has(tile.dataset.id) ? [...state.libSel] : [tile.dataset.id];
  e.dataTransfer.effectAllowed = "move";
  e.dataTransfer.setData("text/plain", state.dragging.join(","));
});
libGrid.addEventListener("dragend", () => {
  state.dragging = null;
  libGrid.querySelectorAll(".is-drop").forEach((el) => el.classList.remove("is-drop"));
});
libGrid.addEventListener("dragover", (e) => {
  const folder = e.target.closest(".tile--folder");
  if (!folder || !state.dragging) return;
  e.preventDefault();
  e.dataTransfer.dropEffect = "move";
  folder.classList.add("is-drop");
});
libGrid.addEventListener("dragleave", (e) => {
  const folder = e.target.closest(".tile--folder");
  if (folder) folder.classList.remove("is-drop");
});
libGrid.addEventListener("drop", (e) => {
  const folder = e.target.closest(".tile--folder");
  if (!folder || !state.dragging) return;
  e.preventDefault();
  e.stopPropagation(); // the library-wide handler would treat it as an upload
  const ids = state.dragging;
  state.dragging = null;
  folder.classList.remove("is-drop");
  moveInto(ids, folder.dataset.folder);
});

/* the crumbs double as a target: drop on "全部文件" to take files out again */
const crumbList = $("#lib-crumb-list");
crumbList.addEventListener("dragover", (e) => {
  if (!state.dragging) return;
  e.preventDefault();
  e.dataTransfer.dropEffect = "move";
});
crumbList.addEventListener("drop", (e) => {
  if (!state.dragging) return;
  e.preventDefault();
  e.stopPropagation();
  const btn = e.target.closest("[data-crumb]");
  const ids = state.dragging;
  state.dragging = null;
  moveInto(ids, btn ? btn.dataset.crumb || null : null);
});

$("#lib-all").addEventListener("change", (e) => selectAllVisible(e.target.checked));
$("#lib-clear").addEventListener("click", () => selectAllVisible(false));
$("#lib-invert").addEventListener("click", () => {
  (state.libShown || []).forEach((d) => {
    if (state.libSel.has(d.id)) state.libSel.delete(d.id); else state.libSel.add(d.id);
  });
  state.libAnchor = null;
  syncSelection();
});

/* ---------------- search, sort, view ---------------- */
$("#lib-q").addEventListener("input", (e) => {
  state.libQuery = e.target.value;
  $("#lib-q-clear").classList.toggle("hidden", !state.libQuery);
  renderLibrary();
});
$("#lib-q-clear").addEventListener("click", () => {
  $("#lib-q").value = "";
  state.libQuery = "";
  $("#lib-q-clear").classList.add("hidden");
  renderLibrary();
  $("#lib-q").focus();
});
$("#lib-sort").addEventListener("change", (e) => {
  state.libSort = e.target.value;
  renderLibrary();
});
document.querySelectorAll("[data-lib-view]").forEach((btn) => {
  btn.addEventListener("click", () => setLibView(btn.dataset.libView));
});
// the toggle must match the remembered view before the first paint of the grid
setLibView(state.libView, { render: false });
$("#lib-upload").addEventListener("click", () => $("#file-input").click());

/* ---------------- confirm dialog ---------------- */
let confirmResolve = null;

function confirmDialog(title, desc, okLabel) {
  $("#confirm-title").textContent = title;
  $("#confirm-desc").textContent = desc;
  $("#confirm-ok").textContent = okLabel || t("lib.delete");
  $("#confirm-modal").classList.remove("hidden");
  document.body.classList.add("modal-open");
  $("#confirm-ok").focus();
  return new Promise((resolve) => { confirmResolve = resolve; });
}

function closeConfirm(result) {
  if ($("#confirm-modal").classList.contains("hidden")) return;
  $("#confirm-modal").classList.add("hidden");
  document.body.classList.remove("modal-open");
  const resolve = confirmResolve;
  confirmResolve = null;
  if (resolve) resolve(result);
}

$("#confirm-ok").addEventListener("click", () => closeConfirm(true));
$("#confirm-cancel").addEventListener("click", () => closeConfirm(false));
$("#confirm-modal").addEventListener("click", (e) => {
  if (e.target === $("#confirm-modal")) closeConfirm(false);
});

/* ---------------- delete ---------------- */
async function deleteSelected() {
  const ids = [...state.libSel];
  if (!ids.length) return;
  const ok = await confirmDialog(
    t(ids.length === 1 ? "lib.delOne" : "lib.delMany", { n: ids.length }),
    t("lib.delDesc"),
    t("lib.delete"),
  );
  if (!ok) return;
  try {
    await api("/api/documents/delete", { method: "POST", body: { ids } });
    state.libSel.clear();
    await loadLibrary();
  } catch (ex) {
    alert(ex.message);
  }
}
$("#lib-delete").addEventListener("click", deleteSelected);

document.addEventListener("keydown", (e) => {
  if (!$("#folder-modal").classList.contains("hidden")) {
    if (e.key === "Escape") { e.preventDefault(); closeFolderDialog(); }
    return;
  }
  if (!$("#move-modal").classList.contains("hidden")) {
    if (e.key === "Escape") { e.preventDefault(); closeMoveDialog(); }
    return;
  }
  if (!$("#confirm-modal").classList.contains("hidden")) {
    if (e.key === "Escape") { e.preventDefault(); closeConfirm(false); }
    return;
  }
  if (!$("#app-view").classList.contains("lib-open")) return;
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName);
  if (e.key === "Escape") {
    if (typing && e.target.id === "lib-q") {
      $("#lib-q").value = "";
      state.libQuery = "";
      $("#lib-q-clear").classList.add("hidden");
      renderLibrary();
    } else if (state.libSel.size) {
      selectAllVisible(false);
      $("#lib-upload").focus();
    }
    return;
  }
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "a" && !typing) {
    e.preventDefault(); // otherwise the browser selects every tile's text
    selectAllVisible(true);
    return;
  }
  if ((e.key === "Delete" || e.key === "Backspace") && !typing && state.libSel.size) {
    e.preventDefault();
    deleteSelected();
  }
});

/* ---------------- upload ---------------- */
const dropZone = $("#drop-zone");
dropZone.addEventListener("click", () => $("#file-input").click());
dropZone.addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") { e.preventDefault(); $("#file-input").click(); }
});
dropZone.addEventListener("dragover", (e) => { e.preventDefault(); dropZone.classList.add("dragover"); });
dropZone.addEventListener("dragleave", () => dropZone.classList.remove("dragover"));
dropZone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropZone.classList.remove("dragover");
  if (e.dataTransfer.files.length) uploadFiles(e.dataTransfer.files);
});
$("#file-input").addEventListener("change", (e) => {
  if (e.target.files.length) uploadFiles(e.target.files);
  e.target.value = "";
});

/* the whole library is a drop target — aiming at a 264px box was the problem */
function hasFiles(e) {
  return !!e.dataTransfer && Array.from(e.dataTransfer.types || []).includes("Files");
}
let dragDepth = 0;
const libEl = $("#library");
libEl.addEventListener("dragenter", (e) => {
  if (!hasFiles(e)) return;
  e.preventDefault();
  dragDepth += 1;
  $("#lib-drop").classList.remove("hidden");
});
libEl.addEventListener("dragover", (e) => {
  if (!hasFiles(e)) return;
  e.preventDefault();
  e.dataTransfer.dropEffect = "copy";
});
libEl.addEventListener("dragleave", () => {
  // dragleave also fires when crossing into a child element, hence the counter.
  // No hasFiles() gate here: when the pointer leaves the window the event can
  // arrive with an empty dataTransfer, and the overlay would stay up for good.
  dragDepth = Math.max(0, dragDepth - 1);
  if (!dragDepth) $("#lib-drop").classList.add("hidden");
});
libEl.addEventListener("drop", (e) => {
  e.preventDefault();
  dragDepth = 0;
  $("#lib-drop").classList.add("hidden");
  if (e.dataTransfer.files.length) uploadFiles(e.dataTransfer.files);
});

function showUpload(total) {
  const panel = $("#up-panel");
  if (state.uploadTimer) { clearTimeout(state.uploadTimer); state.uploadTimer = null; }
  panel.classList.remove("hidden");
  $("#up-fails").innerHTML = "";
  $("#up-fill").style.width = "0%";
  $("#up-label").textContent = t("lib.uploading", { done: 0, total });
}

function renderUploadFails(fails) {
  const ul = $("#up-fails");
  if (!fails.length) { ul.innerHTML = ""; return; }
  const shown = fails.slice(0, 6).map((f) =>
    "<li>" + esc(f.name) + (f.why ? ' <span>' + esc(f.why) + "</span>" : "") + "</li>").join("");
  ul.innerHTML = shown + (fails.length > 6
    ? "<li>… +" + (fails.length - 6) + "</li>" : "");
}

function hideUpload() {
  $("#up-panel").classList.add("hidden");
  if (state.uploadTimer) { clearTimeout(state.uploadTimer); state.uploadTimer = null; }
}
$("#up-close").addEventListener("click", hideUpload);

/* Batched rather than one request per file: a hundred files in one request
   gives no progress at all, a hundred requests flood the server. Eight at a
   time is one round-trip per eight files and a bar that keeps moving. */
async function uploadFiles(fileList) {
  const files = Array.from(fileList || []);
  if (!files.length) return;
  if (state.uploading) { $("#lib-stat").textContent = t("lib.busy"); return; }

  state.uploading = true;
  const fails = [];
  let done = 0;
  showUpload(files.length);
  try {
    for (let i = 0; i < files.length; i += UPLOAD_BATCH) {
      const batch = files.slice(i, i + UPLOAD_BATCH);
      const fd = new FormData();
      // uploads land wherever the user is standing, not always at the root
      if (state.libFolder) fd.append("folder_id", state.libFolder);
      batch.forEach((f) => fd.append("files", f));
      try {
        const data = await api("/api/documents", { method: "POST", body: fd });
        (data.results || []).forEach((r) => {
          if (!r.ok) fails.push({ name: r.filename, why: r.error });
        });
      } catch (ex) {
        fails.push({ name: t("lib.uploadBatchFailed", { n: batch.length }), why: ex.message });
      }
      done = Math.min(files.length, i + batch.length);
      $("#up-label").textContent = t("lib.uploading", { done, total: files.length });
      $("#up-fill").style.width = Math.round((done / files.length) * 100) + "%";
      renderUploadFails(fails);
    }
  } finally {
    state.uploading = false;
  }

  const okCount = files.length - fails.length;
  $("#up-label").textContent = fails.length
    ? t("lib.uploadSome", { ok: okCount, n: fails.length })
    : t("lib.uploadDone", { n: files.length });
  $("#up-fill").style.width = "100%";
  renderUploadFails(fails);
  if (!fails.length) state.uploadTimer = setTimeout(hideUpload, 4000);

  await loadLibrary();
}

/* ---------------- chat rendering ---------------- */
function messagesEl() { return $("#messages"); }

function ensureWrap() {
  let wrap = messagesEl().querySelector(".messages-wrap");
  if (!wrap) {
    messagesEl().innerHTML = '<div class="messages-wrap"></div>';
    wrap = messagesEl().querySelector(".messages-wrap");
  }
  return wrap;
}

function showWelcome() {
  state.view = "welcome";
  state.sources = [];
  messagesEl().innerHTML =
    '<div class="welcome"><h2>' + esc(t("chat.welcome.title")) + "</h2>" +
    "<p>" + esc(t("chat.welcome.desc")) + "</p>" +
    '<div class="suggestions">' +
    ["chat.sugg.1", "chat.sugg.2", "chat.sugg.3"]
      .map((key) => '<button class="suggestion" type="button">' + esc(t(key)) + "</button>")
      .join("") +
    "</div></div>";
  document.querySelectorAll(".suggestion").forEach((btn) =>
    btn.addEventListener("click", () => {
      $("#chat-input").value = btn.textContent;
      $("#composer").requestSubmit();
    }));
  $("#sources-list").innerHTML = '<p class="dim sources-empty">' + esc(t("sources.empty")) + "</p>";
}

function mdInline(s) {
  return s
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>")
    .replace(/`([^`]+)`/g, '<code class="inline-code">$1</code>');
}

function renderMarkdownLite(text) {
  // Escape first, then a small block-level renderer: headings, lists, tables
  // and paragraphs. Answers used to be a few sentences, so inline formatting
  // was enough; now that retrieval can return a whole listing, the model
  // replies with markdown structure and a literal "# " or "| a | b |" in the
  // transcript reads as a rendering bug.
  const lines = esc(text).split("\n");
  const out = [];
  let mode = null; // "ul" | "ol" | "table"
  let firstRow = false;
  const close = () => {
    if (mode === "ul" || mode === "ol") out.push("</" + mode + ">");
    else if (mode === "table") out.push("</tbody></table>");
    mode = null;
  };

  for (const raw of lines) {
    const line = raw.trim();
    if (!line) { close(); continue; }

    let m;
    if ((m = line.match(/^(#{1,4})\s+(.*)$/))) {
      close();
      const h = Math.min(6, m[1].length + 2); // "# " -> h3, "## " -> h4
      out.push("<h" + h + ">" + mdInline(m[2]) + "</h" + h + ">");
      continue;
    }
    if ((m = line.match(/^[-*•●]\s+(.*)$/))) {
      if (mode !== "ul") { close(); out.push("<ul>"); mode = "ul"; }
      out.push("<li>" + mdInline(m[1]) + "</li>");
      continue;
    }
    if ((m = line.match(/^\d+[.)、]\s*(.*)$/))) {
      if (mode !== "ol") { close(); out.push("<ol>"); mode = "ol"; }
      out.push("<li>" + mdInline(m[1]) + "</li>");
      continue;
    }
    if (line.startsWith("|") && line.endsWith("|") && line.length > 2) {
      const cells = line.slice(1, -1).split("|").map((c) => c.trim());
      if (cells.every((c) => /^:?-{2,}:?$/.test(c))) continue; // |---|---| separator
      if (mode !== "table") { close(); out.push("<table><tbody>"); mode = "table"; firstRow = true; }
      const tag = firstRow ? "th" : "td";
      firstRow = false;
      out.push("<tr>" + cells.map((c) => "<" + tag + ">" + mdInline(c) + "</" + tag + ">").join("") + "</tr>");
      continue;
    }
    close();
    out.push("<p>" + mdInline(line) + "</p>");
  }
  close();
  return out.join("");
}

function renderCitationChips(text) {
  // turn [n] into clickable citation chips
  const escaped = renderMarkdownLite(text);
  return escaped.replace(/\[(\d{1,2})\]/g, (m, n) =>
    '<button type="button" class="cite-chip" data-cite="' + n + '" aria-label="Jump to source ' + n + '">[' + n + "]</button>");
}

function bindCiteChips(container) {
  container.querySelectorAll(".cite-chip").forEach((chip) =>
    chip.addEventListener("click", () => {
      $("#sources-panel").classList.remove("hidden");
      const item = document.getElementById("source-" + chip.dataset.cite);
      if (item) {
        item.scrollIntoView({ behavior: "smooth", block: "nearest" });
        item.classList.add("flash");
        setTimeout(() => item.classList.remove("flash"), 1400);
      }
    }));
}

function addMessage(role, content, sources) {
  const wrap = ensureWrap();
  // remove welcome block if present
  const welcome = messagesEl().querySelector(".welcome");
  if (welcome) welcome.remove();

  const div = document.createElement("div");
  div.className = "msg " + role;
  const label = role === "user" ? t("chat.you") : t("chat.assistant");
  const initial = role === "user" ? "U" : "AI";
  let html =
    '<div class="msg-avatar">' + initial + '</div>' +
    '<div class="msg-body"><div class="msg-role">' + esc(label) + "</div>" +
    '<div class="msg-text">' + (role === "assistant" ? renderCitationChips(content) : renderMarkdownLite(content)) + "</div>";
  if (sources && sources.length) {
    html += '<div class="msg-sources">' + esc(t("chat.sourcesPrefix")) + sources.map(
      (s) => '<button type="button" class="src-pill" data-cite="' + s.rank + '">' +
        '<span class="num">' + s.rank + "</span>" + esc(s.doc_name) + "</button>"
    ).join("") + "</div>";
  }
  html += "</div>";
  div.innerHTML = html;
  wrap.appendChild(div);
  bindCiteChips(div);
  div.querySelectorAll(".src-pill").forEach((pill) =>
    pill.addEventListener("click", () => {
      $("#sources-panel").classList.remove("hidden");
      const item = document.getElementById("source-" + pill.dataset.cite);
      if (item) item.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }));
  messagesEl().scrollTop = messagesEl().scrollHeight;
  return div;
}

function renderMessages(messages) {
  state.view = "chat";
  messagesEl().innerHTML = '<div class="messages-wrap"></div>';
  for (const m of messages) addMessage(m.role, m.content, m.sources);
}

function renderSources(sources) {
  state.sources = sources || [];
  const list = $("#sources-list");
  if (!state.sources.length) {
    list.innerHTML = '<p class="dim sources-empty">' + esc(t("sources.none")) + "</p>";
    return;
  }
  list.innerHTML = state.sources.map((s) => {
    const pct = Math.round(Math.min(1, Math.max(0, s.score)) * 100);
    return (
      '<div class="source-item" id="source-' + s.rank + '">' +
      '<div class="src-top"><span class="src-num">' + s.rank + '</span>' +
      '<span class="src-name" title="' + esc(s.doc_name) + '">' + esc(s.doc_name) + "</span>" +
      '<span class="src-score">' + pct + "%</span></div>" +
      '<div class="src-bar"><i style="width:' + pct + '%"></i></div>' +
      '<div class="src-snippet">' + esc(s.snippet) + "</div></div>"
    );
  }).join("");
}

$("#sources-toggle").addEventListener("click", () =>
  $("#sources-panel").classList.toggle("hidden"));
$("#sources-close").addEventListener("click", () =>
  $("#sources-panel").classList.add("hidden"));

/* ---------------- composer / streaming ---------------- */
const input = $("#chat-input");
input.addEventListener("input", () => {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 160) + "px";
});
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    $("#composer").requestSubmit();
  }
});

$("#composer").addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text || state.streaming) return;
  input.value = "";
  input.style.height = "auto";
  await sendMessage(text);
});

async function sendMessage(text) {
  state.streaming = true;
  state.view = "chat";
  $("#send-btn").disabled = true;
  addMessage("user", text);

  // placeholder assistant bubble with typing dots
  const wrap = ensureWrap();
  const placeholder = document.createElement("div");
  placeholder.className = "msg assistant";
  placeholder.innerHTML =
    '<div class="msg-avatar">AI</div><div class="msg-body">' +
    '<div class="msg-role">' + esc(t("chat.assistant")) + '</div><div class="msg-text"><span class="typing"><span></span><span></span><span></span></span></div></div>';
  wrap.appendChild(placeholder);
  messagesEl().scrollTop = messagesEl().scrollHeight;
  const textEl = placeholder.querySelector(".msg-text");
  let answer = "";

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(state.token ? { Authorization: "Bearer " + state.token } : {}),
      },
      body: JSON.stringify({ message: text, conversation_id: state.conversationId }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || t("err.chat", { status: res.status }));
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let started = false;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const frames = buffer.split("\n\n");
      buffer = frames.pop();
      for (const frame of frames) {
        const line = frame.trim();
        if (!line.startsWith("data:")) continue;
        const payload = JSON.parse(line.slice(5));
        if (payload.conversation_id && !state.conversationId) {
          state.conversationId = payload.conversation_id;
          loadConversations();
        }
        if (payload.delta !== undefined) {
          if (!started) { textEl.innerHTML = ""; started = true; }
          answer += payload.delta;
          textEl.innerHTML = renderCitationChips(answer);
          messagesEl().scrollTop = messagesEl().scrollHeight;
        }
        if (payload.done) {
          renderSources(payload.sources || []);
          if (payload.sources && payload.sources.length) $("#sources-panel").classList.remove("hidden");
        }
      }
    }
    if (!started) textEl.textContent = t("chat.noResponse");
    bindCiteChips(textEl);
  } catch (ex) {
    textEl.textContent = ex.message;
  } finally {
    placeholder.classList.add("done");
    state.streaming = false;
    $("#send-btn").disabled = false;
    loadConversations();
  }
}

/* ---------------- settings modal ---------------- */
/* Two panes: the instance audit trail and account controls. No team/member
   management in an on-premise install — accounts are created by the operator. */

function wsOpen() { return !$("#ws-modal").classList.contains("hidden"); }

function openWorkspace() {
  if (wsOpen()) return;
  state.lastFocus = document.activeElement;
  $("#ws-modal").classList.remove("hidden");
  document.body.classList.add("modal-open");
  const me = state.me || {};
  $("#ws-head-org").textContent =
    (me.email || "") + " · " + t(me.is_admin ? "st.admin" : "st.user");
  showWsTab(state.wsTab);
  if (state.wsTab === "audit") loadAudit().catch(() => {});
  $("#ws-close").focus();
}

function closeWorkspace() {
  if (!wsOpen()) return;
  $("#ws-modal").classList.add("hidden");
  document.body.classList.remove("modal-open");
  if (state.lastFocus && state.lastFocus.isConnected) state.lastFocus.focus();
  state.lastFocus = null;
}

$("#ws-btn").addEventListener("click", openWorkspace);
$("#ws-close").addEventListener("click", closeWorkspace);
$("#ws-modal").addEventListener("click", (e) => {
  if (e.target === $("#ws-modal")) closeWorkspace();
});

function showWsTab(name) {
  state.wsTab = name;
  document.querySelectorAll("#ws-modal [data-ws-tab]").forEach((tab) => {
    const on = tab.dataset.wsTab === name;
    tab.classList.toggle("active", on);
    tab.setAttribute("aria-selected", String(on));
  });
  document.querySelectorAll("#ws-modal .int-pane").forEach((pane) => {
    pane.classList.toggle("active", pane.id === "ws-pane-" + name);
  });
}
document.querySelectorAll("#ws-modal [data-ws-tab]").forEach((tab) =>
  tab.addEventListener("click", () => {
    showWsTab(tab.dataset.wsTab);
    if (tab.dataset.wsTab === "audit") loadAudit().catch(() => {});
  }));

async function loadAudit() {
  const data = await api("/api/audit?days=30&limit=200");
  const box = $("#ws-audit-list");
  if (!data.events.length) {
    box.innerHTML = '<p class="dim">' + esc(t("ws.auditEmpty")) + "</p>";
    return;
  }
  box.innerHTML = data.events.map((ev) => {
    const what = t("ws.ev." + ev.action) !== "ws.ev." + ev.action
      ? t("ws.ev." + ev.action) : ev.action;
    return '<div class="ws-audit-row"><span class="dim">' + esc(fmtTime(ev.created_at)) +
      "</span><strong>" + esc(ev.actor_email) + "</strong><span>" + esc(what) +
      (ev.detail ? ' <span class="dim">· ' + esc(ev.detail) + "</span>" : "") +
      "</span></div>";
  }).join("");
}

/* ---------- account pane ---------- */
$("#ws-pass-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const data = await api("/api/auth/change-password", {
      method: "POST",
      body: {
        current_password: $("#ws-pass-current").value,
        new_password: $("#ws-pass-new").value,
      },
    });
    // the server just revoked every old session: adopt the replacement token
    state.token = data.token;
    localStorage.setItem("ragbase_token", data.token);
    $("#ws-pass-current").value = "";
    $("#ws-pass-new").value = "";
    showNotice(t("ws.acc.changed"));
  } catch (ex) {
    alert(ex.message);
  }
});

$("#ws-logout-all").addEventListener("click", async () => {
  try {
    await api("/api/auth/sessions/revoke", { method: "POST", body: {} });
    closeWorkspace();
    logout();
  } catch (ex) {
    alert(ex.message);
  }
});

$("#ws-export").addEventListener("click", async () => {
  try {
    const res = await fetch("/api/export", {
      headers: { Authorization: "Bearer " + state.token },
    });
    if (!res.ok) throw new Error(t("err.request", { status: res.status }));
    const disposition = res.headers.get("Content-Disposition") || "";
    const match = disposition.match(/filename="([^"]+)"/);
    const url = URL.createObjectURL(await res.blob());
    const link = document.createElement("a");
    link.href = url;
    link.download = match ? match[1] : "ragbase-export.json";
    link.click();
    URL.revokeObjectURL(url);
  } catch (ex) {
    alert(ex.message);
  }
});

$("#ws-del-account").addEventListener("click", async () => {
  const pass = $("#ws-del-pass").value;
  if (!pass) return;
  if (!(await confirmDialog(t("ws.acc.deleteAccount"), t("ws.acc.deleteHint"), t("ws.acc.deleteGo")))) return;
  try {
    await api("/api/account", { method: "DELETE", body: { password: pass } });
    closeWorkspace();
    logout();
  } catch (ex) {
    alert(ex.message);
  }
});

/* ---------------- language ---------------- */
function refreshForLang() {
  applyI18n();
  $("#auth-lang-label").textContent = t("lang.name");
  if (state.view === "auth") {
    setAuthMode(state.mode);
    return;
  }
  renderBanner();
  loadConversations();
  loadLibrary();
  if (integrateOpen()) {
    loadKeys();
    renderApiDocs();
    if (state.editingKey) updateDocScope(); // its counter is built with t()
  }
  if (wsOpen() && state.wsTab === "audit") loadAudit().catch(() => {});
  if (state.streaming) return; // don't clobber an answer that is still streaming
  if (state.conversationId) {
    api("/api/conversations/" + state.conversationId + "/messages")
      .then((data) => {
        renderMessages(data.messages);
        renderSources(state.sources);
      })
      .catch(() => {});
  } else {
    showWelcome();
  }
}

$("#lang-btn").addEventListener("click", toggleLang);
$("#auth-lang-btn").addEventListener("click", toggleLang);
document.addEventListener("langchange", refreshForLang);

/* ---------------- init ---------------- */
applyI18n();
$("#auth-lang-label").textContent = t("lang.name");
setAuthMode("login");
boot();
