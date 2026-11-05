
// SVWeave graph viewer (ADR-014), a real file so it can be read, linted and diffed as JS.
// graph_template.py inlines it into every page (the CSP names the sha256 of the inlined text)
// and fills OPTIONAL from graph_codec._OPTIONAL. Source text is only ever written with
// textContent; links and scripts only through safeHref / safeChunk.
"use strict";
(function () {
// codec-begin: svweave-graph/2 decoder; graph_codec.decode is its Python twin (tests compare them)
function decodePage(R) {
  if (R.format !== "svweave-graph/2") return R;
  var s = R.s, n = R.n, sg = R.sg, full = {}, paths = [], nodes = [], i;
  var g = n.g || {}, pa = n.pa || {}, po = n.po || {};
  // the optional per-node keys: graph_codec._OPTIONAL (a test keeps the two lists equal)
  var OPTIONAL = @@SVWEAVE_OPTIONAL_KEYS@@;
  n.full.forEach(function (x) { full[x] = 1; });
  function sigPath(k) { var o = sg[k]; return o[0] >= 0 ? paths[o[0]] + "." + s[o[1]] : String(s[o[1]]); }
  function lazyPorts(node, raw) {
    var cache = null;
    Object.defineProperty(node, "ports", { enumerable: true, get: function () {
      if (!cache) cache = raw.map(function (x) { return { port: s[x[0]], dir: s[x[1]], actual: s[x[2]], kind: s[x[3]], signals: x[4].map(sigPath), supported: !!x[5] }; });
      return cache;
    } });
  }
  for (i = 0; i < n.id.length; i++) {
    var p = n.p[i], f = n.f[i], k = String(i);
    paths.push(full[i] ? s[n.r[i]] : paths[p] + "." + s[n.r[i]]);
    var node = { id: n.id[i], parent: p >= 0 ? n.id[p] : null, path: paths[i], name: s[n.nm[i]],
      definition: s[n.d[i]], depth: n.dp[i], top: !!(f & 1), "interface": !!(f & 2), stub: !!(f & 4), blackbox: !!(f & 8),
      generate: (g[k] || []).map(function (x) { return s[x]; }),
      params: (pa[k] || []).map(function (x) { return { name: s[x[0]], value: s[x[1]], overridden: !!x[2] }; }),
      file: s[n.fl[i]], line: n.ln[i] };
    lazyPorts(node, po[k] || []);
    OPTIONAL.forEach(function (o) { if (n[o] && k in n[o]) node[o] = n[o][k]; });
    nodes.push(node);
  }
  var edges = [];
  R.e.forEach(function (grp) { grp[2].forEach(function (it) { edges.push({ from: n.id[grp[0]], to: n.id[grp[1]], signal: sigPath(it[0]), port: s[it[1]], directed: !!it[2] }); }); });
  var out = {};
  Object.keys(R).forEach(function (key) { if (["format", "s", "n", "sg", "e"].indexOf(key) < 0) out[key] = R[key]; });
  out.nodes = nodes;
  out.edges = edges;
  return out;
}
// codec-end
var D = decodePage(JSON.parse(document.getElementById("data").textContent));
var NS = "http://www.w3.org/2000/svg";
var byId = {}, kids = {}, roots = [];
D.nodes.forEach(function (n) { byId[n.id] = n; kids[n.id] = []; n.collapsed = D.diff ? n.diff === "inside" || (n.diff === "changed" && n.depth >= 2) : n.depth >= 2; });
D.nodes.forEach(function (n) { if (n.parent && byId[n.parent]) kids[n.parent].push(n); else roots.push(n); });
function el(tag, attrs, text) {
  var e = document.createElementNS(NS, tag);
  for (var k in attrs) e.setAttribute(k, attrs[k]);
  if (text !== undefined) e.textContent = text;
  return e;
}
function h(tag, text, cls) { var e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (cls) e.className = cls; return e; }

// ---- header and banner
var m = D.meta, meta = document.getElementById("meta");
function badge(t, c) { var b = h("span", t, "badge " + (c || "")); meta.appendChild(b); meta.appendChild(document.createTextNode(" ")); }
badge("build " + m.build_id.slice(0, 12));
badge(m.completeness, m.completeness === "COMPLETE" ? "ok" : "warn");
badge(m.freshness, m.freshness === "CURRENT" ? "ok" : "warn");
badge("profile " + m.profile);
badge(m.evidence_kind);
if (m.diff) badge("diff " + m.diff, "bad");
// atlas links are data: only our own file names are ever used as an href
function safeHref(x) {
  if (x === "index.html") return true;
  return typeof x === "string" && x.length === 17 && x.indexOf("p-") === 0 && x.slice(12) === ".html" && /^[0-9a-f]+$/.test(x.slice(2, 12));
}
if (m.atlas) {
  var crumbs = h("span", undefined, "badge");
  crumbs.id = "crumbs";
  crumbs.appendChild(document.createTextNode("page " + m.atlas.page + " of " + m.atlas.pages + ": "));
  m.atlas.crumbs.forEach(function (c, i) {
    if (i) crumbs.appendChild(document.createTextNode(" / "));
    if (i < m.atlas.crumbs.length - 1 && safeHref(c[1])) { var a = h("a", c[0]); a.href = c[1]; crumbs.appendChild(a); }
    else crumbs.appendChild(document.createTextNode(c[0]));
  });
  meta.appendChild(crumbs);
}
if (m.root) badge("subtree " + m.root, "warn");
var msgs = [];
if (m.completeness !== "COMPLETE") msgs.push("Analysis is " + m.completeness + ": an absent instance or connection is not proof of absence.");
if (m.freshness !== "CURRENT") msgs.push("Freshness is " + m.freshness + ": the workspace changed since this build.");
if (D.truncated) msgs.push("Truncated: " + D.stats.instances_shown + " of " + D.stats.instances_total + " instances shown (shallowest first).");
if (D.hubs.length) msgs.push(D.hubs.length + " high-fanout signal(s) are listed in the side panel, not drawn.");
if (m.lazy) msgs.push("Complete topology in " + m.lazy.parts + " parts (" + m.lazy.instances_total + " instances): dashed-blue nodes are parts not loaded yet; double-click one, or search, to load it.");
else if (m.atlas) msgs.push("Atlas page " + m.atlas.page + " of " + m.atlas.pages + ": this page holds " + D.nodes.length + " instances; dashed-blue portal nodes open the pages of their subtrees, so this page alone is NOT the complete topology (all pages together are).");
else if (!D.truncated && !D.hubs.length && !m.root && !D.diff) msgs.push("Complete topology: all " + D.nodes.length + " instances and all " + D.edges.length + " connections of this build are in the page; deeper levels start collapsed (expand all).");
if (D.diff) msgs.push("Diff view (" + D.diff.base + " -> " + D.diff.head + "): " + D.stats.changed_instances + " instance(s) of " + D.diff.changed_definitions.length + " changed definition(s) are outlined in red with their subtrees; dashed ancestors are context and show only their drawn children.");
else if (m.root && !m.atlas) msgs.push("Subtree view of " + m.root + ": only this instance and its descendants were read; fan-out counts uses inside the subtree.");
if (D.boundary && D.boundary.length) msgs.push(D.boundary.length + " signal(s) declared outside the subtree are listed in the side panel, not drawn.");
msgs.push(D.note);
if (m.limitations && m.limitations.length) msgs.push("Limitations (" + m.limitations.length + "): hover here.");
var bannerEl = document.getElementById("banner");
bannerEl.textContent = msgs.join("  ");
if (m.limitations) bannerEl.title = m.limitations.join("\n");

// ---- layout (left to right tidy tree over the visible nodes)
var NW = 190, NH = 38, GX = 70, GY = 14, pos = {};
function visibleKids(n) { return n.collapsed ? [] : kids[n.id]; }
function layout() {
  pos = {};
  var y = 0;
  function walk(n, d) {
    var ks = visibleKids(n), cy;
    if (!ks.length) { cy = y; y += NH + GY; }
    else { var first = null, last = null; ks.forEach(function (k) { walk(k, d + 1); }); first = pos[ks[0].id].y; last = pos[ks[ks.length - 1].id].y; cy = (first + last) / 2; }
    pos[n.id] = { x: d * (NW + GX), y: cy };
  }
  roots.forEach(function (r) { walk(r, 0); });
}
function visAnc(id) { // nearest visible ancestor-or-self
  var n = byId[id];
  while (n && !pos[n.id]) n = n.parent ? byId[n.parent] : null;
  return n ? n.id : null;
}

// ---- drawing
var root = document.getElementById("root"), sel = null, selEdge = null, hits = null, commitSel = null;
function draw() {
  layout();
  while (root.firstChild) root.removeChild(root.firstChild);
  var gh = el("g", {}), gc = el("g", {}), gn = el("g", {});
  root.appendChild(gh); root.appendChild(gc); root.appendChild(gn);
  D.nodes.forEach(function (n) {
    var p = pos[n.id]; if (!p || !n.parent || !pos[n.parent]) return;
    var q = pos[n.parent], x1 = q.x + NW, y1 = q.y + NH / 2, x2 = p.x, y2 = p.y + NH / 2, mx = (x1 + x2) / 2;
    gh.appendChild(el("path", { "class": "hier", d: "M" + x1 + "," + y1 + " C" + mx + "," + y1 + " " + mx + "," + y2 + " " + x2 + "," + y2 }));
  });
  if (document.getElementById("showConn").checked) {
    var bundles = {}, order = [];
    D.edges.forEach(function (e) {
      var a = visAnc(e.from), b = visAnc(e.to);
      if (!a || !b || a === b) return;
      var key = a + ">" + b;
      if (!bundles[key]) { bundles[key] = { a: a, b: b, items: [], directed: true }; order.push(key); }
      bundles[key].items.push(e);
      if (!e.directed) bundles[key].directed = false;
    });
    order.forEach(function (key, i) {
      var bd = bundles[key], pa = pos[bd.a], pb = pos[bd.b], x1 = pa.x + NW, y1 = pa.y + NH / 2, x2 = pb.x, y2 = pb.y + NH / 2;
      if (pb.x <= pa.x) { x1 = pa.x + NW / 2; x2 = pb.x + NW / 2; y1 = pa.y + (pb.y >= pa.y ? NH : 0); y2 = pb.y + (pb.y >= pa.y ? 0 : NH); }
      var dx = Math.max(40, Math.abs(x2 - x1) / 2), off = 14 + (i % 4) * 8;
      var d = "M" + x1 + "," + y1 + " C" + (x1 + dx) + "," + (y1 - off) + " " + (x2 - dx) + "," + (y2 - off) + " " + x2 + "," + y2;
      var n = bd.items.length;
      var attrs = { "class": "conn" + (bd.directed ? "" : " und") + (selEdge === key ? " sel" : ""), d: d, "stroke-width": String(1.4 + Math.min(3, Math.log(n) / Math.LN2 * 0.6)) };
      if (bd.directed) attrs["marker-end"] = "url(#arr)";
      var pth = el("path", attrs);
      pth.addEventListener("click", function (ev) { ev.stopPropagation(); selEdge = key; sel = null; showBundle(bd); draw(); });
      pth.appendChild(el("title", {}, n + " signal(s): " + bd.items.slice(0, 8).map(function (e) { return e.signal; }).join(", ") + (n > 8 ? ", ..." : "")));
      gc.appendChild(pth);
      if (n > 1) {
        var mx = (x1 + x2) / 2, my = (y1 + y2) / 2 - off * 0.75;
        gc.appendChild(el("text", { x: mx, y: my, "class": "cnt", "text-anchor": "middle" }, "×" + n));
      }
    });
  }
  D.nodes.forEach(function (n) {
    var p = pos[n.id]; if (!p) return;
    var cls = "node" + (n.portal ? " portal" : "") + (n.diff === "changed" ? " changed" : n.diff === "context" ? " context" : "") + (n.top ? " top" : "") + (n.stub ? " stub" : "") + (n.blackbox ? " blackbox" : "") + (sel === n.id ? " sel" : "");
    if (hits) cls += hits[n.id] ? " hit" : " dim";
    else if (commitSel !== null && n.diff !== "context") cls += commitsOf(n).indexOf(commitSel) >= 0 ? " hit" : " dim";
    var g = el("g", { "class": cls, transform: "translate(" + p.x + "," + p.y + ")", tabindex: "0" });
    g.appendChild(el("rect", { width: NW, height: NH, rx: 6 }));
    g.appendChild(el("text", { x: 8, y: 16 }, trunc(relName(n), 26)));
    var sub = n.definition || "", ck = kids[n.id].length;
    if (ck && n.collapsed) sub += "  [+" + ck + "]";
    if (n.portal) sub += n.portal.chunk ? "  [load " + n.portal.instances + "]" : "  -> page of " + n.portal.instances;
    g.appendChild(el("text", { x: 8, y: 30, "class": "sub" }, trunc(sub, 32)));
    g.appendChild(el("title", {}, n.path));
    g.addEventListener("click", function (ev) { ev.stopPropagation(); sel = n.id; selEdge = null; showNode(n); draw(); });
    g.addEventListener("dblclick", function (ev) { ev.stopPropagation(); if (n.portal && n.portal.chunk) { loadChunk(n.portal.chunk, function () { showNode(n); }); return; } if (kids[n.id].length) { n.collapsed = !n.collapsed; draw(); } });
    g.addEventListener("keydown", function (ev) { if (ev.key === "Enter") { sel = n.id; selEdge = null; showNode(n); draw(); } });
    gn.appendChild(g);
  });
  document.getElementById("count").textContent = Object.keys(pos).length + " of " + D.nodes.length + " nodes visible, " + D.edges.length + " connections" + (m.lazy ? " (loaded so far; " + m.lazy.instances_total + " instances in the design)" : "");
}
function relName(n) {
  var p = n.parent && byId[n.parent];
  return p && n.path.indexOf(p.path + ".") === 0 ? n.path.slice(p.path.length + 1) : n.name;
}
function trunc(s, n) { s = String(s == null ? "" : s); return s.length > n ? s.slice(0, n - 1) + "\u2026" : s; }

// ---- side panel
var side = document.getElementById("side");
function clearSide() { while (side.firstChild) side.removeChild(side.firstChild); }
function dl(rows) { var d = h("dl"); rows.forEach(function (r) { if (r[1] === null || r[1] === undefined || r[1] === "") return; d.appendChild(h("dt", r[0])); d.appendChild(h("dd", String(r[1]))); }); return d; }
function table(head, rows) {
  var t = h("table"), tr = h("tr"); head.forEach(function (x) { tr.appendChild(h("th", x)); }); t.appendChild(tr);
  rows.forEach(function (r) { var row = h("tr"); r.forEach(function (c) { row.appendChild(h("td", c)); }); t.appendChild(row); });
  return t;
}
function showNode(n) {
  clearSide();
  side.appendChild(h("h2", n.name));
  if (n.portal && n.portal.chunk) {
    var lb = h("button", "Load this subtree here (" + n.portal.instances + " instances)");
    lb.addEventListener("click", function () { loadChunk(n.portal.chunk, function () { showNode(n); }); });
    side.appendChild(lb);
  }
  if (n.portal && safeHref(n.portal.href)) {
    var pl = h("a", "Open the page of this subtree (" + n.portal.instances + " instances)", "portal-link");
    pl.href = n.portal.href;
    side.appendChild(pl);
  }
  side.appendChild(dl([["path", n.path], ["definition", n.definition], ["top", n.top ? "yes" : ""], ["interface", n.interface ? "yes" : ""],
    ["stub", n.stub ? "yes" : ""], ["blackbox candidate", n.blackbox ? "yes" : ""], ["generate", (n.generate || []).join(" / ")],
    ["source", n.file ? n.file + (n.line ? ":" + n.line : "") : ""], ["children", kids[n.id].length + (n.children_total !== undefined && n.children_total !== kids[n.id].length ? " drawn of " + n.children_total : "")],
    ["changed by", (n.commits || []).map(function (i) { var c = D.diff.commits[i]; return c ? c.sha.slice(0, 8) + " " + c.subject : ""; }).join("\n")],
    ["diff", n.diff === "changed" ? "definition changed by the diff" : n.diff === "context" ? "context (ancestor of a changed instance)" : n.diff === "inside" ? "inside a changed instance" : ""]]));
  if (n.params.length) { side.appendChild(h("h2", "Parameters")); side.appendChild(table(["name", "value", ""], n.params.map(function (p) { return [p.name, p.value, p.overridden ? "overridden" : ""]; }))); }
  if (n.ports.length) { side.appendChild(h("h2", "Port associations")); side.appendChild(table(["port", "dir", "actual"], n.ports.map(function (p) { return [p.port, p.dir, p.actual]; }))); }
  var rel = D.edges.filter(function (e) { return e.from === n.id || e.to === n.id; });
  if (rel.length) { side.appendChild(h("h2", "Connections (" + rel.length + ")")); side.appendChild(table(["signal", "to / from"], rel.slice(0, 200).map(function (e) { var o = e.from === n.id ? byId[e.to] : byId[e.from]; return [e.signal, (e.from === n.id ? "-> " : "<- ") + (o ? o.path : "?")]; }))); }
  changes(); hubs(); boundary();
}
function showBundle(bd) {
  clearSide();
  var a = byId[bd.a], b = byId[bd.b];
  side.appendChild(h("h2", "Connections (" + bd.items.length + ")"));
  side.appendChild(dl([["from", a && a.path], ["to", b && b.path], ["direction", bd.directed ? "declared port direction" : "mixed / undirected"]]));
  side.appendChild(table(["signal", "port"], bd.items.slice(0, 300).map(function (e) { return [e.signal, e.port]; })));
  side.appendChild(h("p", D.note, "mut"));
  changes(); hubs(); boundary();
}
function hubs() {
  if (!D.hubs.length) return;
  side.appendChild(h("h2", "High-fanout signals (not drawn)"));
  side.appendChild(table(["signal", "instances"], D.hubs.slice(0, 50).map(function (x) { return [x.signal, x.instances]; })));
}
function changes() {
  if (!D.diff) return;
  side.appendChild(h("h2", "Changed by the diff (" + D.diff.base + " -> " + D.diff.head + ")"));
  side.appendChild(table(["definition", "instances drawn / total"], D.diff.changed_definitions.map(function (x) { return [x.qualified_name, x.instances_drawn + " / " + x.instances]; })));
  var f = D.diff.files.map(function (x) { return [x.path, (x.side || "") + (x.aligned === true ? "" : " (approximate)")]; });
  if (f.length) side.appendChild(table(["changed file", "mapped side"], f));
  if (D.diff.unmapped_files.length) side.appendChild(h("p", "Not in this build: " + D.diff.unmapped_files.join(", "), "mut"));
  if (D.diff.non_instance_declarations.length) side.appendChild(h("p", "Changed outside any module/interface (not drawn): " + D.diff.non_instance_declarations.join(", "), "mut"));
  commitList();
}
function commitsOf(n) {
  // an unchanged instance inside a changed one belongs to the nearest changed ancestor (or itself)
  var a = n;
  while (a && a.diff !== "changed") a = a.parent ? byId[a.parent] : null;
  return a && a.commits ? a.commits : [];
}
function commitList() {
  if (!D.diff || !D.diff.commits) return;
  side.appendChild(h("h2", "Commits (" + D.diff.commits.length + ")"));
  side.appendChild(h("p", "Click a commit to highlight the instances it changed; click it again to clear.", "mut"));
  var counts = {};
  D.nodes.forEach(function (n) { (n.commits || []).forEach(function (i) { counts[i] = (counts[i] || 0) + 1; }); });
  var t = h("table"), tr = h("tr");
  ["commit", "subject", "instances"].forEach(function (x) { tr.appendChild(h("th", x)); });
  t.appendChild(tr);
  D.diff.commits.forEach(function (c) {
    var row = h("tr", undefined, "crow" + (commitSel === c.index ? " on" : ""));
    row.tabIndex = 0;
    row.appendChild(h("td", c.sha.slice(0, 8), "sha"));
    row.appendChild(h("td", c.subject));
    row.appendChild(h("td", c.changes_drawn_definitions ? String(counts[c.index] || 0) : "none drawn"));
    function pick() { commitSel = commitSel === c.index ? null : c.index; draw(); clearSide(); changes(); }
    row.addEventListener("click", pick);
    row.addEventListener("keydown", function (ev) { if (ev.key === "Enter") pick(); });
    t.appendChild(row);
  });
  side.appendChild(t);
}
function boundary() {
  if (!D.boundary || !D.boundary.length) return;
  side.appendChild(h("h2", "Boundary signals (declared outside the subtree, not drawn)"));
  side.appendChild(table(["signal", "instances in subtree"], D.boundary.slice(0, 50).map(function (x) { return [x.signal, x.instances]; })));
}

// ---- toolbar
document.getElementById("showConn").addEventListener("change", draw);
document.getElementById("expand").addEventListener("click", function () { D.nodes.forEach(function (n) { n.collapsed = false; }); draw(); fit(); });
document.getElementById("collapse").addEventListener("click", function () { D.nodes.forEach(function (n) { n.collapsed = n.depth >= 1; }); draw(); fit(); });
document.getElementById("fit").addEventListener("click", fit);
document.getElementById("q").addEventListener("input", function () {
  var q = this.value.trim().toLowerCase();
  if (!q) { hits = null; draw(); return; }
  hits = {};
  D.nodes.forEach(function (n) {
    if ((n.path + " " + (n.definition || "")).toLowerCase().indexOf(q) >= 0) {
      hits[n.id] = 1;
      var a = n.parent ? byId[n.parent] : null;
      while (a) { a.collapsed = false; a = a.parent ? byId[a.parent] : null; }
    }
  });
  draw();
  atlasSearch(q);
});
// on the index page of an atlas: find any instance of the whole design, on whichever page
var A = D.atlas_search, atlasPaths = null;
function atlasSearch(q) {
  if (!A) return;
  if (!atlasPaths) {
    atlasPaths = [];
    for (var i = 0; i < A.p.length; i++) atlasPaths.push(A.p[i] >= 0 ? atlasPaths[A.p[i]] + "." + A.s[A.r[i]] : A.s[A.r[i]]);
  }
  clearSide();
  if (!q) { changes(); return; }
  var found = [];
  for (var j = 0; j < atlasPaths.length && found.length < 50; j++) if (atlasPaths[j].toLowerCase().indexOf(q) >= 0) found.push(j);
  side.appendChild(h("h2", "Whole design (" + atlasPaths.length + " instances): " + (found.length >= 50 ? "first 50 matches" : found.length + " match(es)")));
  found.forEach(function (j) {
    var file = A.pages[A.pg[j]], row = h("div");
    if (m.lazy) {
      var b2 = h("a", atlasPaths[j]);
      b2.href = "#";
      b2.addEventListener("click", function (ev) { ev.preventDefault(); openFound(j); });
      row.appendChild(b2);
    } else {
      if (!safeHref(file)) return;
      var a = h("a", atlasPaths[j]);
      a.href = file + "#f=" + encodeURIComponent(atlasPaths[j]);
      row.appendChild(a);
    }
    side.appendChild(row);
  });
}
// arriving from a search result: open the tree down to that instance and select it
function focusFromHash() {
  var hsh = location.hash || "";
  if (hsh.indexOf("#f=") !== 0) return;
  var want = "";
  try { want = decodeURIComponent(hsh.slice(3)); } catch (err) { return; }
  focusPath(want);
}
function focusPath(want) {
  var hit = D.nodes.filter(function (n) { return n.path === want; })[0];
  if (!hit) return;
  var a = hit.parent ? byId[hit.parent] : null;
  while (a) { a.collapsed = false; a = a.parent ? byId[a.parent] : null; }
  sel = hit.id; draw(); showNode(hit);
}
// ---- --lazy: parts of the topology are data files (c-<10 hex>.js) beside this page
var loaded = {}, waiting = {};
function safeChunk(x) {
  return typeof x === "string" && x.length === 15 && x.indexOf("c-") === 0 && x.slice(12) === ".js" && /^[0-9a-f]+$/.test(x.slice(2, 12));
}
function loadChunk(file, done) {
  if (!safeChunk(file)) return;
  if (loaded[file]) { if (done) done(); return; }
  if (waiting[file]) { if (done) waiting[file].push(done); return; }
  waiting[file] = done ? [done] : [];
  var sc = document.createElement("script");
  sc.src = file;
  sc.onerror = function () {
    delete waiting[file];
    document.getElementById("banner").textContent = "Could not load the part " + file + " (it must sit beside this page; some browsers refuse local scripts).";
  };
  document.head.appendChild(sc);
}
window.svwChunk = function (raw) {
  var P = decodePage(raw), file = P.meta && P.meta.chunk;
  if (!safeChunk(file) || loaded[file]) return;
  loaded[file] = 1;
  var base = 0;
  P.nodes.forEach(function (n) {
    var have = byId[n.id];
    if (have) { delete have.portal; have.collapsed = false; base = have.depth; return; }  // the portal: now open
    n.depth = n.parent && byId[n.parent] ? byId[n.parent].depth + 1 : base;
    n.collapsed = n.depth >= base + 2;
    byId[n.id] = n; kids[n.id] = [];
    if (n.parent && byId[n.parent]) kids[n.parent].push(n);
    D.nodes.push(n);
  });
  P.edges.forEach(function (e) { D.edges.push(e); });
  draw();
  var cbs = waiting[file] || [];
  delete waiting[file];
  cbs.forEach(function (cb) { cb(); });
};
function openFound(j) {
  var chain = [], pgi = A.pg[j];
  while (pgi > 0) { chain.unshift(A.pages[pgi]); pgi = A.parent[pgi]; }
  (function next() { if (!chain.length) { focusPath(atlasPaths[j]); return; } loadChunk(chain.shift(), next); })();
}

// ---- pan / zoom
var view = document.getElementById("view"), svg = document.getElementById("svg");
var tx = 20, ty = 20, k = 1;
function apply() { root.setAttribute("transform", "translate(" + tx + "," + ty + ") scale(" + k + ")"); }
function fit() {
  var r = view.getBoundingClientRect(), maxx = 0, maxy = 0;
  Object.keys(pos).forEach(function (id) { maxx = Math.max(maxx, pos[id].x + NW); maxy = Math.max(maxy, pos[id].y + NH); });
  k = Math.min(1, (r.width - 40) / Math.max(maxx, 1), (r.height - 40) / Math.max(maxy, 1)); tx = 20; ty = 20; apply();
}
var drag = null;
view.addEventListener("pointerdown", function (e) { if (e.target.closest && e.target.closest(".node,.conn")) return; drag = { x: e.clientX, y: e.clientY, tx: tx, ty: ty }; view.classList.add("drag"); view.setPointerCapture(e.pointerId); });
view.addEventListener("pointermove", function (e) { if (!drag) return; tx = drag.tx + e.clientX - drag.x; ty = drag.ty + e.clientY - drag.y; apply(); });
view.addEventListener("pointerup", function () { drag = null; view.classList.remove("drag"); });
view.addEventListener("wheel", function (e) {
  e.preventDefault();
  var r = svg.getBoundingClientRect(), px = e.clientX - r.left, py = e.clientY - r.top, nk = Math.min(4, Math.max(0.1, k * (e.deltaY < 0 ? 1.12 : 1 / 1.12)));
  tx = px - (px - tx) * (nk / k); ty = py - (py - ty) * (nk / k); k = nk; apply();
}, { passive: false });
draw(); fit();
changes();
focusFromHash();
})();
