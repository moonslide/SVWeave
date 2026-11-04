# Example: per-commit labels on a real SoC (C906-Minimal-SOC)

What this shows: several sub-systems of one SoC changed by several small commits, one of them
twice and one by a deletion only, drawn on one page with each changed instance labelled by the
commits that changed it (`svweave graph export --diff-base ... --diff-head ... --per-commit`).

## Design and history

* Design: [yuffeenn/C906-Minimal-SOC](https://github.com/yuffeenn/C906-Minimal-SOC) at
  `b1e4b60`: the XuanTie OpenC906 RISC-V core in a minimal SoC (`soc`: `c906_wrap`,
  `apb_sub_system` with a UART, `axi_interconnect_wrap`, `axi2apb_wrap`, SRAM, clocks). Its own
  filelists `smart_run/filelists/{C906_asic_rtl,tdt_dmi_top_rtl,soc}.fl` (281 sources, about 141k
  lines, `${CODE_BASE_PATH}` = `rtl/`). No source of that repository is copied here.
* Its real history has ONE RTL commit (`6f0c0a5 Initial commit`); the others change docs. So the
  six commits below are **synthetic DEMO commits made in a local clone only** (branch
  `demo-subsystem-edits` on top of `main`), never pushed:

| Commit | Changes | Expected labels |
|---|---|---|
| DEMO 1 | `apb_sub_system`: one line edited | apb_sub_system |
| DEMO 2 | `uart_baud_gen` (the UART inside apb_sub_system): one line added | uart_baud_gen |
| DEMO 3 | `axi_interconnect_wrap`: one line added | axi_interconnect_wrap |
| DEMO 4 | `apb_sub_system` again: one line edited | apb_sub_system |
| DEMO 5 | `axi2apb_wrap`: one line deleted, nothing added | axi2apb_wrap |
| DEMO 6 | README only | none |

## Workspace

`svweave.yaml` in the clone (git-excluded), profile `soc`: the three filelists, `top_modules:
[soc]`, `compilation_units: single` (the `.h` headers are listed as sources, VCS style),
`environment_allowlist: [CODE_BASE_PATH]` in the profile AND in the trusted `policy.json` (the rule
of the council-review fix), and `language_options.timescale: "1ns/1ps"`: without it the build is
PARTIAL with 277 `MissingTimeScale` errors (some files have a `` `timescale ``, others rely on the
simulator default), which SVWeave reports with this exact remedy.

## Results (one 4-CPU cloud session, real pyslang 12 under bwrap)

* Build of `demo-subsystem-edits`: COMPLETE, NO_ERRORS, 2,072 instances, 37,784 declarations,
  38 s.
* Attribution, exactly as expected: apb_sub_system <- DEMO 1, DEMO 4; uart_baud_gen <- DEMO 2;
  axi_interconnect_wrap <- DEMO 3; axi2apb_wrap <- DEMO 5 (deletion only); DEMO 6 "changes no
  drawn module/interface". Every changed file mapped on the new side, `aligned: true`.
* Page: 20 of 2,072 instances (4 changed, their subtrees, `soc` as context), 0.18 MB, against
  12.66 MB for the whole build. CLI wall time, three runs each, same build:

| Export | Runs (s) |
|---|---|
| whole build | 4.17, 3.68, 3.45 |
| `--diff-base main --diff-head demo-subsystem-edits --per-commit` | 2.45, 2.04, 2.51 |

  The per-commit page includes running `git diff` and one `git log -L` per changed module. The
  saving here is mostly the page size; the time is dominated by start-up and the build, which the
  page does not repeat. One host, one design, a handful of runs: not a benchmark.

`per-commit-demo4.png`: the page after clicking DEMO 4 in the commit list. apb_sub_system and
everything inside it are highlighted; uart_baud_gen (changed by DEMO 2) and the other changed
sub-systems are dimmed.

Two defects of the feature were found with this example and fixed before this was recorded: the
viewer script did not parse (a `\n` escape in the Python template; GRF-U05 now parses it) and an
instance between two changed instances was marked context instead of inside (GRF-I13).
