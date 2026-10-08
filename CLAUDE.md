<!-- markdownlint-disable MD013 -->
<!-- Compiled automatically by praetorctl compile-context from AGENTS.md. DO NOT EDIT DIRECTLY. -->

<!-- markdownlint-disable MD013 MD025 -->
# Pelorus Agent Operating Harness

Before delivery:

```bash
make verify-native
standardsctl compile-context --verify
standardsctl audit
make docs-lint docs-figures
```

`make verify-all` runs all four. Docs gate needs Node 22.12+. Never install
hooks or fake hook file to pass gate.

Engine binary: pinned `go install .../cmd/standardsctl@<pin>` installs
`standardsctl`. `praetorctl`: same engine under its newer name; generated text
uses it. Makefile takes either. Agent hooks call `praetorctl` by name: copy or
rename pinned `standardsctl` to `praetorctl` on `PATH`. Missing binary: hook
exits 127 (bash) or 1 (cmd, PowerShell); clients report non-blocking error and
shell call runs without Praetor policy.

## Core Directives & Invariants (Modernized NASA JPL Power-of-10)

| Invariant | Scope | NASA Rule | Enforcement Mechanism | Failure Action |
| :--- | :--- | :--- | :--- | :--- |
| **HISS-01** | Control flow | Rule 1 | No recursion. Allow one-level `goto fail` cleanup only; reject other new `goto`. | Audit ratchet + C review |
| **HISS-02** | Loops | Rule 2 | Every loop has scalar upper bound; validate external counts before iteration. | Audit ratchet + C review |
| **HISS-03** | Memory | Rule 3 | No dynamic allocation after init in hot per-frame paths. | C review + tests |
| **HISS-04** | Complexity | Rule 4 | New/touched functions: $\le 60$ LOC. Effective policy also sets cyclomatic $\le 10$, cognitive $\le 12$, statements $\le 40$. | LOC: audit ratchet. Other limits: C review only (audit measures Go only; clang-tidy size check advisory). |
| **HISS-07** | Error handling | Rule 7 | Public errors use `pel_result`; every non-void result checked or explicitly discarded. | clang-tidy + C review |
| **HISS-08** | Determinism | Rule 8 | No dynamic execution; reject banned libc from project contract. | C review + build |
| **HISS-09** | Reference safety | Rule 9 | Bound offset arithmetic before pointer formation; avoid pointer chasing. | CERT C review + tests |
| **HISS-10** | Warning hygiene | Rule 10 | Compiler, formatter, and configured clang-tidy gate exit clean. Every CI `meson setup` passes `--werror`; audit ignores `default_options`. | Native gate + audit |
| **HISS-15** | 3D testing | Rule 5 | Public interfaces cover positive, negative, and boundary cases. | Test + PR review |
| **HISS-16** | Context integrity | Fleet | `AGENTS.md` plus `.agents/agents/*.md` are canonical; vendor Markdown is generated. | Context verify |

## Operational Rules

1. **Act on verified state.** Read source and executable help before edits. Never
   guess flags, signatures, or repository configuration.

2. **Lead with output.** Direct answers, diffs, commands. No filler or request
   restatement.

3. **Context transpiler first.** Never edit `CLAUDE.md`,
   `.cursor/rules/*.mdc`, `.windsurfrules`,
   `.github/copilot-instructions.md`, `.gemini/GEMINI.md`, or
   `.codex/rules.md` manually. Update `AGENTS.md`, then:

   ```bash
   standardsctl compile-context
   ```

   Canonical agent text must pass `standardsctl caveman check`.

4. **Preserve useful evidence.** Keep long logs under ignored
   `.workingdir/evidence/`; report root causes with file and line pointers.

5. **No evasion.** Never use `--no-verify`, disable Lefthook, or modify shared
   `.git/hooks` to bypass a gate. Hook installation is explicit.

6. **Stop repeated failure loops.** Same diff and error category three times:
   re-evaluate root cause before another edit.

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
make verify-native

# Recompile and verify cross-agent context outputs
standardsctl compile-context --verify

# Audit repository against declared HISS standards
standardsctl audit

