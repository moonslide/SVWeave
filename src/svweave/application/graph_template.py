"""Self-contained HTML viewer for ``svweave graph export`` (ADR-014).

One file, no external resource: the CSP below forbids every network fetch, the data is an inline
JSON block and the script writes source-derived text with ``textContent`` only (never innerHTML).
"""

from __future__ import annotations

import base64
import hashlib
import json
from importlib.resources import files
from typing import Any

_PLACEHOLDER = "@@SVWEAVE_GRAPH_DATA@@"


def dumps_safe(obj: Any) -> str:
    """JSON for a <script type=application/json> element: ASCII only (so U+2028/2029 are escaped)
    and ``<``, ``>``, ``&`` escaped, so source text can never close the element or open a comment."""
    return (
        json.dumps(obj, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


_CSP_HEAD = "@@SVWEAVE_CSP@@"


def _csp(script_src: str) -> str:
    return (f"default-src 'none'; style-src 'unsafe-inline'; script-src {script_src}; "
            "img-src data:; base-uri 'none'; form-action 'none'")


def render_html(model: dict[str, Any], *, lazy: bool = False) -> str:
    page = _PAGE.replace(_PLACEHOLDER, dumps_safe(model))
    return page.replace(_CSP_HEAD, CSP_LAZY if lazy else CSP_STRICT, 1)


_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="@@SVWEAVE_CSP@@">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SVWeave graph</title>
<style>
:root{--bg:#fff;--fg:#1d2330;--mut:#667085;--line:#c5ccd8;--card:#f5f7fa;--acc:#2457d6;--con:#d9480f;--ok:#2b8a3e;--warn:#b7791f;--bad:#c92a2a;color-scheme:light}
@media (prefers-color-scheme:dark){:root{--bg:#14171f;--fg:#e6e9f0;--mut:#98a2b3;--line:#3a4252;--card:#1d222d;--acc:#7aa2ff;--con:#ff8a5c;--ok:#69db7c;--warn:#fcc419;--bad:#ff8787;color-scheme:dark}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.4 system-ui,sans-serif;display:flex;flex-direction:column;height:100vh}
header{padding:8px 16px;border-bottom:1px solid var(--line);display:flex;gap:12px;align-items:center;flex-wrap:wrap}
h1{font-size:16px;margin:0}
.badge{padding:1px 8px;border-radius:10px;border:1px solid var(--line);font-size:12px}
.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}
#banner{padding:6px 16px;background:var(--card);border-bottom:1px solid var(--line);color:var(--warn);font-size:13px}
#banner:empty{display:none}
#bar{padding:6px 16px;display:flex;gap:10px;align-items:center;flex-wrap:wrap;border-bottom:1px solid var(--line)}
input[type=search]{padding:4px 8px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--fg);min-width:200px}
button{padding:3px 10px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--fg);cursor:pointer}
main{flex:1;display:flex;min-height:0}
#view{flex:1;min-width:0;overflow:hidden;cursor:grab;touch-action:none}
#view.drag{cursor:grabbing}
svg{width:100%;height:100%;display:block}
aside{width:340px;max-width:45vw;border-left:1px solid var(--line);overflow:auto;padding:10px 14px;background:var(--card)}
aside h2{font-size:14px;margin:12px 0 4px}
aside dl{margin:0;display:grid;grid-template-columns:auto 1fr;gap:2px 10px}
aside dt{color:var(--mut)}aside dd{margin:0;word-break:break-all}
table{border-collapse:collapse;width:100%;font-size:12px}
td,th{border-bottom:1px solid var(--line);padding:2px 4px;text-align:left;vertical-align:top;word-break:break-all}
.node rect{fill:var(--card);stroke:var(--line);stroke-width:1.2}
.node.top rect{stroke:var(--acc);stroke-width:2}
.node.stub rect,.node.blackbox rect{stroke-dasharray:4 3;stroke:var(--warn)}
.node.changed rect{fill:color-mix(in srgb,var(--bad) 18%,var(--card));stroke:var(--bad);stroke-width:2}
.node.context rect{stroke-dasharray:3 3;opacity:.75}
.node.portal rect{stroke:var(--acc);stroke-width:2.5;stroke-dasharray:8 3}
#crumbs a{color:var(--acc)}
.portal-link{display:inline-block;margin:6px 0;font-weight:600;color:var(--acc)}
.crow{cursor:pointer}
td.sha{white-space:nowrap;font-family:ui-monospace,Menlo,monospace}
.crow:hover td,.crow.on td{background:color-mix(in srgb,var(--acc) 16%,transparent)}
.crow:focus-visible{outline:2px solid var(--acc)}
dd{white-space:pre-line}
.node.sel rect{stroke:var(--acc);stroke-width:3}
.node.hit rect{fill:color-mix(in srgb,var(--acc) 22%,var(--card))}
.node.dim{opacity:.35}
.node text{fill:var(--fg);font-size:12px;pointer-events:none}
.node text.sub{fill:var(--mut);font-size:10.5px}
.node{cursor:pointer}
.hier{fill:none;stroke:var(--line);stroke-width:1.2}
.conn{fill:none;stroke:var(--con);stroke-width:1.4;stroke-dasharray:5 3;opacity:.8;cursor:pointer}
.conn.sel{opacity:1;stroke:var(--acc)}
.cnt{fill:var(--con);font-size:11px;pointer-events:none}
.conn.und{stroke-dasharray:2 3}
small{color:var(--mut)}
</style>
</head>
<body>
<header><h1>SVWeave graph</h1><span id="meta"></span></header>
<div id="banner"></div>
<div id="bar">
<input id="q" type="search" placeholder="search path or definition" aria-label="search">
<label><input id="showConn" type="checkbox" checked> connections</label>
<button id="expand">expand all</button><button id="collapse">collapse all</button>
<button id="fit">fit</button><span id="count"></span>
</div>
<main>
<div id="view"><svg id="svg" role="img" aria-label="instance hierarchy graph"><defs>
<marker id="arr" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="#d9480f"/></marker></defs><g id="root"></g></svg></div>
<aside id="side"><p><small>Click a node or a connection (a line stands for all signals between two nodes). Double-click a node to collapse or expand it. Drag to pan, wheel to zoom.</small></p></aside>
</main>
<script id="data" type="application/json">@@SVWEAVE_GRAPH_DATA@@</script>
<script>@@SVWEAVE_VIEWER@@</script>
</body>
</html>
"""


def _viewer_script() -> str:
    """graph_viewer.js with the optional node keys of the codec filled in: one definition of
    them, in Python (second council review, architecture findings 3 and 4)."""
    from svweave.application.graph_codec import OPTIONAL_KEYS

    js = files("svweave.application").joinpath("graph_viewer.js").read_text(encoding="utf-8")
    if js.count("@@SVWEAVE_OPTIONAL_KEYS@@") != 1 or "</script" in js.lower():
        raise RuntimeError("graph_viewer.js is not the viewer this module expects")
    return js.replace("@@SVWEAVE_OPTIONAL_KEYS@@", json.dumps(list(OPTIONAL_KEYS)), 1)


_PAGE = _PAGE.replace("@@SVWEAVE_VIEWER@@", _viewer_script(), 1)


def _viewer_hash() -> str:
    """CSP source of the one inline script: its sha256, so no other inline script ever runs
    (a JSON data block is not executed and needs none)."""
    start = _PAGE.index("<script>\n") + len("<script>")
    body = _PAGE[start:_PAGE.index("</script>", start)]
    return "'sha256-" + base64.b64encode(hashlib.sha256(body.encode()).digest()).decode() + "'"


VIEWER_HASH = _viewer_hash()
CSP_STRICT = _csp(VIEWER_HASH)
#: --lazy only: the page loads its data files (c-<hash>.js in its own directory) with <script src>;
#: on file:// that needs 'self' (tested in Chromium; SRI cannot be used there, fetch is blocked).
#: In Chromium 'self' on a file: page admits any local file: the loader only ever asks for
#: c-<10 hex>.js beside the page (safeChunk)
CSP_LAZY = _csp("'self' " + VIEWER_HASH)
