/* DegreeStep — shared behaviour for every page.
   1. Live deadline chips and timelines: pages are built with today's status
      baked in; this recomputes it against the visitor's own date.
   2. Ads: each labelled bay asks AdSense for its ad only when the reader
      scrolls near it, so ads never slow the first paint.
   3. "Privacy choices" reopens Google's consent message where it applies. */
(function () {
  "use strict";
  var HORIZON_DAYS = 120;
  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  var today = new Date(); today.setHours(0, 0, 0, 0);

  function fmt(d) { return MONTHS[d.getMonth()] + " " + d.getDate() + ", " + d.getFullYear(); }
  function nextDate(deadlines) {
    var best = null;
    (deadlines || []).forEach(function (dl) {
      var d = new Date(today.getFullYear(), dl.m - 1, dl.d);
      if (d < today) d = new Date(today.getFullYear() + 1, dl.m - 1, dl.d);
      if (!best || d < best) best = d;
    });
    return best;
  }
  function parse(json) { try { return JSON.parse(json); } catch (e) { return null; } }

  /* ---- 1. live deadline chips ---- */
  document.querySelectorAll("[data-dl]").forEach(function (chip) {
    if (chip.hasAttribute("data-rolling")) return;
    var nd = nextDate(parse(chip.getAttribute("data-dl")));
    if (!nd) return;
    var days = Math.round((nd - today) / 86400000);
    var approx = chip.hasAttribute("data-approx") ? "≈ " : "";
    var status = days <= HORIZON_DAYS ? "approaching" : "closed";
    chip.className = "chip " + status;
    chip.textContent = status === "approaching"
      ? "Due " + approx + "in " + days + " day" + (days === 1 ? "" : "s")
      : "Closed · next " + approx + fmt(nd);
    var glance = chip.closest(".glance");
    var next = glance && glance.querySelector("[data-next]");
    if (next) next.textContent = approx + fmt(nd);
  });

  document.querySelectorAll("[data-plan]").forEach(function (list) {
    var nd = nextDate(parse(list.getAttribute("data-plan")));
    if (!nd) return;
    var weeks = [12, 10, 8, 6, 4, 2, 1, 0];
    list.querySelectorAll(".plan-item").forEach(function (item, i) {
      var d = new Date(nd); d.setDate(d.getDate() - weeks[i] * 7);
      var isFinal = weeks[i] === 0, due = !isFinal && d <= today;
      item.className = "plan-item" + (isFinal ? " final" : due ? " due" : "");
      item.querySelector(".plan-date").textContent = fmt(d) + (due ? " · NOW" : "");
    });
  });

  var month = today.getMonth() + 1;
  document.querySelectorAll(".month[data-month]").forEach(function (m) {
    m.classList.toggle("now", +m.getAttribute("data-month") === month);
  });

  /* ---- 2. ads: request each bay's ad when it comes near the viewport ----
     Each push() fills the first ins.adsbygoogle that Google has not seen, whichever bay asked.
     So bays are built without that class and receive it only at their own turn; otherwise the
     rail's request would fill the in-article bay far below it and leave the rail empty. */
  var slots = Array.prototype.slice.call(document.querySelectorAll("ins[data-sd-ad]"));
  if (slots.length) {
    var pushAd = function (ins) {
      if (ins.classList.contains("adsbygoogle")) return;
      ins.classList.add("adsbygoogle");
      try { (window.adsbygoogle = window.adsbygoogle || []).push({}); } catch (e) {}
    };
    if ("IntersectionObserver" in window) {
      var io = new IntersectionObserver(function (entries) {
        entries.forEach(function (en) {
          if (en.isIntersecting) { pushAd(en.target); io.unobserve(en.target); }
        });
      }, { rootMargin: "600px 0px" });
      slots.forEach(function (ins) {
        // a bay hidden by the layout (the desktop rail on a phone) never requests an ad
        if (ins.offsetParent !== null) io.observe(ins);
      });
    } else {
      slots.forEach(pushAd);
    }
    // With an ad blocker, adsbygoogle.js never runs and the bays would stay as labelled blanks.
    // Fold them away after 8 s; unfold if the script turns up late on a slow connection.
    var root = document.documentElement, waited = 0;
    var watch = setInterval(function () {
      waited += 500;
      if (window.adsbygoogle && window.adsbygoogle.loaded) {
        root.classList.remove("ads-off");
        clearInterval(watch);
        return;
      }
      if (waited >= 8000) root.classList.add("ads-off");
      if (waited >= 30000) clearInterval(watch);
    }, 500);
  }

  /* ---- menu: close on a tap outside it, on Escape, or once a link is chosen ---- */
  var menu = document.querySelector("details.menu");
  if (menu) {
    var summary = menu.querySelector("summary");
    document.addEventListener("click", function (e) { if (menu.open && !menu.contains(e.target)) menu.open = false; });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && menu.open) { menu.open = false; summary.focus(); }
    });
    menu.addEventListener("click", function (e) { if (e.target.closest(".menu-panel a")) menu.open = false; });

    // Dark / Light, remembered on this device (the choice is applied before first paint by the head script)
    var themeBox = menu.querySelector(".menu-theme");
    var themeMeta = document.querySelector('meta[name="theme-color"]');
    var paintTheme = function (theme) {
      if (theme === "light") document.documentElement.setAttribute("data-theme", "light");
      else document.documentElement.removeAttribute("data-theme");
      if (themeMeta) themeMeta.setAttribute("content", theme === "light" ? "#F3F5F9" : "#0B1322");
      themeBox.querySelectorAll("[data-theme-choice]").forEach(function (b) {
        b.setAttribute("aria-pressed", b.getAttribute("data-theme-choice") === theme ? "true" : "false");
      });
    };
    if (themeBox) {
      themeBox.hidden = false;
      paintTheme(document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark");
      themeBox.addEventListener("click", function (e) {
        var b = e.target.closest("[data-theme-choice]");
        if (!b) return;
        var theme = b.getAttribute("data-theme-choice");
        paintTheme(theme);
        try { localStorage.setItem("sd-theme", theme); } catch (err) {}
        try { document.dispatchEvent(new CustomEvent("sd-theme", { detail: theme })); } catch (err) {}
      });
    }
  }

  /* ---- share: the phone's own share sheet where there is one, otherwise the short list ---- */
  var touch = window.matchMedia && window.matchMedia("(pointer: coarse)").matches;
  document.querySelectorAll("details[data-share]").forEach(function (box) {
    var url = box.getAttribute("data-url"), summary = box.querySelector("summary");
    if (touch && navigator.share) {
      summary.addEventListener("click", function (e) {
        e.preventDefault();
        navigator.share({ title: box.getAttribute("data-title"), text: box.getAttribute("data-text"), url: url })
          .catch(function (err) { if (!err || err.name !== "AbortError") box.open = true; });  // no sheet: show the list
      });
    }
    var copy = box.querySelector("[data-share-copy]");
    if (copy && navigator.clipboard && window.isSecureContext) {
      copy.hidden = false;
      copy.addEventListener("click", function () {
        navigator.clipboard.writeText(url).then(function () {
          copy.textContent = "Link copied";
          setTimeout(function () { copy.textContent = "Copy link"; box.open = false; }, 1400);
        }, function () { copy.textContent = "Copy failed"; });
      });
    }
    document.addEventListener("click", function (e) { if (box.open && !box.contains(e.target)) box.open = false; });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && box.open) { box.open = false; summary.focus(); }
    });
    box.addEventListener("click", function (e) { if (e.target.closest(".share-panel a")) box.open = false; });
  });

  /* ---- 3. privacy choices (Google's consent message, EEA/UK/CH visitors) ---- */
  var choices = document.querySelector("[data-privacy-choices]");
  if (choices) {
    window.googlefc = window.googlefc || {};
    window.googlefc.callbackQueue = window.googlefc.callbackQueue || [];
    window.googlefc.callbackQueue.push({
      CONSENT_API_READY: function () {
        choices.hidden = false;
        choices.addEventListener("click", function () {
          if (window.googlefc && window.googlefc.showRevocationMessage) window.googlefc.showRevocationMessage();
        });
      }
    });
  }
})();
