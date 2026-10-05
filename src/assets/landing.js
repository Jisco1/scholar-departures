/* DegreeStep home page: the "Choose your path" tabs, and the boarding pass's countdown.
   Without this script every path is simply shown, one after another. */
(function () {
  "use strict";

  var list = document.querySelector(".lp-tabs");
  if (list) {
    var tabs = Array.prototype.slice.call(list.querySelectorAll('[role="tab"]'));
    var panels = tabs.map(function (t) { return document.getElementById(t.getAttribute("aria-controls")); });
    var select = function (i, focus) {
      tabs.forEach(function (t, j) {
        var on = i === j;
        t.setAttribute("aria-selected", String(on));
        t.tabIndex = on ? 0 : -1;
        if (panels[j]) panels[j].hidden = !on;
      });
      if (focus) tabs[i].focus();
    };
    tabs.forEach(function (t, i) {
      t.addEventListener("click", function () { select(i, false); });
      t.addEventListener("keydown", function (e) {
        var n = tabs.length, k = e.key;
        if (k !== "ArrowRight" && k !== "ArrowLeft" && k !== "Home" && k !== "End") return;
        e.preventDefault();
        select(k === "Home" ? 0 : k === "End" ? n - 1 : (i + (k === "ArrowRight" ? 1 : n - 1)) % n, true);
      });
    });
    list.hidden = false;
    select(0, false);
  }

  /* The page is rebuilt once a day; the countdown follows the visitor's own calendar. */
  var t0 = new Date();
  t0.setHours(0, 0, 0, 0);
  Array.prototype.forEach.call(document.querySelectorAll("[data-due]"), function (el) {
    var p = el.getAttribute("data-due").split("-");
    var days = Math.round((new Date(+p[0], +p[1] - 1, +p[2]) - t0) / 86400000);
    if (days < 0) return;  // keep the built text; the next build moves on to the next deadline
    el.textContent = days === 0 ? "Today" : days === 1 ? "Tomorrow" : "In " + days + " days";
  });
})();
