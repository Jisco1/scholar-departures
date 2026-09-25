(function () {
  "use strict";

  var RM = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var FINE = window.matchMedia("(hover: hover) and (pointer: fine)").matches;

  var SCHOOLS = window.SCHOLAR_DATA || [];
  var DIR = window.SCHOLAR_SCHOOLS || [];
  var HORIZON_DAYS = 120;
  var PER_PAGE_ALL = 6;       // routes per page with no filter on
  var PER_PAGE_FILTERED = 3;  // routes per page once any filter is on
  var PER_PAGE_DIR = 10;      // schools per page in the directory
  var STORAGE_KEY = "scholar-departures:saved";

  /* Premium settings come from site.json at build time. With no Gumroad
     product configured, premium is off and every tool is free. There is no
     admin key or demo key in the page: the real admin is the GitHub repo. */
  var CONFIG = (function () {
    try { return JSON.parse(document.getElementById("sd-config").textContent); }
    catch (e) { return {}; }
  })();
  var PREMIUM = CONFIG.premium || {};
  var PREMIUM_ENABLED = !!(PREMIUM.gumroadProductId && PREMIUM.purchaseUrl);
  var LIC_KEY = "scholar-departures:license";
  var CHARSET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789·—≈ ";

  var TYPE_COLORS = {
    "Tuition-Free": "#7FD1AE",
    "Full Scholarship": "#B3A1E8",
    "Need-Based Aid": "#7EB8E8",
    "Merit": "#E3C892",
    "Low Tuition": "#6FC7C0",
    "Fee Waiver": "#E8919E"
  };
  var REGIONS = ["All", "Europe", "USA", "Canada", "Global"];
  var LEVELS = ["All levels", "Bachelor's", "Master's", "PhD"];
  var TYPES = ["All funding"].concat(Object.keys(TYPE_COLORS));
  var DFILTERS = ["All deadlines", "Approaching", "Closed"];
  var KINDS = ["All kinds", "Universities", "Programmes"];
  var FIELDS = ["Engineering & Tech", "Computer Science", "Natural Sciences", "Medicine & Health",
                "Business & Economics", "Social Sciences & Law", "Arts & Humanities", "Agriculture & Environment"];
  var FIELD_ICON = {
    "Engineering & Tech": "\u2699", "Computer Science": "\u2328", "Natural Sciences": "\u269B",
    "Medicine & Health": "\u2695", "Business & Economics": "\u25F4", "Social Sciences & Law": "\u2696",
    "Arts & Humanities": "\u270E", "Agriculture & Environment": "\u2698"
  };

  /* ============ data + status ============ */
  var now = new Date();
  var t0 = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  function computeStatus(deadlines) {
    var next = null;
    (deadlines || []).forEach(function (dl) {
      var d = new Date(t0.getFullYear(), dl.m - 1, dl.d);
      if (d < t0) d = new Date(t0.getFullYear() + 1, dl.m - 1, dl.d);
      if (!next || d < next) next = d;
    });
    if (!next) return { daysUntil: 9999, status: "closed", nextDate: null };
    var days = Math.round((next - t0) / 86400000);
    return { daysUntil: days, status: days <= HORIZON_DAYS ? "approaching" : "closed", nextDate: next };
  }
  var enriched = SCHOOLS.map(function (s) {
    return Object.assign({}, s, s.rolling
      ? { daysUntil: 9999, status: "rolling", nextDate: null }
      : computeStatus(s.deadlines));
  });
  var approaching = enriched.filter(function (s) { return s.status === "approaching"; })
                            .sort(function (a, b) { return a.daysUntil - b.daysUntil; });

  /* ============ state ============ */
  var state = {
    q: "", region: "All", level: "All levels", type: "All funding",
    dstatus: "All deadlines", kind: "All kinds", field: "", savedOnly: false, saved: loadSaved(), page: 1
  };
  var lastToggled = null;
  var lastFilterKey = "";

  function loadSaved() {
    try {
      var raw = localStorage.getItem(STORAGE_KEY);
      var arr = raw ? JSON.parse(raw) : [];
      return new Set(Array.isArray(arr) ? arr : []);
    } catch (e) { return new Set(); }
  }
  function persistSaved() {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(Array.from(state.saved))); } catch (e) {}
  }

  /* ============ helpers ============ */
  function esc(str) {
    return String(str == null ? "" : str).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  /* Only https links may reach an href: the data file is updated by a bot. */
  function safeUrl(u) {
    return /^https:\/\/[^\s"'<>]+$/i.test(String(u || "")) ? String(u) : "#";
  }
  /* Which slice of a list a page shows; an out-of-range page is pulled back in. */
  function paginate(total, page, perPage) {
    var pages = Math.max(1, Math.ceil(total / perPage));
    var p = Math.min(Math.max(1, page | 0), pages);
    var start = (p - 1) * perPage;
    return { page: p, pages: pages, start: start, end: Math.min(start + perPage, total) };
  }
  /* "7–12", or just "13" when a page holds one item, or "0" when nothing matches. */
  function rangeText(pg) {
    if (pg.end === 0) return "0";
    return pg.end - pg.start === 1 ? String(pg.end) : (pg.start + 1) + "–" + pg.end;
  }
  /* Page buttons to draw: first, last and the current page's neighbours, with
     "gap" where pages are skipped (a single skipped page is shown instead). */
  function pageList(page, pages) {
    var want = [1, page - 1, page, page + 1, pages].filter(function (n, i, a) {
      return n >= 1 && n <= pages && a.indexOf(n) === i;
    }).sort(function (a, b) { return a - b; });
    var out = [];
    want.forEach(function (n, i) {
      var prev = want[i - 1];
      if (i && n - prev === 2) out.push(n - 1);
      else if (i && n - prev > 2) out.push("gap");
      out.push(n);
    });
    return out;
  }
  function fmtDate(d, withYear) {
    var opts = { month: "short", day: "numeric" };
    if (withYear) opts.year = "numeric";
    return d.toLocaleDateString("en-US", opts);
  }
  function fmtVerified(iso) {
    if (!iso) return "";
    var p = iso.split("-");
    return fmtDate(new Date(+p[0], +p[1] - 1, +p[2]), true);
  }

  var I = {
    cal: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></svg>',
    cap: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 10v6M2 10l10-5 10 5-10 5z"/><path d="M6 12v5c3 3 9 3 12 0v-5"/></svg>',
    check: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>',
    ext: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/></svg>',
    bm: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m19 21-7-4-7 4V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v16z"/></svg>',
    bmFill: '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="m19 21-7-4-7 4V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v16z"/></svg>',
    lock: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>',
    zap: '<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><path d="M13 2 3 14h7l-1 8 11-13h-8l1-7z"/></svg>',
    calplus: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18M12 14v4M10 16h4"/></svg>',
    cols: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M12 3v18"/></svg>',
    x: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18M6 6l12 12"/></svg>'
  };

  /* ============ split-flap engine ============ */
  function flap(el, finalText, baseDelay) {
    el.textContent = "";
    var chars = finalText.split("");
    chars.forEach(function (ch, i) {
      var s = document.createElement("span");
      s.className = "fchar";
      if (ch === " ") { s.classList.add("sp"); s.innerHTML = "&nbsp;"; el.appendChild(s); return; }
      s.textContent = "\u00A0";
      el.appendChild(s);
      if (RM) { s.textContent = ch; s.classList.add("set"); return; }
      var ticks = 3 + Math.floor(i * 0.45) + Math.floor(Math.random() * 4);
      var n = 0;
      setTimeout(function cycle() {
        n++;
        if (n >= ticks) { s.textContent = ch; s.classList.add("set"); return; }
        s.textContent = CHARSET[Math.floor(Math.random() * CHARSET.length)];
        s.classList.add("tick");
        requestAnimationFrame(function () { s.classList.remove("tick"); });
        setTimeout(cycle, 42);
      }, baseDelay + i * 26);
    });
  }

  /* ============ kinetic headline ============ */
  (function splitTitle() {
    var h = document.getElementById("title");
    var words = h.textContent.split(" ");
    h.textContent = "";
    var idx = 0;
    words.forEach(function (word, wi) {
      var w = document.createElement("span"); w.className = "w";
      word.split("").forEach(function (ch) {
        var l = document.createElement("span"); l.className = "l";
        l.style.setProperty("--i", idx++);
        l.textContent = ch;
        w.appendChild(l);
      });
      h.appendChild(w);
      if (wi < words.length - 1) h.appendChild(document.createTextNode(" "));
    });
  })();

  /* ============ preloader ============ */
  var pre = document.getElementById("preloader");
  function liftPreloader() {
    if (!pre || pre.classList.contains("lift")) return;
    pre.classList.add("lift");
    document.body.classList.add("loaded");
    setTimeout(function () { pre.remove(); }, 900);
  }
  if (RM) {
    if (pre) pre.remove();
    document.body.classList.add("loaded");
  } else {
    flap(document.getElementById("preBoard"), "SCHOLAR DEPARTURES", 120);
    setTimeout(liftPreloader, 1650);
    pre.addEventListener("click", liftPreloader);
  }

  /* ============ progress + scroll flag ============ */
    var progress = document.getElementById("progress");
  window.addEventListener("scroll", function () {
    var h = document.documentElement;
    var max = h.scrollHeight - h.clientHeight;
    progress.style.width = (max > 0 ? (h.scrollTop / max) * 100 : 0) + "%";
    if (h.scrollTop > 40) document.body.classList.add("scrolled");
  }, { passive: true });

  /* ============ hero canvas: night flight map ============ */
  (function flightMap() {
    var canvas = document.getElementById("fx");
    var ctx = canvas.getContext("2d");
    var W = 0, H = 0, DPR = Math.min(window.devicePixelRatio || 1, 2);
    var stars = [];
    var HUB = { fx: 0.14, fy: 0.82 };
    var DESTS = [
      { fx: 0.46, fy: 0.18 }, { fx: 0.60, fy: 0.30 }, { fx: 0.74, fy: 0.16 },
      { fx: 0.86, fy: 0.34 }, { fx: 0.30, fy: 0.30 }
    ];
    var routeIdx = 0, prog = 0, SPEED = 0.0042, dashShift = 0, pulse = 0;
    var running = false, visible = true;

    function resize() {
      var r = canvas.parentElement.getBoundingClientRect();
      W = r.width; H = r.height;
      canvas.width = W * DPR; canvas.height = H * DPR;
      canvas.style.width = W + "px"; canvas.style.height = H + "px";
      ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
      stars = [];
      var n = Math.floor((W * H) / 11000);
      for (var i = 0; i < n; i++) {
        stars.push({ x: Math.random() * W, y: Math.random() * H, r: Math.random() * 1.3 + 0.3,
                     ph: Math.random() * Math.PI * 2, sp: 0.4 + Math.random() * 0.9 });
      }
    }
    function pt(p) { return { x: p.fx * W, y: p.fy * H }; }
    function ctrl(a, b) {
      return { x: (a.x + b.x) / 2 + (b.y - a.y) * 0.22, y: (a.y + b.y) / 2 - Math.abs(b.x - a.x) * 0.28 };
    }
    function bez(a, c, b, t) {
      var u = 1 - t;
      return { x: u * u * a.x + 2 * u * t * c.x + t * t * b.x,
               y: u * u * a.y + 2 * u * t * c.y + t * t * b.y };
    }
    function ease(t) { return t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2; }

    function draw(t) {
      ctx.clearRect(0, 0, W, H);
      // glow wash
      var g = ctx.createRadialGradient(W / 2, -H * 0.1, 0, W / 2, -H * 0.1, H * 1.1);
      g.addColorStop(0, "rgba(21,35,61,0.9)"); g.addColorStop(1, "rgba(11,19,34,0)");
      ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
      // stars
      stars.forEach(function (s) {
        var a = 0.25 + 0.45 * (0.5 + 0.5 * Math.sin(t * 0.001 * s.sp + s.ph));
        ctx.fillStyle = "rgba(164,180,204," + a.toFixed(3) + ")";
        ctx.beginPath(); ctx.arc(s.x, s.y, s.r, 0, 7); ctx.fill();
      });
      var hub = pt(HUB);
      // routes
      dashShift -= 0.25;
      DESTS.forEach(function (D, i) {
        var d = pt(D), c = ctrl(hub, d);
        ctx.strokeStyle = i === routeIdx ? "rgba(215,169,76,0.5)" : "rgba(215,169,76,0.16)";
        ctx.lineWidth = 1;
        ctx.setLineDash([3, 7]); ctx.lineDashOffset = dashShift;
        ctx.beginPath(); ctx.moveTo(hub.x, hub.y);
        ctx.quadraticCurveTo(c.x, c.y, d.x, d.y); ctx.stroke();
        ctx.setLineDash([]);
        // destination marker
        ctx.fillStyle = "rgba(126,184,232,0.8)";
        ctx.beginPath(); ctx.arc(d.x, d.y, 2.4, 0, 7); ctx.fill();
        if (i === routeIdx && pulse > 0) {
          ctx.strokeStyle = "rgba(215,169,76," + (pulse * 0.6).toFixed(3) + ")";
          ctx.beginPath(); ctx.arc(d.x, d.y, 4 + (1 - pulse) * 16, 0, 7); ctx.stroke();
        }
      });
      if (pulse > 0) pulse -= 0.025;
      // hub
      ctx.fillStyle = "rgba(215,169,76,0.95)";
      ctx.beginPath(); ctx.arc(hub.x, hub.y, 3.2, 0, 7); ctx.fill();
      ctx.font = "9px ui-monospace, Menlo, monospace";
      ctx.fillStyle = "rgba(110,128,160,0.9)";
      ctx.fillText("GLOBAL", hub.x + 8, hub.y + 3);
      // plane along current route
      var D2 = pt(DESTS[routeIdx]), c2 = ctrl(hub, D2);
      prog += SPEED;
      if (prog >= 1) { prog = 0; pulse = 1; routeIdx = (routeIdx + 1) % DESTS.length; }
      var te = ease(Math.min(prog, 1));
      var p = bez(hub, c2, D2, te);
      var p2 = bez(hub, c2, D2, Math.min(te + 0.015, 1));
      var ang = Math.atan2(p2.y - p.y, p2.x - p.x);
      // trail
      for (var k = 1; k <= 9; k++) {
        var tp = bez(hub, c2, D2, Math.max(te - k * 0.012, 0));
        ctx.fillStyle = "rgba(215,169,76," + (0.32 - k * 0.033).toFixed(3) + ")";
        ctx.beginPath(); ctx.arc(tp.x, tp.y, 1.4, 0, 7); ctx.fill();
      }
      // paper plane
      ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(ang);
      ctx.fillStyle = "#D7A94C";
      ctx.beginPath(); ctx.moveTo(7, 0); ctx.lineTo(-5, 4); ctx.lineTo(-2, 0); ctx.lineTo(-5, -4); ctx.closePath(); ctx.fill();
      ctx.restore();
    }
    function frame(t) {
      if (!running) return;
      if (visible && !document.hidden) draw(t);
      requestAnimationFrame(frame);
    }
    resize();
    window.addEventListener("resize", resize, { passive: true });
    if (RM) { draw(0); return; }  // static frame only
    new IntersectionObserver(function (en) { visible = en[0].isIntersecting; }).observe(canvas);
    running = true; requestAnimationFrame(frame);
  })();

  /* ============ count-up stats ============ */
  function countUp(el, target) {
    if (RM) { el.textContent = target; return; }
    var start = null, DUR = 1200;
    function step(ts) {
      if (!start) start = ts;
      var p = Math.min((ts - start) / DUR, 1);
      el.textContent = Math.round(target * (1 - Math.pow(1 - p, 3)));
      if (p < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }
  var countriesN = new Set(SCHOOLS.map(function (s) { return s.country; })).size;
  setTimeout(function () {
    countUp(document.getElementById("statRoutes"), SCHOOLS.length);
    countUp(document.getElementById("statCountries"), countriesN);
    countUp(document.getElementById("statApproaching"), approaching.length);
    countUp(document.getElementById("statSchools"), DIR.length);
  }, RM ? 0 : 1900);

  /* ============ departures board ============ */
  var clockEl = document.getElementById("clock");
  function tickClock() {
    var d = new Date();
    var days = ["SUN","MON","TUE","WED","THU","FRI","SAT"];
    var mons = ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"];
    var hh = String(d.getHours()).padStart(2, "0"), mm = String(d.getMinutes()).padStart(2, "0");
    clockEl.textContent = days[d.getDay()] + " " + d.getDate() + " " + mons[d.getMonth()] + " — " + hh + ":" + mm;
  }
  tickClock(); setInterval(tickClock, 15000);

  var boardBody = document.getElementById("boardBody");
  var boardData = approaching.length ? approaching : null;
  function buildBoard() {
    boardBody.innerHTML = "";
    if (!boardData) {
      var r0 = document.createElement("div");
      r0.className = "brow";
      r0.innerHTML = "<span>NO DEPARTURES — ALL WINDOWS CLOSED</span><span></span><span></span><span></span>";
      boardBody.appendChild(r0);
      return [];
    }
    return boardData.map(function (s) {
      var row = document.createElement("div");
      row.className = "brow r";
      row.setAttribute("role", "button");
      row.setAttribute("tabindex", "0");
      ["route", "country", "closes", "days"].forEach(function (cls) {
        var c = document.createElement("span"); c.className = cls; row.appendChild(c);
      });
      function go() {
        state.q = s.name;
        document.getElementById("q").value = s.name;
        render();
        document.getElementById("controls").scrollIntoView({ behavior: RM ? "auto" : "smooth", block: "start" });
      }
      row.addEventListener("click", go);
      row.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } });
      boardBody.appendChild(row);
      return { row: row, s: s };
    });
  }
  var boardRows = buildBoard();
  var boardFlipped = false;
  function flipBoard() {
    if (boardFlipped) return;
    boardFlipped = true;
    boardRows.forEach(function (item, r) {
      var s = item.s, base = r * 130;
      flap(item.row.querySelector(".route"), s.name.toUpperCase().slice(0, 26), base);
      flap(item.row.querySelector(".country"), s.country.toUpperCase().slice(0, 12), base + 60);
      flap(item.row.querySelector(".closes"), (s.approx ? "≈" : "") + fmtDate(s.nextDate, false).toUpperCase(), base + 120);
      flap(item.row.querySelector(".days"), s.daysUntil + "D", base + 170);
    });
  }

  /* ============ scroll reveals ============ */
  var revealIO = new IntersectionObserver(function (entries) {
    entries.forEach(function (en) {
      if (!en.isIntersecting) return;
      en.target.classList.add("in");
      if (en.target.classList.contains("board")) flipBoard();
      if (en.target.classList.contains("card")) {
        en.target.addEventListener("transitionend", function te(e) {
          if (e.propertyName === "transform") { en.target.classList.add("ready"); en.target.removeEventListener("transitionend", te); }
        });
        if (RM) en.target.classList.add("ready");
      }
      revealIO.unobserve(en.target);
    });
  }, { threshold: 0.12, rootMargin: "0px 0px -4% 0px" });

  [".depart-head", ".board", ".section-tag", ".fieldbar", ".controls", ".home-notes"].forEach(function (sel) {
    document.querySelectorAll(sel).forEach(function (el) { revealIO.observe(el); });
  });

  /* ============ controls ============ */
  function buildSeg(el, options, key, statusClasses) {
    el.innerHTML = "";
    options.forEach(function (opt) {
      var b = document.createElement("button");
      b.textContent = opt;
      if (statusClasses) {
        if (opt === "Approaching") b.classList.add("st-approaching");
        if (opt === "Closed") b.classList.add("st-closed");
      }
      b.addEventListener("click", function () { state[key] = opt; render(); });
      el.appendChild(b);
    });
  }
  buildSeg(document.getElementById("regionSeg"), REGIONS, "region", false);
  buildSeg(document.getElementById("deadlineSeg"), DFILTERS, "dstatus", true);
  buildSeg(document.getElementById("kindSeg"), KINDS, "kind", false);
  (function buildFieldChips() {
    var wrap = document.getElementById("fieldChips");
    var opts = [["", "All fields", "\u2732"]].concat(FIELDS.map(function (f) { return [f, f, FIELD_ICON[f]]; }));
    opts.forEach(function (o) {
      var b = document.createElement("button");
      b.className = "fchip";
      b.dataset.field = o[0];
      b.innerHTML = '<span class="fi">' + o[2] + "</span>" + o[1];
      b.addEventListener("click", function () { state.field = state.field === o[0] ? "" : o[0]; render(); });
      wrap.appendChild(b);
    });
  })();

  var levelSel = document.getElementById("levelSel");
  LEVELS.forEach(function (l) { var o = document.createElement("option"); o.textContent = l; levelSel.appendChild(o); });
  levelSel.addEventListener("change", function () { state.level = levelSel.value; render(); });

  var typeWrap = document.getElementById("typePills");
  TYPES.forEach(function (t) {
    var b = document.createElement("button");
    b.className = "pill"; b.textContent = t; b.dataset.type = t;
    b.addEventListener("click", function () { state.type = t; render(); });
    typeWrap.appendChild(b);
  });

  document.getElementById("q").addEventListener("input", function (e) { state.q = e.target.value; render(); });
  document.getElementById("savedBtn").addEventListener("click", function () { state.savedOnly = !state.savedOnly; render(); });
  document.getElementById("clearBtn").addEventListener("click", clearAll);

  function clearAll() {
    state.q = ""; state.region = "All"; state.level = "All levels";
    state.type = "All funding"; state.dstatus = "All deadlines"; state.kind = "All kinds"; state.field = ""; state.savedOnly = false;
    document.getElementById("q").value = "";
    levelSel.value = "All levels";
    render();
  }

  document.getElementById("subline").textContent =
    SCHOOLS.length + " universities and programmes where tuition is free, fully funded, or fee-waived for international students. Every route has a plain-English page, and every button goes to the official source.";

  /* ============ cards ============ */
  function cardHTML(s, idx) {
    var isSaved = state.saved.has(s.name);
    var chipText = s.tbc
      ? "Next call not yet announced"
      : s.status === "rolling"
      ? "Rolling · open year-round"
      : s.status === "approaching"
      ? "Due " + (s.approx ? "≈ " : "") + "in " + s.daysUntil + " day" + (s.daysUntil === 1 ? "" : "s")
      : "Closed · next " + (s.approx ? "≈ " : "") + (s.nextDate ? fmtDate(s.nextDate, true) : "—");
    var stamps = (s.types || []).map(function (t, i) {
      var col = TYPE_COLORS[t] || "#D7A94C";
      var rot = (i % 2 ? 1.6 : -1.6) + "deg";
      return '<span class="stamp" style="color:' + col + ";border-color:" + col + ";--rot:" + rot + ";--si:" + i + '">' + esc(t) + "</span>";
    }).join("");
    var d = Math.min(idx, 9) * 45;
    var pop = lastToggled === s.name ? " pop" : "";
    return '<article class="card' + (isSaved ? " saved" : "") + '" style="--d:' + d + 'ms" data-name="' + esc(s.name) + '">' +
      '<div class="card-top"><div class="country">' + esc(s.flag) + " " + esc(s.country) + " · " + (s.kind === "program" ? "PROGRAMME" : "UNIVERSITY") + "</div>" +
      '<div class="stamps">' + stamps + "</div></div>" +
      '<h3><a class="card-title" href="routes/' + esc(s.slug) + '/">' + esc(s.name) + "</a></h3>" +
      '<p class="funding">' + esc(s.funding) + "</p>" +
      '<a class="more-link" href="routes/' + esc(s.slug) + '/">Eligibility, costs &amp; how to apply <span aria-hidden="true">\u2192</span></a>' +
      '<div class="card-bottom"><div class="meta">' +
      '<span class="chip ' + s.status + '">' + esc(chipText) + "</span>" +
      '<span class="line">' + I.cal + " " + esc(s.deadline) + "</span>" +
      '<span class="line">' + I.cap + " " + esc((s.levels || []).join(" · ")) + "</span>" +
      '<span class="line fieldline"><span class="fscope' + (s.scope === "limited" ? " lim" : "") + '">' +
        (s.scope === "limited" ? "Specific fields" : "All fields") + "</span> " +
        esc((s.fields || []).slice(0, 3).map(function (f) { return f.split(" ")[0].replace("&", ""); }).join(" · ")) + "</span>" +
      (s.last_verified ? '<span class="line">' + I.check + " Verified " + esc(fmtVerified(s.last_verified)) + "</span>" : "") +
      "</div>" +
      '<div class="actions">' +
      '<button class="planbtn" data-plan="' + esc(s.name) + '" title="Generate an application timeline" aria-label="Application plan">' + I.calplus + '<span class="lockdot">' + I.lock + "</span></button>" +
      '<button class="bookmark' + (isSaved ? " saved" : "") + pop + '" data-save="' + esc(s.name) + '" aria-label="' + (isSaved ? "Remove from shortlist" : "Save to shortlist") + '">' + (isSaved ? I.bmFill : I.bm) + "</button>" +
      '<a class="apply" href="' + esc(safeUrl(s.link)) + '" target="_blank" rel="noopener noreferrer">Official page ' + I.ext + "</a>" +
      "</div></div></article>";
  }

  /* ============ render ============ */
  function render() {
    var needle = state.q.trim().toLowerCase();
    var out = enriched.filter(function (s) {
      var hay = [s.name, s.country, s.region, s.funding, (s.types || []).join(" "), (s.levels || []).join(" "), (s.fields || []).join(" "), s.kind === "program" ? "programme scholarship award" : "university school institution"].join(" ").toLowerCase();
      if (needle && hay.indexOf(needle) === -1) return false;
      if (state.region !== "All" && s.region !== state.region) return false;
      if (state.level !== "All levels" && (s.levels || []).indexOf(state.level) === -1) return false;
      if (state.type !== "All funding" && (s.types || []).indexOf(state.type) === -1) return false;
      if (state.field && (s.fields || []).indexOf(state.field) === -1) return false;
      if (state.kind === "Universities" && s.kind === "program") return false;
      if (state.kind === "Programmes" && s.kind !== "program") return false;
      if (state.dstatus === "Approaching" && s.status !== "approaching" && s.status !== "rolling") return false;
      if (state.dstatus === "Closed" && s.status !== "closed") return false;
      if (state.savedOnly && !state.saved.has(s.name)) return false;
      return true;
    });
    if (state.field) {
      out.sort(function (a, b) {
        var sa = a.scope === "limited" ? 0 : 1, sb = b.scope === "limited" ? 0 : 1;
        return sa !== sb ? sa - sb : a.daysUntil - b.daysUntil;
      });
    } else if (state.dstatus !== "All deadlines") {
      out.sort(function (a, b) { return a.daysUntil - b.daysUntil; });
    }

    var hasFilters = state.q || state.region !== "All" || state.level !== "All levels" ||
      state.type !== "All funding" || state.dstatus !== "All deadlines" || state.kind !== "All kinds" || state.field || state.savedOnly;
    var grouped = state.kind === "All kinds" && state.dstatus === "All deadlines" && !state.field;
    if (grouped) {
      out = out.filter(function (s) { return s.kind !== "program"; })
               .concat(out.filter(function (s) { return s.kind === "program"; }));
    }
    // a new filter starts again at page 1; saving or unsaving a route keeps your page
    var filterKey = [state.q.trim().toLowerCase(), state.region, state.level, state.type,
                     state.dstatus, state.kind, state.field, state.savedOnly].join("|");
    if (filterKey !== lastFilterKey) { state.page = 1; lastFilterKey = filterKey; }
    var pg = paginate(out.length, state.page, hasFilters ? PER_PAGE_FILTERED : PER_PAGE_ALL);
    state.page = pg.page;
    var shown = out.slice(pg.start, pg.end);

    var grid = document.getElementById("grid");
    if (out.length === 0) {
      var msg = (state.savedOnly && state.saved.size === 0)
        ? "Nothing saved yet — tap the bookmark on any card to build your shortlist."
        : (state.field ? "No " + state.field + " routes match the other filters — try clearing one." : "No matches yet. Clear a filter, or try a country like “Germany” or a word like “tuition”.");
      grid.innerHTML = '<div class="empty"><p>' + msg + '</p><button id="emptyClear">Clear all filters</button></div>';
      document.getElementById("emptyClear").addEventListener("click", clearAll);
    } else {
      var html = "", group = null;
      shown.forEach(function (s, i) {
        var g = s.kind === "program" ? "program" : "school";
        if (grouped && g !== group) {
          html += g === "school"
            ? '<div class="grid-sep">Universities — funding from the school itself</div>'
            : '<div class="grid-sep">Funding programmes — awards you take to a school</div>';
          group = g;
        }
        html += cardHTML(s, i);
      });
      grid.innerHTML = html;
      grid.querySelectorAll(".card").forEach(function (c) { revealIO.observe(c); });
    }
    renderPager(document.getElementById("gridPager"), pg, function (p) {
      state.page = p;
      render();
      afterPageTurn("gridPager", document.getElementById("count"));
    });
    lastToggled = null;

    grid.querySelectorAll("[data-save]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var name = btn.getAttribute("data-save");
        if (state.saved.has(name)) state.saved.delete(name); else state.saved.add(name);
        lastToggled = name;
        persistSaved(); render();
      });
    });

    var range = rangeText(pg);
    document.getElementById("count").innerHTML =
      (!out.length
        ? "No matching routes · " + SCHOOLS.length + " funded routes · "
        : hasFilters
        ? "Showing " + range + " of " + out.length + " matching · " + SCHOOLS.length + " funded routes · "
        : "Showing " + range + " of " + SCHOOLS.length + " funded routes · ") + countriesN + " countries · " +
      '<span class="approaching-count">' + approaching.length + " approaching</span>" +
      (state.field ? ' · <span style="color:var(--brass)">' + out.filter(function (x) { return x.scope === "limited"; }).length + " field-specific</span>" : "");

    document.getElementById("clearBtn").hidden = !hasFilters;

    document.querySelectorAll("#regionSeg button").forEach(function (b) { b.classList.toggle("active", b.textContent === state.region); });
    document.querySelectorAll("#deadlineSeg button").forEach(function (b) { b.classList.toggle("active", b.textContent === state.dstatus); });
    document.querySelectorAll("#kindSeg button").forEach(function (b) { b.classList.toggle("active", b.textContent === state.kind); });
    document.querySelectorAll("#fieldChips .fchip").forEach(function (b) {
      b.classList.toggle("active", (b.dataset.field || "") === state.field);
    });
    document.querySelectorAll("#typePills .pill").forEach(function (b) {
      var t = b.dataset.type, active = t === state.type;
      b.classList.toggle("active", active);
      var col = t === "All funding" ? "#D7A94C" : TYPE_COLORS[t];
      b.style.borderColor = active ? col : "";
      b.style.background = active ? col : "";
      b.style.color = active ? "#0B1322" : "";
    });
    var sb = document.getElementById("savedBtn");
    sb.classList.toggle("active", state.savedOnly);
    document.getElementById("savedLabel").textContent = "Saved" + (state.saved.size ? " (" + state.saved.size + ")" : "");
  }

  /* ============ pager ============ */
  function renderPager(el, pg, onGo) {
    if (!el) return;
    if (pg.pages <= 1) { el.innerHTML = ""; el.hidden = true; return; }
    el.hidden = false;
    var html = '<button type="button" class="pg-step" data-go="' + (pg.page - 1) + '"' + (pg.page === 1 ? " disabled" : "") +
      ' aria-label="Previous page">\u2039<span class="pg-word"> Prev</span></button>';
    pageList(pg.page, pg.pages).forEach(function (n) {
      html += n === "gap"
        ? '<span class="pg-gap" aria-hidden="true">\u2026</span>'
        : '<button type="button" class="pg-num" data-go="' + n + '"' + (n === pg.page ? ' aria-current="page"' : "") +
          ' aria-label="Page ' + n + '">' + pad2(n) + "</button>";
    });
    html += '<button type="button" class="pg-step" data-go="' + (pg.page + 1) + '"' + (pg.page === pg.pages ? " disabled" : "") +
      ' aria-label="Next page"><span class="pg-word">Next </span>\u203A</button>' +
      '<span class="pg-status">Page ' + pad2(pg.page) + " of " + pad2(pg.pages) + "</span>";
    el.innerHTML = html;
    el.querySelectorAll("[data-go]").forEach(function (b) {
      b.addEventListener("click", function () { onGo(+b.getAttribute("data-go")); });
    });
  }
  /* After a page turn: bring the top of the list into view and keep keyboard focus on the pager. */
  function afterPageTurn(pagerId, anchor) {
    var cur = document.querySelector("#" + pagerId + " [aria-current]");
    if (cur) cur.focus({ preventScroll: true });
    if (anchor) anchor.scrollIntoView({ behavior: RM ? "auto" : "smooth", block: "start" });
  }

  /* ============ 3D tilt + glare + magnetic (fine pointers only) ============ */
  if (FINE && !RM) {
    var grid = document.getElementById("grid");
    var tiltPending = false;
    grid.addEventListener("mousemove", function (e) {
      var card = e.target.closest(".card");
      if (!card || !card.classList.contains("ready")) return;
      if (tiltPending) return;
      tiltPending = true;
      requestAnimationFrame(function () {
        tiltPending = false;
        var r = card.getBoundingClientRect();
        var px = (e.clientX - r.left) / r.width, py = (e.clientY - r.top) / r.height;
        card.style.transform = "perspective(950px) rotateX(" + ((py - 0.5) * -6).toFixed(2) + "deg) rotateY(" + ((px - 0.5) * 6).toFixed(2) + "deg) translateY(-4px)";
        card.style.setProperty("--gx", (px * 100).toFixed(1) + "%");
        card.style.setProperty("--gy", (py * 100).toFixed(1) + "%");
        // magnetic apply button
        var ap = card.querySelector(".apply");
        if (ap) {
          var ar = ap.getBoundingClientRect();
          var dx = e.clientX - (ar.left + ar.width / 2), dy = e.clientY - (ar.top + ar.height / 2);
          var dist = Math.hypot(dx, dy);
          if (dist < 110) { ap.style.transform = "translate(" + (dx * 0.12).toFixed(1) + "px," + (dy * 0.12).toFixed(1) + "px)"; }
          else ap.style.transform = "";
        }
      });
    }, { passive: true });
    grid.addEventListener("mouseout", function (e) {
      var card = e.target.closest(".card");
      if (!card) return;
      if (card.contains(e.relatedTarget)) return;
      card.style.transform = "";
      var ap = card.querySelector(".apply");
      if (ap) ap.style.transform = "";
    });
  }

  /* ============ schools directory ============ */
  function atlasJump(name) {
    state.q = name;
    document.getElementById("q").value = name;
    render();
    document.getElementById("controls").scrollIntoView({ behavior: RM ? "auto" : "smooth", block: "start" });
  }
  var dirState = { q: "", region: "All", page: 1 };
  var lastDirKey = "";
  var dirSorted = DIR.slice().sort(function (a, b) {
    return a.country === b.country ? a.name.localeCompare(b.name) : a.country.localeCompare(b.country);
  });
  var dirCountries = new Set(DIR.map(function (s) { return s.country; })).size;
  (function buildDirControls() {
    var seg = document.getElementById("dirRegionSeg");
    var present = {};
    DIR.forEach(function (x) { present[x.region] = true; });
    REGIONS.filter(function (r) { return r === "All" || present[r]; }).forEach(function (r) {
      var b = document.createElement("button");
      b.textContent = r;
      b.addEventListener("click", function () { dirState.region = r; renderDir(); });
      seg.appendChild(b);
    });
    document.getElementById("dirQ").addEventListener("input", function (e) { dirState.q = e.target.value; renderDir(); });
  })();
  function dirRowHTML(s, i) {
    var chip = s.atlasName
      ? '<button class="dchip" data-atlas="' + esc(s.atlasName) + '" title="This school has a verified funded route in the atlas above">Funded</button>'
      : "";
    return '<div class="dir-row" style="--d:' + (i % 14) * 25 + 'ms">' +
      '<span class="dname">' + esc(s.flag) + " " + esc(s.name) + "</span>" +
      '<span class="dloc">' + esc(s.city) + " \u00B7 " + esc(s.country) + "</span>" +
      '<span class="dact">' + chip +
      '<a class="dvisit" href="' + esc(safeUrl(s.link)) + '" target="_blank" rel="noopener noreferrer">Visit site ' + I.ext + "</a></span></div>";
  }
  function renderDir() {
    var needle = dirState.q.trim().toLowerCase();
    var out = dirSorted.filter(function (s) {
      if (dirState.region !== "All" && s.region !== dirState.region) return false;
      if (needle) {
        var hay = (s.name + " " + s.country + " " + s.city).toLowerCase();
        if (hay.indexOf(needle) === -1) return false;
      }
      return true;
    });
    var dirKey = dirState.region + "|" + needle;
    if (dirKey !== lastDirKey) { dirState.page = 1; lastDirKey = dirKey; }
    var pg = paginate(out.length, dirState.page, PER_PAGE_DIR);
    dirState.page = pg.page;
    var list = document.getElementById("dirList");
    list.innerHTML = out.length
      ? out.slice(pg.start, pg.end).map(dirRowHTML).join("")
      : '<div class="dir-empty">No schools match — try a country name like \u201CCanada\u201D.</div>';
    list.querySelectorAll(".dir-row").forEach(function (r) { revealIO.observe(r); });
    list.querySelectorAll("[data-atlas]").forEach(function (b) {
      b.addEventListener("click", function () { atlasJump(b.getAttribute("data-atlas")); });
    });
    var range = rangeText(pg);
    document.getElementById("dirCount").textContent = !out.length
      ? "No matching schools \u00B7 " + DIR.length + " schools in the directory"
      : (needle || dirState.region !== "All")
      ? "Showing " + range + " of " + out.length + " matching \u00B7 " + DIR.length + " schools in the directory"
      : "Showing " + range + " of " + DIR.length + " schools \u00B7 " + dirCountries + " countries";
    renderPager(document.getElementById("dirPager"), pg, function (p) {
      dirState.page = p;
      renderDir();
      afterPageTurn("dirPager", document.querySelector("#directory .dir-head"));
    });
    document.querySelectorAll("#dirRegionSeg button").forEach(function (b) {
      b.classList.toggle("active", b.textContent === dirState.region);
    });
  }

  /* ============ premium: license ============ */
  var prem = { licensed: false };
  function premiumOn() { return !PREMIUM_ENABLED || prem.licensed; }

  function toast(msg) {
    var t = document.getElementById("toast");
    t.textContent = msg;
    t.classList.add("show");
    clearTimeout(toast._h);
    toast._h = setTimeout(function () { t.classList.remove("show"); }, 2600);
  }

  /* ----- modal plumbing ----- */
  var modalBack = document.getElementById("modalBack");
  var modalBox = document.getElementById("modalBox");
  function openModal(html) {
    modalBox.innerHTML = html;
    modalBack.classList.add("open");
    modalBack.removeAttribute("hidden");
    var c = modalBox.querySelector(".mclose");
    if (c) c.addEventListener("click", closeModal);
  }
  function closeModal() { modalBack.classList.remove("open"); }
  modalBack.addEventListener("click", function (e) { if (e.target === modalBack) closeModal(); });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") closeModal(); });
  function mhead(title) {
    return '<div class="mhead"><div class="mtitle">' + title + '</div><button class="mclose" aria-label="Close">' + I.x + "</button></div>";
  }

  /* ----- license verification (Gumroad) ----- */
  function applyPremiumUI() {
    document.body.classList.toggle("premium", premiumOn());
    var cmp = document.getElementById("btnCompare");
    var cal = document.getElementById("btnCalendar");
    var lockHtml = premiumOn() ? "" : I.lock + " ";
    cmp.innerHTML = lockHtml + I.cols + " Compare";
    cal.innerHTML = lockHtml + I.calplus + " Calendar";
  }
  function unlockModal(prefillMsg) {
    openModal(
      mhead("Scholar Departures Premium") +
      '<p class="msub">One license, three power tools for serious applicants:</p>' +
      '<div class="featlist">' +
      '<div>' + I.calplus + '<span><b>Calendar sync</b> \u2014 every saved deadline as a calendar file with 30 / 14 / 7 / 1-day reminder alarms built in. One tap into Google Calendar or your phone.</span></div>' +
      '<div>' + I.cols + '<span><b>Compare mode</b> \u2014 your saved routes side by side: funding, levels, status and days left in one table.</span></div>' +
      '<div>' + I.zap + '<span><b>Application timelines</b> \u2014 a week-by-week plan generated backwards from any deadline, exportable to your calendar as scheduled tasks.</span></div>' +
      "</div>" +
      '<div class="licrow"><input id="licInput" type="text" placeholder="Paste your license key" autocomplete="off" maxlength="80" /><button class="btn-brass" id="licGo">Unlock</button></div>' +
      '<div class="licstatus" id="licStatus">' + esc(prefillMsg || "") + "</div>" +
      '<p class="getkey">No key yet? <a href="' + esc(safeUrl(PREMIUM.purchaseUrl)) + '" target="_blank" rel="noopener noreferrer">Get Scholar Departures Premium \u2192</a></p>'
    );
    var input = modalBox.querySelector("#licInput");
    var status = modalBox.querySelector("#licStatus");
    function setStatus(msg, cls) { status.textContent = msg; status.className = "licstatus " + (cls || ""); }
    modalBox.querySelector("#licGo").addEventListener("click", function () {
      var key = input.value.trim();
      if (!key) return setStatus("Enter a key first.", "err");
      setStatus("Checking with Gumroad\u2026");
      verifyLicense(key, function (ok, why) {
        if (ok) {
          prem.licensed = true;
          try { localStorage.setItem(LIC_KEY, key); } catch (e) {}
          applyPremiumUI(); setStatus("Unlocked. Welcome aboard.", "ok");
          setTimeout(closeModal, 800);
        } else setStatus(why || "That key didn\u2019t verify.", "err");
      });
    });
  }
  function verifyLicense(key, cb) {
    fetch("https://api.gumroad.com/v2/licenses/verify", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: "product_id=" + encodeURIComponent(PREMIUM.gumroadProductId) +
            "&license_key=" + encodeURIComponent(key) + "&increment_uses_count=false"
    }).then(function (r) { return r.json(); }).then(function (j) {
      if (j && j.success && j.purchase && !j.purchase.refunded && !j.purchase.chargebacked) cb(true);
      else cb(false, "Key not valid for this product (or refunded).");
    }).catch(function () { cb(false, "Couldn\u2019t reach Gumroad \u2014 check connection and retry."); });
  }
  function requirePremium(fn) {
    return function () { premiumOn() ? fn() : unlockModal(); };
  }

  /* ----- restore state on load ----- */
  (function restorePremium() {
    if (!PREMIUM_ENABLED) return;
    try {
      var lic = localStorage.getItem(LIC_KEY);
      if (!lic) return;
      prem.licensed = true; // grace while we re-verify silently
      verifyLicense(lic, function (ok) {
        if (!ok) { prem.licensed = false; try { localStorage.removeItem(LIC_KEY); } catch (e) {} applyPremiumUI(); }
      });
    } catch (e) {}
  })();

  /* ============ premium feature: ICS calendar export ============ */
  function pad2(n) { return String(n).padStart(2, "0"); }
  function icsEsc(s) { return String(s).replace(/\\/g, "\\\\").replace(/;/g, "\\;").replace(/,/g, "\\,").replace(/\n/g, "\\n"); }
  function icsDate(d) { return d.getFullYear() + pad2(d.getMonth() + 1) + pad2(d.getDate()); }
  function buildICS(events) {
    var stamp = new Date();
    var dtstamp = stamp.getUTCFullYear() + pad2(stamp.getUTCMonth() + 1) + pad2(stamp.getUTCDate()) +
      "T" + pad2(stamp.getUTCHours()) + pad2(stamp.getUTCMinutes()) + pad2(stamp.getUTCSeconds()) + "Z";
    var lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Scholar Departures//EN", "CALSCALE:GREGORIAN"];
    events.forEach(function (ev, i) {
      lines.push("BEGIN:VEVENT",
        "UID:sd-" + Date.now() + "-" + i + "@scholardepartures.com",
        "DTSTAMP:" + dtstamp,
        "DTSTART;VALUE=DATE:" + icsDate(ev.date),
        "SUMMARY:" + icsEsc(ev.title),
        "DESCRIPTION:" + icsEsc(ev.desc || ""));
      if (ev.url && safeUrl(ev.url) !== "#") lines.push("URL:" + ev.url);
      (ev.alarms || []).forEach(function (days) {
        lines.push("BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:" + icsEsc(ev.title), "TRIGGER:-P" + days + "D", "END:VALARM");
      });
      lines.push("END:VEVENT");
    });
    lines.push("END:VCALENDAR");
    return lines.join("\r\n");
  }
  function downloadICS(events, filename) {
    var blob = new Blob([buildICS(events)], { type: "text/calendar;charset=utf-8" });
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 4000);
  }
  function exportSavedICS() {
    var picked = enriched.filter(function (s) { return state.saved.has(s.name) && s.nextDate; });
    if (!picked.length) return toast("Bookmark some routes first \u2014 then export them.");
    downloadICS(picked.map(function (s) {
      return { date: s.nextDate, title: "Deadline: " + s.name,
               desc: s.funding + " \u2014 " + s.deadline + (s.approx ? " (date varies \u2014 confirm on the official page)" : ""),
               url: s.link, alarms: [30, 14, 7, 1] };
    }), "scholar-departures-deadlines.ics");
    toast(picked.length + " deadline" + (picked.length === 1 ? "" : "s") + " exported with reminders.");
  }

  /* ============ premium feature: compare saved ============ */
  function compareModal() {
    var picked = enriched.filter(function (s) { return state.saved.has(s.name); });
    if (picked.length < 2) return toast("Bookmark at least two routes to compare.");
    picked.sort(function (a, b) { return a.daysUntil - b.daysUntil; });
    var rows = picked.map(function (s) {
      return "<tr><td><b>" + esc(s.name) + "</b><br><span style=\"color:var(--faint);font-size:11px\">" + esc(s.flag) + " " + esc(s.country) + " \u00B7 " + (s.kind === "program" ? "Programme" : "University") + "</span></td>" +
        '<td class="cmp-days">' + (s.nextDate ? s.daysUntil + "d" : s.status === "rolling" ? "Rolling" : "TBC") + "</td>" +
        "<td>" + esc(s.deadline) + "</td>" +
        "<td>" + esc((s.types || []).join(", ")) + "</td>" +
        "<td>" + esc((s.levels || []).join(", ")) + "</td>" +
        '<td><a class="btn-ghost" style="padding:5px 10px;font-size:12px" href="' + esc(safeUrl(s.link)) + '" target="_blank" rel="noopener noreferrer">Official page</a></td></tr>';
    }).join("");
    openModal(
      mhead("Compare saved routes") +
      '<p class="msub">Sorted by urgency \u2014 soonest deadline first.</p>' +
      '<div class="cmp-wrap"><table class="cmp-table"><tr><th>Route</th><th>Days</th><th>Typical deadline</th><th>Funding</th><th>Levels</th><th></th></tr>' + rows + "</table></div>"
    );
  }

  /* ============ premium feature: application timeline ============ */
  var PLAN_STEPS = [
    { w: 12, t: "Research the programme in depth \u2014 confirm eligibility, costs and documents" },
    { w: 10, t: "Contact recommenders (for PhDs: email potential supervisors with a short pitch)" },
    { w: 8,  t: "Write the first draft of your SOP / motivation letter" },
    { w: 6,  t: "Order transcripts \u00B7 book or complete language tests" },
    { w: 4,  t: "Second draft \u2014 get feedback from someone who reads critically" },
    { w: 2,  t: "Finalize every document and fill the application form" },
    { w: 1,  t: "Submit \u2014 never on the last day" },
    { w: 0,  t: "Official deadline" }
  ];
  function planModal(name) {
    var s = enriched.find(function (x) { return x.name === name; });
    if (!s) return;
    if (!s.nextDate) return toast(s.tbc
      ? "The next call hasn't been announced yet — check the official page for dates."
      : "Rolling deadline — positions post year-round. Apply when one fits; nothing to count down.");
    var today = new Date(); today.setHours(0, 0, 0, 0);
    var items = PLAN_STEPS.map(function (st) {
      var d = new Date(s.nextDate); d.setDate(d.getDate() - st.w * 7);
      return { date: d, t: st.t, final: st.w === 0, due: st.w !== 0 && d <= today };
    });
    var html = items.map(function (it) {
      return '<div class="plan-item' + (it.final ? " final" : it.due ? " due" : "") + '">' +
        '<span class="plan-date">' + fmtDate(it.date, true) + (it.due ? " \u00B7 NOW" : "") + "</span>" +
        '<span class="plan-dot"></span><span class="plan-text">' + it.t + "</span></div>";
    }).join("");
    openModal(
      mhead("Plan: " + esc(s.name)) +
      '<p class="msub">' + (s.approx ? "\u2248 " : "") + "Built backwards from " + fmtDate(s.nextDate, true) +
      " \u2014 " + s.daysUntil + " days out. Steps marked NOW are already due if you\u2019re starting today.</p>" +
      '<div class="plan-list">' + html + "</div>" +
      '<div class="mfoot"><button class="btn-brass" id="planIcs">' + I.calplus + " Add this plan to my calendar</button>" +
      '<a class="btn-ghost" href="' + esc(safeUrl(s.link)) + '" target="_blank" rel="noopener noreferrer">Official page ' + I.ext + "</a></div>"
    );
    modalBox.querySelector("#planIcs").addEventListener("click", function () {
      downloadICS(items.map(function (it) {
        return { date: it.date, title: (it.final ? "DEADLINE: " : "") + s.name + " \u2014 " + it.t.replace(/\\u00B7/g, "-"),
                 desc: "Application plan step for " + s.name, url: s.link, alarms: it.final ? [7, 1] : [1] };
      }), "plan-" + s.name.toLowerCase().replace(/[^a-z0-9]+/g, "-").slice(0, 40) + ".ics");
      toast("Plan exported \u2014 " + items.length + " calendar entries with reminders.");
    });
  }

  /* ----- wire it up ----- */
  document.getElementById("btnCompare").addEventListener("click", requirePremium(compareModal));
  document.getElementById("btnCalendar").addEventListener("click", requirePremium(exportSavedICS));
  document.getElementById("grid").addEventListener("click", function (e) {
    var b = e.target.closest("[data-plan]");
    if (!b) return;
    var name = b.getAttribute("data-plan");
    premiumOn() ? planModal(name) : unlockModal();
  });
  applyPremiumUI();

  render();
  renderDir();
})();
