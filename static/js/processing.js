/* Examination workspace: live processing progress and paper selection. */
(function () {
  "use strict";

  var section = document.getElementById("processing");
  if (section && section.dataset.active) {
    var url = section.dataset.statusUrl;
    var bar = section.querySelector("[data-proc-bar]");
    var label = section.querySelector("[data-proc-label]");
    var percent = section.querySelector("[data-proc-percent]");
    var poll = function () {
      fetch(url, { headers: { "Accept": "application/json" }, credentials: "same-origin" })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          var job = data.job;
          if (!job) return;
          if (bar) bar.style.width = job.percent + "%";
          if (percent) percent.textContent = job.percent + "%";
          if (label) label.textContent = job.status_label + " — " + job.done + " of " + job.total;
          ["awaiting", "processed", "needs_review", "failed"].forEach(function (k) {
            var el = section.querySelector('[data-stat="' + k + '"]');
            if (el && data.stats[k] != null) el.textContent = data.stats[k];
          });
          if (job.active) setTimeout(poll, 1200);
          else window.location.reload();
        })
        .catch(function () { setTimeout(poll, 4000); });
    };
    setTimeout(poll, 800);
  }

  var form = document.getElementById("reprocessForm");
  if (form) {
    var all = form.querySelector("[data-select-all]");
    var btn = form.querySelector("[data-reprocess]");
    var boxes = function () { return form.querySelectorAll('input[name="papers"]'); };
    var sync = function () {
      var n = Array.prototype.filter.call(boxes(), function (b) { return b.checked; }).length;
      if (btn) {
        btn.disabled = n === 0;
        btn.innerHTML = '<i class="bi bi-arrow-repeat"></i> Reprocess ' + (n ? n + " selected" : "selected");
      }
    };
    if (all) all.addEventListener("change", function () {
      boxes().forEach(function (b) { b.checked = all.checked; });
      sync();
    });
    form.addEventListener("change", sync);
    form.addEventListener("submit", function (e) {
      if (!window.confirm("Read the selected papers again? Answers you corrected by hand are kept.")) e.preventDefault();
    });
  }
})();
