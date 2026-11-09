// The desktop: icons, the taskbar and start menu, and windows. A window is an
// iframe of an ordinary page, or of a route, which may be on another origin
// and is then sandboxed and only ever spoken to through its element.
(function () {
  var desk = document.getElementById("desktop");
  if (!desk) return;
  var icons = document.getElementById("desk-icons");
  var layer = document.getElementById("desk-windows");
  var tasks = document.getElementById("task-list");
  var template = document.getElementById("window-tpl");
  var startButton = document.getElementById("start-button");
  var startMenu = document.getElementById("start-menu");
  var startApps = startMenu.querySelector(".start-list > .has-sub > .menu");
  var contextMenu = document.getElementById("desk-menu");
  var trayDot = document.getElementById("tray-dot");
  var trayClock = document.getElementById("tray-clock");
  var routeIcon = desk.getAttribute("data-route-icon");

  var POLL = 15000;
  var SMALL = 768;
  var CASCADE = 26;
  var windows = [];
  var layerZ = 1;
  var opened = 0;
  var touch = window.matchMedia("(pointer: coarse)").matches;

  // --- windows -----------------------------------------------------------
  function focusWindow(win) {
    windows.forEach(function (w) {
      var on = w === win;
      w.el.classList.toggle("inactive", !on);
      w.task.classList.toggle("active", on);
    });
    if (!win) return;
    win.el.style.zIndex = ++layerZ;
  }

  function frontmost() {
    return windows.filter(function (w) { return !w.el.hidden; })
      .sort(function (a, b) { return b.el.style.zIndex - a.el.style.zIndex; })[0];
  }

  function minimize(win) {
    win.el.hidden = true;
    focusWindow(frontmost());
  }

  function restore(win) {
    win.el.hidden = false;
    focusWindow(win);
  }

  function setMaximized(win, on) {
    win.el.classList.toggle("maximized", on);
    win.maxButton.setAttribute("aria-label", on ? "Restore" : "Maximize");
  }

  function windowOf(frameWindow) {
    return windows.filter(function (w) { return w.frame.contentWindow === frameWindow; })[0];
  }

  // A dialog that has done its work lands on /done: it closes, and the window
  // that opened it goes where the page's Continue link does, which the server
  // has already held to this origin.
  function finish(win) {
    var link = win.frame.contentDocument.querySelector(".done-next");
    var url = link && new URL(link.href);
    var then = url && url.origin === location.origin ? url.pathname + url.search : "/host";
    var opener = windows.indexOf(win.opener) >= 0 ? win.opener : null;
    closeWindow(win);
    if (!opener) { open({ src: then, title: "" }); return; }
    opener.frame.contentWindow.location.replace(then);
    restore(opener);
  }

  // What pages inside windows call, through shell.js.
  window.kelsoDesktop = {
    dialog: function (src, title, from) {
      open({ src: src, title: title, dialog: true, opener: windowOf(from) });
    },
    close: function (from) {
      var win = windowOf(from);
      if (win) closeWindow(win);
    }
  };

  function closeWindow(win) {
    win.el.remove();
    win.task.remove();
    windows.splice(windows.indexOf(win), 1);
    focusWindow(frontmost());
  }

  function setTitle(win, title) {
    win.title.textContent = title;
    win.task.lastChild.textContent = title;
    win.task.title = title;
  }

  function place(win) {
    if (win.opener) {
      var dw = Math.min(680, desk.clientWidth - 20);
      var dh = Math.min(440, desk.clientHeight - 20);
      var o = win.opener.el;
      win.el.style.width = dw + "px";
      win.el.style.height = dh + "px";
      win.el.style.left = Math.max(0, Math.min(o.offsetLeft + (o.offsetWidth - dw) / 2, desk.clientWidth - dw)) + "px";
      win.el.style.top = Math.max(0, Math.min(o.offsetTop + 40, desk.clientHeight - dh)) + "px";
      win.el.style.visibility = "hidden";
      return;
    }
    var w = Math.min(920, desk.clientWidth - 40);
    var h = Math.min(620, desk.clientHeight - 40);
    var step = (opened++ % 8) * CASCADE;
    win.el.style.width = w + "px";
    win.el.style.height = h + "px";
    win.el.style.left = Math.max(0, Math.min(120 + step, desk.clientWidth - w)) + "px";
    win.el.style.top = Math.max(0, Math.min(24 + step, desk.clientHeight - h)) + "px";
    if (desk.clientWidth < SMALL) setMaximized(win, true);
  }

  // A page and the same page with a notice (`?ok=`, `?err=`) are one place.
  function place_of(path) {
    return path.replace(/([?&])(ok|err)=[^&]*&?/g, "$1").replace(/[?&]$/, "");
  }

  // Each window keeps its own history. The iframe's own would be the tab's:
  // shared by every window, and stepped through by the browser's Back button.
  function syncHistory(win, path) {
    var here = win.history[win.at];
    if (here !== undefined && place_of(here) === place_of(path)) {
      win.history[win.at] = path;
    } else {
      win.history = win.history.slice(0, win.at + 1).concat(path);
      win.at = win.history.length - 1;
    }
    win.back.disabled = win.at === 0;
    win.forward.disabled = win.at === win.history.length - 1;
  }

  function step(win, by) {
    var to = win.at + by;
    if (to < 0 || to >= win.history.length) return;
    win.at = to;
    // replace(): a step through this window's history is not a new entry in the tab's.
    win.frame.contentWindow.location.replace(win.history[to]);
  }

  // A link off this server opens beside the window as a route, so the
  // window stays a kelso page that Back can return to.
  function leaveBy(win, doc) {
    doc.addEventListener("click", function (event) {
      if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey ||
          event.shiftKey || event.altKey) return;
      var link = event.target.closest("a[href]");
      if (!link) return;
      var url = new URL(link.href, doc.baseURI);
      if (url.origin === location.origin || !/^https?:$/.test(url.protocol)) return;
      event.preventDefault();
      open({ url: url.href, title: url.host, icon: routeIcon });
    });
  }

  // Follows the page inside: a form post or a link can move it, and a lost
  // session lands it on the sign-in page, which has to be the whole tab.
  function followFrame(win) {
    var inner;
    try { inner = win.frame.contentWindow.location; } catch (e) { return; }
    if (inner.pathname === "/login") { location.href = "/login"; return; }
    if (inner.href === "about:blank") return;
    if (inner.pathname === "/done") { finish(win); return; }
    var path = inner.pathname + inner.search;
    win.key = path;
    win.address.value = path;
    syncHistory(win, path);
    leaveBy(win, win.frame.contentDocument);
    var title = win.frame.contentDocument.title.replace(/ · kelso$/, "");
    if (title) setTitle(win, title);
    if (win.opener) fitHeight(win);
    // A saved form or a finished dialog may have changed what the desktop shows.
    if (/[?&]ok=/.test(path)) refreshIcons();
  }

  // A dialog is as tall as its page's content, up to the desk.
  function fitHeight(win) {
    var doc = win.frame.contentDocument;
    var main = doc.querySelector("main");
    win.el.style.visibility = "";
    if (!main) return;
    var around = doc.body.clientHeight - main.offsetHeight;
    main.style.flex = "none";
    var content = main.offsetHeight;
    main.style.flex = "";
    var chrome = win.el.offsetHeight - win.frame.clientHeight;
    var room = desk.clientHeight - win.el.offsetTop;
    win.el.style.height = Math.max(200, Math.min(room, Math.ceil(chrome + around + content))) + "px";
  }

  function dragging(handle, start, move) {
    handle.addEventListener("pointerdown", function (event) {
      if (event.button !== 0 || event.target.closest("button")) return;
      var from = start(event);
      if (!from) return;
      var x = event.clientX, y = event.clientY;
      // Capture keeps the pointer from being lost to an iframe underneath.
      handle.setPointerCapture(event.pointerId);
      function onMove(e) { move(from, e.clientX - x, e.clientY - y); }
      function onUp() {
        handle.removeEventListener("pointermove", onMove);
        handle.removeEventListener("pointerup", onUp);
      }
      handle.addEventListener("pointermove", onMove);
      handle.addEventListener("pointerup", onUp);
      event.preventDefault();
    });
  }

  function wire(win) {
    var el = win.el;
    var bar = el.querySelector(".title-bar");
    dragging(bar, function () {
      focusWindow(win);
      if (el.classList.contains("maximized")) return null;
      return { left: el.offsetLeft, top: el.offsetTop };
    }, function (from, dx, dy) {
      el.style.left = Math.min(desk.clientWidth - 60, Math.max(60 - el.offsetWidth, from.left + dx)) + "px";
      el.style.top = Math.min(desk.clientHeight - 30, Math.max(0, from.top + dy)) + "px";
    });
    dragging(el.querySelector(".win-resize"), function () {
      focusWindow(win);
      return { width: el.offsetWidth, height: el.offsetHeight };
    }, function (from, dx, dy) {
      el.style.width = Math.max(320, from.width + dx) + "px";
      el.style.height = Math.max(200, from.height + dy) + "px";
    });
    bar.addEventListener("dblclick", function (event) {
      if (!win.opener && !event.target.closest("button")) setMaximized(win, !el.classList.contains("maximized"));
    });
    el.addEventListener("pointerdown", function () { focusWindow(win); });
    el.querySelector('[aria-label="Minimize"]').addEventListener("click", function () { minimize(win); });
    win.maxButton.addEventListener("click", function () {
      setMaximized(win, !el.classList.contains("maximized"));
    });
    el.querySelector('[aria-label="Close"]').addEventListener("click", function () { closeWindow(win); });
    win.task.addEventListener("click", function () {
      if (el.hidden) restore(win);
      else if (win.task.classList.contains("active")) minimize(win);
      else focusWindow(win);
    });
  }

  function open(spec) {
    var key = spec.url || spec.src;
    var found = windows.filter(function (w) { return w.key === key; })[0];
    if (found) { restore(found); return; }
    // A browser will not show http inside https, so the tab is all there is.
    if (spec.url && spec.url.indexOf("http:") === 0 && location.protocol === "https:") {
      window.open(spec.url, "_blank", "noopener");
      return;
    }
    var el = template.content.firstElementChild.cloneNode(true);
    var win = {
      key: key, el: el,
      title: el.querySelector(".win-title"),
      frame: el.querySelector(".win-frame"),
      address: el.querySelector(".win-address"),
      out: el.querySelector(".win-out"),
      maxButton: el.querySelector('[aria-label="Maximize"]'),
      back: el.querySelector(".win-back"),
      forward: el.querySelector(".win-forward"),
      history: [],
      at: -1,
      opener: spec.dialog ? spec.opener || null : null,
      task: document.createElement("button")
    };
    var icon = el.querySelector(".win-icon");
    if (spec.icon) icon.src = spec.icon;
    else if (win.opener) icon.src = win.opener.el.querySelector(".win-icon").src;
    if (spec.dialog) el.classList.add("is-dialog");
    win.task.type = "button";
    win.task.className = "task";
    var taskIcon = document.createElement("img");
    taskIcon.alt = "";
    taskIcon.src = icon.src;
    win.task.appendChild(taskIcon);
    win.task.appendChild(document.createElement("span"));
    setTitle(win, spec.title || key);
    win.address.value = key;
    // Only a route has a tab of its own to open in; a kelso page is a window.
    win.out.hidden = !spec.url;
    if (spec.url) {
      win.out.href = key;
      el.querySelector(".win-nav").hidden = true;
      el.querySelector(".win-status").hidden = false;
      // Forms and logins work; the app cannot take over the tab around it.
      win.frame.sandbox = "allow-scripts allow-same-origin allow-forms allow-popups allow-downloads allow-modals";
    } else {
      win.back.addEventListener("click", function () { step(win, -1); });
      win.forward.addEventListener("click", function () { step(win, 1); });
      win.frame.addEventListener("load", function () { followFrame(win); });
    }
    el.querySelector(".win-refresh").addEventListener("click", function () {
      if (spec.url) win.frame.src = win.frame.src;
      else win.frame.contentWindow.location.reload();
    });
    win.frame.src = key;
    layer.appendChild(el);
    // A dialog belongs to its window and has no button of its own.
    if (!spec.dialog) tasks.appendChild(win.task);
    windows.push(win);
    place(win);
    wire(win);
    focusWindow(win);
  }

  // A click inside an iframe never reaches this document, but it does take
  // focus away from it.
  window.addEventListener("blur", function () {
    setTimeout(function () {
      closeMenus();
      var active = document.activeElement;
      if (!active || active.tagName !== "IFRAME") return;
      var win = windows.filter(function (w) { return w.frame === active; })[0];
      if (win) focusWindow(win);
    }, 0);
  });

  // --- icons -------------------------------------------------------------
  function openIcon(icon) {
    closeMenus();
    open({ src: icon.dataset.src, url: icon.dataset.url, title: icon.dataset.title, icon: icon.dataset.icon });
  }

  function select(icon) {
    icons.querySelectorAll(".selected").forEach(function (i) { i.classList.remove("selected"); });
    if (icon) icon.classList.add("selected");
  }

  // `detail` is 0 for Enter or Space, so the keyboard opens on one press.
  icons.addEventListener("click", function (event) {
    var icon = event.target.closest(".desk-icon");
    if (!icon) return;
    if (touch || event.detail === 0) openIcon(icon);
    else select(icon);
  });
  icons.addEventListener("dblclick", function (event) {
    var icon = event.target.closest(".desk-icon");
    if (icon) openIcon(icon);
  });
  desk.addEventListener("click", function (event) {
    if (!event.target.closest(".desk-icon")) select(null);
  });

  function menuItem(label, action) {
    var item = document.createElement("button");
    item.type = "button";
    item.setAttribute("role", "menuitem");
    item.textContent = label;
    item.addEventListener("click", function () { closeMenus(); action(); });
    contextMenu.appendChild(item);
  }

  icons.addEventListener("contextmenu", function (event) {
    var icon = event.target.closest(".desk-icon");
    if (!icon) return;
    event.preventDefault();
    select(icon);
    var data = icon.dataset;
    contextMenu.textContent = "";
    menuItem("Open", function () { openIcon(icon); });
    if (data.url) menuItem("Open in new tab", function () { window.open(data.url, "_blank", "noopener"); });
    contextMenu.hidden = false;
    contextMenu.style.left = Math.min(event.clientX, window.innerWidth - contextMenu.offsetWidth) + "px";
    contextMenu.style.top = Math.min(event.clientY, window.innerHeight - contextMenu.offsetHeight) + "px";
  });

  // Icons are server-rendered, so following the apps is reading them again.
  var lastIcons = icons.innerHTML;
  var refreshing = false;
  function refreshIcons() {
    if (refreshing) return;
    refreshing = true;
    fetch("/desktop/icons").then(function (r) {
      if (r.status === 401) { location.href = "/login"; return ""; }
      trayDot.classList.toggle("bad", !r.ok);
      return r.ok ? r.text() : "";
    }).then(function (html) {
      if (!html || html === lastIcons) return;
      var picked = icons.querySelector(".selected");
      var title = picked && picked.dataset.title;
      icons.innerHTML = lastIcons = html;
      paint();
      if (title) select(icons.querySelector('[data-title="' + CSS.escape(title) + '"]'));
    }).catch(function () {
      trayDot.classList.add("bad");
    }).then(function () { refreshing = false; });
  }
  setInterval(refreshIcons, POLL);

  function paint() {
    window.kelso.paintDesktop(JSON.parse(icons.querySelector(".desk-colors").textContent));
  }

  // --- start menu --------------------------------------------------------
  function setOpen(li, on) {
    li.classList.toggle("open", on);
    li.firstElementChild.setAttribute("aria-expanded", String(on));
    if (on) { lift(li.lastElementChild); return; }
    li.querySelectorAll(".has-sub.open").forEach(function (o) { setOpen(o, false); });
  }

  // A folder's menu hangs from its row, and rides up if it would go under the taskbar.
  function lift(menu) {
    menu.style.top = "";
    if (getComputedStyle(menu).position === "static") return;
    var over = menu.getBoundingClientRect().bottom - startMenu.getBoundingClientRect().bottom;
    if (over > 0) menu.style.top = Math.max(-menu.parentElement.getBoundingClientRect().top, parseFloat(getComputedStyle(menu).top) - over) + "px";
  }

  function toggle(li) {
    li.parentElement.querySelectorAll(":scope > .has-sub.open").forEach(function (o) {
      if (o !== li) setOpen(o, false);
    });
    setOpen(li, !li.classList.contains("open"));
  }

  function runIn(src, run) {
    var found = windows.filter(function (w) { return w.key === src; })[0];
    if (!found) { open({ src: src + "#run=" + encodeURIComponent(run), title: "" }); return; }
    restore(found);
    found.frame.contentWindow.location.hash = "run=" + encodeURIComponent(run);
  }

  function showStart(on) {
    startMenu.querySelectorAll(".has-sub.open").forEach(function (o) { setOpen(o, false); });
    startMenu.hidden = !on;
    startButton.setAttribute("aria-expanded", String(on));
    startButton.classList.toggle("active", on);
    if (!on) return;
    startApps.textContent = "";
    startApps.appendChild(icons.querySelector(".start-apps-tpl").content.cloneNode(true));
  }

  function closeMenus() {
    contextMenu.hidden = true;
    showStart(false);
  }

  startButton.addEventListener("click", function () { showStart(startMenu.hidden); });
  startMenu.addEventListener("click", function (event) {
    var item = event.target.closest(".menu-item");
    if (!item || item.disabled || item.type === "submit") return;
    var li = item.parentElement;
    if (li.classList.contains("has-sub")) { toggle(li); return; }
    if (item.dataset.run) { closeMenus(); runIn(item.dataset.src, item.dataset.run); }
    else openIcon(item);
  });
  document.addEventListener("click", function (event) {
    if (!event.target.closest("#start-menu, #start-button, #desk-menu")) closeMenus();
  });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") closeMenus();
  });

  // --- tray --------------------------------------------------------------
  function tick() {
    trayClock.textContent = new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  }
  tick();
  setInterval(tick, 10000);

  var balloon = document.getElementById("balloon");
  if (balloon) {
    balloon.querySelector(".balloon-close").addEventListener("click", function () { balloon.remove(); });
  }
})();