# Hosted ratchet: growth guard + debt delta, then fingerprint-exact baseline
CI=true standardsctl audit --base origin/master --touched-debt-delta-reason "<why>"
standardsctl baseline --verify

# Run all formatting, linting, and security gates
make verify-all
```

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
- Release `v0.2.2`: library version 0.x. Interop ABI 1.3 (`PELORUS_ABI_MAJOR` 1, `PELORUS_ABI_MINOR` 3), append-only.
- Architecture: `docs/architecture/overview.md`; rules: `docs/principles.md`.

## Project state

- Inventory: 10 filters (deband, analyze, denoise, grain_estimate, mc, dehalo, aa, deblock, borderfix, scenecut) plus `pelorus_fgs` BSF; 18-patch stack.
- Encoder steering: NVENC, QSV, Vulkan, libaom, SVT-AV1 patches; QP-feedback path. README "Modules" table: current inventory, no stubs.
- FFmpeg 9 base: build-time SPIR-V, no runtime GLSL API (ADR-0143).
- Forge: `VMAFx/pelorus`. Run `gh repo set-default vmafx/pelorus` before any `gh` command.
- Plan/status: `.workingdir/PLAN.md`, `.workingdir/STATE.md`, backlog `.workingdir/AUDIT-2026-08-30.md` (local, git-ignored).

## Hard rules

1. `PelorusSideData` ABI append-only. Add fields at section tail or mint section bit. Bump `PELORUS_ABI_MINOR`. Never reorder, resize, remove, repurpose. Public ABI changes require `Migration:` commit footer with before/after C snippets.
2. Public non-void APIs return `pel_result`. Check each non-void call or cast `(void)`. No bare `return -1` across API boundary.
3. No mutable global state or static-init side effects. Banned: `gets`, `strcpy`, `strcat`, `sprintf`, `strtok`, `atoi`, `atof`, `rand`, `system`.
4. FFmpeg filter shader source lives once: `ffmpeg-patches/files/vulkan/pelorus_<name>.comp.glsl`; FFmpeg 9 compiles SPIR-V at build time. Never add runtime or inline GLSL. `libpelorus/shaders/*.comp`: standalone fast-gate references, not shipped mirrors. Spec IDs `253`, `254`, `255`: reserved workgroup sizes. Descriptor order and push layout must match C exactly.
5. Patch consumers changed -> update `ffmpeg-patches/files/` plus regenerated stack in same PR. Verify full `series.txt` replay; per-patch apply check insufficient.
6. Touched files: `-Wall -Wextra -Werror`, clang-format, clang-tidy clean. Each `// NOLINT`: inline citation. New Meson-built C unit -> add to `.config/clang-tidy/lane-files.txt` (clang-tidy lane; audit fails unread unit, ADR-0168).
7. Every commit: zero warnings; fast suite green; deband shader compiled by glslang.
8. Embeddable library code: no `printf` or `fprintf(stderr, ...)`. Return `pel_result`; host logs.
9. New dependency: ADR names considered alternative plus reason this one wins.
10. No new top-level Markdown docs unless task needs one; extend `docs/` topic tree.

## Layout and ownership

| Path | Contract |
| --- | --- |
| `meson.build`, `meson_options.txt` | build root: libpelorus, tests, shaders |
| `libpelorus/include/pelorus/` | public API, version, append-only interop ABI |
| `libpelorus/src/` | core pack/parse + parameter logic |
| `libpelorus/test/` | ABI and API conformance; UTF-8 path test |
| `libpelorus/shaders/` | standalone reference shaders |
| `ffmpeg-patches/files/` | canonical FFmpeg host/filter sources |
| `ffmpeg-patches/files/vulkan/` | canonical shipped shader sources |
| `ffmpeg-patches/0001-*.patch` | generated artifacts; never hand-edit |
| `ffmpeg-patches/{generate.sh,series.txt,test/}` | regeneration, apply order, replay + smoke gate |
| `docs/adr/` | decisions; reserve via `scripts/adr/next-free.sh --claim <slug>` |
| `docs/{architecture,api,metrics,usage,backends,development}/` | human-readable surface docs |
| `docs/research/` | measured deep-dive evidence |
| `changelog.d/` | Keep-a-Changelog fragments |
| `tools/pelorus_qp_report.c` | libpelorus CLI demonstrator; not installed |
| `tools/markdownlint/`, `tools/figures/` | Praetor-managed docs gate; refresh via `adopt` only |
| `scripts/` | ADR claim, bench, release, build-config + shader checks |
| `Makefile`, `lefthook.yml` | native + governance entry points; Git hooks opt-in (`make hooks-install`; `no_auto_install`) |
| `.standards.yaml`, `.standards.lock`, `.standards-baseline.json`, `.config/` | Praetor policy, lock, catalog, labels, HISS baseline, clang-tidy lane list, Lefthook checkpoint scripts + policy, agent interceptor |
| `.devcontainer/` | Praetor-rendered dev container bundle (audit: byte-exact; regenerate, never hand-edit); `base/Containerfile` toolchain base image (ADR-0153) |
| `.paperclip/` | Paperclip harness pair; string edit: re-pin `register.sources` from `standardsctl caveman check --configured-sources --root=.` |
| `.agents/agents/` | canonical reviewer personas |
| `.claude/agents/`, `.codex/agents/`, `.github/agents/`, `.gemini/agents/` | generated persona projections |
| `.claude/skills/`, `.claude/hooks/`, `.codex/hooks/`, `.gemini/settings.json` | agent skills + hooks; see below |
| `.vscode/`, `.zed/`, `.idea/`, `.helix/`, `.fleet/`, `.nvim.lua`, `lua/`, `.dir-locals.el`, `standards.sublime-project` | reconciled editor settings |
| `.github/workflows/` | `ci.yml` product jobs, `standards-gate.yml`, locked `praetor-docs.yml`, `release.yml` (`ci` -> `build` -> tag-only `publish`), reusable `release-build.yml`: build, SBOM, SLSA L3 attestation, cosign; no artefact download or cache before attestation (ADR-0169); `devcontainer-image.yml` builds base image on PR, pushes + attests `ghcr.io/vmafx/pelorus-dev` from `master` |
| `.github/rulesets/main.json` | rendered `master` ruleset (0 reviews, signed commits, linear history, 7 checks); committed, applied only by maintainer `praetorctl sync --remote` |

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
| governance + native gate | `PRAETORCTL=standardsctl make verify-all` |
| install | `ninja -C build install` |
| regenerate patches | `FFMPEG_REPO=/absolute/path/to/ffmpeg ffmpeg-patches/generate.sh` |
| replay stack | `FFMPEG_REPO=/absolute/path/to/ffmpeg ffmpeg-patches/test/build-and-run.sh` |
| compile contexts | `standardsctl compile-context` |
| verify contexts | `standardsctl compile-context --verify` |
| audit baseline | `standardsctl audit` |
| docs gate (Node 22.12+) | `make docs-lint docs-figures` |
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
| FFmpeg Vulkan models | `vf_gblur_vulkan.c`, `vf_nlmeans_vulkan.c`, `vf_scdet_vulkan.c` |
| AV1 grain ABI mirror | `libavutil/film_grain_params.h` |
| vmafx control plane | `libvmaf_tune`, `/v1/score`, `vmaf-mcp` |
| Praetor engine | `492a00f930e1a2df557ffebb76564cfa65637b77` from `.github/workflows/standards-gate.yml` |
| Go for actionlint (`ci.yml` docs job) | `1.27.x`; change with `ACTIONLINT_GO_VERSION` in `scripts/check-build-config.py`; checker's Renovate manager keeps setup-go's `go` identity ([ADR-0151](docs/adr/0151-renovate-mirrors-checker-toolchain-pins.md)) |

## Delivery

- Non-trivial PR: ADR, per-surface docs, changelog fragment, research digest, runnable verification.
- FFmpeg-impacting PR: rebase note + regenerated stack.
- New module: scoped `AGENTS.md`.
- Every commit: `.workingdir/STATE.md` session-log entry; README "Landed so far" row when build-order step lands; layout row for new top-level package.
- Decision/status truth: `docs/adr/`, `.workingdir/PLAN.md`, `.workingdir/STATE.md`.
- Uncertainty: verify `docs/principles.md`, scoped `AGENTS.md`, current source, current executable help.
