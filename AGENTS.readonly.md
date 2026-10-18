<!-- markdownlint-disable MD013 -->
# VMAFx/pelorus Agent Operating Harness

Run read-only: file edits, state mutations, commits prohibited. Mutating steps dropped; prohibitions kept.

<!-- praetor:head -->

## Core Directives & Invariants

Adopted check = coverage state + mechanism; else audit scanner; else `unknown`. Gates = (none: no generated verification gate, no praetor `lefthook.yml`); edits not reflected.

| Invariant | Rule | Adopted check | On fail |
| :--- | :--- | :--- | :--- |
| **HISS-01** control flow | recursion prohibited; call graph = DAG; C/C++: zero `goto` | `praetorctl audit` HISS scan: Go `goto`, recursion + plain-function call cycles; Rust, Python direct recursion; C `goto` | advisory |
| **HISS-02** loops, I/O | scalar upper bound on every loop; explicit deadline on every I/O call | `praetorctl audit` HISS scan: unbounded loop shapes in Go, C, Rust, Python; I/O deadlines unchecked | advisory |
| **HISS-03** memory | C/C++: zero heap allocation in hot simulation/tick loops | unknown | advisory |
| **HISS-04** complexity | McCabe cyclomatic <= 10, cognitive <= 12, statements <= 40; func LOC <= 60 (audit ceiling) | `praetorctl audit` HISS scan: function length only; other caps need repository linter | advisory |
| **HISS-05** scoping | declare every identifier in smallest lexical scope serving it | unknown | advisory |
| **HISS-06** concurrency | explicit scalar upper bound on every worker pool + concurrent fan-out | unknown | advisory |
| **HISS-07** errors | every error handled or wrapped with context | unknown | advisory |
| **HISS-08** determinism | zero dynamic code execution (`eval` / `exec`); C/C++: zero banned libc (`gets` / `strcpy` / `sprintf`); a process signal's target is bounded above 1 (`killpg`, `kill(-pid)`, Go `syscall.Kill`) | `praetorctl audit` HISS scan: C banned calls, Python `eval` / `exec`, Go `syscall.Kill` / `unix.Kill` | advisory |
| **HISS-09** reference safety | n/a: no such construct in repository languages; advisory | unknown | advisory |
| **HISS-10** warnings | zero warnings: compiler, linter, format sweeps | unknown | advisory |
| **HISS-11** supply chain | pinned lockfiles; zero floating tags; signed provenance | unknown | advisory |
| **HISS-12** secrets | zero credentials in Git history | unknown | advisory |
| **HISS-13** debt ratchet | recorded infractions never grow vs committed baseline | `praetorctl audit` ratchet vs `.standards-baseline.json` | advisory |
| **HISS-14** append-only ABI | public API append-only; breaking change = `!` subject + `Migration:` footer | unknown | advisory |
| **HISS-15** 3D testing | positive + negative + boundary tests, every public interface | unknown | advisory |
| **HISS-16** context integrity | single canonical `AGENTS.md`; vendor files compiled via `praetorctl compile-context` | unknown | advisory |
| **HISS-17** state ledger | turn start `praetorctl state status` | unknown | advisory |
| **HISS-18** CI efficiency | diff-aware gating via `praetorctl ci filter` | unknown | advisory |
| **HISS-19** reuse before writing | one behavior = one implementation; extend or call existing code | unknown | advisory |
| **HISS-20** replayable evidence | every enforcement claim backed by fixtures replayed both directions | unknown | advisory |
| **HISS-21** platform neutrality | gates, hooks, emitted templates run on Linux, macOS, Windows, or skip with stated reason | unknown | advisory |

## Operational Rules

1. **Act on verified state.** Read source files, run real commands before hypothesis or edit. Never guess flag names, library signatures, repo configuration from memory.

2. **Lead with output.** Direct answers, diffs, commands. No filler preamble, no "Based on", no restatement, no chatter.

3. **Context transpiler first.** Never edit `CLAUDE.md`, `.cursor/rules/hiss-invariants.mdc`, `.github/copilot-instructions.md`, `.windsurfrules`, `.gemini/GEMINI.md`, `.codex/rules.md` manually. All agent instruction updates -> `AGENTS.md`, then:

   ```bash
   praetorctl compile-context
   ```

   - `AGENTS.md` = agent-only text -> caveman (internal register). `praetorctl compile-context --verify` + `praetorctl audit` run caveman lint; findings fail gate; no opt-out. Check first: `praetorctl caveman check --kind=context AGENTS.md`.

