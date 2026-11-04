/* RAGbase i18n — zh/en dictionaries + DOM applier, no build step, no deps.
 *
 * Usage in HTML:   <span data-i18n="key"></span>
 *                 <p data-i18n-html="key"></p>        (keeps markup like <strong>)
 *                 <input data-i18n-ph="key" />        (placeholder)
 *                 <button data-i18n-title="key">      (title + aria-label)
 * Usage in JS:     t("key", { n: 3 })
 * Events:          document.addEventListener("langchange", ...)
 */
"use strict";

const RAGBASE_LANG_KEY = "ragbase_lang";

const I18N = {
  en: {
    "app.title": "RAGbase — AI Knowledge Base Chat",
    "lang.name": "EN",
    "lang.switch": "Switch to Chinese",

    /* ---- auth ---- */
    "auth.login.title": "Welcome back",
    "auth.login.sub": "Sign in to chat with your knowledge base.",
    "auth.login.submit": "Sign in",
    "auth.register.title": "Create your account",
    "auth.register.sub": "The first registered account becomes the admin.",
    "auth.register.submit": "Create account",
    "auth.email": "Email",
    "auth.password": "Password",
    "auth.email.ph": "you@company.com",
    "auth.password.ph": "6+ characters",
    "auth.noAccount": "No account yet?",
    "auth.haveAccount": "Already have an account?",
    "auth.createOne": "Create one",
    "auth.toLogin": "Sign in",
    "auth.missingFields": "Email and password are required.",

    /* ---- topbar / nav ---- */
    "nav.toggleSidebar": "Toggle sidebar",
    "nav.sources": "Sources",
    "nav.signOut": "Sign out",
    "nav.newChat": "New chat",
    "nav.tabChats": "Chats",
    "nav.tabKb": "Knowledge base",
    "nav.integrate": "Integrate",

    /* ---- integrate ---- */
    "int.title": "Integration & API",
    "int.back": "Back",
    "int.embed.title": "Embed in your website",
    "int.embed.desc": "Paste this snippet into any page — a chat window connected to your knowledge base appears in the corner.",
    "int.embed.copy": "Copy snippet",
    "int.embed.copied": "Copied!",
    "int.keys.title": "API keys",
    "int.keys.desc": "Used by the embed and server-to-server integrations. A key can only chat — it cannot manage documents or users.",
    "int.keys.create": "Create key",
    "int.keys.once": "Copy it now — it won't be shown again:",
    "int.keys.labelPh": "Label (e.g. company site)",
    "int.keys.empty": "No API keys yet. Create one to embed the chat widget.",
    "int.keys.revoke": "Revoke",

    /* ---- knowledge base ---- */
    "kb.drop": "Drop documents here",
    "kb.dropHint": "or click to browse · PDF, DOCX, TXT, MD, HTML",
    "kb.empty": "Knowledge base is empty.<br>Upload documents to get started.",
    "kb.chunks": "{n} chunks",
    "kb.chunkOne": "1 chunk",
    "kb.uploadAria": "Upload documents",
    "kb.delete": "Delete document",

    /* ---- chat ---- */
    "chat.placeholder": "Ask anything about your documents…",
    "chat.hint": "Answers cite the uploaded knowledge base. Verify important information.",
    "chat.send": "Send",
    "chat.welcome.title": "Ask your knowledge base anything",
    "chat.welcome.desc": "Upload documents in the Knowledge base tab, then ask questions here. Every answer cites its sources.",
    "chat.sugg.1": "What are the main topics covered?",
    "chat.sugg.2": "Summarize the key points",
    "chat.sugg.3": "What does the document say about pricing?",
    "chat.you": "You",
    "chat.assistant": "Assistant",
    "chat.noResponse": "No response.",
    "chat.sourcesPrefix": "Sources: ",

    /* ---- sources panel ---- */
    "sources.title": "Sources",
    "sources.close": "Close sources panel",
    "sources.empty": "Cited sources will appear here after the assistant answers.",
    "sources.none": "No relevant passages were found in the knowledge base for this question.",

    /* ---- conversations ---- */
    "conv.empty": "No conversations yet.",
    "conv.delete": "Delete conversation",
    "conv.newTitle": "New conversation",

    /* ---- banners ---- */
    "banner.demo": "Demo mode — no chat model configured. Answers are extracted locally from your documents. Add <code>RAGBASE_LLM_API_KEY</code> in <code>.env</code> to enable the full LLM.",
    "banner.embeddingLocal": "Chat model: <strong>{model}</strong> — embeddings run locally (no embedding API configured); BM25 lexical search is active.",

    /* ---- errors ---- */
    "err.sessionExpired": "Session expired — please sign in again.",
    "err.request": "Request failed ({status})",
    "err.chat": "Chat failed ({status})",
    "err.upload": "Upload failed: {msg}",
    "err.uploadSome": "Some files failed:\n{list}",
    "err.keyCreate": "Failed to create key: {msg}",
    "err.failed": "Failed",
  },

  zh: {
    "app.title": "RAGbase — AI 知识库问答",
    "lang.name": "中文",
    "lang.switch": "切换到英文",

    /* ---- auth ---- */
    "auth.login.title": "欢迎回来",
    "auth.login.sub": "登录后即可与你的知识库对话。",
    "auth.login.submit": "登录",
    "auth.register.title": "创建账号",
    "auth.register.sub": "首个注册的账号将成为管理员。",
    "auth.register.submit": "创建账号",
    "auth.email": "邮箱",
    "auth.password": "密码",
    "auth.email.ph": "you@company.com",
    "auth.password.ph": "至少 6 位字符",
    "auth.noAccount": "还没有账号？",
    "auth.haveAccount": "已有账号？",
    "auth.createOne": "立即注册",
    "auth.toLogin": "去登录",
    "auth.missingFields": "邮箱和密码不能为空。",

    /* ---- topbar / nav ---- */
    "nav.toggleSidebar": "展开/收起侧栏",
    "nav.sources": "引用来源",
    "nav.signOut": "退出登录",
    "nav.newChat": "新对话",
    "nav.tabChats": "对话",
    "nav.tabKb": "知识库",
    "nav.integrate": "嵌入集成",

    /* ---- integrate ---- */
    "int.title": "嵌入与 API",
    "int.back": "返回",
    "int.embed.title": "嵌入到你的网站",
    "int.embed.desc": "把这段代码粘贴到任意页面，右下角就会出现一个连着知识库的聊天窗口。",
    "int.embed.copy": "复制代码",
    "int.embed.copied": "已复制！",
    "int.keys.title": "API 密钥",
    "int.keys.desc": "用于网页挂件和服务端对接。密钥只能发起对话，不能管理文档或用户。",
    "int.keys.create": "创建密钥",
    "int.keys.once": "请立即复制，密钥只会显示这一次：",
    "int.keys.labelPh": "备注（例如：官网）",
    "int.keys.empty": "还没有 API 密钥。创建一个即可嵌入聊天挂件。",
    "int.keys.revoke": "吊销",

    /* ---- knowledge base ---- */
    "kb.drop": "把文档拖到这里",
    "kb.dropHint": "或点击选择文件 · PDF、DOCX、TXT、MD、HTML",
    "kb.empty": "知识库还是空的。<br>上传文档即可开始。",
    "kb.chunks": "{n} 个片段",
    "kb.chunkOne": "1 个片段",
    "kb.uploadAria": "上传文档",
    "kb.delete": "删除文档",

    /* ---- chat ---- */
    "chat.placeholder": "就你的文档提问…",
    "chat.hint": "回答均引用已上传的知识库内容，重要信息请自行核实。",
    "chat.send": "发送",
    "chat.welcome.title": "向你的知识库提问",
    "chat.welcome.desc": "先在「知识库」标签上传文档，然后在这里提问。每个回答都会标注引用来源。",
    "chat.sugg.1": "文档主要讲了什么？",
    "chat.sugg.2": "总结一下关键要点",
    "chat.sugg.3": "文档里关于价格是怎么说的？",
    "chat.you": "你",
    "chat.assistant": "助手",
    "chat.noResponse": "没有返回内容。",
    "chat.sourcesPrefix": "引用：",

    /* ---- sources panel ---- */
    "sources.title": "引用来源",
    "sources.close": "关闭来源面板",
    "sources.empty": "助手回答后，引用的来源会显示在这里。",
    "sources.none": "这个问题在知识库中没有找到相关段落。",

    /* ---- conversations ---- */
    "conv.empty": "还没有对话。",
    "conv.delete": "删除对话",
    "conv.newTitle": "新对话",

    /* ---- banners ---- */
    "banner.demo": "演示模式 —— 未配置对话模型，回答由本地从文档中抽取生成。在 <code>.env</code> 中填入 <code>RAGBASE_LLM_API_KEY</code> 即可启用完整模型。",
    "banner.embeddingLocal": "对话模型：<strong>{model}</strong> —— 向量在本地计算（未配置 embedding API），BM25 词法检索仍然生效。",

    /* ---- errors ---- */
    "err.sessionExpired": "登录状态已过期，请重新登录。",
    "err.request": "请求失败（{status}）",
    "err.chat": "对话请求失败（{status}）",
    "err.upload": "上传失败：{msg}",
    "err.uploadSome": "部分文件处理失败：\n{list}",
    "err.keyCreate": "创建密钥失败：{msg}",
    "err.failed": "处理失败",
  },
};

