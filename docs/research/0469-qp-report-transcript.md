<!-- markdownlint-disable MD013 -->
# CLI demonstrator golden transcripts and QP report validation

**Date:** 2026-11-09

**Decision:** [ADR-0249](../adr/0249-tool-cli-transcript-tests.md).

**Scope:** CLI demonstrator output and argument testing for `tools/pelorus_qp_report`, the stdlib-only transcript runner `tools/transcript.py`, telemetry fixture `x265-4.3-crf28-psnr.csv`, and fixes for three tool defects (silent fallback sentinel collision, undefined float conversion, and silent frame truncation).

## Verdict

The golden transcript test `tools/transcripts/pelorus_qp_report.t` (27 cases) covers full stdout, stderr, and exit code verification for `pelorus_qp_report`. Replacing the former five exit-code-only tests in `tools/meson.build` ensures output regressions (such as swapping U and V PSNR fields) and boundary argument handling cannot pass silently. The stdlib-only driver `tools/transcript.py` executes without shell dependencies. CI runs the transcript on every fast-suite job (the `core`, `arm64`, `sanitizers`, `windows` (MSYS2 UCRT64) and `windows-msvc` (cl, clang-cl) jobs of `.github/workflows/ci.yml`; `tools` option defaults on, `meson_options.txt:5`); measured locally on Linux x86_64 only.

## Measurements

### Fixed tool defects

1. **Option presence without user-reachable sentinel (Bug A):** Initialised `requested_qp = NAN`. Validated against the x265 QP range `0.0 <= q <= 69.0` (x265 4.3 `--qpmax` default 69). Out-of-range values produce stderr `pelorus_qp_report: --requested-qp %g is outside 0..69 (the x265 QP range)` followed by the usage line, exiting 1. Normalised `-0` to `+0` (`q + 0.0`) so `-0` outputs `0.00`.
2. **Undefined double-to-float conversion (Bug B):** C11 6.3.1.5p1 leaves a double to float conversion outside the float range undefined; Annex F (IEC 60559) implementations such as glibc give infinity; no sanitizer reports it (GCC and clang `-fsanitize=float-cast-overflow` cover float to integer only); measured on base a2d03bd: `--requested-qp 1e300` exits 0 and prints a 301-digit requested QP. Values outside 0..69 are rejected at the bounds check before reaching `(float)requested_qp` (`--requested-qp 1e300` -> exit 1, stderr `pelorus_qp_report: --requested-qp 1e+300 is outside 0..69 (the x265 QP range)`).
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
| `a` | Wrong byte in expected usage stderr line | 1 | 1 |
| `b` | Wrong exit code (`[exit 2]`) | 1 | 1 |
| `c` | Missing stderr line (usage line dropped) | 1 | 1 |
| `d` | Extra stdout line | 1 | 1 |
| `e` | Missing required `[exit N]` | 2 | 2 |
| `f` | Unknown line prefix | 2 | 2 |
| `g` | Path traversal in directive (`% copy ../x y`) | 2 | 2 |
| `h` | Quoted command argument (`"quoted"`) | 2 | 2 |
| `i` | Trailing space after last argument | 2 | 2 |
| `j` | Invalid destination syntax (`% copy x265-4.3-qp32.csv a:b`) | 2 | 2 |
| `k` | Leading dot in fixture name (`% copy .hidden y`) | 2 | 2 |
| `l` | Non-ASCII exit code (`[exit ²]`) | 2 | 2 |
| `m` | Copy non-ASCII filename directive (`% copy x265-4.3-qp32.csv café_文件.csv` + run) | 0 | 0 |
| `pass` | Valid matching command and streams (no args) | 0 | 0 |
| `n` | Empty transcript | 2 | 2 |
| `o` | Comment-only transcript | 2 | 2 |
| `p` | Output flood (`capture` stream limit) | 0 | 0 |
| `q` | Tool executable is a directory | 2 | 2 |
| `r` | Child process holds stdout after the tool exits (`capture` held-pipe verdict) | 0 | 0 |
| `s` | `% repeat` output exactly 8 MiB (128-byte header and rows), fixtures directory given as `<dir>/sub/..` so fixture lookup must compare resolved paths (a Windows 8.3 `TEMP` name differs from its resolved path the same way) | 0 | 0 |
| `t` | `% repeat` output one row over 8 MiB | 1 | 1 |