4. **SARIF diagnostic distillation.** Compiler/linter errors -> distill to $\le 1,500$ tokens ($< 60$ lines): top 3 root-cause failures with file/line pointers; full SARIF logs -> ephemeral storage.

5. **No evasion.** Never attempt `--no-verify`, `LEFTHOOK=0`, or modifying `.git/hooks`. Adoption installed no hooks here (`lefthook.yml` not praetor-written, or `git-hooks` declined). Adoption adds no server-side `praetorctl` gate run. Scaffolded CI: `.github/workflows/praetor-docs.yml` runs step `Stop on a draft pull request`, `node tools/markdownlint/verify.mjs`, step `Verify figures`.

6. **Anti-loop interception.** Same AST diff + error category repeats $\ge 3$ times -> halt immediately. Re-evaluate design; no micro-textual retries.

<!-- praetor:config -->

## Text Register

<!-- praetor:register:start -->
Register follows the audience, then the task label of your brief (`register:` in `.standards.yaml`; labels are the router's `target_tasks`).

| Register | Where | Form |
| :--- | :--- | :--- |
| social | forge: issues, PR bodies, review comments, commit bodies | `social-text` skill: BLUF, full sentences, scannable, enough and no more; conventional commit subject unchanged; changelog fragment unchanged |
| docs | docs/, README, ADR bodies | complete without bloat: newcomer path first, expert reference after; every claim points at a file, command or test; no restated code |
| internal | briefs, agent-to-agent traffic, research fan-outs, workflow returns | `caveman` skill: fragments, no filler, verbatim code/paths/errors; facts, paths, commands, verdict |

- Task rows: social = commit_message_synthesis, waiver_signoff; docs = architecture_synthesis, function_docstrings; every other label and any unlabeled text = internal. Subagent launch brief: `caveman` brief shape with `task:` = routing label.
- Evidence above 58 lines or 1500 tokens leaves the message as a file under `.workingdir/evidence/`; return `evidence: <path> sha256:<12 hex> lines:<n>` and fetch it only when a decision needs it.
- An internal return carries verdict, changed paths, commands run, evidence pointers and open questions, nothing else.
<!-- praetor:register:end -->

<!-- praetor:tail -->

## Primary Verification Commands

```bash
# Fast local test suite
# Declared commands only; run them before claiming application verification.
'make' 'verify-all'

# Recompile and verify cross-agent context outputs
praetorctl compile-context --verify

# Audit repository against declared HISS standards
praetorctl audit
```

<!-- markdownlint-enable MD013 -->
<!-- markdownlint-disable MD025 -->
<!-- praetor:harness:end -->

---

# Pelorus project contract

Canonical cross-tool context. Read scoped `AGENTS.md` before edits. Human rationale: `docs/`. Claude/Cursor/Copilot/Windsurf/Codex/Gemini files: generated projections; never hand-edit.

## Mission

- GPU pre-encode pipeline: Vulkan compute + FFmpeg filters; zero-copy VRAM path.
- Goal: reduce fixed-function encoder BD-rate gap through deband, denoise, grain synthesis, motion hints.
- Codec scope: deband, denoise, motion codec-agnostic; film grain uses AV1 AOM or HEVC/VVC H.274.
- Sibling `VMAFx/vmafx`: quality oracle + autotune control plane.
- Shared contract: `PelorusSideData`; Pelorus writers, vmafx readers.
- Release `v0.4.0`: library version 0.x, interop ABI 1.6 (`PELORUS_ABI_MAJOR` 1, `PELORUS_ABI_MINOR` 6; zero-free carrier, ADR-0183; quality window, ADR-0214), append-only.
- Architecture: `docs/architecture/overview.md`; rules: `docs/principles.md`.

## Project state

- Inventory: 10 filters (deband, analyze, denoise, grain_estimate, mc, dehalo, aa, deblock, borderfix, scenecut) plus `pelorus_fgs` BSF; 22-patch stack (number 0021 retired) on shared FFmpeg fix series `v0.2.0` (VMAFx/ffmpeg-patches, ADR-0185; 0008 = #309 queue de-phase, ADR-0192).
- Encoder steering: NVENC, QSV, Vulkan, libaom, SVT-AV1 patches; QP-feedback path. README "Modules" table: current inventory, no stubs.
- FFmpeg 9 base: build-time SPIR-V, no runtime GLSL API (ADR-0143).
- Forge: `VMAFx/pelorus`. Run `gh repo set-default vmafx/pelorus` before any `gh` command.
- Plan/status: `docs/roadmap.md` + forge milestones; local, git-ignored: `.workingdir/OPEN.md`, `.workingdir/STATE.md`, backlog `.workingdir/BACKLOG.md`.

## Hard rules

1. `PelorusSideData` ABI append-only. Add fields at section tail or mint section bit. Bump `PELORUS_ABI_MINOR`. Never reorder, resize, remove, repurpose. Public ABI changes require `Migration:` commit footer with before/after C snippets.
2. Public non-void APIs return `pel_result`. Check each non-void call or cast `(void)`. No bare `return -1` across API boundary.
3. No mutable global state or static-init side effects. Banned: `gets`, `strcpy`, `strcat`, `sprintf`, `strtok`, `atoi`, `atof`, `rand`, `system`.
4. FFmpeg filter shader source lives once: `ffmpeg-patches/files/vulkan/pelorus_<name>.comp.glsl`; FFmpeg 9 compiles SPIR-V at build time. Never add runtime or inline GLSL. `libpelorus/shaders/*.comp`: standalone fast-gate references, not shipped mirrors. Spec IDs `253`, `254`, `255`: reserved workgroup sizes. Descriptor order and push layout must match C exactly.
5. Patch consumers changed -> update `ffmpeg-patches/files/` plus regenerated stack in same PR. Verify full `series.txt` replay; per-patch apply check insufficient. Shared FFmpeg fix series applies first, fetched + verified by `scripts/fetch-ffmpeg-series.sh` (sha256 pin, cosign, `gh attestation verify`; fail closed). Generic FFmpeg fix (stock file, no Pelorus name) -> VMAFx/ffmpeg-patches, never this stack.
6. Touched files: `-Wall -Wextra -Werror`, clang-format, clang-tidy clean. Each `// NOLINT`: inline citation. New Meson-built C unit -> add to `.config/clang-tidy/lane-files.txt` (clang-tidy lane; audit fails unread unit, ADR-0168).
7. Every commit: zero warnings; fast suite green; deband shader compiled by glslang.
8. Embeddable library code: no `printf` or `fprintf(stderr, ...)`. Return `pel_result`; host logs.
9. New dependency: ADR names considered alternative plus reason this one wins.
10. No new top-level Markdown docs unless task needs one; extend `docs/` topic tree.
11. Licence (ADR-0171): own files EUPL-1.2, copyright `2026 Lusoris`; files joining FFmpeg tree (`ffmpeg-patches/files/`, patches) LGPL-2.1-or-later; vendored files keep upstream licence. New C, shell or Python file: copyright line plus `SPDX-License-Identifier`. Other terms: `REUSE.toml` table after whole-tree default. `reuse lint` exits 0.

## Enforcement here

Harness table = engine rendering; `not enforced` there = no engine-generated check. Checks wired in this repository:

| Rule | Decided here by |
| --- | --- |
| HISS-01 | audit `HISS invariant scan` (C scanner) + `c-reviewer`; baseline ratchet. One-level `goto fail` cleanup allowed; other new `goto` rejected |
| HISS-02 | audit `HISS invariant scan` (unbounded C loops) + `c-reviewer`; baseline ratchet. I/O deadlines not measured for C |
| HISS-03 | review only (`c-reviewer`) + tests; no scanner |
| HISS-04 | LOC <= 60: audit scan + baseline ratchet. Cyclomatic, cognitive, statements: `[REPORT]` only (Go only) + `c-reviewer`; clang-tidy size check advisory |
| HISS-05 | review only (`c-reviewer`); no analyzer |
| HISS-06 | review only; no analyzer; `sanitizers` CI job finds races, not pool bounds |
| HISS-07 | `pel_result` contract: clang-tidy (`make verify-native`) + `c-reviewer`; audit scanner reads Python/shell for this rule, not C |
| HISS-08 | audit scan (banned-call rule) + `c-reviewer` + build |
| HISS-09 | bound offset arithmetic before pointer formation: review only (`c-reviewer`, CERT C) + tests + `sanitizers` CI job |
| HISS-10 | audit `Build warnings (HISS-10)` + `clang-tidy translation-unit coverage` + `make verify-native`; every CI `meson setup` passes `--werror`; fails audit and CI |
| HISS-11 | audit `Supply chain (HISS-11)`: L3 from `release.yml`, SBOM from `release-build.yml`; reads workflow files only, not published attestations |
| HISS-12 | `scripts/check-gitleaks.sh` in required `docs` CI job: pinned gitleaks, every branch + tag, `--self-test` planted side-branch secret; exceptions = `.gitleaksignore` fingerprints only (ADR-0190). Not in verification gate |
| HISS-13 | `baseline --verify` + audit ratchet (`--base`, touched-debt delta) in `make audit`, pre-commit, `standards-gate.yml`; fails CI |
| HISS-14 | footer: `commit-msg` hook (`forge check-message`) + CI `forge check-commits`. Append-only: `interop_test.c` fixture + `interop-abi-reviewer`. Hooks opt-in; CI authoritative |
| HISS-15 | fast suite (`meson test --suite=fast`) in `core` CI job + PR review; skip passes only when named (`check-test-skips.py`, ADR-0188) |
| HISS-16 | `compile-context --verify` (pre-commit `context-check`, `standards-gate.yml`) + audit context, persona, caveman checks |
| HISS-17 | Lefthook `post-commit` `state-sync` (hooks installed) + `session-start` hook; no gate fails on stale ledger |
| HISS-18 | audit `Workflow triggers (HISS-18)`: `[WARN]` only, 10 open findings in 5 of 8 workflows (#207) |
| HISS-19 | review only: `dedupe scan` reads Go only, none here; `dedupe-cadence` post-commit bookkeeping runs |
| HISS-20 | not wired: no `.config/hiss/coverage.yaml`, so `hiss coverage --verify` cannot run |
| HISS-21 | partial: `windows` CI job (MSYS2 UCRT64, fast suite); Make, shell, Lefthook gates Linux-first; no macOS leg |

Engine: pin = `PRAETOR_REF` in `.github/workflows/standards-gate.yml`. Make (`.config/praetor/engine.mk`) + Lefthook jobs run it via `.config/lefthook/engine.sh`: `go install` on first use into `${XDG_CACHE_HOME:-~/.cache}/praetor/engine/<pin>/`, never PATH, fail closed (ADR-0187). Direct engine call: `sh .config/lefthook/engine.sh <args>`. `PRAETORCTL=<path>` overrides make only, unchecked. Agent hooks call `praetorctl` by name: copy pinned `standardsctl` to `praetorctl` on `PATH`; missing binary -> hook exits 127 (bash) or 1 (cmd, PowerShell), client reports non-blocking error, shell call runs without Praetor policy. Docs gate needs Node 22.12+. Never install hooks or fake hook file to pass gate.

## Layout and ownership

| Path | Contract |
| --- | --- |
| `meson.build`, `meson_options.txt` | build root: libpelorus, tests, shaders |
| `LICENSE`, `LICENSES/`, `REUSE.toml` | EUPL-1.2 root text (bytes of `LICENSES/EUPL-1.2.txt`); every licence text in use; per-file copyright + licence, REUSE 3.3, default table first; audit checks table order + root licence (ADR-0171) |
| `libpelorus/include/pelorus/` | public API, version, append-only interop ABI |
| `libpelorus/src/` | core pack/parse + parameter logic |
| `libpelorus/test/` | ABI and API conformance; UTF-8 path test |
| `libpelorus/shaders/` | standalone reference shaders |
| `ffmpeg-patches/files/` | canonical FFmpeg host/filter sources |
| `ffmpeg-patches/files/vulkan/` | canonical shipped shader sources |
| `ffmpeg-patches/0001-*.patch` | generated artifacts; never hand-edit |
| `ffmpeg-patches/{generate.sh,series.txt,test/}` | regeneration, apply order (after shared series), replay + smoke gate |
| `docs/adr/` | decisions; reserve via `scripts/adr/next-free.sh --claim <slug>` |
| `docs/{architecture,api,metrics,usage,backends,development}/` | human-readable surface docs |
| `docs/research/` | measured deep-dive evidence |
| `changelog.d/` | Keep-a-Changelog fragments |
| `tools/pelorus_qp_report.c`, `tools/pelorus_zones.c`, `tools/pelorus_qloop.c` | libpelorus CLI demonstrators; not installed |
| `tools/tester/` | tester report program (stdlib Python, `python3 -I`), JSON schema, `--self-test` (ADR-0173) |
| `tools/markdownlint/`, `tools/figures/` | Praetor-managed docs gate; refresh via `adopt` only |
| `scripts/` | ADR claim, bench, release, build-config + shader checks, shared-series fetch + verify (`fetch-ffmpeg-series.sh`, ADR-0185), secret scan (`check-gitleaks.sh`, ADR-0190), Windows tester zip packaging + PE import check (`windows/`, ADR-0213) |
| `Makefile`, `lefthook.yml` | native + governance entry points; Git hooks opt-in (`make hooks-install`; `no_auto_install`) |
| `.standards.yaml`, `.standards.lock`, `.standards-baseline.json`, `.config/` | Praetor policy, lock, catalog, labels, HISS baseline, clang-tidy lane list, named test skips (`test-skips.json`, ADR-0188), Lefthook checkpoint scripts + policy, engine launcher (`lefthook/engine.sh`) + make fragment (`praetor/engine.mk`), agent interceptor |
| `.devcontainer/` | Praetor-rendered dev container bundle (audit: byte-exact; regenerate, never hand-edit); `base/Containerfile` toolchain base image (ADR-0153) |
| `.paperclip/` | Paperclip harness pair; string edit: re-pin `register.sources` from `sh .config/lefthook/engine.sh caveman check --configured-sources --root=.` |
| `.agents/agents/` | canonical reviewer personas |
| `.claude/agents/`, `.codex/agents/`, `.github/agents/`, `.gemini/agents/` | generated persona projections |
| `.claude/skills/`, `.claude/hooks/`, `.codex/hooks/`, `.gemini/settings.json` | agent skills + hooks; see below |
| `.vscode/`, `.zed/`, `.idea/`, `.helix/`, `.fleet/`, `.nvim.lua`, `lua/`, `.dir-locals.el`, `standards.sublime-project` | reconciled editor settings |
| `.github/workflows/` | `ci.yml` product jobs, `standards-gate.yml`, locked `praetor-docs.yml`, `release.yml` (`ci` -> `build` -> tag-only `publish`), reusable `release-build.yml`: build, SBOM, SLSA L3 attestation, cosign; no artefact download or cache before attestation (ADR-0169); `devcontainer-image.yml` builds base image on PR, pushes + attests `ghcr.io/vmafx/pelorus-dev` from `master`; `windows-tester.yml` (path-filtered MSYS2 replay + tester zip artifact, no release); Praetor-rendered `reuse.yml` runs `reuse lint` |
| `.github/rulesets/main.json` | rendered `master` ruleset (0 reviews, signed commits, linear history, 10 checks); committed, applied only by maintainer `praetorctl sync --remote` |

New top-level package: add row here. New module: scoped `AGENTS.md`.

## Skills and hooks

| Skill (`.claude/skills/`) | Use |
| --- | --- |
| `build` | configure, build, fast suite; local gate |
| `format-all`, `lint-all` | clang-format; clang-tidy + shader compile |
| `add-vulkan-filter` | scaffold new `vf_pelorus_*` filter end-to-end |
| `ffmpeg-build-patches`, `ffmpeg-apply-patches` | regenerate stack; apply + build + smoke |
| `new-adr` | reserve + create ADR before implementing commit |
| `bump-abi` | append-only interop ABI extension |
| `render-changelog` | render `CHANGELOG.md` from `changelog.d/` |
| `cut-release` | version bump + tag; release workflow |
| `caveman`, `social-text`, `adhd-format` | text register forms; Praetor-installed in `.agents/skills/`, projected by `compile-context` |

`.claude/skills/superpowers/`: vendored obra/superpowers process skills (verification-before-completion, systematic-debugging, test-driven-development, code review, git worktrees); template for new skills. Topic links: `docs/references.md`.

Hooks: `.claude/hooks/` wired in `.claude/settings.json`; Codex twins in `.codex/hooks/` via `.codex/hooks.json`. Praetor pre-tool row (adopted `agent-hooks`, ADR-0145) in those two files plus `.gemini/settings.json`; `adopt` owns it, merge keeps Pelorus rows.

| Event | Hook | Effect |
| --- | --- | --- |
| PreToolUse Bash | `block-unsafe-bash` | blocks `rm -rf /`, force-push to `master`, hard reset onto `origin/master`, `git clean -xf`, fork bombs |
| PreToolUse Bash (Claude, Codex); BeforeTool `^run_shell_command$` (Gemini) | `praetorctl hook <client> pre-tool` | Praetor command policy, exit 2 = deny: hook-skip flags (`--no-verify`, commit `-n`), Lefthook disable, `core.hooksPath` writes, `.git/hooks` edits, `lefthook uninstall`; skips outside governed checkout; 15 s budget |
| PostToolUse Edit/Write | `auto-format-on-edit` | clang-format on C/H |
| PostToolUse Edit/Write | `shader-lockstep-warn` | guards single `.comp.glsl` source model |
| PostToolUse Edit/Write | `docs-drift-warn` | ABI, surface, build-flag change: docs, ADR, changelog, patch reminder |
| SessionStart | `session-start` | branch + PLAN/STATE orientation; build staleness |
| Stop | `stop` | local-gate reminder while sources unverified |

## Commands

| Goal | Command |
| --- | --- |
| configure | `meson setup build` |
| build | `ninja -C build` |
| fast tests | `meson test -C build --suite=fast --print-errorlogs` |
| native gate | `make verify-native` |
| governance + native gate | verification gate |
| install | `ninja -C build install` |
| fetch + verify shared series | `scripts/fetch-ffmpeg-series.sh /absolute/new-dir` (`--self-test`: planted defects) |
| regenerate patches | `FFMPEG_REPO=/absolute/path/to/ffmpeg ffmpeg-patches/generate.sh` |
| replay stack | `FFMPEG_REPO=/absolute/path/to/ffmpeg ffmpeg-patches/test/build-and-run.sh` |
| compile contexts | `make compile-context` |
| verify contexts | `make compile-context-verify` |
| audit baseline | `sh .config/lefthook/engine.sh audit` |
| docs gate (Node 22.12+) | `make docs-lint docs-figures` |
| licence check | `reuse lint` (reuse 6.x) |
| explicit hook install | `make hooks-install` |

## Reviewer routing

| Change | Required persona |
| --- | --- |
| `libpelorus/src/`, public headers | `c-reviewer` |
| `interop.h`, `interop.c` | `interop-abi-reviewer` |
| Vulkan shader or host code | `vulkan-shader-reviewer` |
| `ffmpeg-patches/` | `ffmpeg-patch-reviewer` |
| user-visible surface | `doc-reviewer` |
| final PR contract | `pr-body-checker` |

## Pinned upstreams

| Component | Pin |
| --- | --- |
| FFmpeg patch base | `n9.0.2` / `946fcce07b6dcd0331c8cc609192aeff5e1924f8` from `build-config.env` |
| Shared FFmpeg fix series | VMAFx/ffmpeg-patches `v0.2.0` / `d958384a13297fc009c74f4eb5080b9d83db3a74`, tarball sha256 `1f16f72e77bbe4ca14f9dc8712ae98165bfd1e45694e4c0d608fef02bac1db05`, from `build-config.env`; hand bump, no Renovate ([ADR-0185](docs/adr/0185-shared-ffmpeg-fix-series.md)) |
| FFmpeg Vulkan models | `vf_gblur_vulkan.c`, `vf_nlmeans_vulkan.c`, `vf_scdet_vulkan.c` |
| AV1 grain ABI mirror | `libavutil/film_grain_params.h` |
| vmafx control plane | `libvmaf_tune`, `/v1/score`, `vmaf-mcp` |
| Praetor engine | `2d23b292417386fe8259da29690266ce331062c6` from `.github/workflows/standards-gate.yml` |
| Go for actionlint (`ci.yml` docs job) | `1.27.x`; change with `ACTIONLINT_GO_VERSION` in `scripts/check-build-config.py`; checker's Renovate manager keeps setup-go's `go` identity ([ADR-0151](docs/adr/0151-renovate-mirrors-checker-toolchain-pins.md)) |

## Delivery

- Non-trivial PR: ADR, per-surface docs, changelog fragment, research digest, runnable verification.
- FFmpeg-impacting PR: rebase note + regenerated stack.
- New module: scoped `AGENTS.md`.
- Every commit: `.workingdir/STATE.md` session-log entry; README "Landed so far" row when build-order step lands; layout row for new top-level package.
- Decision/status truth: `docs/adr/`, `docs/roadmap.md`, `.workingdir/OPEN.md`, `.workingdir/STATE.md`.
- Uncertainty: verify `docs/principles.md`, scoped `AGENTS.md`, current source, current executable help.
