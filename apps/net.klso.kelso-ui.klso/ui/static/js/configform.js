// A config form's Save and Cancel stay hidden until something in it has
// changed, and go again if every change is undone. Cancel undoes them all.
(function () {
  document.querySelectorAll(".cfg-form").forEach(function (form) {
    // A redirect with no fragment keeps the request's, so the page comes back
    // on the tab that was saved. Not `form.action`: the hidden input named
    // "action" shadows it.
    form.addEventListener("submit", function () {
      var tab = form.querySelector('li[aria-selected="true"] a');
      var action = (form.getAttribute("action") || "").split("#")[0];
      if (tab) form.setAttribute("action", action + tab.getAttribute("href"));
    });
    // A route's value is a hidden input that js/routepick.js sets from its
    // `data-ui` controls, which post nothing themselves.
    var controls = form.querySelectorAll(
      "input:not([type=hidden]):not([data-ui]), input[data-route-value], select:not([data-ui])"
    );
    var save = form.querySelector(".cfg-save");
    if (!controls.length || !save || save.closest(".dialog-buttons")) return;
    // A checkbox's value never changes; whether it is checked does.
    function state(c) { return c.type === "checkbox" ? String(c.checked) : c.value; }
    var initial = Array.prototype.map.call(controls, state);
    function dirty() {
      var on = Array.prototype.some.call(controls, function (c, i) {
        return state(c) !== initial[i];
      });
      form.classList.toggle("is-dirty", on);
      save.disabled = !on;
    }
    controls.forEach(function (c) {
      c.addEventListener("input", dirty);
      c.addEventListener("change", dirty);
    });
    var cancel = form.querySelector(".cfg-cancel");
    if (cancel) {
      cancel.addEventListener("click", function () {
        controls.forEach(function (c, i) {
          if (c.type === "checkbox") c.checked = initial[i] === "true";
          else c.value = initial[i];
          // routepick.js redraws a route's controls from its hidden list on change.
          c.dispatchEvent(new Event("change", { bubbles: true }));
        });
      });
    }
  });
})();
