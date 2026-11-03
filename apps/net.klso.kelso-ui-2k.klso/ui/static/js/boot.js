// Loaded in <head>, before first paint, so the desktop's colours never flash
// the default first. Everything else waits for shell.js.
(function () {
  var root = document.documentElement;
  // Also what desktop.js calls when the colours change.
  function paintDesktop(colors) {
    root.style.setProperty("--desk-top", colors[0]);
    root.style.setProperty("--desk-bottom", colors[1]);
  }
  window.kelso = { paintDesktop: paintDesktop };
  paintDesktop(JSON.parse(document.getElementById("kelso-desktop").textContent));
  // A page outside a desktop window has no title bar to name it.
  if (window.top === window) root.classList.add("standalone");
})();
