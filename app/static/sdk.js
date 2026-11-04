/*!
 * RAGbase embed SDK — one <script> tag instead of hand-written iframe markup.
 *
 *   <script src="https://your-host/sdk.js" data-key="rbk_..." data-lang="zh"></script>
 *
 * Everything renders inside a shadow root so the host page's CSS cannot leak in
 * (and ours cannot leak out). Exposes window.RAGbase.open/close/toggle.
 */
(function (w, d) {
  "use strict";
  if (w.RAGbase && w.RAGbase.version) return;

  var script = d.currentScript;
  var KEY = (script && script.getAttribute("data-key")) || "";
  var LANG = (script && script.getAttribute("data-lang")) || "";
  var BASE = script && script.src ? new URL(script.src, location.href).origin : location.origin;

  var state = { open: false, loaded: false };

  var host = d.createElement("div");
  host.setAttribute("data-ragbase-widget", "");
  host.style.cssText = "position:fixed;inset:0;z-index:2147483000;pointer-events:none;";
  d.body.appendChild(host);

  var root = host.attachShadow({ mode: "open" });
  root.innerHTML = [
    "<style>",
    /* `all:initial` would also wipe inherited custom properties, so reset only
       the inheritable typography that could leak in from the host page */
    ":host{display:block;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;",
    "line-height:1.5;letter-spacing:normal;text-transform:none;direction:ltr}",
    "*{box-sizing:border-box}",
    ".l{position:fixed;right:20px;bottom:20px;width:56px;height:56px;border-radius:50%;",
    "border:0;display:grid;place-items:center;cursor:pointer;pointer-events:auto;",
    "background:var(--accent,#c6f24e);color:var(--ink,#131a08);",
    "box-shadow:0 8px 24px rgba(0,0,0,.4);transition:transform .16s ease,box-shadow .16s ease}",
    ".l:hover{transform:translateY(-1px) scale(1.04)}",
    ".l:focus-visible{outline:2px solid var(--accent);outline-offset:3px}",
    ".l .x{display:none}",
    ":host(.open) .l .c{display:none}",
    ":host(.open) .l .x{display:block}",
    ".p{position:fixed;right:20px;bottom:92px;width:380px;height:600px;",
    "max-height:calc(100vh - 124px);border-radius:16px;overflow:hidden;",
    "background:#141a21;border:1px solid #232c37;box-shadow:0 18px 50px rgba(0,0,0,.5);",
    "opacity:0;transform:translateY(10px) scale(.97);transform-origin:bottom right;",
    "visibility:hidden;pointer-events:none;",
    "transition:opacity .18s ease,transform .18s ease,visibility 0s linear .18s}",
    ".p.open{opacity:1;transform:none;visibility:visible;pointer-events:auto;",
    "transition:opacity .18s ease,transform .18s ease,visibility 0s linear 0s}",
    ".p iframe{display:block;width:100%;height:100%;border:0}",
    ":host(.pos-left) .l{right:auto;left:20px}",
    ":host(.pos-left) .p{right:auto;left:20px;transform-origin:bottom left}",
    "@media (max-width:460px){.p{left:12px;right:12px;width:auto;bottom:88px;",
    "height:calc(100vh - 108px);max-height:none;transform-origin:bottom center}",
    ":host(.pos-left) .p{left:12px;right:12px}",
    ".l{right:16px;bottom:16px}:host(.pos-left) .l{left:16px;right:auto}}",
    "@media (prefers-reduced-motion:reduce){.p,.l{transition:none}}",
    "</style>",
    '<button class="l" type="button" aria-label="Open chat">',
    '<svg class="c" width="24" height="24" viewBox="0 0 24 24" fill="none">',
    '<path d="M20.5 11.6a8.3 8.3 0 0 1-8.9 8.3 9.4 9.4 0 0 1-4.1-.9L3.5 20.3l1.2-3.9a8.1 8.1 0 0 1-1.2-4.4 8.3 8.3 0 0 1 8.5-8A8.3 8.3 0 0 1 20.5 11.6z"',
    ' stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    '<svg class="x" width="22" height="22" viewBox="0 0 22 22" fill="none">',
    '<path d="M6 6l10 10M16 6L6 16" stroke="currentColor" stroke-width="2.1" stroke-linecap="round"/></svg>',
    "</button>",
    '<div class="p" aria-hidden="true"><iframe title="Chat"></iframe></div>',
  ].join("");

  var button = root.querySelector(".l");
  var panel = root.querySelector(".p");
  var frame = root.querySelector("iframe");

  function setOpen(open) {
    if (open && !state.loaded && KEY) {
      state.loaded = true;
      var q = "/widget?key=" + encodeURIComponent(KEY) + (LANG ? "&lang=" + encodeURIComponent(LANG) : "");
      frame.src = BASE + q;
    }
    state.open = open;
    panel.classList.toggle("open", open);
    host.classList.toggle("open", open);
    panel.setAttribute("aria-hidden", String(!open));
    button.setAttribute("aria-expanded", String(open));
    button.setAttribute("aria-label", open ? "Close chat" : "Open chat");
  }

  function apply(config) {
    var b = (config && config.branding) || {};
    var bh = (config && config.behavior) || {};
    if (b.primaryColor) {
      root.host.style.setProperty("--accent", b.primaryColor);
      var hex = b.primaryColor.length === 4
        ? "#" + b.primaryColor[1] + b.primaryColor[1] + b.primaryColor[2] + b.primaryColor[2] + b.primaryColor[3] + b.primaryColor[3]
        : b.primaryColor;
      var m = /^#([0-9a-f]{6})$/i.exec(hex);
      var ink = "#131a08";
      if (m) {
        var n = parseInt(m[1], 16);
        var lum = (0.2126 * ((n >> 16) & 255) + 0.7152 * ((n >> 8) & 255) + 0.0722 * (n & 255)) / 255;
        ink = lum > 0.6 ? "#131a08" : "#ffffff";
      }
      root.host.style.setProperty("--ink", ink);
    }
    if (b.position === "left") host.classList.add("pos-left");
    if (bh.autoOpen === "always") setTimeout(function () { setOpen(true); }, 400);
    else if (bh.autoOpen === "delay") setTimeout(function () { setOpen(true); }, Math.max(1, bh.autoOpenDelay || 5) * 1000);
  }

  button.addEventListener("click", function () { setOpen(!state.open); });
  d.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && state.open) setOpen(false);
  });

  if (KEY) {
    fetch(BASE + "/api/widget/config?key=" + encodeURIComponent(KEY))
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { if (d) apply(d.config); })
      .catch(function () { /* defaults are fine */ });
  }

  w.RAGbase = {
    version: "1.0.0",
    open: function () { setOpen(true); },
    close: function () { setOpen(false); },
    toggle: function () { setOpen(!state.open); },
  };
})(window, document);
