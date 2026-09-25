// An app's console: xterm.js on a websocket to kelsod's PTY, by way of this
// server. Binary frames carry the terminal both ways; a resize goes up as JSON
// text. How the session ended arrives as the close code and reason.
(function () {
  var box = document.getElementById("console");
  if (!box) return;
  var status = document.getElementById("console-status");
  var again = document.getElementById("console-reconnect");
  var url = (location.protocol === "https:" ? "wss://" : "ws://") + location.host +
    box.getAttribute("data-src");
  var IDLE = 4000;

  var ANSI = ["black", "red", "green", "yellow", "blue", "magenta", "cyan", "white",
    "brightBlack", "brightRed", "brightGreen", "brightYellow", "brightBlue",
    "brightMagenta", "brightCyan", "brightWhite"];
  function theme() {
    var c = window.kelso.color;
    var t = {
      background: c("--term-bg"),
      foreground: c("--term-fg"),
      cursor: c("--term-cursor"),
      cursorAccent: c("--term-bg"),
      selectionBackground: c("--term-selection")
    };
    ANSI.forEach(function (name, i) { t[name] = c("--ansi-" + i); });
    return t;
  }

  var style = getComputedStyle(box);
  var term = new Terminal({
    theme: theme(),
    fontFamily: style.fontFamily,
    fontSize: parseFloat(style.fontSize),
    scrollback: 5000,
    cursorBlink: true
  });
  var fit = new FitAddon.FitAddon();
  term.loadAddon(fit);
  term.open(box);
  fit.fit();

  document.addEventListener("kelso:themechange", function () {
    term.options.theme = theme();
  });
  // Plex arrives after the terminal has measured its cells in a fallback face.
  // Changing the family is what makes xterm measure again.
  if (document.fonts) {
    document.fonts.addEventListener("loadingdone", function () {
      var family = term.options.fontFamily;
      term.options.fontFamily = "monospace";
      term.options.fontFamily = family;
      fit.fit();
    });
  }
  new ResizeObserver(function () { fit.fit(); }).observe(box);

  var ws = null;
  var encoder = new TextEncoder();
  function live() { return ws && ws.readyState === WebSocket.OPEN; }
  function sendSize() {
    if (live()) ws.send(JSON.stringify({ resize: [term.cols, term.rows] }));
  }
  term.onResize(sendSize);
  term.onData(function (data) { if (live()) ws.send(encoder.encode(data)); });
  term.onBinary(function (data) {
    if (live()) ws.send(Uint8Array.from(data, function (ch) { return ch.charCodeAt(0); }));
  });

  function connect() {
    again.hidden = true;
    status.textContent = "connecting";
    ws = new WebSocket(url);
    ws.binaryType = "arraybuffer";
    ws.onopen = function () {
      status.textContent = "connected";
      sendSize();
      term.focus();
    };
    ws.onmessage = function (event) {
      if (typeof event.data !== "string") term.write(new Uint8Array(event.data));
    };
    ws.onclose = function (event) {
      again.hidden = false;
      if (event.code === IDLE) status.textContent = "closed: idle";
      else status.textContent = event.reason || "disconnected";
    };
  }

  again.addEventListener("click", function () {
    term.write("\r\n");
    connect();
  });
  connect();
})();
