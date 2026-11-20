// Jobs. Any `.job-open` button -- see `job_button` in macros.html -- describes
// a job in its data attributes, and opens it at /job (pages/job.html): a
// window of its own on the desktop, the whole tab outside it. There, Run posts
// the job and tails its activity log until it ends.
//
// The job is handed over in sessionStorage, never in the URL: a link to /job
// from anywhere else must not be able to name a job, least of all an autorun
// one. It is read once, so a reload or Back runs nothing.
//
// A run is named by its log. Once a job has one the window's address becomes
// /job/<log>, which is also what a button with `data-log` opens: that run's
// output, followed for as long as it is still running.
(function () {
  var desktop = window.parent !== window && window.parent.kelsoDesktop;

  function specOf(btn) {
    return {
      verb: btn.getAttribute("data-verb"),
      title: btn.getAttribute("data-title") || btn.getAttribute("data-verb"),
      desc: btn.getAttribute("data-desc") || "",
      args: JSON.parse(btn.getAttribute("data-args") || "{}"),
      fields: JSON.parse(btn.getAttribute("data-fields") || "[]"),
      choices: JSON.parse(btn.getAttribute("data-choices") || "[]"),
      // Where the page that asked goes once the job has run: by default, itself again.
      done: btn.getAttribute("data-done") || location.pathname + location.search,
      autorun: !!btn.getAttribute("data-autorun")
    };
  }

  // A run's page, under the title its button gave it.
  function viewOf(log, title) {
    return "/job/" + encodeURIComponent(log) + "?title=" + encodeURIComponent(title);
  }

  document.querySelectorAll(".job-open").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var s = specOf(btn);
      var src = viewOf(btn.getAttribute("data-log") || "", s.title);
      if (!btn.hasAttribute("data-log")) {
        var id = Math.random().toString(36).slice(2);
        sessionStorage.setItem("job:" + id, JSON.stringify(s));
        src = "/job?id=" + id;
      }
      if (desktop) desktop.job(src, s.title, window);
      else location.href = src;
    });
  });

  // The start menu opens a page at #run=<verb>, or #run=<verb>:<arg>, to press
  // one of its job buttons: the one with that verb and an argument of that value.
  // The hash goes first, so a reload runs nothing.
  function runFromHash() {
    var want = /^#run=(.+)$/.exec(location.hash);
    if (!want) return;
    history.replaceState(null, "", location.pathname + location.search);
    var parts = decodeURIComponent(want[1]).split(":");
    var btn = Array.prototype.filter.call(document.querySelectorAll(".job-open[data-verb]"), function (b) {
      if (b.getAttribute("data-verb") !== parts[0]) return false;
      var args = JSON.parse(b.getAttribute("data-args"));
      return parts.length < 2 || Object.keys(args).some(function (k) { return args[k] === parts[1]; });
    })[0];
    if (btn && !btn.disabled) btn.click();
  }
  runFromHash();
  window.addEventListener("hashchange", runFromHash);

  var page = document.querySelector(".job-page");
  if (!page) return;
  var titleEl = document.getElementById("job-title");
  var descEl = document.getElementById("job-desc");
  var fieldsEl = document.getElementById("job-fields");
  var choicesEl = document.getElementById("job-choices");
  var outEl = document.getElementById("job-out");
  var go = document.getElementById("job-go");
  var close = document.getElementById("job-close");
  var progress = document.getElementById("job-progress");
  var timer = null, spec = null, ran = false;

  function stopPoll() {
    if (timer) { clearInterval(timer); timer = null; }
    progress.hidden = true;
  }

  function hide() {
    stopPoll();
    if (desktop) desktop.close(window);
    else if (ran) location.replace(spec.done);
    else if (history.length > 1) history.back();
    else location.href = "/activity";
  }

  function showText(text) {
    outEl.hidden = false;
    outEl.textContent = text || "";
    outEl.scrollTop = outEl.scrollHeight;
  }

  function tail(log) {
    fetch("/activity/" + encodeURIComponent(log)).then(function (r) {
      return r.json().then(function (run) {
        if (!r.ok) { stopPoll(); showText(run.error); return; }
        showText(run.text + (run.state === "killed" ? "\n[kelso] this run was killed before it finished\n" : ""));
        if (run.state === "running") return;
        stopPoll();
        // A detached run neither passed nor failed; kelso stopped watching it.
        if (run.state !== "detached") outEl.classList.add(run.state === "ok" ? "ok" : "bad");
        // The page that asked is stale now: its status and last action moved.
        if (ran && desktop) desktop.refresh(window, spec.done);
      });
    }).catch(function () {});
  }

  function follow(log) {
    progress.hidden = false;
    timer = setInterval(function () { tail(log); }, 1000);
    tail(log);
  }

  // A queued job has no log until kelsod starts it.
  function awaitLog(id) {
    fetch("/jobs/" + encodeURIComponent(id)).then(function (r) {
      return r.json();
    }).then(function (job) {
      if (job.log) {
        history.replaceState(null, "", viewOf(job.log, spec.title));
        follow(job.log);
      } else if (job.state === "failed" || job.state === "done") {
        progress.hidden = true;
        showText(job.error || job.state);
        outEl.classList.add(job.state === "done" ? "ok" : "bad");
      } else {
        setTimeout(function () { awaitLog(id); }, 500);
      }
    }).catch(function () { setTimeout(function () { awaitLog(id); }, 2000); });
  }

  function chosen() {
    if (!spec.choices.length) return { verb: spec.verb, args: spec.args };
    var picked = choicesEl.querySelector("input[name=job-choice]:checked");
    return spec.choices[picked ? Number(picked.value) : 0];
  }

  // Nothing left to cancel.
  function settle() {
    go.hidden = true;
    close.textContent = "Close";
  }

  function submit() {
    var pick = chosen();
    var args = {};
    Object.keys(pick.args || {}).forEach(function (k) { args[k] = pick.args[k]; });
    fieldsEl.querySelectorAll("input").forEach(function (input) {
      if (input.value.trim()) args[input.name] = input.value;
    });
    ran = true;
    fieldsEl.querySelectorAll("input").forEach(function (i) { i.disabled = true; });
    choicesEl.querySelectorAll("input").forEach(function (i) { i.disabled = true; });
    settle();
    showText("queued…");
    progress.hidden = false;
    fetch("/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ verb: pick.verb, args: args })
    }).then(function (r) {
      return r.json().then(function (body) { return { ok: r.ok, body: body }; });
    }).then(function (res) {
      if (!res.ok) { progress.hidden = true; showText(res.body.error || "failed"); return; }
      awaitLog(res.body.id);
    }).catch(function (err) { progress.hidden = true; showText(String(err)); });
  }

  function show(s) {
    spec = s;
    titleEl.textContent = s.title;
    document.title = s.title + " · kelso";
    descEl.textContent = s.desc;
    descEl.hidden = !s.desc;
    s.fields.forEach(function (f) {
      var input = document.createElement("input");
      input.name = f.name;
      input.placeholder = f.placeholder || f.name;
      input.className = "grow";
      input.autocomplete = "off";
      fieldsEl.appendChild(input);
    });
    fieldsEl.hidden = !s.fields.length;
    s.choices.forEach(function (c, i) {
      var label = document.createElement("label");
      label.className = "job-choice";
      var radio = document.createElement("input");
      radio.type = "radio";
      radio.name = "job-choice";
      radio.value = String(i);
      radio.disabled = !!c.disabled;
      var text = document.createElement("span");
      var strong = document.createElement("b");
      strong.textContent = c.label;
      var note = document.createElement("span");
      note.className = "sub";
      note.textContent = c.desc || "";
      text.appendChild(strong);
      text.appendChild(note);
      label.appendChild(radio);
      label.appendChild(text);
      choicesEl.appendChild(label);
    });
    var first = choicesEl.querySelector("input:not(:disabled)");
    if (first) first.checked = true;
    choicesEl.hidden = !s.choices.length;
    go.disabled = s.choices.length > 0 && !first;
    if (s.autorun) {
      submit();
    } else if (s.fields.length) {
      fieldsEl.querySelector("input").focus();
    } else {
      go.focus();
    }
  }

  go.addEventListener("click", submit);
  close.addEventListener("click", hide);
  if (page.hasAttribute("data-log")) {
    settle();
    follow(page.getAttribute("data-log"));
    return;
  }
  var key = "job:" + new URLSearchParams(location.search).get("id");
  try {
    var handed = sessionStorage.getItem(key);
    sessionStorage.removeItem(key);
    show(JSON.parse(handed));
  } catch (e) {
    showText("There is no job here to run. Open it again from its button.");
    settle();
  }
})();
