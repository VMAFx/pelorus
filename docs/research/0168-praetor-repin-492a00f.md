<!-- markdownlint-disable MD013 -->
# Research 0168: Praetor re-pin `0af07a73` → `492a00f9` measurements

Measurements behind [ADR-0168](../adr/0168-praetor-engine-492a00f.md), taken
on 2026-10-08: first against `master` at `11e183e`, then against `master` at
`3728458`, after pull request 76 merged (26 commits; same tree as its branch tip
`b11341a`).

## Setup

| Item | Value |
| --- | --- |
| Engine | `492a00f930e1a2df557ffebb76564cfa65637b77` (`praetorctl version 492a00f930e1`) |
| Previous pin | `0af07a733e6534269b435cea185da4d1df7aba0c` (31 squash commits earlier) |
| Toolchain | Go 1.27.1, Node.js 26.10.0, Meson 1.12.1, Ninja 1.13.2, GCC 16.2.1, LLVM/clang-tidy 23.1.1, glslc 2026.4 |

## Procedure

```bash
git worktree add <wt> -b chore/praetor-engine-492a00f origin/master && cd <wt>
praetorctl adopt --force --lock-source-root=<praetor at 492a00f9> --path .
git checkout -- AGENTS.md .zed/settings.json      # keep the Pelorus text, as ADR-0154 did
praetorctl compile-context
# move the staged change onto pull request 76, then onto master once it merged
git diff --cached --binary > phase1.patch
git checkout -B chore/praetor-engine-492a00f origin/fix/bug-wave-20261003   # b11341a
git apply --3way --index phase1.patch    # conflicts: README.md, docs/adr/README.md,
                                         # docs/development/build.md, CHANGELOG.md
bash scripts/release/concat-changelog-fragments.sh --write   # CHANGELOG.md regenerated
git reset --soft origin/master           # 3728458, same tree as b11341a
```

Adopt created six files (the `caveman`, `social-text`, and `adhd-format`
skills under `.agents/skills/` and `.claude/skills/`), refreshed
`praetor-docs.yml`, three `tools/figures/` files, and three
`tools/markdownlint/` files, and left `.standards.lock` and
`.standards-baseline.json` unchanged. After the revert, `compile-context`
leaves the six vendor contexts byte for byte as they were.

## Audit failures at the new pin

The audit stops at the first failing check. Each row was fixed, or excepted in
a throwaway copy, to reach the next one.

| # | Check | Result at `11e183e` with the new engine | Pelorus change |
| --- | --- | --- | --- |
| 1 | Documentation gate assets | `tools/markdownlint/package.json holds an earlier Praetor text` | Accept adopt's refresh |
| 2 | Nested `AGENTS.md` Caveman lint | 3 of 3 files fail (table below) | Rewrite the three files |
| 3 | clang-tidy translation-unit coverage | 21 of 21 tracked units unread: no lane declared (23 on `3728458`) | One `files` lane of 10 Meson-built units; one dated exception for each of the other 13 `ffmpeg-patches` units |
| 4 | Supply chain (HISS-11) | SLSA level 3 declared, level 0 measured; no cosign step; no SBOM step; no exception | Reusable release build ([ADR-0169](../adr/0169-release-provenance-slsa3.md), [research 0169](0169-release-provenance.md)) |
| 5 | Build warnings (HISS-10) | 5 lanes without warnings as errors: `ci.yml` lines 47, 51, 155, 213; `release.yml` line 43 | `--werror` on each `meson setup` → `5 build lanes fail on a warning (Meson 5)` |
| 6 | Paperclip rules | `rules.md` is not the rendering of `harness.json` (12 expected lines missing, 8 not expected) | Keep `harness.json`; `rules.md` = the pinned renderer's output for it |
| 7 | `register.sources` pin | Passes with Pelorus's `harness.json` (12 strings). The engine-regenerated `harness.json` would yield 13 strings, digest `sha256:ca38d7be8852847d9325ad2f9c045b4db495d8f656f5edcd90c0cbf8c960b9fe` | None: `harness.json` is kept |

On the final tree the audit exits 0. The `git-hooks` check prints
`[PASS] Git hooks (lefthook.yml and its activation) declined by adoption.decline.`
Six HISS-18 warnings remain for `ci.yml` and `standards-gate.yml` jobs that run
on draft pull requests. HISS-18 reports them without failing the audit.

## Paperclip `rules.md`

The engine compares `rules.md` byte for byte with `renderRules` of the
`harness.json` on disk (`internal/paperclip/drift.go`, `CompareGenerated`).
`praetorctl paperclip harness` would rewrite both files, so `rules.md` was
rendered by calling the pinned `renderRules` on Pelorus's `harness.json` from
a throwaway copy of the engine source at `492a00f9` (a one-off Go test, not
kept). The result wraps list items at 80 columns and titles the push section
"AGit Push Protocol", because the harness stores its push command as
`agit_push_format`. The audit then reports
`operator-owned harness.json, not this release's synthesis; adopt keeps it;
rules.md renders it`.

## Baseline after the rebase

Pull request 76 burns the baseline down from 93 to 51 at the old engine
(HISS-01 28, HISS-04 21, HISS-07 2). At `492a00f9`, `baseline --verify` on the
rebased tree reports `51 active infractions within the 51 recorded`, so the new
engine finds the same 51 and the file is not re-recorded. Against `master`,
the hosted ratchet reports `51 -> 51` against `master` at `3728458`.

