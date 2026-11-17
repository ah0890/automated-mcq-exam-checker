/* Result charts (Chart.js). Every chart reads its data from a <script type="application/json">
   block rendered by the server from stored results — nothing is generated client-side. */
(function () {
  "use strict";
  if (!window.Chart) return;

  var css = getComputedStyle(document.documentElement);
  var C = {
    series: "#2a78d6",          // categorical slot 1 (validated)
    fail: "#d03b3b",            // status critical (validated against series blue)
    ink: css.getPropertyValue("--ink").trim() || "#0f172a",
    muted: css.getPropertyValue("--muted").trim() || "#64748b",
    grid: "#eef1f6",
    surface: "#ffffff"
  };

  Chart.defaults.font.family = "Inter, system-ui, sans-serif";
  Chart.defaults.font.size = 12;
  Chart.defaults.color = C.muted;
  Chart.defaults.plugins.legend.display = false;
  Chart.defaults.plugins.tooltip.backgroundColor = "#0f172a";
  Chart.defaults.plugins.tooltip.padding = 10;
  Chart.defaults.plugins.tooltip.cornerRadius = 8;
  Chart.defaults.plugins.tooltip.displayColors = false;
  Chart.defaults.animation.duration = 300;

  function data(id) {
    var el = document.getElementById(id);
    return el ? JSON.parse(el.textContent) : null;
  }

  // Bars: thin, 4px rounded data end, square at the baseline, 2px surface gap.
  function bar(color) {
    return { backgroundColor: color, borderRadius: 4, borderSkipped: "start", maxBarThickness: 24,
             borderColor: C.surface, borderWidth: { top: 0, bottom: 0, left: 0, right: 0 },
             hoverBackgroundColor: color };
  }

  function axis(opts) {
    return Object.assign({
      grid: { color: C.grid, drawTicks: false }, border: { display: false },
      ticks: { color: C.muted, padding: 6 }
    }, opts || {});
  }

  // Dashed reference line (e.g. pass mark) drawn across a horizontal-bar chart.
  var referenceLine = {
    id: "referenceLine",
    afterDatasetsDraw: function (chart, args, opts) {
      if (opts == null || opts.value == null) return;
      var scale = chart.scales[opts.axis || "x"], area = chart.chartArea, ctx = chart.ctx;
      var pos = scale.getPixelForValue(opts.value);
      ctx.save();
      ctx.strokeStyle = C.ink; ctx.lineWidth = 1.5; ctx.setLineDash([4, 4]);
      ctx.beginPath();
      if ((opts.axis || "x") === "x") { ctx.moveTo(pos, area.top); ctx.lineTo(pos, area.bottom); }
      else { ctx.moveTo(area.left, pos); ctx.lineTo(area.right, pos); }
      ctx.stroke();
      ctx.setLineDash([]); ctx.fillStyle = C.ink; ctx.font = "600 11px Inter, sans-serif";
      if ((opts.axis || "x") === "x") ctx.fillText(opts.label, Math.min(pos + 6, area.right - 70), area.top + 10);
      else ctx.fillText(opts.label, area.right - 90, pos - 6);
      ctx.restore();
    }
  };
  Chart.register(referenceLine);

  // --- marks distribution -------------------------------------------------------------
  var dist = data("chart-distribution");
  if (dist && document.getElementById("distChart")) {
    new Chart(document.getElementById("distChart"), {
      type: "bar",
      data: { labels: dist.map(function (b) { return b.label + "%"; }),
              datasets: [Object.assign({ label: "Students", data: dist.map(function (b) { return b.count; }) },
                                       bar(C.series))] },
      options: {
        maintainAspectRatio: false,
        scales: { x: axis({ grid: { display: false } }),
                  y: axis({ beginAtZero: true, ticks: { precision: 0, color: C.muted } }) },
        plugins: { tooltip: { callbacks: {
          title: function (items) { return "Score " + items[0].label; },
          label: function (item) { return item.parsed.y + " student" + (item.parsed.y === 1 ? "" : "s"); } } } }
      }
    });
  }

  // --- student comparison (horizontal, sorted, pass-mark line) ------------------------
  var studs = data("chart-students");
  if (studs && document.getElementById("studentChart")) {
    var el = document.getElementById("studentChart");
    el.parentElement.style.height = Math.max(160, studs.students.length * 26 + 60) + "px";
    new Chart(el, {
      type: "bar",
      data: { labels: studs.students.map(function (s) { return s.label; }),
              datasets: [Object.assign({ label: "Marks", data: studs.students.map(function (s) { return s.marks; }) },
                                       bar(studs.students.map(function (s) { return s.passed ? C.series : C.fail; })))] },
      options: {
        indexAxis: "y", maintainAspectRatio: false,
        scales: { x: axis({ beginAtZero: true, suggestedMax: studs.maxMarks }), y: axis({ grid: { display: false } }) },
        plugins: {
          referenceLine: { value: studs.passMarks, axis: "x", label: "Pass mark " + studs.passMarks },
          tooltip: { callbacks: {
            title: function (items) { var s = studs.students[items[0].dataIndex]; return s.label + (s.name ? " · " + s.name : ""); },
            label: function (item) { var s = studs.students[item.dataIndex];
              return s.marks + " marks · " + s.pct + "% · " + (s.passed ? "Pass" : "Fail"); } } }
        }
      }
    });
  }

  // --- question accuracy -----------------------------------------------------------------
  var qs = data("chart-questions");
  if (qs && document.getElementById("questionChart")) {
    new Chart(document.getElementById("questionChart"), {
      type: "bar",
      data: { labels: qs.questions.map(function (q) { return "Q" + q.q; }),
              datasets: [Object.assign({ label: "% correct", data: qs.questions.map(function (q) { return q.correct; }) },
                                       bar(C.series))] },
      options: {
        maintainAspectRatio: false,
        scales: { x: axis({ grid: { display: false }, ticks: { autoSkip: true, maxRotation: 0, color: C.muted } }),
                  y: axis({ beginAtZero: true, max: 100, ticks: { callback: function (v) { return v + "%"; }, color: C.muted } }) },
        plugins: {
          referenceLine: { value: 50, axis: "y", label: "50% correct" },
          tooltip: { callbacks: {
            label: function (item) { var q = qs.questions[item.dataIndex];
              return [q.correct + "% correct · " + q.incorrect + "% incorrect", q.unanswered + " left blank of " + q.students]; } } }
        }
      }
    });
  }

  // --- subject performance -----------------------------------------------------------------
  var subj = data("chart-subjects");
  if (subj && subj.length && document.getElementById("subjectChart")) {
    var sEl = document.getElementById("subjectChart");
    sEl.parentElement.style.height = Math.max(140, subj.length * 34 + 50) + "px";
    new Chart(sEl, {
      type: "bar",
      data: { labels: subj.map(function (s) { return s.subject; }),
              datasets: [Object.assign({ label: "Average %", data: subj.map(function (s) { return parseFloat(s.average_pct); }) },
                                       bar(C.series))] },
      options: {
        indexAxis: "y", maintainAspectRatio: false,
        scales: { x: axis({ beginAtZero: true, max: 100, ticks: { callback: function (v) { return v + "%"; }, color: C.muted } }),
                  y: axis({ grid: { display: false } }) },
        plugins: { tooltip: { callbacks: { label: function (item) { var s = subj[item.dataIndex];
          return s.average_pct + "% average · " + s.students + " student result" + (s.students === 1 ? "" : "s"); } } } }
      }
    });
  }
})();
