<!-- markdownlint-disable MD013 -->
# Tester kit: run it, read the report, file it

A tester runs one prepared kit on hardware the project does not own and sends
back one JSON report. This page covers the report program
([ADR-0173](../adr/0173-tester-programme.md)). The stage runners and their pass
rules are in [tester kit stages](../usage/tester.md). The images that bundle
the kit (NVIDIA, Intel, AMD, arm64) are later children of
[#108](https://github.com/VMAFx/pelorus/issues/108); `libpelorus_suite` and
`registration` have no runner yet and are recorded as `not_run` with that
reason.

A tester package is evidence for one `master` commit, named
`tester-<YYYYMMDD>-<sha8>`. It is not a release and is never `latest`.

## 1. Run the kit

From a checkout, with Python 3 and (for the probe) `vulkaninfo`:

```bash
python3 -I tools/tester/pelorus_tester_report.py run --out pelorus-tester-report
```

Inside a tester image the same command is the entry point. Give the container
the device it should test, or the GPU stages report `no_device`:

| Hardware | Container option |
| --- | --- |
| Intel, AMD | `--device /dev/dri` |
| NVIDIA | `--gpus all` (needs the NVIDIA Container Toolkit; the driver comes from the host) |

Options of `run`:

| Option | Effect |
| --- | --- |
| `--out DIR` | where `report.json`, `SHA256SUMS` and `manifest.json` go (default `pelorus-tester-report`) |
| `--require-device` | a missing hardware device ends the run with exit 100 instead of 0 |
| `--bench` | also run the opt-in bench stage (performance data, never gating) |
| `--note TEXT` | free text stored in the report (redacted like everything else) |
| `--plan FILE` | JSON that overrides stage commands, `{"stages": {"<id>": {"argv": [...], "timeout_s": 60, "expect": "regex"}}}` |

The run needs no network. It writes the report only under `--out`, keeps its
scratch files in a temporary directory that it removes, and reads fixtures from
the cache described in [tester kit stages](../usage/tester.md#fixtures).
`FFMPEG_BIN` names the patched FFmpeg; `PELORUS_VALIDATE=1` fails every GPU
stage when the Vulkan validation layer is absent.

## 2. Read the report

The program prints one line per stage and a verdict. The exit code is the
verdict:

| Exit | Verdict | Meaning |
| --- | --- | --- |
| 0 | `pass` | no stage failed; `no_device` and `not_run` stages carry a reason |
| 1 | `fail` | at least one stage failed its pass rule |
| 2 | `incomplete` | a stage timed out or could not be run, or the report was refused |
| 100 | `unavailable` | `--require-device` was given and a stage found no hardware device |

Stage statuses:

| Status | Meaning |
| --- | --- |
| `pass` | exit code 0 and, if the stage names one, the expected output matched |
| `fail` | non-zero exit code, the expected output was absent, or the stage's pass rule was not met |
| `not_run` | skipped on purpose, with the reason (opt-in stage, command not found, runner not yet part of the kit) |
| `no_device` | needs a hardware Vulkan device and none is visible; the reason names the option to add. Not a failure |
| `incomplete` | the stage timed out |

The eight stages run in this order: `probe` (`vulkaninfo --summary`),
`libpelorus_suite`, `registration`, `format_matrix`, `steering_smoke`,
`sidedata_roundtrip`, `zero_copy_chain`, `bench`. The pass rule of each stage
is listed in [tester kit stages](../usage/tester.md#stages). A software
device (lavapipe, `PHYSICAL_DEVICE_TYPE_CPU`) does not count as hardware.

`report.json` follows [`tools/tester/report.schema.json`](../../tools/tester/report.schema.json),
schema version 1. `SHA256SUMS` lists its hash and `manifest.json` lists the
hash of `SHA256SUMS`. Both give integrity only, not proof of who made the file.

### What the report contains

Tool and schema version, UTC time, the source commit and package name (from
`PELORUS_TESTER_COMMIT` and `PELORUS_TESTER_PACKAGE`, `unknown` and
`source-checkout` outside an image), OS, architecture, CPU count, Python
version, per device name, driver, Vulkan API version, vendor id and device
type, per stage status, reason, duration, exit code and the last 4000
characters of its output.

### What it excludes

Before writing, the program replaces and then re-scans for:

- host name, home directory, repository path and user name;
- Vulkan `deviceUUID`, `driverUUID` and LUID values, and any UUID;
- PCI bus ids (`0000:01:00.0`);
- `/home/<user>`, `/Users/<user>` and `C:\Users\<user>` paths.

If an identifier survives, the program writes no report and exits 2. Check the
file anyway before you send it; `--note` is your own text.

Check any report, including one you did not make:

```bash
python3 -I tools/tester/pelorus_tester_report.py validate pelorus-tester-report/report.json
python3 -I tools/tester/pelorus_tester_report.py validate report.json --forbid "$(hostname)"
```

`validate` exits 0 for a valid report. It rejects a missing field, a truncated
stage list, a schema-version mismatch, an exit code that does not follow from
the stages, a changed body (hash mismatch) and any identifier listed above.

## 3. File it

The intake path (an issue form and a tracked `docs/hardware-reports/`
directory) is [#233](https://github.com/VMAFx/pelorus/issues/233) and is not
live yet. Until then, attach `report.json` to a new issue on
[VMAFx/pelorus](https://github.com/VMAFx/pelorus/issues) and say which package
and machine you used. Do not attach logs from outside the kit.

## 4. Change the program

Gate for any change under `tools/tester/`:

```bash
python3 -I tools/tester/pelorus_tester_report.py --self-test
```

The self-test plants one case per rule: a CPU-only host (probe finds nothing)
must exit 0 with `no_device` stages and a valid report; a failing stage must
exit non-zero; a timeout must exit 2; `--require-device` must exit 100; and
the validator must reject each planted bad report (missing field, UUID, PCI
bus, user path, wrong exit mapping, schema-version mismatch, truncated stage
list, missing reason, malformed JSON, a forbidden literal). To prove a rule is
live, switch it off and expect the self-test to fail:

```bash
python3 -I tools/tester/pelorus_tester_report.py --self-test --disable redaction   # exits 1
```

Rule names for `--disable`: `no_device_nonfailure`, `redaction`,
`exit_mapping`, `truncation`, `schema_version`, `reason_required`,
`stage_failure`, `bench_nongating`, plus the stage and fixture rules listed in
[tester kit stages](../usage/tester.md#planted-failures). Changing a report field means changing the schema, the
program, a planted case and this page in one change.

## Related

- Licence rules for what goes into a tester artifact:
  [ADR-0173](../adr/0173-tester-programme.md) and [licensing](../licensing.md).
- Design and the vmafx precedent: [research 0172](../research/0172-tester-programme.md).