## Nested `AGENTS.md` rewrite

`praetorctl caveman check --kind=context <file>` before and after:

| File | Before | After |
| --- | --- | --- |
| `ffmpeg-patches/AGENTS.md` | FAIL: 67 articles / 846 prose words = 7.9 per 100; line 90 C5 long sentence (31 words) | PASS: 0 / 734 = 0.0 |
| `libpelorus/AGENTS.md` | FAIL: 44 / 396 = 11.1 | PASS: 0 / 321 = 0.0 |
| `tools/AGENTS.md` | FAIL: 18 / 189 = 9.5 | PASS: 0 / 152 = 0.0 |

The limit is 2.0 articles per 100 prose words and 30 words per sentence. A
token comparison of each file before and after (every backticked span, ADR
number, number, link, and quoted string) differs only where a span wrapped
across lines; no rule, path, flag, or number was dropped. The long QSV dense
MBQP sentence became a three-item condition list.

## Build warnings (HISS-10)

`meson.build` already sets `werror=true` in `default_options`, and the gate
does not read it (engine `docs/guides/build-warnings.md`, "What the gate does
not read"). Adding `--werror` to the five hosted command lines changes no
build: a fresh `meson setup --werror` tree reports `werror: true`, as before.

`scripts/check-build-config.py` pins the Windows job's commands. Its literal
is now `meson setup --werror build && ninja -C build`, and a new self-test
case, "Windows build without warnings as errors", rejects the old form. In a
throwaway copy:

| Check | Exit |
| --- | --- |
| new checker, new `ci.yml` | 0 |
| new checker, Windows step reverted to `meson setup build && ninja -C build` | 1: `Windows job is missing meson setup --werror build && ninja -C build` |
| old checker literal, new self-test case (`--self-test`) | 1: `workflow regression: Windows build without warnings as errors was accepted` |

## clang-tidy lane

At `11e183e` the Meson compile database lists eight C units:
`libpelorus/src/{deband_params,denoise_params,interop,qp_report_csv,version}.c`,
`libpelorus/test/{interop_test,path_utf8_test}.c`, and
`tools/pelorus_qp_report.c`. `path_utf8_test.c` is built on every platform, so
it is in the Linux database. Pull request 76 adds two more that Meson builds
against a stub `libavutil/frame.h`: `ffmpeg-patches/test/mc_stats_test.c` and
`ffmpeg-patches/test/pelorus_sidedata_test.c`. `.config/clang-tidy/lane-files.txt`
lists all ten, and `make tidy` and the `core` job run
`grep -Ev '^(#|$)' .config/clang-tidy/lane-files.txt | xargs clang-tidy -p build`.

| Run | Result |
| --- | --- |
| clang-tidy over the ten units (rebased tree) | exit 0; six `bugprone-signed-bitwise` warnings (`deband_params.c:54`, `denoise_params.c:56`, `version.c:25`, `interop_test.c:269`, `interop_test.c:492`, `pelorus_qp_report.c:99`) and one `readability-function-size` (`pelorus_sidedata_test.c:179`), none in `WarningsAsErrors` |
| Planted `bugprone-integer-division` in `tools/pelorus_qp_report.c` (throwaway copy) | `make tidy` exit 2, `error: result of integer division used in a floating point context` |
| Audit coverage with the lane, before the exceptions | `13 problem(s) over 21 tracked translation units`: the 11 `ffmpeg-patches/files/*.c` units, `qsv-roi-regression.c`, and `static-libavfilter-consumer.c` |
| Audit coverage with the lane and 13 exceptions (rebased tree) | `all 23 tracked translation units read by a lane (libpelorus: 10) or excused by a live exception (13)` |

The six warnings come from clang-tidy 23.1.1; three of them are in
`libpelorus/src`, which the previous lane already read.

## Gate results

Final tree (on `master` at `3728458`, all changes of ADR-0168 and ADR-0169):

| Command | Exit | Note |
| --- | --- | --- |
| `praetorctl compile-context --verify` | 0 | 32 persona projections, 3 skill projections, 3 nested `AGENTS.md` pass |
| `praetorctl audit` | 0 | `configured governance gates passed`; 6 HISS-18 warnings |
| `CI=true praetorctl audit --base origin/master --touched-debt-delta-reason <reason>` | 0 | HISS-13 `51 -> 51` |
| `praetorctl baseline --verify` | 0 | 51 within 51; file not rewritten |
| `meson setup --werror` + `ninja -j4` + fast suite | 0 | 41 of 41 pass |
| `MESON_TESTTHREADS=4 make verify-native BUILD_DIR=<that tree>` | 0 | format, tidy over the lane list, changelog check |
| `make docs-lint docs-figures` | 0, 0 | figures skipped: no figure spec |
| `python3 scripts/check-build-config.py` and `--self-test` | 0, 0 | |
| `go run github.com/rhysd/actionlint/cmd/actionlint@v1.7.12` | 0 | shellcheck present on the host |
| `praetorctl caveman check --configured-sources --root=.` | 0 | `register.sources` 12 strings |

The workflow edits take effect only in a hosted run.

## Upstream items

- `praetorctl paperclip harness` writes `main` into the rendered rules although
  `.standards.yaml` declares `default_branch: master`; reported on Praetor
  issue 71.
- Dated exceptions expire after at most 90 days and have no renewal tooling
  (Praetor issue 798).
