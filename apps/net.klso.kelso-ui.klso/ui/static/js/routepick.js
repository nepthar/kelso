// The routes tab: each route's providers are checkboxes in a dropdown, and its
// primary is a select over the URLs of the ones picked. Both write the hidden
// comma list the field posts, primary first. Picking none leaves the field's
// empty value (data-empty), not a blank, which would change nothing.
(function () {
  document.querySelectorAll("tr[data-route-pick]").forEach(function (row) {
    var input = row.querySelector("input[data-route-value]");
    var empty = input.dataset.empty || "";
    var boxes = row.querySelectorAll(".multi-pick input[type=checkbox]");
    var summary = row.querySelector(".multi-pick summary");
    var primary = row.querySelector("select[data-route-primary]");

    function picked() {
      return input.value.split(",").map(function (t) { return t.trim(); })
        .filter(function (t) { return t && t !== empty; });
    }
    function show() {
      var on = picked();
      boxes.forEach(function (b) { b.checked = on.indexOf(b.value) !== -1; });
      summary.textContent = on.length ? on.join(", ") : "none";
      Array.prototype.forEach.call(primary.options, function (o) {
        o.hidden = o.disabled = on.indexOf(o.value) === -1;
      });
      primary.disabled = !on.length;
      primary.value = on.length ? on[0] : "";
    }
    function write() {
      var on = Array.prototype.filter.call(boxes, function (b) { return b.checked; })
        .map(function (b) { return b.value; });
      var first = on.indexOf(primary.value) !== -1 ? primary.value : on[0];
      if (first) on = [first].concat(on.filter(function (t) { return t !== first; }));
      input.value = on.length ? on.join(",") : empty;
      show();
      // configform.js shows Save on an input event.
      input.dispatchEvent(new Event("input", { bubbles: true }));
    }

    boxes.forEach(function (b) { b.addEventListener("change", write); });
    primary.addEventListener("change", write);
    // configform.js's Cancel puts the list back and says so.
    input.addEventListener("change", show);
    show();
  });

  document.querySelectorAll("details.multi-pick").forEach(function (d) {
    d.addEventListener("toggle", function () {
      if (!d.open) return;
      var box = d.querySelector("summary").getBoundingClientRect();
      var menu = d.querySelector(".multi-pick-menu");
      menu.style.left = box.left + "px";
      menu.style.top = box.bottom + 2 + "px";
      menu.style.minWidth = box.width + "px";
    });
  });
  function closeAll(except) {
    document.querySelectorAll("details.multi-pick[open]").forEach(function (d) {
      if (d !== except) d.open = false;
    });
  }
  // One open at a time; a click elsewhere, or a scroll, which would leave it
  // behind, closes it.
  document.addEventListener("click", function (event) {
    closeAll(event.target.closest && event.target.closest("details.multi-pick"));
  });
  document.addEventListener("scroll", function () { closeAll(null); }, true);
})();
