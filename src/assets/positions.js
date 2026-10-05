/* DegreeStep funded positions: filter by type and field, and recount each deadline in the
   visitor's own calendar. Without this script every open position is simply listed. */
(function () {
  "use strict";

  var t0 = new Date();
  t0.setHours(0, 0, 0, 0);
  Array.prototype.forEach.call(document.querySelectorAll("[data-closes]"), function (el) {
    var p = el.getAttribute("data-closes").split("-");
    var days = Math.round((new Date(+p[0], +p[1] - 1, +p[2]) - t0) / 86400000);
    if (days < 0) {  // closed since the last daily build
      var card = el.closest(".pos, .row");
      if (card) card.remove();
      else el.textContent = "Closed";  // the position's own page
      return;
    }
    el.textContent = days === 0 ? "Closes today" : days === 1 ? "Closes tomorrow" : "Closes in " + days + " days";
  });

  var bar = document.querySelector(".pos-filters");
  if (!bar) return;
  var cards = Array.prototype.slice.call(document.querySelectorAll(".pos"));
  var buttons = Array.prototype.slice.call(bar.querySelectorAll("button[data-level]"));
  var select = bar.querySelector("select");
  var count = bar.querySelector(".pos-count");
  var level = "All";

  function apply() {
    var field = select.value, shown = 0;
    cards.forEach(function (c) {
      var on = (level === "All" || c.getAttribute("data-level") === level) &&
               (field === "All fields" || c.getAttribute("data-field") === field);
      c.hidden = !on;
      if (on) shown++;
    });
    buttons.forEach(function (b) {
      var on = b.getAttribute("data-level") === level;
      b.classList.toggle("active", on);
      b.setAttribute("aria-pressed", String(on));
    });
    count.textContent = shown + " of " + cards.length + " open";
  }
  buttons.forEach(function (b) {
    b.addEventListener("click", function () { level = b.getAttribute("data-level"); apply(); });
  });
  select.addEventListener("change", apply);
  bar.hidden = false;
  apply();
})();
