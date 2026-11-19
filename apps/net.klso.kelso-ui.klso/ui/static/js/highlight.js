// Manifests in `pre.app-card-manifest`, highlighted if highlight.js loaded.
(function () {
  if (window.hljs) {
    document.querySelectorAll("pre.app-card-manifest code").forEach(function (el) {
      if (el.classList.contains("language-markdown")) highlightBundle(el);
      else hljs.highlightElement(el);
    });
  }

  function escape(text) {
    return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function highlight(text, language) {
    if (!hljs.getLanguage(language)) return escape(text);
    return hljs.highlight(text, { language: language, ignoreIllegals: true }).value;
  }

  // A .klso.md whole: hljs's markdown mode leaves a fenced block as one plain
  // span, so each block is highlighted in its own fence's language instead.
  function highlightBundle(el) {
    var out = [], prose = [], body = [], language = null;
    function flush(lines, as) {
      if (lines.length) out.push(highlight(lines.join("\n"), as));
    }
    function fence(line) {
      out.push('<span class="hljs-meta">' + escape(line) + "</span>");
    }
    el.textContent.split("\n").forEach(function (line) {
      if (language === null) {
        var open = line.match(/^```(\S*)/);
        if (!open) { prose.push(line); return; }
        flush(prose, "markdown");
        prose = [];
        fence(line);
        language = open[1];
        body = [];
      } else if (/^```\s*$/.test(line)) {
        flush(body, language);
        fence(line);
        language = null;
      } else {
        body.push(line);
      }
    });
    flush(language === null ? prose : body, language === null ? "markdown" : language);
    el.innerHTML = out.join("\n");
    el.classList.add("hljs");
    el.dataset.highlighted = "yes";
  }
})();
