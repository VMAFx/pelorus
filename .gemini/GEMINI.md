<!-- markdownlint-disable MD013 -->
<!-- Compiled automatically by praetorctl compile-context from AGENTS.md. DO NOT EDIT DIRECTLY. -->

<!-- markdownlint-disable MD013 -->
# VMAFx/pelorus Agent Operating Harness

Before concluding any turn:

```bash
make verify-all
```

`make verify-all` = repository-owned gate; adoption kept it unread + unexecuted. Steps live in `Makefile`: read there, never restate. Footer commands = project markers only.
Pass = exit 0. Signed Ed25519 Exit-0 receipt only from `praetorctl gate run`; report no receipt it did not mint. Fail -> SARIF diagnostic distillation (<= 1500 tokens).

## Core Directives & Invariants

Adopted check = coverage state + mechanism; else audit scanner; else `unknown`. Gates = (none: no generated `make verify-all`, no praetor `lefthook.yml`); edits not reflected.

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
| **HISS-17** state ledger | turn start `praetorctl state status`; turn end `praetorctl state sync .` | unknown | advisory |
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

## Primary Verification Commands

```bash
# Fast local test suite
# Declared commands only; run them before claiming application verification.
'make' 'verify-all'

# Recompile and verify cross-agent context outputs
praetorctl compile-context --verify

# Audit repository against declared HISS standards
praetorctl audit

# Repository gate; steps live in Makefile
make verify-all
```

<!-- markdownlint-enable MD013 -->
<!-- markdownlint-disable MD025 -->
<!-- praetor:harness:end -->

---

# Pelorus project contract

Canonical cross-tool context. Read scoped `AGENTS.md` before edits. Rationale: `docs/`. Client files: generated projections; never hand-edit. Reference tables (HISS enforcement, layout, skills, hooks, commands, pins): `docs/development/agent-reference.md`.

## Mission

- GPU pre-encode pipeline: Vulkan compute + FFmpeg filters, zero-copy VRAM path; narrows fixed-function encoder BD-rate gap via deband, denoise, grain synthesis, motion hints. Film grain: AV1 AOM or HEVC/VVC H.274.
- Sibling `VMAFx/vmafx`: quality oracle + autotune. Shared contract `PelorusSideData`: Pelorus writes, vmafx reads.
- Release `v0.4.0` line: library 0.x, interop ABI 1.6 (`PELORUS_ABI_MAJOR` 1, `PELORUS_ABI_MINOR` 6; ADR-0183, ADR-0214), append-only.
- FFmpeg base `n9.0.2` / `946fcce07b6dcd0331c8cc609192aeff5e1924f8` (`build-config.env`): build-time SPIR-V, no runtime GLSL (ADR-0143). Shared fix series pin: `build-config.env` (ADR-0185).
- Forge `VMAFx/pelorus`: `gh repo set-default vmafx/pelorus` before `gh`. Plan: `docs/roadmap.md`, milestones; local `.workingdir/{OPEN,STATE,BACKLOG}.md`.
- Inventory: README "Modules" table; patch order `ffmpeg-patches/series.txt`. Architecture `docs/architecture/overview.md`; rules `docs/principles.md`.

## Hard rules

1. `PelorusSideData` append-only: fields at section tail or new section bit; bump `PELORUS_ABI_MINOR`; never reorder, resize, remove, repurpose. Public ABI change: `Migration:` footer, before/after C.
2. Public non-void APIs return `pel_result`; check each non-void call or cast `(void)`; no bare `return -1` across API.
3. No mutable globals, no static-init side effects. Banned: `gets`, `strcpy`, `strcat`, `sprintf`, `strtok`, `atoi`, `atof`, `rand`, `system`.
4. Shader source once: `ffmpeg-patches/files/vulkan/pelorus_<name>.comp.glsl`; no runtime or inline GLSL. `libpelorus/shaders/*.comp` = fast-gate references. Spec IDs `253`-`255` reserved (workgroup). Descriptor order + push layout match C.
5. Consumer change -> `ffmpeg-patches/files/` + regenerated stack, same PR; full `series.txt` replay; per-patch apply check insufficient. Shared series applies first via `scripts/fetch-ffmpeg-series.sh` (fail closed). Generic FFmpeg fix -> VMAFx/ffmpeg-patches.
6. Touched files: `-Wall -Wextra -Werror`, clang-format, clang-tidy clean; `// NOLINT` cites reason. New Meson C unit -> `.config/clang-tidy/lane-files.txt` (ADR-0168).
7. Every commit: zero warnings, fast suite green, deband shader compiles (glslang).
8. Library code: no `printf`/`fprintf(stderr)`; return `pel_result`, host logs.
9. New dependency: ADR names alternative + why this wins.
10. No new top-level Markdown; extend `docs/`.
11. Licence (ADR-0171): own files EUPL-1.2, `2026 Lusoris`; FFmpeg-tree files LGPL-2.1-or-later; vendored keep upstream. New C/shell/Python: copyright + `SPDX-License-Identifier`. `reuse lint` exits 0.

## Commands

`meson setup build`; `ninja -C build`; `meson test -C build --suite=fast --print-errorlogs`; `make verify-native`; `make verify-all`; `make compile-context`; rest: reference table.

## Reviewer routing

| Change | Required persona |
| --- | --- |
| `libpelorus/src/`, public headers | `c-reviewer` |
| `interop.h`, `interop.c` | `interop-abi-reviewer` |
| Vulkan shader or host code | `vulkan-shader-reviewer` |
| `ffmpeg-patches/` | `ffmpeg-patch-reviewer` |
| user-visible surface | `doc-reviewer` |
| final PR contract | `pr-body-checker` |

## Delivery

- Non-trivial PR: ADR, surface docs, changelog fragment, research digest, runnable verification. FFmpeg-impacting PR: rebase note + regenerated stack.
- New module: scoped `AGENTS.md`; new top-level package: layout row in the reference.
- Every commit: `.workingdir/STATE.md` log entry; README "Landed so far" row per landed build-order step. Truth: `docs/adr/`, `docs/roadmap.md`, `.workingdir/OPEN.md`.
- Unsure: verify `docs/principles.md`, scoped `AGENTS.md`, current source, executable help.
