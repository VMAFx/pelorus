<!-- markdownlint-disable MD013 -->
# ADR-0249: CLI demonstrators carry golden transcripts run by an in-repo driver

- **Status**: Accepted
- **Date**: 2026-11-09
- **Deciders**: Lusoris
- **Tags**: test, tools, meson, ci

## Context

The developer tools under `tools/` (`pelorus_qp_report`, `pelorus_zones`, `pelorus_qloop`) are user-facing command-line demonstrators: their usage lines, report lines and exit codes are what a reader of `docs/metrics/qp-feedback.md` copies. Until this decision, `tools/meson.build` tested `pelorus_qp_report` with four `should_fail` tests and one plain test. Those tests check the exit code only; no test reads a byte of standard output or standard error. A mutant that swaps the U and V PSNR values in the report passes all five, because the only fixture, `libpelorus/test/fixtures/telemetry/x265-4.3-qp32.csv`, has no PSNR columns and a flat QP.

Reading the output also exposed three behaviours that exit-code tests cannot see: `--requested-qp -1` is silently ignored (the option's "absent" sentinel is a value a user can type), `--requested-qp 1e300` reaches a `double` to `float` conversion outside the `float` range (C11 6.3.1.5p1 leaves a double to float conversion outside the float range undefined; Annex F (IEC 60559) implementations such as glibc give infinity; no sanitizer reports it (GCC and clang `-fsanitize=float-cast-overflow` cover float to integer only); measured on base a2d03bd: `--requested-qp 1e300` exits 0 and prints a 301-digit requested QP), and a CSV with more than 4096 frame rows is cut to 4096 without a word on standard error.

The fast suite runs on the `core`, `arm64`, `sanitizers`, `windows` (MSYS2 UCRT64) and `windows-msvc` (cl, clang-cl) jobs of `.github/workflows/ci.yml`; the `windows-msvc` job runs its steps in Git for Windows bash (`ci.yml:605-608`, `defaults.run.shell: bash`); `meson.build:65` requires bash on every leg and `meson.build:52` python3; `docs/development/build.md:270-277` documents a local MSVC fast-suite run from PowerShell. A shell must not sit between transcript and executable: it adds a second argument parser (quoting, globbing, expansion), and under Git for Windows bash the MSYS2 runtime rewrites arguments that look like POSIX paths before a native executable sees them (msys2.org/docs/filesystem-paths, section "Process arguments"), so the transcript would pin the shell's rewriting, not the tool's argv.

## Decision

We will test each CLI demonstrator with a golden transcript: a text file of commands and the exact standard output lines, standard error lines and exit code each command must produce. A stdlib-only driver in the repository, `tools/transcript.py`, reads the transcripts with an allow-list parser, runs the built executable directly (no shell, stdin from the null device) in a fresh temporary directory, compares both streams and the exit code exactly (on Windows each CRLF in the tool's output reads as LF, since the C runtime writes text-mode streams; a lone CR stays), and prints a unified diff on mismatch. Each run is bounded: 30 s wall time, 1 MiB per output stream, 8 MiB per transcript, fixture file or file a `% repeat` directive writes, and an output stream a child process still holds 5 s after the tool exits fails the case. Meson registers one fast test per transcript plus the driver's own self-test of planted failing transcripts. A change to a tool's output or arguments updates its transcript in the same change.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| prysk 0.20.0, the maintained fork of cram (runner-up) | Mature `.t` format with regex and glob matching; diff and interactive merge of new output | Runs each test through `/bin/sh` by default (`--shell=PATH`); new pip dependency on every CI leg (hard rule 9) | A shell between the transcript and the executable is the part this test must not depend on, and the format's matching features are not needed for byte-exact demonstrator output |
| scrut 0.5.1 (facebookincubator/scrut, MIT) | Markdown and cram formats; maintained | pipes every test expression into bash (scrut docs `custom-shell.md` at v0.5.1: only bash >= 3.2); measured execve trace of one case: bash, env, grep, sed, tail, head, mkdir around the executable; crates.io newest 0.4.3, v0.5.1 has no release assets, build from the tag pulls 255 crates | A shell and POSIX userland between the transcript and the executable ([runner measurements](../research/0469-qp-report-transcript.md#runner-measurements)). |
| trycmd 1.2.1 (assert-rs/snapbox, MIT OR Apache-2.0) | No shell: spawns the executable directly (measured execve trace: test binary and executable only); no pip; upstream CI on windows-latest | Needs a Cargo package and a `cargo test` harness: 56 crates (7.08 MB) in a fast suite that today needs a C compiler, Meson, Ninja, python3 and bash, plus a Rust package on the MSYS2 UCRT64 leg (`ci.yml:552-558` installs none); `.md` cases merge stderr into stdout; a TOML case without a `.stderr` file passed while the executable wrote 2 stderr lines; argv[0] is the absolute executable path, so usage lines need per-leg redaction | New toolchain dependency on every leg (hard rule 9), and its stream checks need guards the in-repo driver has by design ([runner measurements](../research/0469-qp-report-transcript.md#runner-measurements)). |
| cram 0.7 | Original format | Unmaintained parent of prysk; same shell requirement | Superseded by prysk |
| syrupy 6.1.1 or pytest-golden 1.0.2 | Snapshot update workflows | Both are pytest plugins; pytest is not a repository dependency | Adds pytest to every leg for one test file |
| Keep exit-code-only `should_fail` tests (status quo) | No new code | Misses every output regression; the PSNR swap mutant survives | Does not test what the tool prints |

## Consequences

- **Positive**: Report text, usage text and exit codes of `pelorus_qp_report` are pinned on every CI leg, including the non-ASCII file name path (ADR-0149). The driver is generic over tools, so `pelorus_zones` and `pelorus_qloop` can gain transcripts without new code. The driver's self-test proves it fails on wrong output, wrong exit codes, malformed or empty transcripts, an output flood, an output stream held by a child process and a tool path that is not an executable.
- **Negative**: Any intentional output change now needs a transcript edit; the driver has no automatic update mode, so expected text is written and reviewed by hand (record mode: issue #581). One more script to maintain.
- **Revisit**: pelorus gains a Cargo package in its fast suite, or scrut ships a no-shell runner on crates.io; then re-measure both runners and switch if one meets the no-shell and no-new-toolchain needs.
- **Neutral / follow-ups**: The five `tool-qp-report-*` exit-code tests are replaced by the transcript. `tools/` stays excluded from the tester kit (`-Dtools=false`, ADR-0248), so the transcript tests do not ship in the kit. No CI leg runs macOS (`.github/workflows/*.yml` name no macOS runner), so the transcript tests do not run on macOS in CI; the driver takes the same stdlib `subprocess` path there as on Linux; not measured on macOS.

## References

- Issue #469 (plan nodes C15-1 and C9-8).
- prysk README and package metadata, read 2026-11-09 from PyPI (`pypi.org/pypi/prysk/json`): default shell `/bin/sh`.
- scrut v0.5.1 tag (commit ac2bb728) docs read 2026-11-09.
- trycmd 1.2.1 crates.io, snapbox source read 2026-11-09 (commit a8a8e6a1a78a, `crates/trycmd/src/schema.rs:312`, `:603`).
- actions/runner-images main 741e4eb8ef5f (Cargo 1.99.1 on windows-2025 and ubuntu-26.04).
- msys2.org filesystem-paths read 2026-11-09.
- Issue #581.
- ADR-0149 (UTF-8 paths), ADR-0248 (kit-shipped fast tests).
- Source: `req` (paraphrased, 2026-11-09, controller order for unit tl1-vmafx-pelorus-c15-1): add a transcript test for the `pelorus_qp_report` command-line usage and CSV output, and an integration test that runs the executable.
