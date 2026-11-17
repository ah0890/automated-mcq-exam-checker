/* Answer-key verification: live count of answered questions. */
(function () {
  "use strict";
  var form = document.getElementById("verifyForm"), out = document.getElementById("filledCount");
  if (!form || !out) return;
  form.addEventListener("change", function (e) {
    if (e.target.type !== "radio") return;
    var names = new Set(Array.prototype.map.call(form.querySelectorAll("input[type=radio]:checked"),
                                                 function (i) { return i.name; }));
    out.textContent = names.size;
    var row = e.target.closest(".ans-row");
    if (row) row.classList.remove("flag-missing", "flag-conflict");
  });
})();
