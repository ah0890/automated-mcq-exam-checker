/* Batch paper uploader: one request per file with progress, then automatic
   checking with live progress, then a link to the results. */
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

  function plural(n, word) { return n + " " + word + (n === 1 ? "" : "s"); }

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

  function postJson(url, csrf) {
    return fetch(url, { method: "POST", credentials: "same-origin",
                        headers: { "X-CSRFToken": csrf, "X-Requested-With": "XMLHttpRequest", "Accept": "application/json" } })
      .then(function (r) { return r.json(); });
  }

  function getJson(url) {
    return fetch(url, { credentials: "same-origin", headers: { "Accept": "application/json" } })
      .then(function (r) { return r.json(); });
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
    var resultsLink = form.querySelector("[data-results-link]");
    var reviewLink = form.querySelector("[data-review-link]");
    var check = form.querySelector("[data-check]");
    var checkLabel = form.querySelector("[data-check-label]");
    var checkCount = form.querySelector("[data-check-count]");
    var checkBar = form.querySelector("[data-check-bar]");
    var checkResult = form.querySelector("[data-check-result]");
    var submit = form.querySelector("button[type=submit]");
    var csrf = form.querySelector("input[name=csrfmiddlewaretoken]").value;

    function target() {
      // Data attributes describing the chosen examination.
      return select ? (select.options[select.selectedIndex] || {}).dataset || {} : form.dataset;
    }

    form.querySelector("[data-reset]").addEventListener("click", function () {
      input.value = "";
      form.querySelector(".dz-file").textContent = "";
      list.innerHTML = "";
      panel.hidden = true;
      done.hidden = true;
      check.hidden = true;
      submit.disabled = false;
    });

    function finish(t, stats) {
      var after = form.dataset.afterUrl;
      resultsLink.href = after ? after.replace("{uid}", t.examUid) : t.resultsUrl;
      viewLink.href = (t.examUrl || "#") + "#papers";
      reviewLink.href = t.reviewUrl;
      reviewLink.hidden = !(stats && stats.to_review);
      done.hidden = false;
      if (after && stats) {
        // On the dashboard: reload it to show the results there automatically.
        var url = resultsLink.href.replace("#", "&checked=" + Date.now() + "#");
        setTimeout(function () { window.location.assign(url); }, 1800);
      }
    }

    function checkPapers(t, attempt) {
      check.hidden = false;
      checkResult.textContent = "";
      checkLabel.innerHTML = '<span class="spinner-border spinner-border-sm text-primary me-1"></span> Checking papers…';
      postJson(t.startUrl, csrf).then(function (res) {
        if (!res.started && !res.running) {
          // Nothing waiting (e.g. all were duplicates) or not possible.
          checkLabel.textContent = res.error || "Nothing to check.";
          checkBar.style.width = "100%";
          return getJson(t.statusUrl).then(function (s) { finish(t, s.stats); });
        }
        var poll = function () {
          getJson(t.statusUrl).then(function (s) {
            var job = s.job;
            if (job) {
              checkBar.style.width = job.percent + "%";
              checkCount.textContent = job.done + " of " + job.total;
            }
            if (job && job.active) { setTimeout(poll, 1000); return; }
            // Papers uploaded while an earlier run was busy are checked in one more pass.
            if (s.stats.startable && attempt < 2) { checkPapers(t, attempt + 1); return; }
            checkBar.classList.remove("progress-bar-animated", "progress-bar-striped");
            checkBar.style.width = "100%";
            var st = s.stats;
            checkLabel.innerHTML = '<i class="bi bi-check-circle-fill text-success me-1"></i> Checking complete';
            var parts = [plural(st.processed, "paper") + " marked"];
            if (st.needs_review) parts.push(st.needs_review + " need your review (doubtful marks or roll numbers)");
            if (st.failed) parts.push(plural(st.failed, "page") + " could not be read");
            checkResult.textContent = parts.join(" · ");
            finish(t, st);
          }).catch(function () { setTimeout(poll, 3000); });
        };
        poll();
      }).catch(function () {
        checkLabel.textContent = "Could not start checking. Open the examination page and click “Process papers”.";
        finish(t, null);
      });
    }

    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var url = select ? select.value : form.getAttribute("action");
      if (!url) { select.focus(); select.classList.add("is-invalid"); return; }
      if (select) select.classList.remove("is-invalid");
      var files = Array.prototype.slice.call(input.files);
      if (!files.length) { input.click(); return; }
      var t = target();
      var ppp = form.querySelector("[name=pages_per_paper]").value;
      var batch = uuid();

      submit.disabled = true;
      panel.hidden = false;
      done.hidden = true;
      check.hidden = true;
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
          summary.textContent = plural(added, "paper") + " uploaded" +
            (skipped ? " · " + plural(skipped, "duplicate") + " skipped" : "") +
            (failed ? " · " + plural(failed, "file") + " rejected" : "");
          if (t.autoCheck) checkPapers(t, 0);
          else {
            check.hidden = false;
            checkLabel.textContent = "Attach a verified answer key to this examination to check the papers.";
            finish(t, null);
          }
          return;
        }
        var row = rows[i];
        row.msg.textContent = "Uploading…";
        sendOne(url, csrf, files[i], batch, ppp, function (p) {
          row.fill.style.width = (p * 100).toFixed(0) + "%";
          setTotal(p * 0.9);
          if (p >= 1) row.msg.textContent = "Checking file and splitting pages…";
        }).then(function (data) {
          row.fill.style.width = "100%";
          var r = data.results ? data.results[0] : null;
          if (!r) {
            failed++; row.li.classList.add("is-error"); row.msg.textContent = data.error;
          } else if (r.status === "added") {
            added += r.papers_added;
            skipped += r.skipped_pages.length;
            row.li.classList.add("is-ok");
            var text = plural(r.papers_added, "paper") + " added";
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
      summary.textContent = "Uploading " + plural(files.length, "file") + "…";
      next();
    });
  }

  document.querySelectorAll("form[data-uploader]").forEach(init);
})();
