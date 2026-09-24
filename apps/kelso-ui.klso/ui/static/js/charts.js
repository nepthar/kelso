// The dashboard's host CPU and memory charts, drawn with uPlot from the
// #host-metrics data block in the current theme's tokens -- and drawn again
// when the theme changes, since a canvas does not follow CSS.
(function () {
  var el = document.getElementById("host-metrics");
  if (!el || typeof uPlot === "undefined") return;
  var payload = JSON.parse(el.textContent);
  var charts = [
    { id: "chart-cpu", points: payload.cpu },
    { id: "chart-mem", points: payload.mem }
  ];
  var MONO = '11px "IBM Plex Mono", ui-monospace, monospace';

  function axis(extra) {
    var base = {
      stroke: kelso.token("--dim"),
      font: MONO,
      grid: { stroke: kelso.token("--muted"), width: 1 },
      ticks: { stroke: kelso.token("--line") }
    };
    for (var k in extra) base[k] = extra[k];
    return base;
  }

  function draw(chart) {
    var mount = document.getElementById(chart.id);
    if (!mount) return;
    if (!chart.points.length) {
      mount.innerHTML = '<p class="muted">No samples in the last hour.</p>';
      return;
    }
    if (chart.plot) chart.plot.destroy();
    var coral = kelso.token("--coral");
    chart.plot = new uPlot({
      width: mount.clientWidth || 400,
      height: 220,
      cursor: { focus: { prox: 24 } },
      legend: { show: false },
      scales: {
        x: { time: true, auto: false, range: [payload.since, payload.until] },
        y: { auto: false, range: [0, 1] }
      },
      axes: [
        axis({}),
        axis({
          values: function (u, splits) {
            return splits.map(function (v) { return Math.round(v * 100) + "%"; });
          }
        })
      ],
      series: [
        {},
        {
          stroke: coral,
          width: 2,
          fill: kelso.rgba("--coral", 0.1),
          points: { show: true, size: 5, fill: coral }
        }
      ]
    }, [chart.points.map(function (p) { return p.t; }),
        chart.points.map(function (p) { return p.v; })], mount);
    if (!chart.observer) {
      chart.observer = new ResizeObserver(function () {
        if (chart.plot) chart.plot.setSize({ width: mount.clientWidth || 400, height: 220 });
      });
      chart.observer.observe(mount);
    }
  }

  charts.forEach(draw);
  document.addEventListener("kelso:themechange", function () { charts.forEach(draw); });
})();
