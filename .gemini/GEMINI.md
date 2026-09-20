<!-- markdownlint-disable MD013 -->
<!-- Compiled automatically by praetorctl compile-context from AGENTS.md. DO NOT EDIT DIRECTLY. -->

<!-- markdownlint-disable MD013 MD025 -->
# Pelorus Agent Operating Harness

Before delivery:

```bash
make verify-native
standardsctl compile-context --verify
standardsctl audit
```

`make verify-all` combines those commands. Pinned Praetor currently fails only
after successful policy, baseline, context, and persona checks because audit
ignores Pelorus's explicit `branch-ruleset` decline (Praetor issue 408). Never
claim full-gate success or add a false ruleset artifact to bypass that defect.

## Core Directives & Invariants (Modernized NASA JPL Power-of-10)

| Invariant | Scope | NASA Rule | Enforcement Mechanism | Failure Action |
| :--- | :--- | :--- | :--- | :--- |
| **HISS-01** | Control flow | Rule 1 | No recursion. Allow one-level `goto fail` cleanup only; reject other new `goto`. | Audit ratchet + C review |
| **HISS-02** | Loops | Rule 2 | Every loop has scalar upper bound; validate external counts before iteration. | Audit ratchet + C review |
| **HISS-03** | Memory | Rule 3 | No dynamic allocation after init in hot per-frame paths. | C review + tests |
| **HISS-04** | Complexity | Rule 4 | New/touched functions: $\le 60$ LOC effective Praetor cap, Cyclomatic $\le 10$, Statements $\le 50$. | Audit ratchet + clang-tidy |
| **HISS-07** | Error handling | Rule 7 | Public errors use `pel_result`; every non-void result checked or explicitly discarded. | clang-tidy + C review |
| **HISS-08** | Determinism | Rule 8 | No dynamic execution; reject banned libc from project contract. | C review + build |
| **HISS-09** | Reference safety | Rule 9 | Bound offset arithmetic before pointer formation; avoid pointer chasing. | CERT C review + tests |
| **HISS-10** | Warning hygiene | Rule 10 | Compiler, formatter, and configured clang-tidy gate exit clean. | Native gate |
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
   praetorctl compile-context
   ```

   Canonical agent text must pass `praetorctl caveman check`.

4. **Preserve useful evidence.** Keep long logs under ignored
   `.workingdir2/evidence/`; report root causes with file and line pointers.

5. **No evasion.** Never use `--no-verify`, disable Lefthook, or modify shared
   `.git/hooks` to bypass a gate. Hook installation is explicit.

6. **Stop repeated failure loops.** Same diff and error category three times:
   re-evaluate root cause before another edit.

## Text Register

<!-- praetor:register:start -->
Register follows the audience, then the task label of your brief (`register:` in `.standards.yaml`; labels are the router's `target_tasks`).

| Register | Where | Form |
| :--- | :--- | :--- |
| social | forge: issues, PR bodies, review comments, commit bodies | `social-text` skill: BLUF, full sentences, scannable, enough and no more; PR template, receipt fence, conventional commit subject and changelog fragment unchanged |
| docs | docs/, README, ADR bodies | complete without bloat: newcomer path first, expert reference after; every claim points at a file, command or test; no restated code |
| internal | briefs, agent-to-agent traffic, research fan-outs, workflow returns | `caveman` skill: fragments, no filler, verbatim code/paths/errors; facts, paths, commands, verdict |

- Task rows: social = commit_message_synthesis, waiver_signoff; docs = architecture_synthesis, function_docstrings; every other label and any brief without one = internal.
- Evidence above 58 lines or 1500 tokens leaves the message as a file under `.workingdir/evidence/`; return `evidence: <path> sha256:<12 hex> lines:<n>` and fetch it only when a decision needs it.
- An internal return carries verdict, changed paths, commands run, evidence pointers and open questions, nothing else.
<!-- praetor:register:end -->

## Primary Verification Commands

```bash
# Fast local test suite
make verify-native

# Recompile and verify cross-agent context outputs
praetorctl compile-context --verify

# Audit repository against declared HISS standards
praetorctl audit

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
- Current project release: `v0.2.2`; public ABI remains pre-1.0 and append-only.
- Architecture: `docs/architecture/overview.md`; rules: `docs/principles.md`.

## Hard rules

1. `PelorusSideData` ABI append-only. Add fields at section tail or mint section bit. Bump `PELORUS_ABI_MINOR`. Never reorder, resize, remove, repurpose. Public ABI changes require `Migration:` commit footer.
2. Public non-void APIs return `pel_result`. Check each non-void call or cast `(void)`. No bare `return -1` across API boundary.
3. No mutable global state or static-init side effects. Banned: `gets`, `strcpy`, `strcat`, `sprintf`, `strtok`, `atoi`, `atof`, `rand`, `system`.
4. FFmpeg filter shader source lives once: `ffmpeg-patches/files/vulkan/pelorus_<name>.comp.glsl`; FFmpeg 9 compiles SPIR-V at build time. Never add runtime or inline GLSL. `libpelorus/shaders/*.comp`: standalone fast-gate references, not shipped mirrors. Spec IDs `253`, `254`, `255`: reserved workgroup sizes. Descriptor order and push layout must match C exactly.
5. Patch consumers changed -> update `ffmpeg-patches/files/` plus regenerated stack in same PR. Verify full `series.txt` replay; per-patch apply check insufficient.
6. Touched files: `-Wall -Wextra -Werror`, clang-format, clang-tidy clean. Each `// NOLINT`: inline citation.
7. Every commit: zero warnings; fast suite green; deband shader compiled by glslang.

## Ownership map

| Path | Contract |
| --- | --- |
| `libpelorus/include/pelorus/` | public API, version, append-only interop ABI |
| `libpelorus/src/` | core pack/parse + parameter logic |
| `libpelorus/test/` | ABI and API conformance |
| `libpelorus/shaders/` | standalone reference shaders |
| `ffmpeg-patches/files/` | canonical FFmpeg host/filter sources |
| `ffmpeg-patches/files/vulkan/` | canonical shipped shader sources |
| `ffmpeg-patches/0001-*.patch` | generated artifacts; never hand-edit |
| `docs/adr/` | decisions; reserve via `scripts/adr/next-free.sh --claim <slug>` |
| `docs/{architecture,api,metrics,usage,backends,development}/` | human-readable surface docs |
| `docs/research/` | measured deep-dive evidence |
| `changelog.d/` | Keep-a-Changelog fragments |
| `.agents/agents/` | canonical reviewer personas |
| `.claude/agents/`, `.codex/agents/`, `.github/agents/`, `.gemini/agents/` | generated persona projections |

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

## Delivery

- Non-trivial PR: ADR, per-surface docs, changelog fragment, research digest, runnable verification.
- FFmpeg-impacting PR: rebase note + regenerated stack.
- New module: scoped `AGENTS.md`.
- Decision/status truth: `docs/adr/`, `.workingdir/PLAN.md`, `.workingdir/STATE.md`.
- Uncertainty: verify `docs/principles.md`, scoped `AGENTS.md`, current source, current executable help.
