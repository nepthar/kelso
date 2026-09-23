// Loaded in <head>, before first paint, so a collapsed nav or a chosen theme
// never flashes the default first. Everything else waits for shell.js.
(function () {
  var root = document.documentElement;
  // The one list of themes. "" is the stylesheet's :root; the rest are
  // html[data-theme] blocks in kelso.css.
  var themes = [
    { id: "", name: "rally" },
    { id: "mojave", name: "mojave night" },
    { id: "mojave-day", name: "mojave day" }
  ];
  window.kelso = { themes: themes };
  try {
    if (localStorage.getItem("kelso-nav") === "collapsed") root.classList.add("nav-collapsed");
    var saved = localStorage.getItem("kelso-theme");
    if (saved && themes.some(function (t) { return t.id === saved; })) root.dataset.theme = saved;
  } catch (e) {
    // Storage blocked: the defaults are fine.
  }
  // IBM Plex, if the internet is there to serve it. Added from script, not as a
  // <link> in the page, because a stylesheet in <head> holds up rendering until
  // it loads or fails -- and on a network that drops outbound traffic, failing
  // takes a TCP timeout. This way the page draws at once in the fallback faces
  // kelso.css names, and swaps to Plex if and when it arrives.
  var fonts = document.createElement("link");
  fonts.rel = "stylesheet";
  fonts.href = "https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500" +
    "&family=IBM+Plex+Sans:wght@400;500;600&display=swap";
  document.head.appendChild(fonts);
})();
