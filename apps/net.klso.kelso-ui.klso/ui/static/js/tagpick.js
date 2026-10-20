// A pick-several config field: its tags toggle themselves into a hidden
// comma-separated list, in the order clicked. Where the order matters
// (data-primary-desc), the first pick is marked the primary and says so on
// hover; drop it and the next one up takes its place. Deselecting every one
// leaves the field's empty value (data-empty), not a blank, which would
// change nothing.
(function () {
  document.querySelectorAll("input[data-tag-pick]").forEach(function (input) {
    var picks = input.parentNode.querySelector(".tag-picks");
    if (!picks) return;
    var empty = input.dataset.empty || "";
    var primaryDesc = input.dataset.primaryDesc || "";
    var buttons = picks.querySelectorAll("button[data-tag]");

    function picked() {
      return input.value.split(",").map(function (t) { return t.trim(); })
        .filter(function (t) { return t && t !== empty; });
    }
    function show() {
      var on = picked();
      buttons.forEach(function (b) {
        b.setAttribute("aria-pressed", String(on.indexOf(b.dataset.tag) !== -1));
        if (primaryDesc && on[0] === b.dataset.tag) {
          b.dataset.primary = "";
          b.title = primaryDesc;
        } else {
          delete b.dataset.primary;
          b.removeAttribute("title");
        }
      });
    }

    buttons.forEach(function (b) {
      b.addEventListener("click", function () {
        var tag = b.dataset.tag;
        var on = picked();
        if (on.indexOf(tag) !== -1) on = on.filter(function (t) { return t !== tag; });
        else on.push(tag);
        input.value = on.length ? on.join(",") : empty;
        show();
        // configform.js shows Save on an input event.
        input.dispatchEvent(new Event("input", { bubbles: true }));
      });
    });
  });
})();