### Transcript test matrix

`tools/transcripts/pelorus_qp_report.t` evaluates 27 test cases covering:

- **Usage/negative:** no args (rc 1), missing value (rc 1), non-numeric `1x` (rc 1), non-finite `nan`/`inf` (rc 1), overflow `1e309` (rc 1), extra positional (rc 1), unknown flag (rc 1), duplicate flag (rc 1), nonexistent file (rc 1), empty CSV (rc 1), header-only CSV (rc 1).
- **Positive:** baseline report without option (rc 0), report with flat requested QP (rc 0), flag before positional argument (rc 0), PSNR-enabled CSV without option (rc 0), PSNR-enabled CSV with `--requested-qp 32` (rc 0), non-ASCII filename `café_文件.csv` (rc 0).
- **Boundary:** `--requested-qp 0` (rc 0), `--requested-qp 69` (rc 0), `--requested-qp -0` (rc 0, prints `0.00`), rejected `-0.01` (rc 1), rejected `69.01` (rc 1), rejected `-1` (rc 1), rejected `1e300` (rc 1), repeat 4096 frames (rc 0, no warning), repeat 4097 frames (rc 0, stderr warning).

## Runner measurements

Measured 2026-11-09 on Linux x86_64 with cargo 1.98.1 comparing candidate external runners (scrut 0.5.1 and trycmd 1.2.1) against the repository's fast-suite requirements.

### CI environment and toolchain constraints

The fast suite runs across five distinct CI legs in `.github/workflows/ci.yml`: `core`, `arm64`, `sanitizers`, `windows` (MSYS2 UCRT64), and `windows-msvc` (cl, clang-cl). The `windows-msvc` job runs steps under Git for Windows bash (`ci.yml:605-608`, `defaults.run.shell: bash`). The repository build requires only C, Meson, Ninja, python3 (`meson.build:52`) and bash (`meson.build:65`), while local MSVC execution runs from PowerShell (`docs/development/build.md:270-277`). Introducing an external test runner must not impose a shell between the test specification and the binary, nor require a new toolchain dependency across all CI legs (hard rule 9). Under Git for Windows bash, MSYS2 path translation rewrites arguments resembling POSIX paths before native binaries see them (msys2.org/docs/filesystem-paths, section "Process arguments"), which would test shell path translation rather than the demonstrator's argv parser.

### Candidate runner evaluation

- **scrut 0.5.1 (facebookincubator/scrut, MIT):**
  - Evaluated at tag v0.5.1 (commit ac2bb728).
  - Shell dependency: pipes every test expression through bash (scrut documentation `custom-shell.md` specifies bash >= 3.2).
  - Process execution: an `strace -f -e trace=execve` trace of a single test case executed bash, env, grep, sed, tail, head, and mkdir wrappers around the target binary.
  - Distribution and dependencies: newest release on crates.io is 0.4.3; v0.5.1 has no release assets, and building from source pulls 255 crate dependencies.
  - Verdict: rejected because a shell and POSIX userland sit between the transcript and the executable.

- **trycmd 1.2.1 (assert-rs/snapbox, MIT OR Apache-2.0):**
  - Evaluated at version 1.2.1 (crates.io; snapbox source commit a8a8e6a1a78a, `crates/trycmd/src/schema.rs:312`, `:603`).
  - Shell dependency: no shell; spawns the executable directly (verified via `strace -f -e trace=execve` showing only test binary and demonstrator execution).
  - Toolchain footprint: requires Cargo and `cargo test` harness, pulling 56 crates (7.08 MB source). The MSYS2 UCRT64 leg (`ci.yml:552-558`) installs no Rust or Cargo, so trycmd would add a new toolchain dependency on that leg (hard rule 9: an ADR must name the alternative and why it wins). While windows-2025 and ubuntu-26.04 images provide Cargo 1.99.1 (actions/runner-images main 741e4eb8ef5f), MSYS2 packaging remains separate.
  - Stream semantics: Markdown (`.md`) cases merge stderr into stdout. In TOML cases, omitting a `.stderr` file passed even when the executable wrote 2 lines to stderr (measured; `runner-trycmd-streams.log`).
  - Path redaction: `argv[0]` is the absolute path to the binary, requiring path redaction across environments.
  - Verdict: rejected due to new toolchain dependency on every leg and stream check limitations.

