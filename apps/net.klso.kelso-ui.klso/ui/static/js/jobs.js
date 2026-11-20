// Jobs. Any `.job-open` button -- see `job_button` in macros.html -- describes
// a job in its data attributes. On the desktop it opens a window of its own
// (/job, pages/job.html); a page opened outside the desktop shows it in the
// job modal (modals.html) instead. Either way Run posts the job and tails its
// activity log until it ends. A button with `data-log` instead opens a
// finished run's output, read-only.
(function () {
  var shade = document.getElementById("job-shade");
  var page = document.querySelector(".job-page");
  if (!shade && !page) return;
  var desktop = window.parent !== window && window.parent.kelsoDesktop;
  var titleEl = document.getElementById("job-title");
  var descEl = document.getElementById("job-desc");
  var fieldsEl = document.getElementById("job-fields");
  var choicesEl = document.getElementById("job-choices");
  var outEl = document.getElementById("job-out");
  var bar = document.getElementById("job-bar");
  var go = document.getElementById("job-go");
  var close = document.getElementById("job-close");
  var dismiss = document.getElementById("job-dismiss");
  var progress = document.getElementById("job-progress");
  var modal = shade && shade.querySelector(".job-modal");
  var timer = null, jobId = null, spec = null, ran = false;
  var choices = [];

  function stopPoll() {
    if (timer) { clearInterval(timer); timer = null; }
    progress.hidden = true;
  }

  // The page that asked for a job is stale once it has run: its status,
  // config and last-action all moved.
  function refreshOpener() {
    if (page && desktop) desktop.refresh(window, spec.done);
  }

  function hide() {
    stopPoll();
    if (page) {
      if (desktop) desktop.close(window);
      else history.back();
      return;
    }
    shade.hidden = true;
    if (ran) window.location.href = spec.done || window.location.href;
  }

  // Centred, a dialog dragged by its corner would grow from both sides and
  // leave the pointer behind: pin it where it is before the drag starts.
  if (modal) modal.addEventListener("pointerdown", function () {
    if (outEl.hidden || modal.style.position) return;
    var at = modal.getBoundingClientRect();
    modal.style.position = "fixed";
    modal.style.left = at.left + "px";
    modal.style.top = at.top + "px";
  });

  function showText(text) {
    outEl.hidden = false;
    outEl.textContent = text || "";
    outEl.scrollTop = outEl.scrollHeight;
  }

  function pullLog(log) {
    if (!log) return Promise.resolve();
    return fetch("/activity/" + encodeURIComponent(log)).then(function (r) {
      return r.json().then(function (body) {
        if (r.ok && body && body.text != null) showText(body.text);
        else if (!r.ok && !outEl.textContent) showText((body && body.error) || "Could not read this run's output.");
      });
    }).catch(function () {
      if (!outEl.textContent) showText("Could not read this run's output.");
    });
  }

  function poll() {
    if (!jobId) return;
    fetch("/jobs/" + encodeURIComponent(jobId)).then(function (r) {
      return r.json();
    }).then(function (job) {
      var over = job.state === "done" || job.state === "failed";
      return pullLog(job.log).then(function () {
        if (!over) return;
        stopPoll();
        if (!job.log && job.error) showText(job.error);
        outEl.classList.add(job.state === "done" ? "ok" : "bad");
        refreshOpener();
      });
    }).catch(function () {});
  }

  function chosen() {
    if (!choices.length) return { verb: spec.verb, args: spec.args };
    var picked = choicesEl.querySelector("input[name=job-choice]:checked");
    return choices[picked ? Number(picked.value) : 0];
  }

  // Nothing left to cancel: in the modal the bar goes and Close moves up
  // beside the title, clear of the output; in a window the bar keeps Close.
  function settle() {
    if (page) {
      go.hidden = true;
      close.textContent = "Close";
    } else {
      bar.hidden = true;
      dismiss.hidden = false;
    }
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
      jobId = res.body.id;
      timer = setInterval(poll, 1000);
      poll();
    }).catch(function (err) { progress.hidden = true; showText(String(err)); });
  }

  function specOf(btn) {
    return {
      verb: btn.getAttribute("data-verb"),
      title: btn.getAttribute("data-title") || btn.getAttribute("data-verb"),
      desc: btn.getAttribute("data-desc") || "",
      args: JSON.parse(btn.getAttribute("data-args") || "{}"),
      fields: JSON.parse(btn.getAttribute("data-fields") || "[]"),
      choices: JSON.parse(btn.getAttribute("data-choices") || "[]"),
      done: btn.getAttribute("data-done") || "",
      autorun: !!btn.getAttribute("data-autorun"),
      log: btn.getAttribute("data-log") || "",
      status: btn.getAttribute("data-status") || ""
    };
  }

  function show(s) {
    stopPoll();
    spec = s;
    jobId = null; ran = false;
    if (titleEl) titleEl.textContent = s.title;
    if (page) document.title = s.title + " · kelso";
    descEl.textContent = s.desc;
    descEl.hidden = !descEl.textContent;
    fieldsEl.innerHTML = "";
    s.fields.forEach(function (f) {
      var input = document.createElement("input");
      input.name = f.name;
      input.placeholder = f.placeholder || f.name;
      input.className = "grow";
      input.autocomplete = "off";
      fieldsEl.appendChild(input);
    });
    fieldsEl.hidden = fieldsEl.children.length === 0;
    choices = s.choices;
    choicesEl.innerHTML = "";
    choices.forEach(function (c, i) {
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
    var blocked = choices.length > 0 && !first;
    choicesEl.hidden = choices.length === 0;
    outEl.textContent = "";
    outEl.classList.remove("ok", "bad");
    outEl.hidden = true;
    if (modal) modal.removeAttribute("style");
    bar.hidden = false;
    go.hidden = false;
    go.disabled = blocked;
    close.textContent = "Cancel";
    if (dismiss) dismiss.hidden = true;
    if (shade) shade.hidden = false;
    if (s.log) {
      settle();
      // A detached run neither passed nor failed; kelso stopped watching it.
      if (s.status !== "detached") outEl.classList.add(s.status === "ok" ? "ok" : "bad");
      pullLog(s.log);
      return;
    }
    if (s.autorun) {
      submit();
    } else if (fieldsEl.children.length) {
      fieldsEl.querySelector("input").focus();
    } else {
      go.focus();
    }
  }

  function openJob(btn) {
    var s = specOf(btn);
    if (!desktop) { show(s); return; }
    // The window refreshes this page when its job ends, or goes where `done` says.
    s.done = s.done || location.pathname + location.search;
    desktop.job("/job?spec=" + encodeURIComponent(JSON.stringify(s)), s.title, window);
  }

  if (page) {
    try {
      show(JSON.parse(new URLSearchParams(location.search).get("spec")));
    } catch (e) {
      showText("This window was opened without a job to run.");
      settle();
    }
  }

  document.querySelectorAll(".job-open").forEach(function (btn) {
    btn.addEventListener("click", function () { openJob(btn); });
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

  go.addEventListener("click", submit);
  close.addEventListener("click", hide);
  if (dismiss) dismiss.addEventListener("click", hide);
  if (shade) shade.addEventListener("click", function (event) {
    if (event.target === shade) hide();
  });
})();
