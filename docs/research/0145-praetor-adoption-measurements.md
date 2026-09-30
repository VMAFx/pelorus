<!-- markdownlint-disable MD013 -->
# Research digest 0145 — Praetor governance adoption

Evidence for
[ADR-0145](../adr/0145-praetor-governance-adoption.md). The current
measurements use Praetor commit `25451d888c8710822dd578907625dc69a0975142` on
the merged tree: `origin/master` `f03badd` (the squashed n9.0.2 integration
stack) plus this branch. They were taken on 2026-09-30. The first adoption at
`846da5908d15b3cf5581ca6b0205cc644b249599` is kept below as history.

## Declared policy

| Input | Value |
| --- | --- |
| Profile | `native-gpu-systems` |
| Facets | `security:high`, `api:public-contract`, `docs:seo-portal`, `agent:sandboxed` |
| Declined surfaces | `branch-ruleset`, `dev-container`, `git-hooks` (`agent-hooks` was declined at the re-pin and adopted afterwards; see [Agent-hook adoption](#agent-hook-adoption-2026-09-30)) |
| Repository identity | `VMAFx/pelorus` |
| Default branch | `master`, declared as `repository.default_branch` |
| Engine | `25451d888c8710822dd578907625dc69a0975142`, Go module version `v0.0.0-20260929221210-25451d888c87` |

The lock pins one profile and four facets at catalog version
`v0.0.0+catalog.3536de06c71a`.

## Re-pin to 25451d88

### Upgrade procedure

Praetor main was 162 commits ahead of the first pin. The engine documents a
re-run of `adopt` as the upgrade path. A plain run refused the existing lock,
because two facets changed values (`gocyclo` left `agent:sandboxed` and
`oapi-codegen` left `api:public-contract`). Changed catalog values need
`--force`. To keep `--force` away from the shared `.git` directory, it ran in a
throwaway copy inside the `pelorus-ci:26.04` container:

```bash
GOBIN=/s/bin go install github.com/cordanaLLM/praetor/cmd/standardsctl@25451d888c8710822dd578907625dc69a0975142
cp -a /w /tmp/t && cd /tmp/t
# manifest: default_branch: master; decline agent-hooks
/s/bin/standardsctl adopt --force --lock-source-root=/s/praetor   # praetor checkout at 25451d88
git checkout HEAD -- AGENTS.md .zed/settings.json .vscode/settings.json
/s/bin/standardsctl compile-context
```

The run created 24 files, reconciled 75, and replaced four (the lock, two
facet files, and `AGENTS.md`). It recorded 14 warnings, all about declined
steps or consumer-owned files that it kept. Its diff was applied to the
branch after this review:

| Output | Decision |
| --- | --- |
| `.standards.lock`, `.config/archetypes/` | Accepted: re-pinned catalog |
| `.standards-baseline.json` | Accepted after the per-rule comparison below |
| `tools/markdownlint/`, `tools/figures/`, `.github/workflows/praetor-docs.yml`, `Makefile` and `.gitattributes` blocks | Accepted: the audit requires them byte for byte under `docs:seo-portal` |
| `register.sources` in `.standards.yaml` | Accepted: the audit lints the 12 Paperclip strings it names |
| managed `.gitignore` block, README governance block | Accepted |
| `renovate.json` rule, actionlint runner label | Accepted |
| regenerated `AGENTS.md` harness | Rejected: generic text that marks every invariant unenforced, including the audit-ratcheted ones, and drops Pelorus rules; `compile-context` renders only the register block. The engine is right about HISS-04 complexity, so the Pelorus harness now marks cyclomatic, cognitive, and statement limits as review-only |
| `.config/labels.yaml` differs from the scaffold (-1/+49 lines) | Kept by `adopt`; replaced with the engine's 14-label taxonomy in the review fixes. The audit checks only that the file exists |
| `.zed/settings.json`, `.vscode/settings.json` merges | Rejected: add Go language and unrelated editor settings (Praetor issue 202) |
| `praetorctl hook <client> pre-tool` in Claude, Codex, Gemini settings | Declined at this step (`agent-hooks`); adopted later by maintainer decision, see [Agent-hook adoption](#agent-hook-adoption-2026-09-30) |

The Paperclip harness failed the new internal-register lint on three strings
(copula `is` twice, modal `must` once). They now use the engine's own current
wording, adapted to `master`.

### Baseline, per rule

| Rule | 846da590 | 25451d88 | Change |
| --- | ---: | ---: | --- |
| HISS-01 | 31 | 31 | Identical fingerprints (`goto` cleanup jumps) |
| HISS-02 | 1 | 1 | Identical |
| HISS-04 | 46 | 41 | Five scanner misparses gone; nine entries renamed |
| HISS-07 | 0 | 2 | New check: `sys.exit` outside a `__main__` entry point |
| **Total** | **78** | **75** | |

Removed HISS-04 entries, all false positives of the old scanner:

- `ffmpeg-patches/files/h265_pelorus_fgs_bsf.c:266`,
  `vf_pelorus_deband_vulkan.c:273`, `vf_pelorus_dehalo_vulkan.c:197`, and
  `vf_pelorus_denoise_vulkan.c:907`: the `AVOption` table after
  `#define FLAGS` was measured as a function named `FLAGS`.
- `libpelorus/include/pelorus/interop.h:64`: the `extern "C" {` block was
  measured as a 492-line function.

Nine entries keep their file:line fingerprint but now name the function
instead of `{`: `coalesce_roi`, `attach_interop`, `denoise_dispatch`,
`attach_motion`, `mc_dispatch`, `pel_blob_pack`, `pel_blob_find_section`,
`pel_qp_report_from_blocks`, and `pel_x265_csv_parse`. The other 32 HISS-04
entries are unchanged.

The two HISS-07 entries are `scripts/bench/bd_rate.py:22`, an import guard
that exits when NumPy is missing, and `scripts/check-shader-bindings.py:57`, a
module-level script without a `__main__` guard. Both are legacy code that this
change does not touch.

The total fell, so recording needed no `--allow-increase` exception. Under the
old baseline, the new engine reported exactly the two HISS-07 entries as new
unbaselined findings, with zero in touched files. With the new baseline, the
hosted job's two ratchet steps pass (see the review fixes below): 75 of 75,
and no committed baseline on `master` for the growth guard to compare.
Fingerprints are still keyed by line
([Praetor issue 29](https://github.com/cordanaLLM/praetor/issues/29)).

### Documentation gate

On the merged tree, the locked gate styled 100 public Markdown files and
reported 507 diagnostics in 50 of them:

| Rule | Count | Fix |
| --- | ---: | --- |
| MD060 table column style | 475 | Spaced delimiter rows (`\| --- \|`), autofix plus one manual table |
| MD032 blanks around lists | 12 | Autofix; three were `+` continuation lines parsed as lists, rewrapped by hand |
| MD040 fence language | 8 | `text` on diagrams and filter chains |
| MD004 list style | 3 | The same three `+` lines, rewrapped instead of becoming `-` bullets |
| MD029 ordered list prefix | 3 | Autofix renumbered a list written 1, 2, 2, 3, 4 |
| MD001 heading increment | 3 | One section and the task headings of two plans step down one level |
| MD034 bare URL | 2 | Autofix: e-mail autolinks |
| MD012 blank lines | 1 | Autofix |

The autofix ran `markdownlint-cli2` 0.23.2 with the locked configuration. No
file is excluded through `documentation.style_exclude`. After the fixes,
`node tools/markdownlint/verify.mjs` exits 0. The figure checks skip because
`docs/figures/` holds no spec.

### Platform check

A Windows build of the same commit ran natively on Windows 11 against a copy
of the branch. `compile-context --verify` and every audit gate gave the same
results as the Linux container, including the final hook failure
(`.git\hooks\pre-commit`). That build came from a checkout, so it carries a VCS
stamp and `version` prints `25451d888c87`; a `go install` build prints
`unknown` (issue 642 below).

### Upstream

- [Praetor issue 642](https://github.com/cordanaLLM/praetor/issues/642) (filed):
  `version` ignores the module version of a `go install module@commit` build,
  so the Standards job cannot prove the pin that way. CI now also prints
  `go version -m`.
- [Praetor issue 175](https://github.com/cordanaLLM/praetor/issues/175)
  ([comment](https://github.com/cordanaLLM/praetor/issues/175#issuecomment-5908433580)):
  `auditGitHooks` ignores the `git-hooks` decline outside CI; Linux and Windows
  evidence at the new pin.
- [Praetor issue 408](https://github.com/cordanaLLM/praetor/issues/408):
  closed; the new pin honours the `branch-ruleset` decline.
- The HISS-04 name misparse (`Function '{'`) reported during stacked review is
  fixed at the new pin; no issue was needed.

### Review fixes (2026-09-30)

Review of `7b5cdca` found that the hosted job ran `standardsctl audit`
without `--base`. The engine then skips its HISS-13 growth guard
(`auditBaselineGrowth` in `cmd/standardsctl/audit_ratchet.go` returns at once
without a base ref) and compares the scan only with the pull request's own
baseline. The job now runs two steps:

1. `standardsctl audit --base <target> --touched-debt-delta-reason <reason>`,
   where the target is `origin/<base branch>` for a pull request and the
   replaced commit for a push;
2. `standardsctl baseline --verify`.

`--base` alone would revoke every baselined finding in a touched file, which
is the deferred zero-debt mode. With the debt-delta reason, a touched file
fails only when one of its rules gains a finding (per-file, per-rule counts in
`internal/baseline/baseline.go`). That mode also accepts findings that only
moved lines, so step 2 keeps the old requirement that the baseline records
every finding at its current line.

A probe ran each mode against synthetic commits in a throwaway copy of the
branch (`pelorus-ci:26.04`, the pinned engine, `CI=true`; the target is the
branch head with the review fixes):

| Commit against the target | Old job: `audit` | Step 1 | Step 2 | Zero-debt: `audit --base` |
| --- | --- | --- | --- | --- |
| No change | pass | pass (75 -> 75) | pass | pass |
| Comment line inserted above the three baselined HISS-04 findings in `libpelorus/src/interop.c`; baseline not re-recorded | fail: 3 new | pass | fail: 3 new | fail: 3 in touched file |
| Same insertion; baseline re-recorded (75) | pass | pass | pass | fail: 3 in touched file |
| New `goto` in that file; baseline unchanged | fail: 1 new | fail: 4 in touched file | fail: 1 new | fail |
| New `goto`; baseline grown to 76 by hand; README block counts edited to match | **pass** | fail: HISS-13, 75 -> 76 | pass | fail |
| New `goto`; baseline grown with `--allow-increase --reason`; README block counts edited | pass | pass, with a `[WARN]` that prints the reason | pass | fail |

The fifth row is the gap the review reported: the old job accepts a grown
baseline. Without the README edit, every mode fails a grown baseline, because
the audit compares the managed README block with the baseline count. The
sixth row is the engine's designed exception for a deliberate, reasoned
increase.

The other review fixes:

- `AGENTS.md` regained the Claude Code guide content that `CLAUDE.md` carried
  on `master` before it became a projection: the `gh repo set-default` rule,
  project state, a layout-and-ownership map of every top-level entry, the
  skills and hooks inventory, the dependency, logging, and no-new-top-level-doc
  rules, and the per-commit sync rules. It compiles to 247-253 lines per
  projection (budget 300) and passes the context lint at 953 prose words and
  0.2 articles per 100.
- The HISS-04 row and the Paperclip invariant now state the effective policy
  (60 LOC ratcheted; cyclomatic 10, cognitive 12, statements 40 by review).
  The engine measures complexity only for Go (`internal/hiss/go_ast.go` is the
  only caller of `recordMeasurement`), and the pinned catalog sets cognitive
  12 and statements 40 (`.config/archetypes/native-gpu-systems.yaml`).
- The Paperclip string edit changed the `register.sources` digest. `adopt`
  refused to re-bind it ("adoption never re-binds a contract to drift it did
  not cause, --force included"), so the new digest came from
  `standardsctl caveman check --configured-sources --root=.` on the staged
  tree, as that error instructs. The count stays 12.
- The `vulkan-shader-reviewer` and `ffmpeg-patch-reviewer` personas regained
  the checks the caveman rewrite had dropped: push-constant ordering, reserved
  words, `CmdFillBuffer` zeroing, `uint32` accumulator overflow, the
  `vf_scdet_vulkan.c` cross-check, the `Use when` trigger, `git am --3way`
  replay, `av_free`, `FILTER_SINGLE_PIXFMT`, `AVFILTER_FLAG_HWDEVICE`, and the
  Makefile object wiring. The Codex `.toml` roles match.
- `.config/labels.yaml` now carries the engine's 14-label taxonomy.
- The two new 0145 documents no longer suppress MD060. The onboarding plan
  keeps inline MD001, MD010, and MD032 suppressions: its `Makefile` snippets
  need hard tabs, and it is a historical record. "No file is excluded" refers
  to `documentation.style_exclude`; inline suppressions, mostly MD013 line
  length, predate this change in many documents.

### Merge of master eb3b045 (2026-09-30)

Pull request 59 (Go 1.27.x for actionlint, squashed as `eb3b045`) extended
`scripts/check-build-config.py` by 378 lines. After it was merged into this
branch, the hosted ratchet failed: 76 findings against the recorded 75. The
scanner reported one new finding, `renovate_validator_regressions` (HISS-04,
100 lines), and ten findings of that file at new lines (all HISS-04:
`consumer_validator_regressions`, `git_dirty_worktree_cleanup_regression`,
`git_worktree_hook_regression`, `git_am_hook_regression`,
`git_smudge_cleanup_regression`, `git_format_config_regression`,
`validate_consumer_text`, `validate_replay_text`, `validate_qsv_replay_text`,
and `validate_workflow_text`, which grew from 119 to 126 lines).

`master` carries no Standards gate yet, so nothing stopped the new function
there. The branch records the increase with the engine's documented exception
in a throwaway copy, then refreshes the README managed block with a plain
`adopt`:

```bash
standardsctl baseline --record --allow-increase \
  --reason "Merge of origin/master eb3b045 (PR 59): renovate_validator_regressions ..."
standardsctl adopt --lock-source-root=/s/praetor
```

The baseline now holds 76 findings (31 HISS-01, one HISS-02, 42 HISS-04, two
HISS-07) and stores the reason as `increase_rationale`. The README block and
badge moved from 75 to 76. With that, `CI=true standardsctl audit --base
origin/master --touched-debt-delta-reason ...` and `baseline --verify` both
passed; `origin/master` has no baseline, so the growth guard had nothing to
compare.

### Agent-hook adoption (2026-09-30)

The re-pin kept `agent-hooks` declined. On 2026-09-30 the maintainer chose to
adopt it instead. After `origin/master` `eb3b045` was merged into the branch,
`agent-hooks` left `adoption.decline` and a plain `adopt` (no `--force`) ran in
a throwaway copy inside `pelorus-ci:26.04`:

```bash
cp -a /w /tmp/t && cd /tmp/t && git add -A
/s/bin/standardsctl adopt --lock-source-root=/s/praetor   # praetor checkout at 25451d88
```

It exited 0, created one file, and changed two; every other managed file was
reported as already in sync:

| Output | Decision |
| --- | --- |
| `.claude/settings.json`: `praetorctl hook claude pre-tool`, timeout 15, added to the existing `Bash` group after `block-unsafe-bash.sh` | Row accepted. The engine also re-indented the file and dropped the blank separator lines in `permissions.allow`; that part was not taken |
| `.codex/hooks.json`: `praetorctl hook codex pre-tool`, timeout 15, added to the existing `Bash` group | Accepted as generated (the file was already in the engine's layout) |
| `.gemini/settings.json` (new): `BeforeTool`, matcher `^run_shell_command$`, `praetorctl hook gemini pre-tool`, timeout 15000 | Accepted as generated |

The engine copied the two changed files to
`.workingdir/adopt-backups/<UTC stamp>/` in the throwaway copy; `/.workingdir/`
is ignored. A second `adopt` on the tree with the hand-merged Claude row
reported `Pre-tool interceptor already registered` for Claude and Codex and
changed no tracked file, so the row survives later adoption runs without the
re-indentation. The merge matched the engine's matcher rules: `Bash` selects
the same tool as the registration's `^Bash$` for Claude Code and Codex.

The hook was exercised with the exact command strings from the three files,
the pinned engine installed as `praetorctl`, and hand-written payloads (allow:
`ls`; deny: `git commit --no-verify -m x`):

| Host and shell | Allow | Deny | Engine missing from `PATH` |
| --- | --- | --- | --- |
| Linux container, `sh -c`, Claude, Codex, and Gemini rows | exit 0, no output | exit 2, `[BLOCKED BY HISS] verification evasion prohibited; ...` | exit 127, `praetorctl: not found` |
| Windows 11, Git Bash 5.3 `bash -c`, Claude row, payload `cwd` `C:\tmp\pel\s56` | exit 0, no output | exit 2, same message | exit 127 |
| Windows 11, PowerShell 7.6 direct call | exit 0 | exit 2 | not measured |
| Windows 11, `cmd /c` | exit 0 | exit 2 | exit 1 |
| Windows 11, `powershell.exe -Command "<row>; exit $LASTEXITCODE"` | exit 0 | exit 2 | not measured |
| Windows 11, `pwsh -Command "<row>"` (no exit propagation) | exit 0 | exit 1 | exit 1 |
| Windows 11, Gemini row in Gemini CLI's PowerShell form (`<row>; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }`) | exit 0 | exit 2 | not measured |

A payload with a `cwd` outside any repository skipped with exit 0 and
`praetor hook: no repository, skipped`. One call took under 0.1 s on the
Windows host, far inside the 15 s budget. The last-but-one row is PowerShell's
own rule: `-Command` reports 1 when its last native command fails, whatever
its exit code. Claude Code's default Windows hook shell is Git Bash (its hooks
reference lists `"bash"` as the default and PowerShell only as the fallback
when Git Bash is missing); that fallback was not measured here. Gemini CLI
propagates the exit code itself (`packages/core/src/hooks/hookRunner.ts`).
Codex documents only exit 0 and 2, so how it treats 127 or 1 is unverified,
as Praetor's own guide notes.

### Verification (2026-09-30)

These commands ran in the `pelorus-ci:26.04` container (Ubuntu 26.04.1, GCC 15.2,
clang 21.1.8, Meson 1.10.1, Go 1.27.1, actionlint 1.7.12) on a clone of the
committed branch head. The CI jobs' commands are mirrored.

| Check | Result |
| --- | --- |
| `meson setup build && ninja -C build`; fast suite | 27 of 27 pass |
| clang-format over `libpelorus`; `clang-tidy -p build libpelorus/src/*.c` | exit 0 (pre-existing advisories only) |
| ASan/UBSan fast suite (`sanitizers` job commands) | 27 of 27 pass, after installing the missing runtime (below) |
| `make verify-native` | exit 0 |
| `concat-changelog-fragments.sh --check`; ADR index loop; `actionlint` | exit 0 |
| `check-build-config.py`, `--self-test`, and `--self-test` with `GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null` | exit 0 |
| `standardsctl compile-context --verify` | exit 0: six contexts, 32 persona copies |
| `standardsctl audit` (local) | exit 1 on the pre-commit hook check only (issue 175) |
| Hosted ratchet: `CI=true standardsctl audit --base origin/master --touched-debt-delta-reason …`, then `baseline --verify` | exit 0: 75 of 75, 177 touched files clean, no baseline on `master` for the growth guard |
| docs gate (`node:24` container) | exit 0 |
| pinned n9.0.2 fetch; `generate.sh` | peels to `946fcce0`; patches byte-identical |
| `ffmpeg-patches/test/build-and-run.sh` (`JOBS=8`) | exit 0: 18 patches applied, FFmpeg and a static external `libavfilter` consumer linked against a private libpelorus, and every Pelorus filter, BSF, and encoder option registered |

The first local stack replay was killed when the WSL VM under Docker
restarted for reasons outside this work; the repeated run above completed. On
the pushed head `a96feaf`, all seven hosted checks passed: `core`,
`sanitizers`, `ffmpeg-stack`, `docs`, `Standards`, and `Documentation
Governance` for both the push and pull-request events. The hosted Standards
job printed the module version `v0.0.0-20260929221210-25451d888c87` and
audited 75 of 75 findings.

The container image lacks the clang sanitizer runtime, so the ASan/UBSan
setup first failed with `Linker clang does not support sanitizer arguments`.
After `apt-get install libclang-rt-21-dev` in a throwaway container, the
`sanitizers` job's exact commands passed 27 of 27 fast tests.

The review-fix head `f5d0bb1` repeated every row above in the same container,
with the sanitizer runtime installed first, and got the same results. The
stack replay was not repeated: no patch, `libpelorus`, or build input changed,
and `generate.sh` still reproduced the committed patches byte for byte. The
hosted ratchet row above is from that head; the earlier heads ran the plain
`CI=true standardsctl audit`. The documentation gate passed again in
`node:24`.

## First adoption at 846da590 (2026-09-20/21, history)

The first adoption measured 74 findings on the pre-review integration tree, 77
at `6ccc730`, and 78 on the exact stacked merge with verified tip
`3aa2f61bd97b15fb105beb9b25b0ac13c58765f6` (31 HISS-01, one HISS-02, 46
HISS-04). Its materializing run created 40 files and reconciled 12. The
reconciliation established:

- `AGENTS.md` as the canonical repository guide;
- eight canonical reviewer personas under `.agents/agents/`;
- six synchronized vendor context projections and 32 persona projections;
- a Meson/C/GLSL-native `Makefile` and `lefthook.yml`, with hook installation
  left to an explicit `make hooks-install`;
- clangd, editor tasks, and Paperclip settings adapted to the `build` tree,
  the Make targets, and `master`; and
- an explicit `[extend] useDefault = true` Gitleaks configuration in place of
  the generated comment-only file, which disabled every default rule (Praetor
  issue 410, since closed).

At that pin the audit passed every check through persona projections, then
failed because it required `.github/rulesets/main.json` despite the
`branch-ruleset` decline (issue 408). Line-number fingerprints turned one net
new finding into 23 reported ones after the integration base advanced
([issue 29 comment](https://github.com/cordanaLLM/praetor/issues/29#issuecomment-5754015107)).
Generator assumptions about Go layouts, editor tooling, Paperclip forges, and
skills that consumers do not receive went to Praetor issues 68, 202, 235, 321,
365, and 380, and default-branch, devcontainer, and hook assumptions to issues
71, 75, 242, and 370.
