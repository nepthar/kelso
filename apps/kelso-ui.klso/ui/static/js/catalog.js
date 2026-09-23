// The Repos page: a row opens its app card in the shade; clicking the shade
// closes it. Manifests are highlighted if highlight.js loaded.
(function () {
  var shade = document.getElementById("catalog-shade");
  if (shade) {
    var cards = shade.querySelectorAll(".app-card");
    function closeCard() {
      // Opened from a link (`?app=`): closing goes back to the plain list.
      var closeTo = shade.getAttribute("data-close");
      if (closeTo) { window.location = closeTo; return; }
      shade.hidden = true;
      cards.forEach(function (card) { card.hidden = true; });
    }
    document.querySelectorAll(".catalog-row").forEach(function (row) {
      row.addEventListener("click", function () {
        var card = document.getElementById(row.getAttribute("data-card"));
        if (!card) return;
        cards.forEach(function (other) { other.hidden = true; });
        card.hidden = false;
        shade.hidden = false;
      });
    });
    shade.addEventListener("click", function (event) {
      if (event.target === shade) closeCard();
    });
  }
  if (window.hljs) {
    document.querySelectorAll("pre.app-card-manifest code").forEach(function (el) {
      hljs.highlightElement(el);
    });
  }
})();