let CURRENT_LANG = "en";

function detectLang() {
  const saved = localStorage.getItem(RAGBASE_LANG_KEY);
  if (saved && I18N[saved]) return saved;
  const nav = (navigator.language || "en").toLowerCase();
  return nav.startsWith("zh") ? "zh" : "en";
}

function t(key, vars) {
  const dict = I18N[CURRENT_LANG] || I18N.en;
  let s = dict[key];
  if (s === undefined) s = I18N.en[key];
  if (s === undefined) return key;
  if (vars) {
    for (const name in vars) s = s.split("{" + name + "}").join(vars[name]);
  }
  return s;
}

function currentLang() {
  return CURRENT_LANG;
}

function applyI18n(root) {
  const scope = root || document;
  document.documentElement.lang = CURRENT_LANG === "zh" ? "zh-CN" : "en";
  document.title = t("app.title");
  scope.querySelectorAll("[data-i18n]").forEach((el) => {
    el.textContent = t(el.dataset.i18n);
  });
  scope.querySelectorAll("[data-i18n-html]").forEach((el) => {
    el.innerHTML = t(el.dataset.i18nHtml);
  });
  scope.querySelectorAll("[data-i18n-ph]").forEach((el) => {
    el.placeholder = t(el.dataset.i18nPh);
  });
  scope.querySelectorAll("[data-i18n-title]").forEach((el) => {
    const label = t(el.dataset.i18nTitle);
    el.title = label;
    el.setAttribute("aria-label", label);
  });
  const langLabel = document.getElementById("lang-label");
  if (langLabel) {
    langLabel.textContent = t("lang.name");
    const btn = langLabel.closest("button");
    if (btn) {
      btn.title = t("lang.switch");
      btn.setAttribute("aria-label", t("lang.switch"));
    }
  }
}

function setLang(lang, opts) {
  CURRENT_LANG = I18N[lang] ? lang : "en";
  if (!opts || opts.persist !== false) localStorage.setItem(RAGBASE_LANG_KEY, CURRENT_LANG);
  applyI18n();
  document.dispatchEvent(new CustomEvent("langchange", { detail: { lang: CURRENT_LANG } }));
}

function toggleLang() {
  setLang(CURRENT_LANG === "zh" ? "en" : "zh");
}

CURRENT_LANG = detectLang();
