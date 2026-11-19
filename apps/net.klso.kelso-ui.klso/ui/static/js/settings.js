// The Settings window. Picking a theme previews it here; the background
// preview shows the id's colors or the two picked, as the radios say, and
// custom colors are only editable when chosen.
(function () {
  var theme = document.getElementById("theme");
  theme.addEventListener("change", function () {
    window.kelso.setTheme(theme.selectedOptions[0].getAttribute("data-href"));
  });

  var preview = document.getElementById("bg-preview");
  var form = preview.closest("form");
  var generated = preview.getAttribute("data-generated").split(",");
  var pickers = form.querySelectorAll("input[type=color]");
  function paint() {
    var custom = form.querySelector("input[name=mode]:checked").value === "custom";
    pickers.forEach(function (p) { p.disabled = !custom; });
    var pair = custom ? [pickers[0].value, pickers[1].value] : generated;
    preview.style.setProperty("--desk-top", pair[0]);
    preview.style.setProperty("--desk-bottom", pair[1]);
  }
  form.addEventListener("input", paint);
  form.addEventListener("change", paint);
  paint();
})();
