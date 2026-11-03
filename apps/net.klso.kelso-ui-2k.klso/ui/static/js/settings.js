// The Settings window's background preview: the id's colours or the two
// picked, as the radios say. Custom colours are only editable when chosen.
(function () {
  var preview = document.getElementById("bg-preview");
  if (!preview) return;
  var form = preview.closest("form");
  var generated = preview.getAttribute("data-generated").split(",");
  var pickers = form.querySelectorAll("input[type=color]");
  function paint() {
    var custom = form.querySelector("input[name=mode]:checked").value === "custom";
    pickers.forEach(function (p) { p.disabled = !custom; });
    var pair = custom ? [pickers[0].value, pickers[1].value] : generated;
    preview.style.background = "linear-gradient(" + pair[0] + ", " + pair[1] + ")";
  }
  form.addEventListener("input", paint);
  form.addEventListener("change", paint);
  paint();
})();
