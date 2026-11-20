// Tabs: a menu[role=tablist]. A tab's link names its panel by id;
// choosing one shows that panel and hides its siblings'. A page opened at
// #<panel id> starts on that tab.
(function () {
  document.querySelectorAll('menu[role="tablist"]').forEach(function (list) {
    var tabs = list.querySelectorAll('li[role="tab"]');
    function panel(tab) {
      return document.getElementById(tab.querySelector("a").getAttribute("href").slice(1));
    }
    function choose(tab) {
      tabs.forEach(function (t) {
        var on = t === tab;
        t.setAttribute("aria-selected", String(on));
        var p = panel(t);
        if (p) p.hidden = !on;
      });
    }
    tabs.forEach(function (tab) {
      tab.querySelector("a").addEventListener("click", function (event) {
        event.preventDefault();
        // The hash follows the tab, so asking for one again is a change.
        history.replaceState(null, "", location.pathname + location.search + this.getAttribute("href"));
        choose(tab);
      });
    });
    function fromHash() {
      var tab = Array.prototype.filter.call(tabs, function (t) {
        return t.querySelector("a").getAttribute("href") === location.hash;
      })[0];
      if (tab) choose(tab);
    }
    fromHash();
    window.addEventListener("hashchange", fromHash);
  });
})();
