// Small progressive enhancements. Every page works without JavaScript.
(function () {
  // Confirmation prompts on destructive forms.
  document.addEventListener("submit", function (e) {
    var form = e.target;
    var msg = form.getAttribute("data-confirm");
    if (msg && !window.confirm(msg)) { e.preventDefault(); return; }
    // Prevent double-submits (a common source of duplicate records on slow connections).
    if (form.method.toLowerCase() === "post") {
      if (form.dataset.submitted) { e.preventDefault(); return; }
      form.dataset.submitted = "1";
      setTimeout(function () { delete form.dataset.submitted; }, 8000);
    }
  });

  // Click-to-sort tables.
  document.querySelectorAll("table.sortable th").forEach(function (th, idx) {
    th.style.cursor = "pointer";
    th.title = "Sort";
    th.addEventListener("click", function () {
      var tbody = th.closest("table").tBodies[0];
      var asc = th.dataset.dir !== "asc";
      th.parentNode.querySelectorAll("th").forEach(function (o) { delete o.dataset.dir; });
      th.dataset.dir = asc ? "asc" : "desc";
      var rows = Array.prototype.slice.call(tbody.rows);
      var collator = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });
      rows.sort(function (a, b) {
        var x = a.cells[idx].textContent.trim(), y = b.cells[idx].textContent.trim();
        return asc ? collator.compare(x, y) : collator.compare(y, x);
      });
      rows.forEach(function (r) { tbody.appendChild(r); });
    });
  });
})();

function initRollCall() {
  var form = document.getElementById("vote-form");
  if (!form) return;
  var threshold = form.querySelector("[name=threshold]");
  var tie = form.querySelector("[name=tie_breaker]");
  var voice = document.getElementById("voice-toggle");

  function update() {
    var counts = { Y: 0, N: 0, A: 0, X: 0 };
    form.querySelectorAll(".rollcall input[type=radio]:checked").forEach(function (r) { counts[r.value]++; });
    Object.keys(counts).forEach(function (k) {
      var el = form.querySelector("[data-count=" + k + "]");
      if (el) el.textContent = counts[k];
    });
    var y = counts.Y, n = counts.N, passed;
    if (y + n === 0) passed = false;
    else if (threshold && threshold.value === "two_thirds") passed = y * 3 >= (y + n) * 2;
    else if (y === n && window.VGS_TIEBREAK && tie && tie.value) passed = tie.value === "Y";
    else passed = y > n;
    var res = form.querySelector("[data-result]");
    if (res) {
      res.textContent = (y + n === 0) ? "" : (passed ? "Passing" : "Failing");
      res.className = passed ? "good" : "bad";
    }
  }

  form.addEventListener("change", update);
  form.querySelectorAll("[data-set-all]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var v = btn.getAttribute("data-set-all");
      form.querySelectorAll(".rollcall input[value=" + v + "]").forEach(function (r) { r.checked = true; });
      update();
    });
  });
  // Keyboard entry: focus a row and press Y/N/A/X; focus advances to the next member.
  form.querySelectorAll(".roll-row").forEach(function (row) {
    row.addEventListener("keydown", function (e) {
      var key = e.key.toUpperCase();
      if ("YNAX".indexOf(key) === -1 || e.target.tagName === "INPUT" && e.target.type !== "radio") return;
      var input = row.querySelector("input[value=" + key + "]");
      if (!input) return;
      e.preventDefault();
      input.checked = true;
      update();
      var next = row.nextElementSibling;
      if (next) next.focus();
    });
  });
  if (voice) {
    voice.addEventListener("change", function () {
      var isVoice = voice.value === "1";
      document.querySelectorAll(".voice-only").forEach(function (el) { el.hidden = !isVoice; });
      document.querySelectorAll(".roll-only").forEach(function (el) { el.hidden = isVoice; });
    });
  }
  update();
}
