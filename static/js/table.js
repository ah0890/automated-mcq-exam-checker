/* Client-side search / filter / sort for tables marked data-smart-table.
   Rows carry data-search (text) and data-filter (status); sortable headers
   carry data-sort="<index>" and cells data-value for numeric sorting. */
(function () {
  "use strict";
  document.querySelectorAll("[data-smart-table]").forEach(function (wrap) {
    var table = wrap.querySelector("table");
    var tbody = table.tBodies[0];
    var rows = Array.prototype.slice.call(tbody.rows);
    var search = wrap.querySelector("[data-table-search]");
    var filters = wrap.querySelectorAll("[data-table-filter]");
    var count = wrap.querySelector("[data-table-count]");
    var empty = wrap.querySelector("[data-table-empty]");
    var active = "all";

    function apply() {
      var q = (search && search.value || "").trim().toLowerCase();
      var shown = 0;
      rows.forEach(function (r) {
        var ok = (!q || (r.dataset.search || "").indexOf(q) !== -1) && (active === "all" || r.dataset.filter === active);
        r.hidden = !ok;
        if (ok) shown++;
      });
      if (count) count.textContent = shown + " of " + rows.length;
      if (empty) empty.hidden = shown !== 0;
    }

    if (search) search.addEventListener("input", apply);
    filters.forEach(function (btn) {
      btn.addEventListener("click", function () {
        active = btn.dataset.tableFilter;
        filters.forEach(function (b) { b.classList.toggle("active", b === btn); b.setAttribute("aria-pressed", b === btn); });
        apply();
      });
    });

    table.querySelectorAll("th[data-sort]").forEach(function (th) {
      th.tabIndex = 0;
      th.setAttribute("role", "button");
      var dir = th.dataset.dir || "desc";
      function sort() {
        var idx = parseInt(th.dataset.sort, 10);
        dir = dir === "asc" ? "desc" : "asc";
        table.querySelectorAll("th[data-sort]").forEach(function (h) { h.removeAttribute("aria-sort"); h.classList.remove("sorted"); });
        th.setAttribute("aria-sort", dir === "asc" ? "ascending" : "descending");
        th.classList.add("sorted");
        rows.sort(function (a, b) {
          var va = a.cells[idx].dataset.value, vb = b.cells[idx].dataset.value;
          var na = va === "" || va == null ? null : parseFloat(va), nb = vb === "" || vb == null ? null : parseFloat(vb);
          if (na === null && nb === null) return 0;
          if (na === null) return 1;   // blanks (pending) always last
          if (nb === null) return -1;
          return dir === "asc" ? na - nb : nb - na;
        });
        rows.forEach(function (r) { tbody.appendChild(r); });
      }
      th.addEventListener("click", sort);
      th.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); sort(); } });
    });
    apply();
  });
})();
