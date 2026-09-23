// A config form's Save stays hidden and disabled until something in it has
// changed, and goes again if every change is undone.
(function () {
  document.querySelectorAll(".cfg-form").forEach(function (form) {
    var controls = form.querySelectorAll("input:not([type=hidden]), select");
    var save = form.querySelector(".cfg-save");
    if (!controls.length || !save) return;
    var initial = Array.prototype.map.call(controls, function (c) { return c.value; });
    function dirty() {
      var on = Array.prototype.some.call(controls, function (c, i) {
        return c.value !== initial[i];
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
