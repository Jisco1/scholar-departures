/* DegreeStep: one university's page, school/?s=<slug>, filled in from app/schools.json (the same directory as the
   app). Where it is, the degrees it awards (US universities), our funding guide for it, funded routes and our guide
   for its country, then its official website, then other universities in the same country. */
(function () {
  "use strict";
  var cfg = JSON.parse(document.getElementById("schoolConfig").textContent);
  var ROOT = cfg.root, ROUTES = window.SCHOLAR_DATA || [];
  var REGION_OF = { Europe: "Europe", Asia: "Asia", Africa: "Africa" };  // continent to the board's region filter
  var slug = "";
  try { slug = new URLSearchParams(window.location.search).get("s") || ""; } catch (e) { slug = ""; }

  function $(id) { return document.getElementById(id); }
  function esc(t) {
    return String(t == null ? "" : t).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function safeUrl(u) { return /^https?:\/\/[^\s"<>]+$/i.test(u || "") ? u : "#"; }
  function host(u) { return String(u).replace(/^https?:\/\//i, "").split("/")[0].replace(/^www\./i, ""); }
  function pageOf(s) { return ROOT + "school/?s=" + encodeURIComponent(s.slug); }
  function place(s) { return s.city ? s.city + ", " + s.country : s.country; }
  var WITH_THE = /^(United States|United Kingdom|United Arab Emirates|Netherlands|Philippines|Bahamas|Gambia|Maldives|Czech Republic|Dominican Republic|Central African Republic|Democratic Republic of the Congo|Republic of the Congo|Holy See|Solomon Islands|Marshall Islands|Cayman Islands|British Virgin Islands)$/;
  function inCountry(c) { return (WITH_THE.test(c) ? "the " : "") + c; }  // "in the United States", "in Ghana"
  function show(id, html) { var el = $(id); el.innerHTML = html; el.hidden = false; }
  function cap(t) { return t ? t.charAt(0).toUpperCase() + t.slice(1) : t; }
  function num(n) { return Number(n).toLocaleString("en-US"); }
  var STATES = { AL: "Alabama", AK: "Alaska", AZ: "Arizona", AR: "Arkansas", CA: "California", CO: "Colorado", CT: "Connecticut",
    DE: "Delaware", DC: "Washington, DC", FL: "Florida", GA: "Georgia", HI: "Hawaii", ID: "Idaho", IL: "Illinois", IN: "Indiana",
    IA: "Iowa", KS: "Kansas", KY: "Kentucky", LA: "Louisiana", ME: "Maine", MD: "Maryland", MA: "Massachusetts", MI: "Michigan",
    MN: "Minnesota", MS: "Mississippi", MO: "Missouri", MT: "Montana", NE: "Nebraska", NV: "Nevada", NH: "New Hampshire",
    NJ: "New Jersey", NM: "New Mexico", NY: "New York", NC: "North Carolina", ND: "North Dakota", OH: "Ohio", OK: "Oklahoma",
    OR: "Oregon", PA: "Pennsylvania", RI: "Rhode Island", SC: "South Carolina", SD: "South Dakota", TN: "Tennessee", TX: "Texas",
    UT: "Utah", VT: "Vermont", VA: "Virginia", WA: "Washington", WV: "West Virginia", WI: "Wisconsin", WY: "Wyoming",
    PR: "Puerto Rico", GU: "Guam", VI: "the U.S. Virgin Islands", AS: "American Samoa", MP: "the Northern Mariana Islands",
    FM: "Micronesia", MH: "the Marshall Islands", PW: "Palau" };
  var DEGREE = { a: ["associate degree", "associate degrees"], b: ["bachelor's degree", "bachelor's degrees"], m: ["master's degree", "master's degrees"],
    d: ["research doctorate", "research doctorates"], p: ["professional doctorate (such as an MD or JD)", "professional doctorates (such as MD or JD)"] };

  function notFound() {
    $("schName").textContent = "We couldn’t find that university";
    $("schPlace").innerHTML = 'Its name may have changed. <a href="' + ROOT + 'scholarships/#directory">Search the schools directory</a>.';
  }
  if (!slug || !window.fetch) { notFound(); return; }

  fetch(ROOT + "app/schools.json")
    .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
    .then(render)
    .catch(notFound);

  function levelNames(doc, s) {
    if (!s.offers) return [];
    return ["b", "m", "d"].filter(function (k) { return s.offers[k] && s.offers[k].length; })
      .map(function (k) { return doc.levels[k]; });
  }

  function render(doc) {
    var all = doc.schools || [], s = null;
    for (var i = 0; i < all.length; i++) { if (all[i].slug === slug) { s = all[i]; break; } }
    if (!s) { notFound(); return; }
    var levels = levelNames(doc, s);

    document.title = s.name + " · DegreeStep";
    var crumb = document.querySelector(".crumbs span:last-child");
    if (crumb) crumb.textContent = s.name;
    $("schEyebrow").textContent = (s.flag ? s.flag + " " : "") + s.country + " · " + s.continent;
    $("schName").textContent = s.name;
    $("schPlace").textContent = place(s) + (levels.length ? ". Degrees: " + levels.join(", ") + "." : ".");
    share(s, levels);

    // our funding guide for this school
    var own = null;
    ROUTES.forEach(function (r) { if (r.slug === s.route) own = r; });
    if (own) {
      show("schGuide", "<h2>Funding guide</h2>" +
        '<a class="school-card" href="' + ROOT + "routes/" + encodeURIComponent(own.slug) + '/">' +
        '<span class="school-card-k">' + esc((own.types || []).join(" · ")) + "</span>" +
        "<strong>" + esc(own.name) + "</strong><span>" + esc(own.funding) + "</span>" +
        '<span class="school-card-go">Read the guide →</span></a>');
    }

    // degrees awarded (US universities, from IPEDS)
    if (s.offers && doc.fields) {
      var rows = ["b", "m", "d"].filter(function (k) { return s.offers[k] && s.offers[k].length; }).map(function (k) {
        return "<h3>" + esc(doc.levels[k]) + "</h3><p>" + esc(s.offers[k].map(function (f) { return doc.fields[f]; })
          .filter(Boolean).join(", ")) + "</p>";
      }).join("");
      show("schDegrees", "<h2>Degrees awarded</h2>" + rows +
        '<p class="school-src">Subjects in which it awarded degrees in 2022-23, from U.S. Department of Education data. ' +
        "Check the university’s own site for current programmes.</p>");
    }

    // funded routes and our guide for its country
    var routes = ROUTES.filter(function (r) { return r.country === s.country && r.slug !== s.route; });
    var guide = cfg.countryPages[s.country];
    var region = s.country === "United States" ? "USA" : s.country === "Canada" ? "Canada" : REGION_OF[s.continent];
    var html = "<h2>Funding for study in " + esc(inCountry(s.country)) + "</h2>";
    if (routes.length) {
      html += '<ul class="school-routes">' + routes.slice(0, 8).map(function (r) {
        return '<li><a href="' + ROOT + "routes/" + encodeURIComponent(r.slug) + '/">' + esc(r.name) + "</a>" +
          '<span class="school-route-meta">' + esc((r.types || []).join(" · ")) +
          (r.deadline ? " · " + esc(r.deadline) : "") + "</span></li>";
      }).join("") + "</ul>";
    } else {
      html += "<p>We don’t list funded routes in " + esc(inCountry(s.country)) + " yet.</p>";
    }
    var links = [];
    if (guide) links.push('<a href="' + ROOT + "countries/" + guide + '/">Our guide to studying in ' + esc(inCountry(s.country)) + " →</a>");
    if (region) links.push('<a href="' + ROOT + "scholarships/?region=" + encodeURIComponent(region) + '">Funded routes across ' +
      esc(region === "USA" ? "the United States" : region) + " →</a>");
    else links.push('<a href="' + ROOT + 'scholarships/">The scholarship board →</a>');
    show("schFunding", html + '<p class="school-links">' + links.join("") + "</p>");
    $("schApply").hidden = false;

    // the essentials of studying in its country, from our country guide
    var facts = (cfg.countryFacts || {})[s.country];
    if (facts && facts.length) {
      show("schFacts", "<h2>Studying in " + esc(inCountry(s.country)) + ": the essentials</h2>" +
        '<div class="table-wrap"><table>' + facts.map(function (f) {
          return "<tr><th scope=\"row\">" + esc(f[0]) + "</th><td>" + esc(f[1]) + "</td></tr>";
        }).join("") + "</table></div>" +
        (guide ? '<p class="school-links"><a href="' + ROOT + "countries/" + guide + '/">Visas, costs and work rules in our guide →</a></p>' : ""));
    }

    // US universities: their own facts and pages, from U.S. Department of Education data
    if (s.country === "United States") {
      fetch(ROOT + "app/us-profiles.json")
        .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
        .then(function (d) { var p = (d.profiles || {})[s.slug]; if (p) profile(s, p); })
        .catch(function () {});
    }

    // other universities in the same country: ones with a funding guide or degree data first
    var others = all.filter(function (x) { return x.country === s.country && x.slug !== s.slug; });
    others.sort(function (a, b) {
      var ra = (a.route ? 0 : 1) + (a.offers ? 0 : 1), rb = (b.route ? 0 : 1) + (b.offers ? 0 : 1);
      return ra - rb || a.name.localeCompare(b.name);
    });
    if (others.length) {
      show("schMore", "<h2>More universities in " + esc(inCountry(s.country)) + "</h2>" +
        '<ul class="school-more">' + others.slice(0, 8).map(function (x) {
          return '<li><a href="' + pageOf(x) + '">' + esc(x.name) + "</a>" +
            (x.city ? '<span class="school-route-meta">' + esc(x.city) + "</span>" : "") + "</li>";
        }).join("") + "</ul>" +
        (others.length > 8 ? '<p class="school-links"><a href="' + ROOT + 'scholarships/#directory">All ' +
          (others.length + 1).toLocaleString("en-US") + " universities in " + esc(inCountry(s.country)) + " in the directory →</a></p>" : ""));
    }

    // the official website, before the other universities in its country
    show("schOfficial", '<a class="btn btn-primary route-official-btn" href="' + esc(safeUrl(s.url)) +
      '" target="_blank" rel="noopener noreferrer">Official website: ' + esc(host(s.url)) + " " + cfg.ext + "</a>" +
      "<small>Programmes, entry requirements, fees and how to apply are on the university’s own site.</small>");
  }

  function profile(s, p) {
    if (p.city && !s.city) {  // the directory only knows the country; IPEDS knows the town
      var lede = $("schPlace").textContent;
      $("schPlace").textContent = p.city + ", " + (STATES[p.state] || p.state) + ", " + lede;
    }
    var rows = [];
    if (p.control) rows.push(["Type", cap(p.control)]);
    if (p.kind) rows.push(["Classification", cap(p.kind.replace(/^an? /, ""))]);
    if (p.city) rows.push(["Location", p.city + ", " + (STATES[p.state] || p.state) + (p.setting ? " (" + p.setting + ")" : "")]);
    if (p.size) rows.push(["Students", p.size]);
    if (p.flags) rows.push(["Also", cap(p.flags.join("; "))]);
    var deg = ["a", "b", "m", "d", "p"].filter(function (k) { return p.degrees && p.degrees[k]; }).map(function (k) {
      return num(p.degrees[k]) + " " + DEGREE[k][p.degrees[k] === 1 ? 0 : 1];
    });
    if (deg.length) rows.push(["Degrees awarded in 2022-23", deg.join(" · ")]);
    if (p.top_fields) rows.push(["Most graduates in", p.top_fields.join(", ")]);
    if (rows.length) {
      show("schProfile", "<h2>At a glance</h2>" + '<dl class="school-dl">' + rows.map(function (r) {
        return "<div><dt>" + esc(r[0]) + "</dt><dd>" + esc(r[1]) + "</dd></div>";
      }).join("") + "</dl>" +
        '<p class="school-src">From U.S. Department of Education data (IPEDS): institutional directory 2024-25, degrees awarded 2022-23.</p>');
    }
    var L = p.links || {}, pages = [
      ["admissions", "Admissions", "entry requirements, tests and deadlines"],
      ["aid", "Financial aid", "what it offers and how to apply for it"],
      ["apply", "How to apply", "the application itself"],
      ["netprice", "Net price calculator", "an estimate of what you would pay after aid"]
    ].filter(function (x) { return L[x[0]]; });
    if (pages.length) {
      show("schPages", "<h2>" + esc(s.name) + "’s own pages</h2>" + '<ul class="school-routes">' + pages.map(function (x) {
        return '<li><a href="' + esc(safeUrl(L[x[0]])) + '" target="_blank" rel="noopener noreferrer">' + esc(x[1]) + " " + cfg.ext + "</a>" +
          '<span class="school-route-meta">' + esc(x[2]) + "</span></li>";
      }).join("") + "</ul>");
      var steps = [];
      if (L.admissions) steps.push("Check entry requirements, English tests and deadlines on " + esc(s.name) + "’s admissions page above.");
      if (L.netprice) steps.push("Run its net price calculator to estimate what you would pay after aid, before you spend on an application.");
      if (L.aid) steps.push("Read its financial aid page for what it offers international students, and the forms it asks for.");
      steps.push("Apply through the university or the portal it names. Never pay an agent who promises admission.");
      show("schApply", "<h2>Before you apply</h2><ul>" + steps.map(function (t) { return "<li>" + t + "</li>"; }).join("") + "</ul>");
    }
  }

  // the Share button carries this university's own link and message
  function share(s, levels) {
    var box = document.querySelector("#schShare details[data-share]");
    if (!box) return;
    var url = cfg.base + "school/?s=" + encodeURIComponent(s.slug);
    var title = s.name + " · DegreeStep";
    var text = "🎓 " + s.name + "\n" + place(s) + (levels.length ? " · " + levels.join(", ") : "");
    box.setAttribute("data-url", url);
    box.setAttribute("data-title", title);
    box.setAttribute("data-text", text);
    var q = encodeURIComponent, hrefs = {
      WhatsApp: "https://wa.me/?text=" + q(text + "\n" + url),
      Facebook: "https://www.facebook.com/sharer/sharer.php?u=" + q(url),
      X: "https://x.com/intent/post?text=" + q(text) + "&url=" + q(url),
      LinkedIn: "https://www.linkedin.com/sharing/share-offsite/?url=" + q(url),
      Telegram: "https://t.me/share/url?url=" + q(url) + "&text=" + q(text),
      Email: "mailto:?subject=" + q(title) + "&body=" + q(text + "\n\n" + url)
    };
    box.querySelectorAll(".share-panel a").forEach(function (a) {
      if (hrefs[a.textContent.trim()]) a.setAttribute("href", hrefs[a.textContent.trim()]);
    });
    $("schShare").hidden = false;
  }
})();
