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
  if (!res.ok) throw new Error(data.detail || t("err.request", { status: res.status }));
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
    $("#user-email").textContent = me.email;
    state.banner = me.demo_mode ? "demo" : me.embedding_local ? "embedding" : null;
    state.bannerModel = me.model;
    renderBanner();
    showApp();
    await Promise.all([loadConversations(), loadDocuments()]);
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
  $("#tab-chats").classList.toggle("active", which === "chats");
  $("#tab-kb").classList.toggle("active", which === "kb");
  $("#pane-chats").classList.toggle("hidden", which !== "chats");
  $("#pane-kb").classList.toggle("hidden", which !== "kb");
  $("#pane-int").classList.add("hidden");
  $("#settings-btn").classList.remove("active");
}

function openIntegrate() {
  $("#pane-chats").classList.add("hidden");
  $("#pane-kb").classList.add("hidden");
  $("#pane-int").classList.remove("hidden");
  $("#settings-btn").classList.add("active");
  loadKeys();
  renderEmbedCode();
  closeSidebarMobile();
}

$("#tab-chats").addEventListener("click", () => switchTab("chats"));
$("#tab-kb").addEventListener("click", () => switchTab("kb"));
$("#settings-btn").addEventListener("click", openIntegrate);
$("#int-back").addEventListener("click", () => switchTab($("#tab-kb").classList.contains("active") ? "kb" : "chats"));

/* ---------------- integrate: embed code + API keys ---------------- */
function renderEmbedCode(key) {
  const origin = location.origin;
  const snippet =
    '<!-- RAGbase chat widget -->\n' +
    '<iframe src="' + origin + "/widget?key=" + (key || "YOUR_API_KEY") + '"\n' +
    '  style="position:fixed;bottom:20px;right:20px;width:380px;height:600px;max-height:80vh;border:none;z-index:9999;border-radius:16px;box-shadow:0 8px 30px rgba(0,0,0,.35);"\n' +
    '  title="Knowledge base chat"></iframe>';
  $("#embed-code").value = snippet;
}

$("#copy-embed").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText($("#embed-code").value);
    $("#copy-embed").textContent = t("int.embed.copied");
  } catch {
    $("#embed-code").select();
    document.execCommand("copy");
    $("#copy-embed").textContent = t("int.embed.copied");
  }
  setTimeout(() => ($("#copy-embed").textContent = t("int.embed.copy")), 1500);
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
    item.innerHTML =
      '<span class="key-prefix">' + esc(k.prefix) + '…</span>' +
      '<span class="key-label">' + esc(k.label || "—") + "</span>" +
      '<button class="key-del" aria-label="' + esc(t("int.keys.revoke")) + '" title="' + esc(t("int.keys.revoke")) + '">' +
      '<svg width="12" height="12" viewBox="0 0 14 14" fill="none"><path d="M3 3l8 8M11 3l-8 8" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg></button>';
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
    renderEmbedCode(data.key);
    loadKeys();
  } catch (ex) {
    alert(t("err.keyCreate", { msg: ex.message }));
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

/* ---------------- documents ---------------- */
const FILE_ICON =
  '<svg class="doc-icon" width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M4 1.5h5.5L13 5v9.5H4z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/><path d="M9.5 1.5V5H13" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg>';

async function loadDocuments() {
  const data = await api("/api/documents");
  const list = $("#doc-list");
  list.innerHTML = "";
  if (!data.documents.length) {
    list.innerHTML = '<p class="pane-empty">' + t("kb.empty") + "</p>";
    return;
  }
  for (const doc of data.documents) {
    const item = document.createElement("div");
    item.className = "doc-item";
    const meta = doc.status === "ready"
      ? esc(t(doc.num_chunks === 1 ? "kb.chunkOne" : "kb.chunks", { n: doc.num_chunks })) + " · " + fmtTime(doc.created_at)
      : '<span class="doc-error">' + esc(doc.error || t("err.failed")) + "</span>";
    item.innerHTML =
      FILE_ICON +
      '<div class="doc-body"><div class="doc-name" title="' + esc(doc.filename) + '">' + esc(doc.filename) + "</div>" +
      '<div class="doc-meta">' + meta + "</div></div>" +
      '<button class="doc-del" aria-label="' + esc(t("kb.delete")) + '" title="' + esc(t("kb.delete")) + '">' +
      '<svg width="13" height="13" viewBox="0 0 14 14" fill="none"><path d="M3 3l8 8M11 3l-8 8" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg></button>';
    item.querySelector(".doc-del").addEventListener("click", async () => {
      await api("/api/documents/" + doc.id, { method: "DELETE" });
      loadDocuments();
    });
    list.appendChild(item);
  }
}

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

async function uploadFiles(fileList) {
  const fd = new FormData();
  for (const f of fileList) fd.append("files", f);
  dropZone.style.opacity = "0.5";
  dropZone.style.pointerEvents = "none";
  try {
    const data = await api("/api/documents", { method: "POST", body: fd });
    const fails = data.results.filter((r) => !r.ok);
    if (fails.length) {
      alert(t("err.uploadSome", {
        list: fails.map((f) => "• " + f.filename + ": " + f.error).join("\n"),
      }));
    }
  } catch (ex) {
    alert(t("err.upload", { msg: ex.message }));
  } finally {
    dropZone.style.opacity = "";
    dropZone.style.pointerEvents = "";
    await loadDocuments();
  }
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

function renderMarkdownLite(text) {
  // escape first, then apply minimal markdown: bold, italic, inline code
  let h = esc(text);
  h = h.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  h = h.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
  h = h.replace(/`([^`]+)`/g, '<code class="inline-code">$1</code>');
  return h;
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
  loadDocuments();
  if (!$("#pane-int").classList.contains("hidden")) loadKeys();
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
