// The Repos page's manifest editor. Edit swaps a card's manifest for a
// textarea; Save posts it to /catalog/edit, and kelsod checks and commits it.
// A refused edit stays in the textarea; a saved one shows its diff.
(function () {
  document.querySelectorAll(".app-card").forEach(function (card) {
    var open = card.querySelector(".edit-open");
    var form = card.querySelector(".app-card-edit");
    if (!open || !form) return;
    var text = form.querySelector("textarea");
    var original = text.value;
    var errorBox = form.querySelector(".error");
    var save = form.querySelector(".edit-save");
    var saved = card.querySelector(".app-card-saved");
    var app = form.getAttribute("data-app");

    function leave(ok) {
      window.location = "/catalog?app=" + encodeURIComponent(app) +
        "&ok=" + encodeURIComponent(ok);
    }
    function fail(message) {
      errorBox.querySelector("p").textContent = message;
      errorBox.hidden = false;
    }

    open.addEventListener("click", function () {
      card.classList.add("editing");
      form.hidden = false;
      text.focus();
    });
    form.querySelector(".edit-cancel").addEventListener("click", function () {
      text.value = original;
      errorBox.hidden = true;
      form.hidden = true;
      card.classList.remove("editing");
    });
    saved.querySelector(".edit-done").addEventListener("click", function () {
      leave("Saved " + app);
    });

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      save.disabled = true;
      errorBox.hidden = true;
      fetch("/catalog/edit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          target: form.getAttribute("data-target"),
          base: form.getAttribute("data-base"),
          text: text.value,
          message: form.elements.message.value
        })
      })
        .then(function (response) {
          return response.json().then(function (body) {
            if (!response.ok) throw new Error(body.error || "Saving failed");
            return body;
          });
        })
        .then(function (body) {
          if (!body.diff) { leave("No changes to " + app); return; }
          var code = saved.querySelector("code");
          code.textContent = body.diff;
          // catalog.js highlighted this block, empty, on load; hljs skips it
          // again unless told it is new.
          delete code.dataset.highlighted;
          if (window.hljs) hljs.highlightElement(code);
          saved.querySelector(".saved-note").textContent =
            "Committed " + body.commit.slice(0, 8);
          form.hidden = true;
          saved.hidden = false;
        })
        .catch(function (error) { fail(error.message); })
        .finally(function () { save.disabled = false; });
    });
  });
})();
