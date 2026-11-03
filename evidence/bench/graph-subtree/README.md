# Graph export: whole build vs `--root` subtree (paired timing)

Script: `scripts/benchmark_graph_subtree.py` (sha256 in each JSON). Design: the hand-written fixture
`fixtures/language/grf_subtree` (`grf_soc`, CLUSTERS x CORES x 4 pipeline stages), elaborated once
by the real pyslang 12 worker under mandatory bwrap. Source identity in each JSON: the `src/`
tree hash equals that of commit `342d388` (the change itself) in all three files; `c4x4` and
`c32x64` record git head `04fddfa` because they ran just before that commit on the same code. Host: Intel Xeon @ 2.10GHz, 4 logical CPUs,
15.7 GiB RAM, Linux 6.18, Python 3.12, page cache warm. One host, one synthetic design: this is not
the plan 22.3 reference corpus and makes no qualification claim.

`cli` = wall time of the real `svweave` subprocess (Python start-up included, about 1.1 s here);
`in-process` = `graph.export_graph` only. Medians; raw samples are in the JSON. Each run also
checks that every subtree page equals the full page restricted to that subtree (all PASS).

| File | Instances | Mode | CLI median | In-process median | Page |
|---|---:|---|---:|---:|---:|
| `c4x4.json` (2 runs) | 90 | full | 1.173 s | 0.033 s | 0.20 MB |
| | | cluster subtree (22) | 1.232 s | 0.010 s | 0.06 MB |
| | | core subtree (5) | 1.155 s | 0.007 s | 0.03 MB |
| `c32x64.json` (5 runs) | 10,306 | full | 2.927 s | 1.563 s | 19.4 MB |
| | | cluster subtree (322) | 1.331 s | 0.039 s | 0.62 MB |
| | | core subtree (5) | 1.213 s | 0.007 s | 0.03 MB |
| `c64x128.json` (3 runs) | 41,090 | full | 8.429 s | 7.222 s | 77.6 MB |
| | | cluster subtree (642) | 1.244 s | 0.060 s | 1.23 MB |
| | | core subtree (5) | 1.160 s | 0.005 s | 0.03 MB |

Reading: on a small design the CLI time is start-up either way (no user-visible saving; the
cluster run even measured slower, within noise). The subtree cost follows the size of the subtree,
not of the design, so the saving grows with the design: 2.2x (CLI) at 10k instances and 6.8x at
41k; the export work itself 40x and 120x for a cluster. An earlier run of the same design sizes
(before a NULL-safe sort-key change in `graph.py`) measured 3.25 s -> 1.19 s and 10.8 s -> 1.29 s;
it is superseded by these files.
