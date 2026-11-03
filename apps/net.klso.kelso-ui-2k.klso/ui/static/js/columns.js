// Column widths: drag the right edge of a header. The first drag fixes every
// column at the width it has, so moving one edge moves only that column.
(function () {
  var MIN = 32;
  document.querySelectorAll("table").forEach(function (table) {
    var heads = table.querySelectorAll(":scope > thead > tr > th");
    if (heads.length < 2) return;
    heads.forEach(function (th) {
      var grip = document.createElement("span");
      grip.className = "col-grip";
      th.appendChild(grip);
      grip.addEventListener("pointerdown", function (event) {
        if (event.button !== 0) return;
        event.preventDefault();
        if (!table.classList.contains("sized")) {
          var widths = Array.prototype.map.call(heads, function (h) { return h.offsetWidth; });
          heads.forEach(function (h, i) { h.style.width = widths[i] + "px"; });
          table.style.width = table.offsetWidth + "px";
          table.classList.add("sized");
        }
        var x = event.clientX, width = th.offsetWidth, total = table.offsetWidth;
        grip.setPointerCapture(event.pointerId);
        function move(e) {
          var by = Math.max(MIN - width, e.clientX - x);
          th.style.width = width + by + "px";
          table.style.width = total + by + "px";
        }
        function up() {
          grip.removeEventListener("pointermove", move);
          grip.removeEventListener("pointerup", up);
        }
        grip.addEventListener("pointermove", move);
        grip.addEventListener("pointerup", up);
      });
    });
  });
})();
