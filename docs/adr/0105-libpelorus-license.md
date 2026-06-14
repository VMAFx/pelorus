<!-- markdownlint-disable MD013 -->
# ADR-0105: libpelorus is BSD-2-Clause-Patent; FFmpeg filter files are LGPL-2.1

- **Status**: Accepted
- **Date**: 2026-06-14
- **Deciders**: Lusoris
- **Tags**: license

## Context

`libpelorus/src/interop.c` (and its headers) is meant to be **vendored or linked
verbatim by both Pelorus and vmafx** so the side-data ABI cannot drift. vmafx is
licensed BSD-2-Clause-Patent (the upstream Netflix/vmaf license). Files that
become part of FFmpeg when the patches apply must be license-compatible with
FFmpeg (LGPL-2.1+).

## Decision

We will license **`libpelorus` under BSD-2-Clause-Patent** (a.k.a. BSD+Patent),
matching vmafx so the shared interop translation unit is co-vendorable with no
license seam. Files under `ffmpeg-patches/files/` that are added to the FFmpeg
tree carry FFmpeg's **LGPL-2.1** header (`Copyright 2026 Lusoris`), as vmafx's
`vf_vmaf_pre.c` does. Wholly-new non-FFmpeg files use the `Copyright 2026
Lusoris` BSD+Patent header block.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
|---|---|---|---|
| BSD-2-Clause-Patent for libpelorus | Matches vmafx; co-vendorable interop TU; patent grant | — | **Chosen** |
| MIT | Simple, permissive | No explicit patent grant; a license seam with vmafx's BSD+Patent when vendoring the shared TU | Rejected |
| LGPL for everything | One license | Over-restrictive for a library meant to be linked by closed pipelines; mismatched with vmafx | Rejected |

## Consequences

- **Positive**: `interop.c`/`interop.h` drop into vmafx with no relicensing;
  explicit patent grant for downstream encoder integrations.
- **Negative**: two headers in one repo (BSD+Patent vs LGPL) — contributors must
  pick the right one per file (documented in CONTRIBUTING.md + AGENTS.md).
- **Neutral / follow-ups**: a license-header check in CI.

## References

- vmafx LICENSE (BSD-2-Clause-Patent) + `ffmpeg-patches/0002` LGPL header.
- [LICENSE](../../LICENSE).
- Source: `req` — "design the filter to be usable by vmafx and vice versa".
