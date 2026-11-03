// The gate on any form carrying `data-confirm`: its text in the #ask-shade
// dialog (modals.html), and the form submits only on "Yes, continue".
(function () {
  var shade = document.getElementById("ask-shade");
  if (!shade) return;
  var textEl = document.getElementById("ask-text");
  var go = document.getElementById("ask-go");
  var close = document.getElementById("ask-close");
  var pending = null;

  function hide() { shade.hidden = true; pending = null; }

  document.querySelectorAll("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      if (form.dataset.confirmed) return;
      event.preventDefault();
      pending = form;
      textEl.textContent = form.getAttribute("data-confirm");
      shade.hidden = false;
      go.focus();
    });
  });

  go.addEventListener("click", function () {
    if (!pending) return;
    pending.dataset.confirmed = "1";
    pending.submit();
    hide();
  });
  close.addEventListener("click", hide);
  shade.addEventListener("click", function (e) { if (e.target === shade) hide(); });
})();
