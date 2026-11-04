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
    "lang.name": "中文",
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
    "nav.integrate": "Embed & API",

    /* ---- integrate ---- */
    "int.title": "Integration & API",
    "int.close": "Close",
    "int.navAria": "Sections",
    "int.tab.embed": "Embed",
    "int.tab.api": "API",
    "int.tab.keys": "API keys",
    "int.embed.title": "Embed in your website",
    "int.embed.desc": "Paste this snippet into any page — a chat window connected to your knowledge base appears in the corner.",
    "int.embed.copy": "Copy snippet",
    "int.embed.copied": "Copied!",
    "int.embed.demo": "Open demo",
    "int.embed.demoTitle": "Open the embed demo in a new tab",
    "int.embed.demoKeyLabel": "Embed demo",
    "int.api.title": "API access",
    "int.api.desc": "Call your knowledge base from your own backend. Ask once and get the full answer plus its sources — or stream it token by token.",
    "int.api.endpoint": "Endpoint",
    "int.api.header": "Header",
    "int.api.tabCurl": "cURL",
    "int.api.tabPython": "Python",
    "int.api.tabNode": "Node.js",
    "int.api.copy": "Copy",
    "int.api.sample": "What is the refund policy?",
    "int.api.copied": "Copied!",
    "int.api.note": "Multi-turn: send the conversation_id from the previous response back. Streaming stays on this same endpoint — just add \"stream\": true.",
    "int.api.modeJson": "Full response",
    "int.api.modeStream": "Streaming",
    "int.api.modeAria": "Response mode",
    "int.api.noteStream": "Server-sent events: each token arrives as data: {\"delta\": \"…\"}. The last event is data: {\"done\": true, \"conversation_id\": …, \"sources\": […]}.",
    "int.api.noKey": "Create a key below — it replaces YOUR_API_KEY in the examples.",
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
    "kb.chunks": "{n} chunks",
    "kb.chunkOne": "1 chunk",
    "kb.count": "{n} documents · {c} chunks",
    "kb.countOne": "1 document · {c} chunks",
    "kb.uploadAria": "Upload documents",
    "kb.delete": "Delete document",
    "kb.hint": "Search, multi-select and bulk delete live in the file library.",

    /* ---- file library ---- */
    "lib.title": "File library",
    "lib.search": "Search by file name",
    "lib.searchClear": "Clear search",
    "lib.sortAria": "Sort documents",
    "lib.sortNew": "Newest first",
    "lib.sortOld": "Oldest first",
    "lib.sortAz": "Name A→Z",
    "lib.sortZa": "Name Z→A",
    "lib.sortChunks": "Most chunks",
    "lib.viewAria": "View",
    "lib.viewGrid": "Grid view",
    "lib.viewList": "List view",
    "lib.gridAria": "Documents",
    "lib.upload": "Upload",
    "lib.selectAll": "Select all",
    "lib.selected": "{n} selected",
    "lib.invert": "Invert",
    "lib.delete": "Delete",
    "lib.clear": "Cancel",
    "lib.cancel": "Cancel",
    "lib.dismiss": "Dismiss",
    "lib.pick": "Select {name}",
    "lib.emptyTitle": "No documents yet",
    "lib.emptyHint": "Drop PDF, Word or Markdown files anywhere in this area, or use Upload.",
    "lib.noMatchTitle": "No file matches “{q}”",
    "lib.noMatchHint": "Try another keyword, or clear the search.",
    "lib.found": "{n} of {total} documents",
    "lib.delOne": "Delete this document?",
    "lib.delMany": "Delete {n} documents?",
    "lib.delDesc": "Their chunks and vectors go with them. This cannot be undone.",
    "lib.uploading": "Uploading {done} / {total}",
    "lib.uploadDone": "Uploaded {n} documents",
    "lib.uploadSome": "{ok} uploaded · {n} failed",
    "lib.uploadBatchFailed": "{n} files failed",
    "lib.drop": "Release to upload",
    "lib.busy": "An upload is already running",

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
    "err.keyCreate": "Failed to create key: {msg}",
    "err.keySave": "Failed to save: {msg}",
    "err.failed": "Failed",

    /* ---- per-key customizer ---- */
    "key.edit": "Customize",
    "key.locked": "Domains restricted",
    "key.back": "Back to the list",
    "key.editTitle": "Customize widget",
    "key.save": "Save",
    "key.saved": "Saved",
    "key.secBranding": "Branding",
    "key.secBehavior": "Behaviour",
    "key.secScope": "Scope & limits",
    "key.secSecurity": "Security",
    "key.theme": "Theme",
    "key.themeAuto": "Auto",
    "key.themeLight": "Light",
    "key.themeDark": "Dark",
    "key.accent": "Accent colour",
    "key.title": "Header title",
    "key.subtitle": "Header subtitle",
    "key.greeting": "Greeting",
    "key.placeholder": "Input placeholder",
    "key.phTitle": "Ask us anything",
    "key.phSubtitle": "answers from our docs",
    "key.phGreeting": "Hi! Ask me anything…",
    "key.phPlaceholder": "Type your question…",
    "key.position": "Launcher corner",
    "key.posRight": "Bottom right",
    "key.posLeft": "Bottom left",
    "key.icon": "Launcher icon",
    "key.iconChat": "Chat",
    "key.iconSpark": "Spark",
    "key.iconLogo": "Logo",
    "key.logoUrl": "Logo URL",
    "key.hideBranding": "Hide “Powered by”",
    "key.proOnly": "Pro",
    "key.autoOpen": "Auto-open",
    "key.autoNever": "Never",
    "key.autoDelay": "After delay",
    "key.autoAlways": "On load",
    "key.delaySeconds": "Delay (seconds)",
    "key.starters": "Starter questions",
    "key.phStarters": "One per line, up to 4",
    "key.showSources": "Show cited sources",
    "key.persist": "Keep the conversation across reloads",
    "key.fallback": "“Nothing found” reply",
    "key.phFallback": "Leave empty to let the model answer",
    "key.docsHint": "Only these documents can be retrieved. Leave all unchecked to expose the whole knowledge base.",
    "key.docsAll": "Select all",
    "key.docsAllDone": "Every document is already selected",
    "key.docsInvert": "Invert",
    "key.docsCount": "{n} of {total} selected",
    "key.docsUnrestricted": "No filter — whole knowledge base",
    "key.quotaMin": "Requests / minute",
    "key.quotaDay": "Requests / day",
    "key.quotaHint": "0 means unlimited.",
    "key.originsHint": "One domain per line. While empty, any site holding the key can embed the widget. Supports *.example.com.",
    "key.preview": "Live preview",
    "key.launcher": "Launcher",
    "key.usageMin": "This minute",
    "key.usageDay": "Today",
    "key.usageTotal": "Total",
    "key.loading": "Loading…",
    "key.noDocs": "No documents uploaded yet.",
    "key.pvTitle": "Ask us anything",
    "key.pvSub": "answers from our docs",
    "key.pvGreeting": "Hi! Ask me anything — I answer from your documentation.",
    "key.pvPh": "Type your question…",
  },

  zh: {
    "app.title": "RAGbase — AI 知识库问答",
    "lang.name": "English",
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
    "nav.integrate": "嵌入与 API",

    /* ---- integrate ---- */
    "int.title": "嵌入与 API",
    "int.close": "关闭",
    "int.navAria": "分区",
    "int.tab.embed": "嵌入",
    "int.tab.api": "API",
    "int.tab.keys": "API 密钥",
    "int.embed.title": "嵌入到你的网站",
    "int.embed.desc": "把这段代码粘贴到任意页面，右下角就会出现一个连着知识库的聊天窗口。",
    "int.embed.copy": "复制代码",
    "int.embed.copied": "已复制！",
    "int.embed.demo": "打开 Demo",
    "int.embed.demoTitle": "在新标签页打开嵌入效果演示",
    "int.embed.demoKeyLabel": "演示密钥",
    "int.api.title": "API 对接",
    "int.api.desc": "在你自己的后端直接调用知识库。既可以一次性拿到完整答案与来源，也可以开流式逐字输出，适合接客服系统、小程序、内部工具。",
    "int.api.endpoint": "端点",
    "int.api.header": "请求头",
    "int.api.tabCurl": "cURL",
    "int.api.tabPython": "Python",
    "int.api.tabNode": "Node.js",
    "int.api.copy": "复制",
    "int.api.sample": "退款期限是多久？",
    "int.api.copied": "已复制！",
    "int.api.note": "多轮对话：把上一次响应里的 conversation_id 带回来即可延续上下文。流式走同一个端点，只需加 \"stream\": true。",
    "int.api.modeJson": "完整响应",
    "int.api.modeStream": "流式",
    "int.api.modeAria": "响应方式",
    "int.api.noteStream": "SSE 推送：每个 token 是一帧 data: {\"delta\": \"…\"}，最后一帧是 data: {\"done\": true, \"conversation_id\": …, \"sources\": […]}。",
    "int.api.noKey": "在下方创建一个密钥，示例里的 YOUR_API_KEY 会自动替换成真实密钥。",
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
    "kb.chunks": "{n} 个片段",
    "kb.chunkOne": "1 个片段",
    "kb.count": "{n} 份文档 · {c} 个片段",
    "kb.countOne": "1 份文档 · {c} 个片段",
    "kb.uploadAria": "上传文档",
    "kb.delete": "删除文档",
    "kb.hint": "搜索、多选与批量删除都在右侧的文件库中进行。",

    /* ---- 文件库 ---- */
    "lib.title": "文件库",
    "lib.search": "按文件名搜索",
    "lib.searchClear": "清除搜索",
    "lib.sortAria": "排序方式",
    "lib.sortNew": "最新上传",
    "lib.sortOld": "最早上传",
    "lib.sortAz": "名称 A→Z",
    "lib.sortZa": "名称 Z→A",
    "lib.sortChunks": "片段最多",
    "lib.viewAria": "视图",
    "lib.viewGrid": "网格视图",
    "lib.viewList": "列表视图",
    "lib.gridAria": "文档列表",
    "lib.upload": "上传",
    "lib.selectAll": "全选",
    "lib.selected": "已选 {n} 项",
    "lib.invert": "反选",
    "lib.delete": "删除",
    "lib.clear": "取消",
    "lib.cancel": "取消",
    "lib.dismiss": "关闭",
    "lib.pick": "选择 {name}",
    "lib.emptyTitle": "还没有文档",
    "lib.emptyHint": "把 PDF、Word 或 Markdown 拖到这个区域的任意位置，或点右上角上传。",
    "lib.noMatchTitle": "没有匹配「{q}」的文件",
    "lib.noMatchHint": "换个关键词，或者清掉搜索框。",
    "lib.found": "找到 {n} 份 · 共 {total} 份",
    "lib.delOne": "删除这份文档？",
    "lib.delMany": "删除选中的 {n} 份文档？",
    "lib.delDesc": "文档的片段与向量索引会一并删除，无法撤销。",
    "lib.uploading": "正在上传 {done} / {total}",
    "lib.uploadDone": "已上传 {n} 份文档",
    "lib.uploadSome": "{ok} 份成功 · {n} 份失败",
    "lib.uploadBatchFailed": "{n} 个文件上传失败",
    "lib.drop": "松开即可上传",
    "lib.busy": "已有上传任务在进行",

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
    "err.keyCreate": "创建密钥失败：{msg}",
    "err.keySave": "保存失败：{msg}",
    "err.failed": "处理失败",

    /* ---- per-key customizer ---- */
    "key.edit": "定制",
    "key.locked": "已限制域名",
    "key.back": "返回列表",
    "key.editTitle": "定制挂件",
    "key.save": "保存",
    "key.saved": "已保存",
    "key.secBranding": "品牌外观",
    "key.secBehavior": "交互行为",
    "key.secScope": "范围与配额",
    "key.secSecurity": "安全",
    "key.theme": "主题",
    "key.themeAuto": "跟随系统",
    "key.themeLight": "浅色",
    "key.themeDark": "深色",
    "key.accent": "主色",
    "key.title": "标题",
    "key.subtitle": "副标题",
    "key.greeting": "欢迎语",
    "key.placeholder": "输入框提示",
    "key.phTitle": "有问题尽管问",
    "key.phSubtitle": "答案来自我们的文档",
    "key.phGreeting": "你好！有什么想了解的尽管问…",
    "key.phPlaceholder": "输入你的问题…",
    "key.position": "挂件位置",
    "key.posRight": "右下角",
    "key.posLeft": "左下角",
    "key.icon": "图标",
    "key.iconChat": "聊天气泡",
    "key.iconSpark": "星芒",
    "key.iconLogo": "自定义 Logo",
    "key.logoUrl": "Logo 地址",
    "key.hideBranding": "隐藏“Powered by”",
    "key.proOnly": "Pro",
    "key.autoOpen": "自动弹出",
    "key.autoNever": "从不",
    "key.autoDelay": "延迟弹出",
    "key.autoAlways": "加载即弹",
    "key.delaySeconds": "延迟秒数",
    "key.starters": "建议问题",
    "key.phStarters": "每行一个问题，最多 4 条",
    "key.showSources": "显示引用来源",
    "key.persist": "刷新后保留会话",
    "key.fallback": "检索不到时的回复",
    "key.phFallback": "留空则由模型自行回答",
    "key.docsHint": "只有勾选的文档可被检索。全部不勾选表示开放整个知识库。",
    "key.docsAll": "全选",
    "key.docsAllDone": "所有文档都已勾选",
    "key.docsInvert": "反选",
    "key.docsCount": "已选 {n}/{total}",
    "key.docsUnrestricted": "未过滤 · 整库可检索",
    "key.quotaMin": "每分钟请求数",
    "key.quotaDay": "每日请求数",
    "key.quotaHint": "填 0 表示不限制。",
    "key.originsHint": "一行一个域名。留空时，任何拿到密钥的站点都能嵌入。支持 *.example.com。",
    "key.preview": "实时预览",
    "key.launcher": "挂件按钮",
    "key.usageMin": "本分钟",
    "key.usageDay": "今日",
    "key.usageTotal": "累计",
    "key.loading": "加载中…",
    "key.noDocs": "还没有上传文档。",
    "key.pvTitle": "有问题尽管问",
    "key.pvSub": "答案来自我们的文档",
    "key.pvGreeting": "你好！有什么想了解的尽管问 —— 我会基于你们的文档回答。",
    "key.pvPh": "输入你的问题…",
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
