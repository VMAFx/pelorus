<!-- markdownlint-disable MD013 -->
# Claude Code guide — Pelorus

> Claude Code-specific guide. For cross-tool conventions read [AGENTS.md](AGENTS.md)
> first; this file extends it. Repo: `vmafx/pelorus` (hosted under the `vmafx`
> GitHub org, alongside `VMAFx/vmafx`). Run `gh repo set-default vmafx/pelorus`
> at the start of a session before any `gh` command.

## What this is

The GPU pre-encode sibling of vmafx. Vulkan compute + FFmpeg filters that
pre-process frames in VRAM to close the BD-rate gap to CPU encoders. Flagship:
`vf_pelorus_deband_vulkan` (smart deband). The shared `libpelorus` interop ABI
is what makes Pelorus⇄vmafx bidirectional.

## How to build / test

```bash
meson setup build && ninja -C build      # libpelorus + interop test + shader
meson test -C build --suite=fast         # the pre-push gate
ninja -C build install                   # install so the FFmpeg patches see it
cd ffmpeg-patches && ./generate.sh        # regenerate the patch stack
ffmpeg-patches/test/build-and-run.sh      # apply + build + smoke the filter
```

## Where the code is

- `libpelorus/include/pelorus/interop.h` — the side-data ABI (the cross-repo
  contract; append-only).
- `libpelorus/src/interop.c` — pack/parse; **vendored verbatim by vmafx too**.
- `libpelorus/shaders/pelorus_deband.comp` — standalone reference shader.
- `ffmpeg-patches/files/vf_pelorus_deband_vulkan.c` — the in-tree filter (inline
  GLSL). Keep its algorithm in lockstep with the `.comp`.

## Tone

- Be terse. No preamble.
- When changing the `libpelorus` public ABI: write the `Migration:` footer in
  the commit body, with before/after C snippets, and bump `PELORUS_ABI_MINOR`.
- When adding a dependency: state the alternative you considered and why this
  one wins, in the ADR.
- Never add static-init side effects. Lifecycle is explicit.

## Don't

- Don't edit the two deband implementations independently — the `.comp` and the
  filter's inline GLSL must stay in lockstep (AGENTS.md hard rule 4).
- Don't reorder/resize/remove a field in any `PelorusSideData` struct — it is a
  frozen wire ABI (interop.h R1/R2). Append only; new meaning = new section bit.
- Don't hand-edit a generated `*.patch` — edit `ffmpeg-patches/files/` and rerun
  `generate.sh`.
- Don't `printf`/`fprintf(stderr,...)` from library code paths meant to be
  embeddable; return a `pel_result` and let the host log.
- Don't silence a linter without an inline justification next to the `// NOLINT`.
- Don't create new top-level markdown docs unless the task needs them; extend
  the existing `docs/` topic tree.

## Per-PR deliverables (mirrors vmafx)

Every non-trivial PR ships, in the same PR: an **ADR** (claim the number with
`scripts/adr/next-free.sh --claim <slug>`), **per-surface docs** under `docs/`,
a **changelog.d fragment**, and — for anything touching a surface the FFmpeg
patches consume — the **regenerated patch**. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Project state

- v0.1.0 scaffold. Landed: `libpelorus` interop ABI + smart-deband flagship
  (`vf_pelorus_deband_vulkan`, working) + the patch stack against n8.1.1.
  Stubs/roadmap: temporal denoise, FGS estimation + BSF, optical-flow MV hints,
  `vf_pelorus_analyze` (measured banding/variance maps).
- Plan + status: `.workingdir/PLAN.md` and `.workingdir/STATE.md` (local).

## Every commit: keep docs + state in sync

- Update `.workingdir/STATE.md` session log with the commit summary.
- Update [README.md](README.md) "Landed so far" when a build-order step lands.
- Update [AGENTS.md](AGENTS.md) layout tree when adding top-level packages.
- Write a per-subpackage `AGENTS.md` for any new module.
- Land the ADR + per-surface docs + changelog fragment in the same PR.