### Measurement methodology

- **How measured:** scrut built from tag v0.5.1 via `cargo build --release`. trycmd 1.2.1 configured as a dev-dependency in a scratch Cargo package with one `.toml` and one `.md` case targeting `tools/pelorus_qp_report`. Process execution traced with `strace -f -e trace=execve`.
- **Not measured:** windows-2025 execution was not run directly; the deciding constraints (shell wrapping and toolchain dependencies) are platform-independent.
- **Evidence logs:** raw logs recorded in `runner-trycmd.log`, `runner-trycmd-streams.log`, `runner-trycmd-execve.log`, `runner-scrut.log`, and `runner-images-rust.log` (maintainer local evidence, untracked).

## Mutation check

The 27-case transcript was evaluated against six planted mutations (m1–m6) in `tools/pelorus_qp_report.c`. Each mutant produced transcript exit code 1 with exact mismatch diffs:

| Mutant | Description | Transcript rc | Failing case count | Failing test cases |
| :--- | :--- | :--- | :--- | :--- |
| `m1` | PSNR U/V field swap | 1 | 2 | `positive: report on CSV with PSNR columns (line 123)`, `positive: report on CSV with PSNR columns and requested QP (line 136)` |
| `m2` | Drop 4097 frame truncation warning | 1 | 1 | `boundary: 4097 frame rows triggers truncation warning on stderr (line 246)` |
| `m3` | Revert to -1 absent sentinel | 1 | 1 | `boundary: requested QP sentinel -1 rejected (line 219)` |
| `m4` | Loosen upper bound to 70 | 1 | 1 | `boundary: requested QP above 69 rejected (line 212)` |
| `m5` | Drop `-0` to `+0` normalisation | 1 | 1 | `boundary: requested QP -0 normalised to 0.00 (line 191)` |
| `m6` | Remove requested QP range check (`if (0) {`) | 1 | 4 | `boundary: requested QP below 0 rejected (line 205)`, `boundary: requested QP above 69 rejected (line 212)`, `boundary: requested QP sentinel -1 rejected (line 219)`, `boundary: requested QP large float 1e300 rejected (line 226)` |

## Limits

- The transcript runner supports simple space-separated arguments without quotes or shell escapes. Complex shell pipelines remain in bash integration scripts.
- The driver reads a transcript as bytes and strips one CR before each LF, so a CRLF checkout reads the same as LF; a CR elsewhere in a line is kept and compared byte for byte, and an expected line cannot end in a literal CR.
- On Windows each CRLF in the tool's output reads as LF, since the C runtime writes text-mode streams; a lone CR stays.
- Each tool run is bounded: 30 s wall time, 1 MiB per output stream (the tool is killed at the cap), and 8 MiB per transcript or fixture file, checked on the bytes read before any parse. A `% repeat` directive whose output would pass 8 MiB fails the case before anything is allocated. An output stream a child process still holds 5 s after the tool exits fails the case and is left open, since closing it would wait for that child.
- The fixture was recorded with relative file names, so its Summary row carries no absolute or build-directory path; checked once at recording time with `grep -c` for the temp dir (0).

## Evidence

- `meson test -C $TMPDIR/b tool-qp-report-transcript tool-transcript-self-test tool-argv --verbose` (3 passed, exit 0).
- `python3 -I tools/transcript.py --exe $TMPDIR/b/tools/pelorus_qp_report --tool pelorus_qp_report --fixtures libpelorus/test/fixtures/telemetry tools/transcripts/pelorus_qp_report.t` (27 cases passed, exit 0).
- `python3 -I tools/transcript.py --exe $TMPDIR/b/tools/pelorus_qp_report --tool pelorus_qp_report --fixtures libpelorus/test/fixtures/telemetry --self-test` (19 plants passed, exit 0).
