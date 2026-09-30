# Office-box handover evidence (2026-09-30)

Orphan branch; never merge. Raw text evidence from the Windows office box
(Intel Arc B580 + UHD 770), copied for the home workstation. The handover
summary and next steps are in the GitHub issue titled
"Handover: office box → home workstation (2026-09-30)".

- `shw-results/` — logs, CSVs and summaries from the Intel hardware stages:
  `shw-1` (native MSYS2 UCRT64 FFmpeg n9.0.2 + 18-patch build, device map),
  `shw-2` (Vulkan format matrix + 482-run option sweep with validation layers
  on both GPUs, driver bug reproducers), `qsv` (partial QSV MBQP /
  surface-lifetime runs; interrupted by host crashes).
- `shw-scratch/` — every script those stages ran (bash for MSYS2, Python sweep,
  C probes such as `vk-hostcopy-repro.c`, `vk-fmtprops3-sweep.c`,
  `vpl-mbqp-query.c`).
- `adopt-scratch-evidence/` — evidence from the unfinished full Praetor
  adoption (PR #67).
- `STATE-office-2026-09-30.md` — the office session log (`.workingdir/STATE.md`
  is git-ignored); merge it into the home `.workingdir/STATE.md`.

Only text files under 512 KiB were copied. Encoded bitstreams, raw video,
OCI tarballs and binaries stayed on the office box under `C:\tmp\pel\`.
