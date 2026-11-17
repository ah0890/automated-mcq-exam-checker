/* Batch paper uploader: one request per file, with progress and per-file results. */
(function () {
  "use strict";

  function uuid() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) {
      var r = (Math.random() * 16) | 0;
      return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
    });
  }

  function fmtSize(bytes) {
    if (bytes < 1024 * 1024) return Math.max(1, Math.round(bytes / 1024)) + " KB";
    return (bytes / 1024 / 1024).toFixed(1) + " MB";
  }

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  function sendOne(url, csrf, file, batch, ppp, onProgress) {
    return new Promise(function (resolve) {
      var xhr = new XMLHttpRequest();
      var fd = new FormData();
      fd.append("files", file);
      fd.append("batch", batch);
      fd.append("pages_per_paper", ppp);
      xhr.open("POST", url);
      xhr.setRequestHeader("X-CSRFToken", csrf);
      xhr.setRequestHeader("X-Requested-With", "XMLHttpRequest");
      xhr.setRequestHeader("Accept", "application/json");
      xhr.upload.onprogress = function (e) { if (e.lengthComputable) onProgress(e.loaded / e.total); };
      xhr.onload = function () {
        var data = null;
        try { data = JSON.parse(xhr.responseText); } catch (err) { /* not JSON */ }
        if (xhr.status === 200 && data && data.results) resolve(data);
        else if (xhr.status === 403) resolve({ error: "Your session expired. Sign in again and retry." });
        else resolve({ error: (data && data.error) || "Upload failed (server error " + xhr.status + ")." });
      };
      xhr.onerror = function () { resolve({ error: "Network error — the file was not uploaded." }); };
      xhr.send(fd);
    });
  }

  function init(form) {
    var input = form.querySelector("input[type=file]");
    var select = form.querySelector("[data-exam-select]");
    var panel = form.querySelector(".upload-progress");
    var list = form.querySelector("[data-list]");
    var summary = form.querySelector("[data-summary]");
    var counter = form.querySelector("[data-counter]");
    var totalBar = form.querySelector("[data-total-bar]");
    var done = form.querySelector("[data-done]");
    var viewLink = form.querySelector("[data-view-link]");
    var submit = form.querySelector("button[type=submit]");
    var csrf = form.querySelector("input[name=csrfmiddlewaretoken]").value;

    form.querySelector("[data-reset]").addEventListener("click", function () {
      input.value = "";
      form.querySelector(".dz-file").textContent = "";
      list.innerHTML = "";
      panel.hidden = true;
      done.hidden = true;
      submit.disabled = false;
    });

    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var url = select ? select.value : form.getAttribute("action");
      if (!url) { select.focus(); select.classList.add("is-invalid"); return; }
      if (select) select.classList.remove("is-invalid");
      var files = Array.prototype.slice.call(input.files);
      if (!files.length) { input.click(); return; }
      var examUrl = select ? select.options[select.selectedIndex].dataset.examUrl : (form.dataset.examUrl || "");
      var ppp = form.querySelector("[name=pages_per_paper]").value;
      var batch = uuid();

      submit.disabled = true;
      panel.hidden = false;
      done.hidden = true;
      list.innerHTML = "";
      var rows = files.map(function (f) {
        var li = el("li", "upload-item");
        var ic = el("span", "ui-ic"); ic.innerHTML = '<i class="bi bi-file-earmark"></i>';
        var body = el("div", "ui-body");
        var top = el("div", "ui-top");
        top.appendChild(el("span", "ui-name", f.name));
        top.appendChild(el("span", "ui-size", fmtSize(f.size)));
        var bar = el("div", "progress ui-bar"); var fill = el("div", "progress-bar"); bar.appendChild(fill);
        var msg = el("div", "ui-msg", "Waiting…");
        body.appendChild(top); body.appendChild(bar); body.appendChild(msg);
        li.appendChild(ic); li.appendChild(body);
        list.appendChild(li);
        return { li: li, fill: fill, msg: msg, ic: ic };
      });

      var added = 0, failed = 0, skipped = 0, i = 0;
      function setTotal(partial) {
        totalBar.style.width = (((i + partial) / files.length) * 100).toFixed(1) + "%";
        counter.textContent = Math.min(i + 1, files.length) + " of " + files.length;
      }
      function next() {
        if (i >= files.length) {
          totalBar.style.width = "100%";
          counter.textContent = files.length + " of " + files.length;
          summary.textContent = added + " paper" + (added === 1 ? "" : "s") + " added" +
            (skipped ? " · " + skipped + " duplicate" + (skipped === 1 ? "" : "s") + " skipped" : "") +
            (failed ? " · " + failed + " file" + (failed === 1 ? "" : "s") + " rejected" : "");
          viewLink.href = (examUrl || "#") + "#papers";
          done.hidden = false;
          return;
        }
        var row = rows[i];
        row.msg.textContent = "Uploading…";
        sendOne(url, csrf, files[i], batch, ppp, function (p) {
          row.fill.style.width = (p * 100).toFixed(0) + "%";
          setTotal(p * 0.9);
          if (p >= 1) row.msg.textContent = "Checking and splitting pages…";
        }).then(function (data) {
          row.fill.style.width = "100%";
          var r = data.results ? data.results[0] : null;
          if (!r) {
            failed++; row.li.classList.add("is-error"); row.msg.textContent = data.error;
          } else if (r.status === "added") {
            added += r.papers_added;
            skipped += r.skipped_pages.length;
            row.li.classList.add("is-ok");
            var text = r.papers_added + " paper" + (r.papers_added === 1 ? "" : "s") + " added";
            var notes = r.skipped_pages.concat(r.warnings);
            row.msg.textContent = notes.length ? text + " · " + notes.join(" ") : text;
          } else if (r.status === "duplicate_file") {
            skipped++; row.li.classList.add("is-warn");
            row.msg.textContent = r.error || (r.skipped_pages.join(" ") || "Already uploaded — skipped.");
          } else {
            failed++; row.li.classList.add("is-error"); row.msg.textContent = r.error || "Rejected.";
          }
          row.ic.innerHTML = row.li.classList.contains("is-ok") ? '<i class="bi bi-check-circle-fill"></i>'
            : row.li.classList.contains("is-warn") ? '<i class="bi bi-exclamation-circle-fill"></i>'
            : '<i class="bi bi-x-circle-fill"></i>';
          i++;
          setTotal(0);
          next();
        });
      }
      summary.textContent = "Uploading " + files.length + " file" + (files.length === 1 ? "" : "s") + "…";
      next();
    });
  }

  document.querySelectorAll("form[data-uploader]").forEach(init);
})();
