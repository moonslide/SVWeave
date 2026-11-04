# Large SoC pages: compact format (A), --split atlas (B), --lazy parts (C)

One 4-CPU cloud session (Intel Xeon @ 2.80GHz, 15.7 GiB), real pyslang 12 under bwrap, Chromium
from Playwright for the browser figures. Not a qualification run; a handful of runs, not a
benchmark. Design: the hand-written fixture `fixtures/language/grf_subtree` at CLUSTERS=180,
CORES=128, i.e. **115,562 instances**, plus the C906 minimal SoC (2,072 instances,
`evidence/c906-per-commit-example/`).

## A. Compact page format (svweave-graph/2), lossless

| Page | Before | After |
|---|---|---|
| C906 complete topology (30,378 connections) | 15.73 MB | 1.98 MB (8.0x) |
| C906 per-commit diff page | 0.16 MB | 0.03 MB |
| 115,562 instances, complete (554,584 connections) | about 870 MB (7.6 KB per instance) | 40.9 MB |

`decode(encode(page)) == page` on both C906 pages; the viewer's JavaScript decoder returns what
the Python one does (GRF-C03).

## B and C on 115,562 instances (default budget 5,000)

| Output | Export (CLI) | Files | Largest file | Open + first draw in Chromium | JS heap |
|---|---|---|---|---|---|
| single complete page | 61.9 s | 1 | 40.9 MB | 10.1 s | 304 MB |
| `--split` | 51.4 s | 174 pages, largest 4,669 instances, 42.3 MB total | 2.9 MB | 0.56 s (index) | 19 MB |
| `--lazy` | 49 s | 1 page + 173 parts, 37.4 MB total | 2.9 MB | 0.55 s (index) | 19 MB |

* `--split`: a search over all 115,562 instances on the index page took 0.77 s; opening the
  result's page and selecting the instance 0.92 s.
* `--lazy`: opening the same search result (its part loaded into the page, instance selected)
  1.0 s.
* No page error in any run. The build itself took 247 s.

## Correctness of the cut

* GRF-I15 / GRF-I17 (610 instances, budget 200): every instance on exactly one page or part,
  same ports and parameters as on the single complete page, the union of all edges equals the
  complete page's edge set with no edge twice; every portal leads to the page or part whose root
  it is; the search index maps every instance to where it is shown.
* C906 with budget 300 (16 parts) in Chromium: loading every part of the `--lazy` page gives
  2,072 instances and 30,378 connections, exactly the single complete page; a `--split` search
  result six levels deep opens page 8 of 16 with the right breadcrumb and selects the instance.

## Limits

* `--lazy` needs `script-src 'self'` on its index page (Chromium blocks local scripts otherwise,
  SRI always fails on file://, fetch is blocked). Verified in Chromium only; Firefox treats file://
  origins more strictly. `--split` uses plain links and the strict CSP.
* A subtree under 50 instances is never a page of its own, so a page can exceed the budget; the
  envelope reports oversized pages (none here).
