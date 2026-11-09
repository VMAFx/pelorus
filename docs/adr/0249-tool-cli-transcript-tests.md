<!-- markdownlint-disable MD013 -->
# ADR-0249: CLI demonstrators carry golden transcripts run by an in-repo driver

- **Status**: Proposed
- **Implementation**: pending (#469)
- **Date**: 2026-11-09
- **Deciders**: Lusoris
- **Tags**: test, tools, meson, ci

## Context

The developer tools under `tools/` (`pelorus_qp_report`, `pelorus_zones`, `pelorus_qloop`) are user-facing command-line demonstrators: their usage lines, report lines and exit codes are what a reader of `docs/metrics/qp-feedback.md` copies. Until this decision, `tools/meson.build` tested `pelorus_qp_report` with four `should_fail` tests and one plain test. Those tests check the exit code only; no test reads a byte of standard output or standard error. A mutant that swaps the U and V PSNR values in the report passes all five, because the only fixture, `libpelorus/test/fixtures/telemetry/x265-4.3-qp32.csv`, has no PSNR columns and a flat QP.

Reading the output also exposed three behaviours that exit-code tests cannot see: `--requested-qp -1` is silently ignored (the option's "absent" sentinel is a value a user can type), `--requested-qp 1e300` reaches a `double` to `float` conversion outside the `float` range (undefined behaviour, C11 6.3.1.5), and a CSV with more than 4096 frame rows is cut to 4096 without a word on standard error.

The fast suite runs on Linux, under sanitizers, on Windows MSYS2 UCRT64 and on Windows MSVC (`.github/workflows/ci.yml`, jobs `windows` and `windows-msvc`); the MSVC leg runs `meson test` from PowerShell, not from a POSIX shell. Every leg already has Python 3, because the top-level `meson.build` registers Python fast tests with `find_program('python3', required: true)`.

## Decision

We will test each CLI demonstrator with a golden transcript: a text file of commands and the exact standard output lines, standard error lines and exit code each command must produce. A stdlib-only driver in the repository, `tools/transcript.py`, reads the transcripts with an allow-list parser, runs the built executable directly (no shell) in a fresh temporary directory, compares both streams and the exit code exactly, and prints a unified diff on mismatch. Meson registers one fast test per transcript plus the driver's own self-test of planted failing transcripts. A change to a tool's output or arguments updates its transcript in the same change.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| prysk 0.20.0, the maintained fork of cram (runner-up) | Mature `.t` format with regex and glob matching; diff and interactive merge of new output | Runs each test through `/bin/sh` by default (`--shell=PATH`), which the MSVC leg does not run its tests from; new pip dependency on every CI leg (hard rule 9); GPL-2.0-or-later | A shell between the transcript and the executable is the part this test must not depend on, and the format's matching features are not needed for byte-exact demonstrator output |
| cram 0.7 | Original format | Unmaintained parent of prysk; same shell requirement | Superseded by prysk |
| syrupy 6.1.1 or pytest-golden 1.0.2 | Snapshot update workflows | Both are pytest plugins; pytest is not a repository dependency | Adds pytest to every leg for one test file |
| Keep exit-code-only `should_fail` tests (status quo) | No new code | Misses every output regression; the PSNR swap mutant survives | Does not test what the tool prints |

## Consequences

- **Positive**: Report text, usage text and exit codes of `pelorus_qp_report` are pinned on every CI leg, including the non-ASCII file name path (ADR-0149). The driver is generic over tools, so `pelorus_zones` and `pelorus_qloop` can gain transcripts without new code. The driver's self-test proves it fails on wrong output, wrong exit codes and malformed transcripts.
- **Negative**: Any intentional output change now needs a transcript edit; the driver has no automatic update mode, so expected text is written and reviewed by hand. One more script to maintain.
- **Neutral / follow-ups**: The five `tool-qp-report-*` exit-code tests are replaced by the transcript. `tools/` stays excluded from the tester kit (`-Dtools=false`, ADR-0248), so the transcript tests do not ship in the kit.

## References

- Issue #469 (plan nodes C15-1 and C9-8).
- prysk README and package metadata, read 2026-11-09 from PyPI (`pypi.org/pypi/prysk/json`): default shell `/bin/sh`, licence classifier GPL-2.0-or-later.
- ADR-0149 (UTF-8 paths), ADR-0248 (kit-shipped fast tests).
- Source: `req` (paraphrased, 2026-11-09, controller order for unit tl1-vmafx-pelorus-c15-1): add a transcript test for the `pelorus_qp_report` command-line usage and CSV output, and an integration test that runs the executable.
