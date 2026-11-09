<!-- markdownlint-disable MD013 -->
# CLI demonstrator golden transcripts and QP report validation

**Date:** 2026-11-09

**Decision:** [ADR-0249](../adr/0249-tool-cli-transcript-tests.md).

**Scope:** CLI demonstrator output and argument testing for `tools/pelorus_qp_report`, the stdlib-only transcript runner `tools/transcript.py`, telemetry fixture `x265-4.3-crf28-psnr.csv`, and fixes for three tool defects (silent fallback sentinel collision, undefined float conversion, and silent frame truncation).

## Verdict

The golden transcript test `tools/transcripts/pelorus_qp_report.t` (27 cases) covers full stdout, stderr, and exit code verification for `pelorus_qp_report`. Replacing the former five exit-code-only tests in `tools/meson.build` ensures output regressions (such as swapping U and V PSNR fields) and boundary argument handling cannot pass silently. The stdlib-only driver `tools/transcript.py` executes without shell dependencies across all CI platforms (Linux, macOS, Windows MSYS2, Windows MSVC).

## Measurements

### Fixed tool defects

1. **Option presence without user-reachable sentinel (Bug A):** Initialised `requested_qp = NAN`. Validated against the x265 QP range `0.0 <= q <= 69.0` (x265 4.3 `--qpmax` default 69). Out-of-range values produce stderr `pelorus_qp_report: --requested-qp %g is outside 0..69 (the x265 QP range)` followed by the usage line, exiting 1. Normalised `-0` to `+0` (`q + 0.0`) so `-0` outputs `0.00`.
2. **Undefined double-to-float conversion (Bug B):** Finite double values exceeding float range (e.g. `1e300`) are rejected at the `0..69` bounds check before reaching `(float)requested_qp`, eliminating undefined behaviour under C11 6.3.1.5p1.
3. **Explicit CSV truncation diagnostic (Bug C):** On `PEL_ERR_RANGE` from `pel_x265_csv_parse` (input exceeding `MAX_FRAMES` 4096), `pelorus_qp_report` emits a diagnostic warning to stderr naming the file and bounds (`pelorus_qp_report: warning: %s has more than 4096 frame rows; the report covers the first 4096`), then continues processing the first 4096 frames with exit 0.

### Fixture recording (`x265-4.3-crf28-psnr.csv`)

Recorded in an isolated temporary working directory with unedited x265 4.3 output:

- Command: `/usr/bin/ffmpeg -hide_banner -loglevel error -f lavfi -i testsrc2=size=176x144:rate=25 -frames:v 24 -pix_fmt yuv420p -f rawvideo -y src.yuv`
- Command: `/usr/bin/x265 --input src.yuv --input-res 176x144 --fps 25 --frames 24 --crf 28 --psnr --csv x265-crf28-psnr.csv --csv-log-level 1 --output x265-crf28-psnr.hevc --pools 1 --frame-threads 1 --no-progress`
- Frame data rows 1–24 are byte-identical across repeated runs; only the Summary timestamp and elapsed time differ.
- Demonstrator output on fixture:
  - `frames parsed     : 24`
  - `avg_qp (bit-wtd)  : 31.0561`
  - `total_bits        : 118640`
  - `psnr_y/u/v (dB)   : 37.606 / 36.522 / 36.230`
  - With `--requested-qp 32`: `honored_fraction  : 0.1250`

### Driver self-test matrix

`python3 -I tools/transcript.py --exe <exe> --tool pelorus_qp_report --fixtures <dir> --self-test` verifies driver error handling via planted inline transcripts:

| Plant | Description | Expected rc | Measured rc |
| :--- | :--- | :--- | :--- |
| `a` | Unexpected stdout line | 1 | 1 |
| `b` | Unexpected stderr line | 1 | 1 |
| `c` | Mismatched exit code | 1 | 1 |
| `d` | Missing required `[exit N]` | 2 | 2 |
| `e` | Unknown line prefix | 2 | 2 |
| `f` | Path traversal in fixture (`% copy ../x y`) | 2 | 2 |
| `g` | Shell quotes in command line (`"quoted"`) | 2 | 2 |
| `pass` | Valid matching command and streams | 0 | 0 |

### Transcript test matrix

`tools/transcripts/pelorus_qp_report.t` evaluates 27 test cases covering:

- **Usage/negative:** no args (rc 1), missing value (rc 1), non-numeric `1x` (rc 1), non-finite `nan`/`inf` (rc 1), overflow `1e309` (rc 1), extra positional (rc 1), unknown flag (rc 1), duplicate flag (rc 1), nonexistent file (rc 1), empty CSV (rc 1), header-only CSV (rc 1).
- **Positive:** baseline report without option (rc 0), report with flat requested QP (rc 0), flag before positional argument (rc 0), PSNR-enabled CSV without option (rc 0), PSNR-enabled CSV with `--requested-qp 32` (rc 0), non-ASCII filename `café_文件.csv` (rc 0).
- **Boundary:** `--requested-qp 0` (rc 0), `--requested-qp 69` (rc 0), `--requested-qp -0` (rc 0, prints `0.00`), rejected `-0.01` (rc 1), rejected `69.01` (rc 1), rejected `-1` (rc 1), rejected `1e300` (rc 1), repeat 4096 frames (rc 0, no warning), repeat 4097 frames (rc 0, stderr warning).

## Limits

- The transcript runner supports simple space-separated arguments without quotes or shell escapes. Complex shell pipelines remain in bash integration scripts.
- Fixture command strings recorded in the CSV Summary row are validated to ensure no absolute or build-directory paths leak into repository fixtures.

## Evidence

- `meson test -C $TMPDIR/b tool-qp-report-transcript tool-transcript-self-test tool-argv --verbose` (3 passed, exit 0).
- `python3 -I tools/transcript.py --exe $TMPDIR/b/tools/pelorus_qp_report --tool pelorus_qp_report --fixtures libpelorus/test/fixtures/telemetry tools/transcripts/pelorus_qp_report.t` (27 cases passed, exit 0).
- `python3 -I tools/transcript.py --exe $TMPDIR/b/tools/pelorus_qp_report --tool pelorus_qp_report --fixtures libpelorus/test/fixtures/telemetry --self-test` (8 plants passed, exit 0).
