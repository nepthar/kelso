// The job modal (modals.html). Any `.job-open` button fills it from its data
// attributes -- see `job_button` in macros.html -- then Run posts the job and
// tails its activity log until it ends. A button with `data-log` instead
// opens a finished run's output, read-only.
(function () {
  var shade = document.getElementById("job-shade");
  if (!shade) return;
  var titleEl = document.getElementById("job-title");
  var descEl = document.getElementById("job-desc");
  var fieldsEl = document.getElementById("job-fields");
  var choicesEl = document.getElementById("job-choices");
  var outEl = document.getElementById("job-out");
  var bar = document.getElementById("job-bar");
  var go = document.getElementById("job-go");
  var close = document.getElementById("job-close");
  var dismiss = document.getElementById("job-dismiss");
  var timer = null, jobId = null, verb = null, fixed = {}, ran = false, done = null;
  var choices = [];

  function stopPoll() { if (timer) { clearInterval(timer); timer = null; } }

  function hide() {
    stopPoll();
    shade.hidden = true;
    // The page behind is stale once a job has run: its status, config and
    // last-action all moved.
    if (ran) {
      window.location.href = done || window.location.href;
    }
  }

  function showText(text) {
    outEl.hidden = false;
    outEl.textContent = text || "";
    outEl.scrollTop = outEl.scrollHeight;
  }

  function pullLog(log) {
    if (!log) return Promise.resolve();
    return fetch("/activity/" + encodeURIComponent(log)).then(function (r) {
      if (!r.ok) return;
      return r.json().then(function (body) {
        if (body && body.text != null) showText(body.text);
      });
    }).catch(function () {});
  }

  function poll() {
    if (!jobId) return;
    fetch("/jobs/" + encodeURIComponent(jobId)).then(function (r) {
      return r.json();
    }).then(function (job) {
      var done = job.state === "done" || job.state === "failed";
      return pullLog(job.log).then(function () {
        if (!done) return;
        stopPoll();
        if (!job.log && job.error) showText(job.error);
        outEl.classList.add(job.state === "done" ? "ok" : "bad");
      });
    }).catch(function () {});
  }

  function chosen() {
    if (!choices.length) return { verb: verb, args: fixed };
    var picked = choicesEl.querySelector("input[name=job-choice]:checked");
    return choices[picked ? Number(picked.value) : 0];
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
    // Nothing left to cancel, so the bar goes and Close moves up beside the
    // title, clear of the output.
    bar.hidden = true;
    dismiss.hidden = false;
    showText("queued\u2026");
    fetch("/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ verb: pick.verb, args: args })
    }).then(function (r) {
      return r.json().then(function (body) { return { ok: r.ok, body: body }; });
    }).then(function (res) {
      if (!res.ok) { showText(res.body.error || "failed"); return; }
      jobId = res.body.id;
      timer = setInterval(poll, 1000);
      poll();
    }).catch(function (err) { showText(String(err)); });
  }

  document.querySelectorAll(".job-open").forEach(function (btn) {
    btn.addEventListener("click", function () {
      stopPoll();
      jobId = null; ran = false;
      var log = btn.getAttribute("data-log");
      verb = btn.getAttribute("data-verb");
      fixed = JSON.parse(btn.getAttribute("data-args") || "{}");
      done = btn.getAttribute("data-done");
      titleEl.textContent = btn.getAttribute("data-title") || verb;
      descEl.textContent = btn.getAttribute("data-desc") || "";
      descEl.hidden = !descEl.textContent;
      fieldsEl.innerHTML = "";
      (JSON.parse(btn.getAttribute("data-fields") || "[]")).forEach(function (f) {
        var input = document.createElement("input");
        input.name = f.name;
        input.placeholder = f.placeholder || f.name;
        input.className = "grow";
        input.autocomplete = "off";
        fieldsEl.appendChild(input);
      });
      fieldsEl.hidden = fieldsEl.children.length === 0;
      choices = JSON.parse(btn.getAttribute("data-choices") || "[]");
      choicesEl.innerHTML = "";
      choices.forEach(function (c, i) {
        var label = document.createElement("label");
        label.className = "job-choice";
        var radio = document.createElement("input");
        radio.type = "radio";
        radio.name = "job-choice";
        radio.value = String(i);
        if (i === 0) radio.checked = true;
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
      choicesEl.hidden = choices.length === 0;
      outEl.textContent = "";
      outEl.classList.remove("ok", "bad");
      outEl.hidden = true;
      bar.hidden = false;
      go.hidden = false;
      go.disabled = false;
      dismiss.hidden = true;
      shade.hidden = false;
      if (log) {
        bar.hidden = true;
        dismiss.hidden = false;
        outEl.classList.add(btn.getAttribute("data-status") === "ok" ? "ok" : "bad");
        pullLog(log);
        return;
      }
      if (btn.getAttribute("data-autorun")) {
        submit();
      } else if (fieldsEl.children.length) {
        fieldsEl.querySelector("input").focus();
      } else {
        go.focus();
      }
    });
  });

  go.addEventListener("click", submit);
  close.addEventListener("click", hide);
  dismiss.addEventListener("click", hide);
  shade.addEventListener("click", function (event) {
    if (event.target === shade) hide();
  });
})();
