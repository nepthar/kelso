// Loaded in <head>, before first paint, so a chosen theme never flashes the
// default first. Everything else waits for shell.js.
(function () {
  var root = document.documentElement;
  // The registered theme ids (themes.py), from the data block just above.
  // The first is the default.
  var ids = JSON.parse(document.getElementById("kelso-themes").textContent);
  window.kelso = { themes: ids };
  var theme = ids[0];
  try {
    var saved = localStorage.getItem("kelso-theme");
    // A theme since removed from the registry falls back to the default.
    if (ids.indexOf(saved) >= 0) theme = saved;
  } catch (e) {
    // Storage blocked: the defaults are fine.
  }
  root.dataset.theme = theme;
  var colors = JSON.parse(document.getElementById("kelso-desktop").textContent);
  root.style.setProperty("--desk-top", colors[0]);
  root.style.setProperty("--desk-bottom", colors[1]);
  // A page outside a desktop window has no title bar to name it.
  if (window.top === window) root.classList.add("standalone");
})();
