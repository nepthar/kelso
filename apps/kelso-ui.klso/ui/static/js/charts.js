// The dashboard's host CPU and memory charts, drawn with uPlot from the
// #host-metrics data block, in the current theme's tokens.
(function () {
  var el = document.getElementById("host-metrics");
  if (!el || typeof uPlot === "undefined") return;
  var payload = JSON.parse(el.textContent);
  draw("chart-cpu", payload.cpu, payload.since, payload.until);
  draw("chart-mem", payload.mem, payload.since, payload.until);

  function draw(id, points, since, until) {
    var mount = document.getElementById(id);
    if (!mount) return;
    if (!points.length) {
      mount.innerHTML = '<p class="muted">No samples in the last hour.</p>';
      return;
    }
    var xs = points.map(function (p) { return p.t; });
    var ys = points.map(function (p) { return p.v; });
    var css = getComputedStyle(document.documentElement);
    function token(name) { return css.getPropertyValue(name).trim(); }
    function fade(hex, alpha) {
      var n = parseInt(hex.slice(1), 16);
      return "rgba(" + ((n >> 16) & 255) + "," + ((n >> 8) & 255) + "," + (n & 255) + "," + alpha + ")";
    }
    var coral = token("--coral");
    var plot = new uPlot({
      width: mount.clientWidth || 400,
      height: 220,
      cursor: { focus: { prox: 24 } },
      legend: { show: false },
      scales: {
        x: { time: true, auto: false, range: [since, until] },
        y: { auto: false, range: [0, 1] }
      },
      axes: [
        {
          stroke: token("--dim"),
          font: '11px "IBM Plex Mono", ui-monospace, monospace',
          grid: { stroke: token("--muted"), width: 1 },
          ticks: { stroke: token("--line") }
        },
        {
          stroke: token("--dim"),
          font: '11px "IBM Plex Mono", ui-monospace, monospace',
          grid: { stroke: token("--muted"), width: 1 },
          ticks: { stroke: token("--line") },
          values: function (u, splits) {
            return splits.map(function (v) { return Math.round(v * 100) + "%"; });
          }
        }
      ],
      series: [
        {},
        {
          stroke: coral,
          width: 2,
          fill: fade(coral, 0.1),
          points: { show: true, size: 5, fill: coral }
        }
      ]
    }, [xs, ys], mount);
    new ResizeObserver(function () {
      plot.setSize({ width: mount.clientWidth || 400, height: 220 });
    }).observe(mount);
  }
})();
