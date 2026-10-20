// A pick-several config field: the tags under its input add themselves to
// the comma-separated list, in the order clicked, or take themselves out.
// "none" stands alone: picking it clears the rest, and taking the last one
// out leaves "none" rather than a blank, which would change nothing.
(function () {
  var NONE = "none";
  document.querySelectorAll("input[data-tag-pick]").forEach(function (input) {
    var picks = input.parentNode.querySelector(".tag-picks");
    if (!picks) return;
    var buttons = picks.querySelectorAll("button[data-tag]");

    function picked() {
      return input.value.split(",").map(function (t) { return t.trim(); })
        .filter(function (t) { return t; });
    }
    function show() {
      var on = picked();
      buttons.forEach(function (b) {
        b.setAttribute("aria-pressed", String(on.indexOf(b.dataset.tag) !== -1));
      });
    }

    buttons.forEach(function (b) {
      b.addEventListener("click", function () {
        var tag = b.dataset.tag;
        var on = picked().filter(function (t) { return t !== NONE; });
        if (tag === NONE) on = [NONE];
        else if (on.indexOf(tag) !== -1) on = on.filter(function (t) { return t !== tag; });
        else on.push(tag);
        input.value = (on.length ? on : [NONE]).join(",");
        // configform.js shows Save on an input event.
        input.dispatchEvent(new Event("input", { bubbles: true }));
      });
    });
    input.addEventListener("input", show);
    show();
  });
})();
