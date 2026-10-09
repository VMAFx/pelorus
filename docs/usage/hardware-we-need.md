<!-- markdownlint-disable MD013 -->
# Hardware we need reports from

Pelorus runs on Vulkan, so one binary covers every vendor, but the Vulkan driver
and the hardware encoder differ per vendor and the project owns few of them. If
you have one of the machines below, one command gives the project a report it
cannot produce itself, and you can be credited for it.

## What is wanted

| Hardware | Why it matters | Package |
| --- | --- | --- |
| NVIDIA, Turing to Blackwell (GeForce RTX 20 to 50, RTX professional) | NVENC steering and the Vulkan path on the proprietary driver; the project tests a GeForce RTX 4090 only | NVIDIA GPU image |
| Intel Arc and Xe graphics (Alchemist, Battlemage, Core Ultra) | QSV steering and Vulkan Video on ANV; the project tests one Arc A380 only | Intel GPU image |
| AMD Radeon (RDNA 2 to RDNA 4, Radeon iGPUs) | Vulkan compute and Vulkan Video encode on RADV; the project tests one Ryzen iGPU only | AMD GPU image |
| Windows 11 with any of the above | the native Windows driver stacks differ from Linux | Windows zip |
| Any machine without a GPU | software Vulkan (lavapipe) is functional evidence only and never counts as a GPU result, but it shows which stages run without a device | CPU image |

The images and the Windows zip are built by the tester programme
([ADR-0173](../adr/0173-tester-programme.md)); until a package for your machine is
published, run the kit [from a checkout](../development/tester.md#1-run-the-kit).
The hardware listed here is what the project could not test itself on 2026-10-09;
a report from a machine already tested repeats the measurement on other silicon
and is welcome too.

## What to do

1. Run the kit and keep `report.json`: [tester kit](../development/tester.md).
2. Open a [hardware report issue](https://github.com/VMAFx/pelorus/issues/new?template=hardware_report.yml)
   and attach the file. Say which package and machine you used. A report with
   verdict `fail` is welcome.
3. Optional: give a name or GitHub handle to be credited. Leave it empty to be
   listed as anonymous. Do not give an e-mail address; the field is published.

## Send a report

A maintainer commits an accepted report to [`docs/hardware-reports/`](../hardware-reports/index.md).
You can also open a pull request with the file yourself.

- **File name.** `YYYY-MM-DD-<slug>.json`, lower case, digits and `-`; the date
  is the report's `generated_utc` date.
- **Content.** An intake record: `{"intake_version": 1, "credit": "", "machine": "",
  "report": <the unchanged report.json>}`. `credit` and `machine` are optional;
  an empty or missing `credit` is listed as `anonymous`.
- **What is checked** by `scripts/hardware-reports/check-reports.py`, in CI and in
  `make docs-check`:
  the record against [`intake.schema.json`](../hardware-reports/intake.schema.json);
  the report with the tester's own validator (schema version 2, body hash,
  redaction, execution class); the verdict (`pass` or `fail`; `incomplete` and
  `unavailable` runs are not evidence); a full 40-character source commit; and
  the tool hash.
- **Tool hash.** `tool.sha256` in the report is a digest of the tester program
  files (`pelorus_tester_report.py`, `pelorus_tester_stages.py`,
  `pelorus_tester_fixtures.py`, `report.schema.json`). It must appear in
  [`tools/tester/tool-hashes.json`](../../tools/tester/tool-hashes.json) with the
  same tool version, so a report from a modified or unknown program version is
  refused. It catches an altered program and an accidental hand edit. It does not
  prove who made the report; anyone can recompute the hashes of a file they edit.
- **Index.** [`docs/hardware-reports/index.md`](../hardware-reports/index.md) is
  generated: `python3 -I scripts/hardware-reports/generate-index.py --write`.
  `--check` fails when it is stale.

A report that claims GPU evidence (`evidence_claim: gpu`) must come from a
`hardware` execution class; see
[execution class and evidence claim](tester.md#execution-class-and-evidence-claim).
