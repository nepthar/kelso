// Tabs: 98.css's menu[role=tablist]. A tab's link names its panel by id;
// choosing one shows that panel and hides its siblings'.
(function () {
  document.querySelectorAll('menu[role="tablist"]').forEach(function (list) {
    var tabs = list.querySelectorAll('li[role="tab"]');
    function panel(tab) {
      return document.getElementById(tab.querySelector("a").getAttribute("href").slice(1));
    }
    tabs.forEach(function (tab) {
      tab.querySelector("a").addEventListener("click", function (event) {
        event.preventDefault();
        tabs.forEach(function (t) {
          var on = t === tab;
          t.setAttribute("aria-selected", String(on));
          var p = panel(t);
          if (p) p.hidden = !on;
        });
      });
    });
  });
})();
