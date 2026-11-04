# Graph export: whole build vs `--root` subtree vs `--diff-base` diff view (paired timing)

Script: `scripts/benchmark_graph_subtree.py` (sha256 in each JSON). Design: the hand-written fixture
`fixtures/language/grf_subtree` (`grf_soc`, CLUSTERS x CORES x 4 pipeline stages), elaborated once
by the real pyslang 12 worker under mandatory bwrap. The two diff views are commit-to-commit diffs
of the workspace's own git repository against the build of `main`: `chg-reduce` changes one line
of `grf_reduce` (CLUSTERS+1 instances, a localized change), `chg-core` one line of `grf_core`
(CLUSTERS*CORES instances with their stages: most of the design).

Source identity: the `src/` tree hash in all three files equals commit `862cd1b` (the diff view);
`c4x4` and `c32x64` record git head `ffaf3b4` because they ran just before that commit on the same
code. Host: Intel Xeon @ 2.10GHz, 4 logical CPUs, 15.7 GiB RAM, Linux 6.18, Python 3.12, page cache
warm. One host, one synthetic design: not the plan 22.3 reference corpus, no qualification claim.
This host measured slower than the one of the previous version of this file (full export at 41k
instances 14.6 s here vs 8.4 s there); compare rows within one file only.

`cli` = wall time of the real `svweave` subprocess (Python start-up included, about 1.5 s here);
`in-process` = `graph.export_graph` only (for the diff views this includes running `git diff` and
mapping it). Medians; raw samples are in the JSON. Each run also checks every subtree page, and one
changed instance of each diff page, against the full page (same nodes, ports, edges): all PASS.

| File | Instances | Mode | Drawn | CLI median | In-process median | Page |
|---|---:|---|---:|---:|---:|---:|
| `c4x4.json` (2 runs) | 90 | full | 90 | 1.640 s | 0.048 s | 0.20 MB |
| | | cluster subtree | 22 | 1.583 s | 0.010 s | 0.06 MB |
| | | core subtree | 5 | 1.609 s | 0.007 s | 0.03 MB |
| | | diff, grf_reduce changed | 10 | 1.596 s | 0.021 s | 0.04 MB |
| | | diff, grf_core changed | 85 | 1.550 s | 0.031 s | 0.19 MB |
| `c32x64.json` (5 runs) | 10,306 | full | 10,306 | 3.905 s | 2.055 s | 19.4 MB |
| | | cluster subtree | 322 | 1.644 s | 0.083 s | 0.63 MB |
| | | core subtree | 5 | 1.632 s | 0.010 s | 0.03 MB |
| | | diff, grf_reduce changed | 66 | 1.675 s | 0.056 s | 0.11 MB |
| | | diff, grf_core changed | 10,273 | 4.401 s | 2.402 s | 19.5 MB |
| `c64x128.json` (3 runs) | 41,090 | full | 41,090 | 14.610 s | 10.101 s | 77.6 MB |
| | | cluster subtree | 642 | 1.697 s | 0.113 s | 1.23 MB |
| | | core subtree | 5 | 1.564 s | 0.011 s | 0.03 MB |
| | | diff, grf_reduce changed | 130 | 1.628 s | 0.149 s | 0.20 MB |
| | | diff, grf_core changed | 41,025 | 15.727 s | 11.449 s | 78.2 MB |

Reading:

* A subtree or a localized diff costs what it draws, not what the design holds: at 41k instances
  the reducer diff takes 1.63 s instead of 14.6 s (9x on the CLI, 68x in-process), at 10k 2.3x.
* A diff that changes a module instantiated almost everywhere (`grf_core`) draws almost the whole
  design and is slightly SLOWER than the full export (15.7 s vs 14.6 s): it pays for git and the
  mapping and saves nothing. The view is only as small as the change.
* On the 90-instance design every mode is Python start-up (no visible difference).
