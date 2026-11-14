<!-- markdownlint-disable MD013 -->
# Changed-code lint in the local gate: cppcheck and ruff format

**Date:** 2026-11-14

**Decision:** [ADR-0258](../adr/0258-changed-code-lint-gate.md).

**Scope:** cppcheck 2.22.0 and Ruff 0.17.4 (the hash-locked copy under `.cache/python-lint`), tree at 881ec9b. Source: a Codacy `unreadVariable` finding on pull request 619 that no local step caught.

## Verdict

Neither tool can gate the whole tree today. On master, `ruff format --check` would rewrite 95 tracked Python files, and cppcheck `--enable=style` reports 39 findings over the 55 clang-tidy lane units. Gating the delta a change introduces works for both tools. For cppcheck that means comparing base and head findings keyed by file, check id and source line text. For Ruff it means checking only the changed line ranges with `ruff format --check --range`.

## Measurements

| Probe | Result |
| --- | --- |
| `ruff format --check` over tracked Python, tree at 881ec9b | 95 files would be reformatted |
| `ruff format --check --range <start>-<end>` on a file with drift outside the range | exit 0: drift outside the range is not reported |
| Same, with the range covering an unformatted line | exit 1 |
| cppcheck `--enable=style --std=c11 --language=c` over the 55 units in `.config/clang-tidy/lane-files.txt` | 39 findings: 11 `variableScope`, 10 `redundantAssignment`, 3 each `unusedStructMember`, `nullPointer`, `constParameterPointer`, 2 each `unreadVariable`, `uninitvar`, 1 each `syntaxError`, `incorrectStringBooleanError`, `constVariable`, `constParameter`, `arrayIndexOutOfBounds` |
| Same, over every tracked `.c` and `.h` file | 224 findings, 164 of them `unusedStructMember` from public headers, 8 `unknownMacro` from FFmpeg-tree files |
| Wall time, 55 lane units, `-j4` | 3.8 s |
| `scripts/check-changed-lint.py --self-test` (eight fixture cases, real tools) | all pass, 0.26 s |
| Same, with the cppcheck step returning no findings, the format step returning clean, the key using line numbers, or the range step yielding no ranges | fails on the planted `unreadVariable`, the planted unformatted file, the moved finding, and the unformatted line in a modified file respectively |
| `--base HEAD~1` on the branch, one C change (two cppcheck passes, host load about 40) | exit 0: 4 files, 37 ranges, 0 unformatted; cppcheck base 39, head 39, new 0; 19.2 s |
| `--base HEAD~3` (the three commits before this change) | exit 1: 11 unformatted ranges plus one unformatted new file, in 3 files that landed in pull requests 616 and 618; cppcheck new 0 |

The `HEAD~3` row shows the gap in practice: the existing gate let two recent pull requests add unformatted Python lines.

## Limits

- FFmpeg-tree units (`ffmpeg-patches/files/`) need the FFmpeg headers and macros, and cppcheck stops on them with `unknownMacro`. They stay outside the local step; Codacy still analyses them on every pull request.
- A finding whose source line text changes counts as new, even when the change only reformats the line.
- `libpelorus/test/interop_test.c:2328` raises `syntaxError`, which ends cppcheck's analysis of that file. Later code in that file is not checked until the parse error is fixed.
- The 39 master findings stay as debt. They include `arrayIndexOutOfBounds` at `libpelorus/src/qp_report_csv.c:299` and `nullPointer` at `libpelorus/src/quality.c:346`, `:355` and `:363`, which need their own triage.
