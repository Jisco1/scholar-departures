/* DegreeStep: one university's page, school/?s=<slug>, filled in from app/schools.json (the same directory as the
   app). The university comes first: what stands out about it, its facts and degrees, its own funding and pages, then
   the button to apply on its own site. Only after that: the country's essentials, national scholarships and other
   universities in the same country. */
(function () {
  "use strict";
  var cfg = JSON.parse(document.getElementById("schoolConfig").textContent);
  var ROOT = cfg.root, ROUTES = window.SCHOLAR_DATA || [];
  var REGION_OF = { Europe: "Europe", Asia: "Asia", Africa: "Africa", Oceania: "Oceania" };  // continent to the board's region filter
  var slug = "";
  try { slug = new URLSearchParams(window.location.search).get("s") || ""; } catch (e) { slug = ""; }

  function $(id) { return document.getElementById(id); }
  function esc(t) {
    return String(t == null ? "" : t).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function safeUrl(u) { return /^https?:\/\/[^\s"<>]+$/i.test(u || "") ? u : "#"; }
  function host(u) { return String(u || "").replace(/^https?:\/\//i, "").split("/")[0].replace(/^www\./i, "").toLowerCase(); }
  // the part of a host that names the institution: mit.edu, cam.ac.uk, uct.ac.za
  function site(h) {
    var p = h.split(".");
    var n = p.length >= 3 && p[p.length - 1].length === 2 && /^(ac|edu|co|gov|org|com|net|or)$/.test(p[p.length - 2]) ? 3 : 2;
    return p.slice(-n).join(".");
  }
  function pageOf(s) { return ROOT + "school/?s=" + encodeURIComponent(s.slug); }
  function cap(t) { return t ? t.charAt(0).toUpperCase() + t.slice(1) : t; }
  function low(t) { return t ? t.charAt(0).toLowerCase() + t.slice(1) : t; }
  function num(n) { return Number(n).toLocaleString("en-US"); }
  function and(xs) { return xs.length < 2 ? xs.join("") : xs.slice(0, -1).join(", ") + " and " + xs[xs.length - 1]; }
  function an(word) { return /^[aeiou]/i.test(word) ? "an" : "a"; }
  var WITH_THE = /^(United States|United Kingdom|United Arab Emirates|Netherlands|Philippines|Bahamas|Gambia|Maldives|Czech Republic|Dominican Republic|Central African Republic|Democratic Republic of the Congo|Republic of the Congo|Holy See|Solomon Islands|Marshall Islands|Cayman Islands|British Virgin Islands)$/;
  function inCountry(c) { return (WITH_THE.test(c) ? "the " : "") + c; }  // "in the United States", "in Ghana"
  // "University of Aberdeen" reads "The University of Aberdeen is ..." at the start of a sentence
  function subject(name) { return /^(University|College|Institute|School|Academy|Polytechnic) of /.test(name) ? "The " + name : name; }
  function named(name) { return /^(University|College|Institute|School|Academy|Polytechnic) of /.test(name) ? "the " + name : name; }
  function show(id, html) { var el = $(id); el.innerHTML = html; el.hidden = false; }
  var STATES = { AL: "Alabama", AK: "Alaska", AZ: "Arizona", AR: "Arkansas", CA: "California", CO: "Colorado", CT: "Connecticut",
    DE: "Delaware", DC: "Washington, DC", FL: "Florida", GA: "Georgia", HI: "Hawaii", ID: "Idaho", IL: "Illinois", IN: "Indiana",
    IA: "Iowa", KS: "Kansas", KY: "Kentucky", LA: "Louisiana", ME: "Maine", MD: "Maryland", MA: "Massachusetts", MI: "Michigan",
    MN: "Minnesota", MS: "Mississippi", MO: "Missouri", MT: "Montana", NE: "Nebraska", NV: "Nevada", NH: "New Hampshire",
    NJ: "New Jersey", NM: "New Mexico", NY: "New York", NC: "North Carolina", ND: "North Dakota", OH: "Ohio", OK: "Oklahoma",
    OR: "Oregon", PA: "Pennsylvania", RI: "Rhode Island", SC: "South Carolina", SD: "South Dakota", TN: "Tennessee", TX: "Texas",
    UT: "Utah", VT: "Vermont", VA: "Virginia", WA: "Washington", WV: "West Virginia", WI: "Wisconsin", WY: "Wyoming",
    PR: "Puerto Rico", GU: "Guam", VI: "the U.S. Virgin Islands", AS: "American Samoa", MP: "the Northern Mariana Islands",
    FM: "Micronesia", MH: "the Marshall Islands", PW: "Palau" };
  var DEGREE = { a: ["associate degree", "associate degrees"], b: ["bachelor's degree", "bachelor's degrees"],
    m: ["master's degree", "master's degrees"], d: ["research doctorate", "research doctorates"],
    p: ["professional doctorate (such as an MD or JD)", "professional doctorates (such as MD or JD)"] };
  var SIZE = { "under 1,000": "fewer than 1,000 students", "1,000 to 4,999": "between 1,000 and 4,999 students",
    "5,000 to 9,999": "between 5,000 and 9,999 students", "10,000 to 19,999": "between 10,000 and 19,999 students",
    "20,000 or more": "20,000 or more students" };

  function notFound() {
    $("schName").textContent = "We couldn’t find that university";
    $("schPlace").innerHTML = 'Its name may have changed. <a href="' + ROOT + 'scholarships/#directory">Search the schools directory</a>.';
  }
  if (!slug || !window.fetch) { notFound(); return; }

  function json(path) { return fetch(ROOT + path).then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); }); }

  json("app/schools.json").then(function (doc) {
    var all = doc.schools || [], s = null;
    for (var i = 0; i < all.length; i++) { if (all[i].slug === slug) { s = all[i]; break; } }
    if (!s) { notFound(); return; }
    var us = s.country === "United States";
    // its own facts come first, so wait for them; a page without them still renders
    json(us ? "app/us-profiles.json" : "app/world-profiles.json")
      .then(function (d) { return (d.profiles || {})[s.slug] || null; }, function () { return null; })
      .then(function (p) { render(doc, all, s, us, p); });
  }).catch(notFound);

  function levelNames(doc, s) {
    if (!s.offers) return [];
    return ["b", "m", "d"].filter(function (k) { return s.offers[k] && s.offers[k].length; }).map(function (k) { return doc.levels[k]; });
  }

  function render(doc, all, s, us, p) {
    var levels = levelNames(doc, s);
    var town = s.city || (p && p.city ? p.city + (us && p.state ? ", " + (STATES[p.state] || p.state) : "") : "");
    document.title = s.name + " · DegreeStep";
    var crumb = document.querySelector(".crumbs span:last-child");
    if (crumb) crumb.textContent = s.name;
    $("schEyebrow").textContent = s.country + " · " + s.continent;
    $("schName").textContent = s.name;
    $("schPlace").textContent = (town ? town + ", " : "") + s.country + (levels.length ? ". Degrees: " + levels.join(", ") + "." : ".");
    share(s, levels, town);

    // 1. what stands out about the university, and its facts
    if (p) (us ? aboutUS : aboutWorld)(s, p);

    // 2. subjects it awards degrees in (US universities)
    if (s.offers && doc.fields) {
      var rows = ["b", "m", "d"].filter(function (k) { return s.offers[k] && s.offers[k].length; }).map(function (k) {
        return "<h3>" + esc(doc.levels[k]) + "</h3><p>" + esc(s.offers[k].map(function (f) { return doc.fields[f]; })
          .filter(Boolean).join(", ")) + "</p>";
      }).join("");
      show("schDegrees", "<h2>Subjects it awards degrees in</h2>" + rows);
    }

    // 3. funding at this university: our guides to its own scholarships, and awards tied to it
    var mine = site(host(s.url));
    var own = ROUTES.filter(function (r) {
      return r.slug === s.route || ((r.kind === "school" || r.only_at) && site(host(r.only_at ? "https://" + r.only_at : r.link)) === mine);
    });
    if (own.length) {
      show("schOwn", "<h2>Funding at " + esc(named(s.name)) + "</h2>" + own.map(function (r) {
        return '<a class="school-card" href="' + ROOT + "routes/" + encodeURIComponent(r.slug) + '/">' +
          '<span class="school-card-k">' + esc((r.types || []).join(" · ")) + "</span>" +
          "<strong>" + esc(r.name) + "</strong><span>" + esc(r.funding) + "</span>" +
          '<span class="school-route-meta">' + esc(r.deadline) + "</span>" +
          '<span class="school-card-go">Read our guide →</span></a>';
      }).join(""));
    }

    // 4. its own pages, the steps before applying, and the button to apply
    var L = (us && p && p.links) || {};
    if (us && p) ownPages(s, L);
    $("schApply").hidden = false;
    var apply = L.apply ? L.apply : s.url;
    show("schOfficial", '<a class="btn btn-primary route-official-btn" href="' + esc(safeUrl(apply)) +
      '" target="_blank" rel="noopener noreferrer">' + (L.apply ? "Start your application at " : "Apply through ") +
      esc(host(apply)) + " " + cfg.ext + "</a>" +
      "<small>Programmes, entry requirements, fees and the application itself are on " + esc(named(s.name)) + "’s own site" +
      (L.apply && host(L.apply) !== host(s.url) ? ' (main website: <a href="' + esc(safeUrl(s.url)) +
        '" target="_blank" rel="noopener noreferrer">' + esc(host(s.url)) + "</a>)" : "") + ".</small>");

    // ---- after the university itself: the country, national scholarships, other universities
    var guide = cfg.countryPages[s.country];
    var facts = (cfg.countryFacts || {})[s.country];
    if (facts && facts.length) {
      show("schFacts", "<h2>Studying in " + esc(inCountry(s.country)) + ": the essentials</h2>" +
        '<div class="table-wrap"><table>' + facts.map(function (f) {
          return '<tr><th scope="row">' + esc(f[0]) + "</th><td>" + esc(f[1]) + "</td></tr>";
        }).join("") + "</table></div>" +
        (guide ? '<p class="school-links"><a href="' + ROOT + "countries/" + guide + '/">Visas, costs and work rules in our guide →</a></p>' : ""));
    }

    // national schemes only: an award tied to one university (Gates, Rhodes) belongs on that university's page
    var national = ROUTES.filter(function (r) { return r.country === s.country && r.kind === "program" && !r.only_at; });
    var region = us ? "USA" : s.country === "Canada" ? "Canada" : REGION_OF[s.continent];
    var more = region ? '<p class="school-links"><a href="' + ROOT + "scholarships/?region=" + encodeURIComponent(region) +
      '">Funded routes across ' + esc(region === "USA" ? "the United States" : region) + " →</a></p>" : "";
    if (national.length) {
      show("schFunding", "<h2>National scholarships for study in " + esc(inCountry(s.country)) + "</h2>" +
        "<p>Awards you take to a university of your choice. Check that " + esc(named(s.name)) + " and your course qualify before you apply.</p>" +
        '<ul class="school-routes">' + national.map(function (r) {
          return '<li><a href="' + ROOT + "routes/" + encodeURIComponent(r.slug) + '/">' + esc(r.name) + "</a>" +
            '<span class="school-route-meta">' + esc((r.types || []).join(" · ")) + " · " + esc(r.deadline) + "</span></li>";
        }).join("") + "</ul>" + more);
    } else if (more) {
      show("schFunding", more);
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
          num(others.length + 1) + " universities in " + esc(inCountry(s.country)) + " in the directory →</a></p>" : ""));
    }
    if (p) {
      $("schSource").textContent = us ? "Figures from the U.S. Department of Education (IPEDS): directory 2024-25, degrees awarded 2022-23."
        : "Figures from Wikidata. Check them on the university’s own site before you rely on them.";
      $("schSource").hidden = false;
    }
  }

  function facts(rows) {
    return '<dl class="school-dl">' + rows.map(function (r) {
      return "<div><dt>" + esc(r[0]) + "</dt><dd>" + esc(r[1]) + "</dd></div>";
    }).join("") + "</dl>";
  }

  function aboutWorld(s, p) {
    var type = p.type ? low(p.type) : "university";
    var town = s.city || p.city;  // our directory's own town first
    var where = town ? town + ", " + s.country : inCountry(s.country);
    var text = [subject(s.name) + " is " + an(type) + " " + type + " in " + where + "."];
    if (p.founded) {
      var age = new Date().getFullYear() - p.founded;
      var years = age >= 200 ? Math.floor(age / 100) * 100 : age >= 100 ? 100 : 0;
      if (p.among_oldest) text.push("Founded in " + p.founded + ", it is one of the oldest universities in " + inCountry(s.country) +
        (years ? ", with more than " + years + " years of teaching behind it." : "."));
      else text.push("It was founded in " + p.founded + (years ? " and has more than " + years + " years of history." : "."));
    }
    if (p.students) text.push("About " + num(p.students) + " students were enrolled there in " + p.students_year + ".");
    var rows = [];
    if (p.type) rows.push(["Type", cap(p.type)]);
    if (p.founded) rows.push(["Founded", String(p.founded)]);
    if (town) rows.push(["Location", town + ", " + s.country]);
    if (p.students) rows.push(["Students", "About " + num(p.students) + " (" + p.students_year + ")"]);
    show("schAbout", "<h2>About " + esc(named(s.name)) + "</h2><p>" + esc(text.join(" ")) + "</p>" + facts(rows));
  }

  function aboutUS(s, p) {
    var state = STATES[p.state] || p.state;
    var kind = p.kind ? p.kind.replace(/^an? /, "") : "college";
    var what = (p.control ? p.control + " " : "") + kind;
    var text = [subject(s.name) + " is " + an(what) + " " + what + " in " + p.city + ", " + state + (p.setting ? " (" + p.setting + ")" : "") + "."];
    if (p.flags) text.push("It is also " + and(p.flags) + ".");
    if (p.size && SIZE[p.size]) text.push("It has " + SIZE[p.size] + ".");
    var deg = ["a", "b", "m", "d", "p"].filter(function (k) { return p.degrees && p.degrees[k]; }).map(function (k) {
      return num(p.degrees[k]) + " " + DEGREE[k][p.degrees[k] === 1 ? 0 : 1];
    });
    if (deg.length) text.push("In 2022-23 it awarded " + and(deg) + (p.top_fields ? ", most often in " + and(p.top_fields.map(low)) : "") + ".");
    var rows = [];
    if (p.control) rows.push(["Type", cap(p.control)]);
    if (p.kind) rows.push(["Classification", cap(kind)]);
    if (p.city) rows.push(["Location", p.city + ", " + state + (p.setting ? " (" + p.setting + ")" : "")]);
    if (p.size) rows.push(["Students", p.size]);
    if (p.top_fields) rows.push(["Most graduates in", p.top_fields.join(", ")]);
    show("schAbout", "<h2>About " + esc(named(s.name)) + "</h2><p>" + esc(text.join(" ")) + "</p>" + facts(rows));
  }

  function ownPages(s, L) {
    var pages = [
      ["admissions", "Admissions", "entry requirements, tests and deadlines"],
      ["aid", "Financial aid", "what it offers and how to apply for it"],
      ["netprice", "Net price calculator", "an estimate of what you would pay after aid"]
    ].filter(function (x) { return L[x[0]]; });
    if (pages.length) {
      show("schPages", "<h2>" + esc(cap(named(s.name))) + "’s own pages</h2>" + '<ul class="school-routes">' + pages.map(function (x) {
        return '<li><a href="' + esc(safeUrl(L[x[0]])) + '" target="_blank" rel="noopener noreferrer">' + esc(x[1]) + " " + cfg.ext + "</a>" +
          '<span class="school-route-meta">' + esc(x[2]) + "</span></li>";
      }).join("") + "</ul>");
    }
    var steps = [];
    if (L.admissions) steps.push("Check entry requirements, English tests and deadlines on " + esc(named(s.name)) + "’s admissions page.");
    if (L.netprice) steps.push("Run its net price calculator to see what you would pay after aid, before you spend on an application.");
    if (L.aid) steps.push("Read its financial aid page for what it offers international students, and the forms it asks for.");
    if (steps.length) {
      steps.push("Apply through the university or the portal it names. Never pay an agent who promises admission.");
      show("schApply", "<h2>Before you apply</h2><ul>" + steps.map(function (t) { return "<li>" + t + "</li>"; }).join("") + "</ul>");
    }
  }

  // the Share button carries this university's own link and message
  function share(s, levels, town) {
    var box = document.querySelector("#schShare details[data-share]");
    if (!box) return;
    var url = cfg.base + "school/?s=" + encodeURIComponent(s.slug);
    var title = s.name + " · DegreeStep";
    var text = "🎓 " + s.name + "\n" + (town ? town + ", " : "") + s.country + (levels.length ? " · " + levels.join(", ") : "");
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
