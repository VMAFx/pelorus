<!-- markdownlint-disable MD013 -->
# ADR-0104: Delivery — libpelorus core + an FFmpeg patch stack

- **Status**: Accepted
- **Date**: 2026-06-14
- **Deciders**: Lusoris
- **Tags**: ffmpeg, build, delivery

## Context

The Vulkan filters must integrate with FFmpeg to be usable in real encodes, and
must share a contract with vmafx. Two things need a home: the reusable core (the
interop ABI + filter parameter contracts, which vmafx also consumes) and the
FFmpeg-specific filter code (which depends on FFmpeg-internal Vulkan headers).

## Decision

We will ship **`libpelorus`** — a standalone Meson library holding the interop
ABI and filter contracts — and integrate it into FFmpeg as a **patch stack**
under `ffmpeg-patches/`, exactly mirroring vmafx's model. Canonical filter
sources live in `ffmpeg-patches/files/`; `generate.sh` checks out a pristine
n8.1.1 in an isolated git worktree, drops the sources in, wires the registration
hunks (configure / Makefile / allfilters.c), commits, and `git format-patch`es
the artifact. Filters `require_pkg_config libpelorus` and are gated
`vulkan spirv_library`. Apply with `git am --3way`; verify with a full series
replay (`ffmpeg-patches/test/build-and-run.sh`), never per-patch
`git apply --check`.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
|---|---|---|---|
| libpelorus core + FFmpeg patch stack | Reusable ABI both repos link; true zero-copy in libavfilter; matches vmafx tooling | Two build artifacts; patch-rebase maintenance | **Chosen** |
| FFmpeg patch stack only (no shared lib) | Simpler | Interop reduced to copy-pasted structs; no linkable contract → drift | Rejected — the shared ABI is the point |
| Out-of-tree shared lib + thin generic FFmpeg filter loading shaders at runtime | Decoupled from FFmpeg internals | Least zero-copy-native; diverges from vmafx; runtime shader loading | Rejected |

## Consequences

- **Positive**: `libpelorus` is unit-testable in isolation and vendorable by
  vmafx; the FFmpeg integration reuses vmafx's proven patch tooling and CI gate.
- **Negative**: patches must be re-applied/rebased on FFmpeg upstream bumps;
  a libpelorus surface change requires regenerating the patch in the same PR
  (AGENTS.md hard rule 5).
- **Neutral / follow-ups**: future filters (denoise, analyze, FGS, mc) land as
  additional patches in `series.txt`; a `docs/rebase-notes.md` tracks re-apply
  work.

## References

- vmafx `ffmpeg-patches/` (`series.txt`, `README.md`, `0002-add-vmaf_pre-filter.patch`).
- FFmpeg `configure`, `libavfilter/Makefile`, `libavfilter/allfilters.c`.
- Source: `Q1.3` — "libpelorus core + ffmpeg patch-stack".
