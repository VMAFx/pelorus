<!-- markdownlint-disable MD013 MD060 -->
# Research digest 0145 — Praetor governance adoption

Evidence for
[ADR-0145](../adr/0145-praetor-governance-adoption.md). The current
measurements use Praetor commit `25451d888c8710822dd578907625dc69a0975142` on
the merged tree: `origin/master` `f03badd` (the squashed n9.0.2 integration
stack) plus this branch. They were taken on 2026-09-30. The first adoption at
`846da5908d15b3cf5581ca6b0205cc644b249599` is kept below as history.

## Declared policy

| Input | Value |
|---|---|
| Profile | `native-gpu-systems` |
| Facets | `security:high`, `api:public-contract`, `docs:seo-portal`, `agent:sandboxed` |
| Declined surfaces | `agent-hooks`, `branch-ruleset`, `dev-container`, `git-hooks` |
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
|---|---|
| `.standards.lock`, `.config/archetypes/` | Accepted: re-pinned catalog |
| `.standards-baseline.json` | Accepted after the per-rule comparison below |
| `tools/markdownlint/`, `tools/figures/`, `.github/workflows/praetor-docs.yml`, `Makefile` and `.gitattributes` blocks | Accepted: the audit requires them byte for byte under `docs:seo-portal` |
| `register.sources` in `.standards.yaml` | Accepted: the audit lints the 12 Paperclip strings it names |
| managed `.gitignore` block, README governance block | Accepted |
| `renovate.json` rule, actionlint runner label | Accepted |
| regenerated `AGENTS.md` harness | Rejected: generic text that marks every invariant unenforced and drops Pelorus rules; `compile-context` renders only the register block |
| `.zed/settings.json`, `.vscode/settings.json` merges | Rejected: add Go language and unrelated editor settings (Praetor issue 202) |
| `praetorctl hook <client> pre-tool` in Claude, Codex, Gemini settings | Declined (`agent-hooks`): runs an engine binary on every agent tool call |

The Paperclip harness failed the new internal-register lint on three strings
(copula `is` twice, modal `must` once). They now use the engine's own current
wording, adapted to `master`.

### Baseline, per rule

| Rule | 846da590 | 25451d88 | Change |
|---|---:|---:|---|
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
unbaselined findings, with zero in touched files. With the new baseline,
`CI=true standardsctl audit --base origin/master` passes: 75 of 75, 177
touched files clean, and no committed baseline on `master` for the growth
guard to compare. Fingerprints are still keyed by line
([Praetor issue 29](https://github.com/cordanaLLM/praetor/issues/29)).

### Documentation gate

On the merged tree, the locked gate styled 100 public Markdown files and
reported 507 diagnostics in 50 of them:

| Rule | Count | Fix |
|---|---:|---|
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

### Verification (2026-09-30)

These commands ran in the `pelorus-ci:26.04` container (Ubuntu 26.04.1, GCC 15.2,
clang 21.1.8, Meson 1.10.1, Go 1.27.1, actionlint 1.7.12) on a clone of the
committed branch head. The CI jobs' commands are mirrored.

| Check | Result |
|---|---|
| `meson setup build && ninja -C build`; fast suite | 27 of 27 pass |
| clang-format over `libpelorus`; `clang-tidy -p build libpelorus/src/*.c` | exit 0 (pre-existing advisories only) |
| ASan/UBSan fast suite (`sanitizers` job commands) | 27 of 27 pass, after installing the missing runtime (below) |
| `make verify-native` | exit 0 |
| `concat-changelog-fragments.sh --check`; ADR index loop; `actionlint` | exit 0 |
| `check-build-config.py`, `--self-test`, and `--self-test` with `GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null` | exit 0 |
| `standardsctl compile-context --verify` | exit 0: six contexts, 32 persona copies |
| `standardsctl audit` (local) | exit 1 on the pre-commit hook check only (issue 175) |
| `CI=true standardsctl audit`; `--base origin/master`; `baseline --verify` | exit 0 |
| docs gate (`node:24` container) | exit 0 |
| pinned n9.0.2 fetch; `generate.sh` | peels to `946fcce0`; patches byte-identical |
| `ffmpeg-patches/test/build-and-run.sh` | see the pull request body for the final result |

The container image lacks the clang sanitizer runtime, so the ASan/UBSan
setup first failed with `Linker clang does not support sanitizer arguments`.
After `apt-get install libclang-rt-21-dev` in a throwaway container, the
`sanitizers` job's exact commands passed 27 of 27 fast tests.

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
