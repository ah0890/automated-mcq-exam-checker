/* Site-wide behaviour. No inline scripts are used anywhere, so a strict
   Content-Security-Policy (script-src 'self') can be enforced. */
(function () {
  "use strict";

  // Tooltips
  if (window.bootstrap) {
    document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(function (el) { new bootstrap.Tooltip(el); });
  }

  // Confirmation before destructive or locking actions: <button data-confirm="Question?">
  document.addEventListener("click", function (e) {
    var el = e.target.closest("[data-confirm]");
    if (el && !window.confirm(el.dataset.confirm)) { e.preventDefault(); e.stopImmediatePropagation(); }
  }, true);

  // Forms with data-loading show a full-screen progress message while the server works.
  document.querySelectorAll("form[data-loading]").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      var btn = e.submitter;
      if (btn && btn.hasAttribute("formnovalidate")) return;
      document.getElementById("loadingMsg").textContent = form.dataset.loading;
      document.getElementById("loadingSub").textContent = form.dataset.loadingSub || "";
      document.getElementById("loadingOverlay").classList.add("show");
    });
  });

  // Forms whose action is chosen in a select: <form data-action-select> … <select> with URL values.
  document.querySelectorAll("form[data-action-select]").forEach(function (form) {
    form.addEventListener("submit", function () { form.action = form.querySelector("select").value; });
  });

  // <select data-autosubmit> submits its form on change.
  document.querySelectorAll("select[data-autosubmit]").forEach(function (sel) {
    sel.addEventListener("change", function () { sel.form.submit(); });
  });

  // <button data-scroll-to="#id"> scrolls smoothly to a section.
  document.querySelectorAll("[data-scroll-to]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var t = document.querySelector(btn.dataset.scrollTo);
      if (t) t.scrollIntoView({ behavior: "smooth" });
    });
  });

  // <div data-show-when="checkbox-id"> is visible only while that checkbox is ticked.
  document.querySelectorAll("[data-show-when]").forEach(function (wrap) {
    var box = document.getElementById(wrap.dataset.showWhen);
    if (!box) return;
    var sync = function () { wrap.style.display = box.checked ? "" : "none"; };
    box.addEventListener("change", sync);
    sync();
  });

  // Drop zones: show the chosen file name(s).
  document.querySelectorAll(".dropzone").forEach(function (dz) {
    var input = dz.querySelector("input[type=file]"), out = dz.querySelector(".dz-file");
    if (!input) return;
    ["dragenter", "dragover"].forEach(function (ev) { dz.addEventListener(ev, function () { dz.classList.add("is-over"); }); });
    ["dragleave", "drop"].forEach(function (ev) { dz.addEventListener(ev, function () { dz.classList.remove("is-over"); }); });
    input.addEventListener("change", function () {
      if (!out) return;
      var n = input.files.length;
      out.textContent = n === 0 ? "" : (n === 1 ? input.files[0].name : n + " files selected");
    });
  });
})();
