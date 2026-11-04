/* RAGbase — a ~110-line syntax highlighter, no dependencies.
 *
 * Why not highlight.js from a CDN: the only code this app ever renders is the
 * four snippets in API_SNIPPETS / EMBED_SNIPPETS, all generated a few lines
 * away in app.js. Shipping 1 MB and a network round-trip to colour our own
 * fixed strings would be absurd — and the app is meant to run air-gapped.
 *
 * Contract: highlight(text, lang) -> HTML string. Plain text between tokens is
 * escaped, and token bodies are escaped too, so the result is safe to assign to
 * innerHTML. That matters because the snippet embeds a user-supplied API key.
 *
 * Token classes match the CSS in styles.css (.tok-com/.tok-str/...), which
 * lives on a One-Dark-ish palette tuned to the lime accent.
 */
(function (global) {
  "use strict";

  var ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;" };

  function esc(s) {
    return s.replace(/[&<>]/g, function (c) {
      return ESC[c];
    });
  }

  /* Rules are tried in order at each position; first match wins. That ordering
     is the grammar: strings before keywords means "def" inside a string stays
     a string, comments before flags means "# -N keeps curl..." is a comment. */
  var RULES = {
    bash: [
      ["com", "#[^\\n]*"],
      ["str", '"(?:[^"\\\\\\n]|\\\\.)*"|\'(?:[^\'\\\\\\n]|\\\\.)*\''],
      ["url", 'https?:\\/\\/[^\\s"\'\\\\]+'],
      ["flag", "-{1,2}[A-Za-z][\\w-]*"],
      ["fn", "\\b(?:curl|wget|echo|export|cd|head|jq)\\b"],
      ["kw", "\\b(?:GET|POST|PUT|PATCH|DELETE)\\b"],
      ["num", "\\b\\d+(?:\\.\\d+)?\\b"],
    ],
    python: [
      ["com", "#[^\\n]*"],
      [
        "str",
        '(?:[rRbBuU]{0,2})(?:"""[\\s\\S]*?"""|\'\'\'[\\s\\S]*?\'\'\'|"(?:[^"\\\\\\n]|\\\\.)*"|\'(?:[^\'\\\\\\n]|\\\\.)*\')',
      ],
      [
        "kw",
        "\\b(?:and|as|assert|async|await|break|class|continue|def|del|elif|else|except|False|finally|for|from|global|if|import|in|is|lambda|None|nonlocal|not|or|pass|raise|return|True|try|while|with|yield)\\b",
      ],
      [
        "bui",
        "\\b(?:print|open|len|round|range|repr|str|int|float|bool|dict|list|set|tuple|isinstance|enumerate|zip|sorted|sum|min|max|type|super|getattr|Exception|ValueError|KeyError)\\b",
      ],
      ["num", "\\b\\d+(?:\\.\\d+)?\\b"],
      ["fn", "\\b[A-Za-z_]\\w*(?=\\s*\\()"],
      ["prop", "\\b[A-Za-z_]\\w*(?=\\s*=(?!=))"],
    ],
    js: [
      ["com", "\\/\\/[^\\n]*|\\/\\*[\\s\\S]*?\\*\\/"],
      ["str", '"(?:[^"\\\\\\n]|\\\\.)*"|\'(?:[^\'\\\\\\n]|\\\\.)*\'|`(?:[^`\\\\]|\\\\.)*`'],
      [
        "kw",
        "\\b(?:async|await|break|case|catch|class|const|continue|default|delete|do|else|export|extends|false|finally|for|from|function|if|import|in|instanceof|let|new|null|of|return|static|super|switch|this|throw|true|try|typeof|undefined|var|void|while|yield)\\b",
      ],
      [
        "bui",
        "\\b(?:console|process|JSON|Math|Object|Array|String|Number|Boolean|Promise|Map|Set|fetch|require|module|setTimeout|TextDecoderStream|Buffer|window|document)\\b",
      ],
      ["num", "\\b\\d+(?:\\.\\d+)?\\b"],
      ["fn", "\\b[A-Za-z_$][\\w$]*(?=\\s*\\()"],
      ["prop", "\\b[A-Za-z_$][\\w$]*(?=\\s*:(?!:))"],
    ],
    html: [
      ["com", "<!--[\\s\\S]*?-->"],
      ["tag", "<\\/?[a-zA-Z][\\w:-]*"],
      ["attr", "\\b[a-zA-Z_:][\\w:.-]*(?=\\s*=)"],
      ["str", '"[^"]*"|\'[^\']*\''],
      ["punc", "\\/?>"],
    ],
  };

  var ALIAS = { sh: "bash", shell: "bash", curl: "bash", py: "python", js: "js", javascript: "js", node: "js", html: "html", xml: "html" };

  var compiled = Object.create(null);

  function grammar(lang) {
    var name = ALIAS[lang] || lang;
    if (!RULES[name]) return null;
    if (!compiled[name]) {
      compiled[name] = RULES[name].map(function (r) {
        return { cls: r[0], re: new RegExp(r[1], "y") }; // sticky: match exactly here
      });
    }
    return compiled[name];
  }

  function highlight(text, lang) {
    var rules = grammar(lang);
    if (!rules) return esc(text);

    var out = "";
    var plain = "";
    var i = 0;
    while (i < text.length) {
      var hit = null;
      var cls = null;
      for (var r = 0; r < rules.length; r++) {
        var re = rules[r].re;
        re.lastIndex = i;
        var m = re.exec(text);
        if (m) {
          hit = m[0];
          cls = rules[r].cls;
          break;
        }
      }
      if (hit === null) {
        plain += text[i];
        i += 1;
        continue;
      }
      if (plain) {
        out += esc(plain);
        plain = "";
      }
      out += '<span class="tok-' + cls + '">' + esc(hit) + "</span>";
      i += hit.length;
    }
    if (plain) out += esc(plain);
    return out;
  }

  global.RAGhl = { highlight: highlight, langs: Object.keys(RULES) };
})(window);
