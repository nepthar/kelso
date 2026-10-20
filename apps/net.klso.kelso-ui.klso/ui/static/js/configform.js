// A config form's Save stays hidden and disabled until something in it has
// changed, and goes again if every change is undone.
(function () {
  document.querySelectorAll(".cfg-form").forEach(function (form) {
    // A pick-several field's value is a hidden input that js/tagpick.js sets.
    var controls = form.querySelectorAll(
      "input:not([type=hidden]), input[data-tag-pick], select"
    );
    var save = form.querySelector(".cfg-save");
    if (!controls.length || !save) return;
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
  });
})();
