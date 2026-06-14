<!-- markdownlint-disable MD013 -->
# ADR-0102: Smart deband (f3kdb) is the flagship filter

- **Status**: Accepted
- **Date**: 2026-06-14
- **Deciders**: Lusoris
- **Tags**: deband, vulkan, scope

## Context

The first build implements one technique end-to-end to prove the whole pipeline
(Vulkan compute → side-data interop → FFmpeg integration → VMAF measurability).
Three candidates were on the table: temporal denoise (the biggest raw BD-rate
lever, ~15–20%), smart deband (fixes the dark-scene blockiness that most visibly
tanks VMAF on hardware encodes), and film-grain (FGS) parameter estimation
(the most novel, but it cross-cuts the encoder and needs a bitstream filter —
an AV1 grain OBU or an HEVC/VVC H.274 film-grain SEI). All three help **HEVC and
AV1** alike; deband in particular is pure pre-processing with no codec coupling.

## Decision

We will implement **smart deband** (modeled on flash3kyuu_deband / f3kdb) first.
It is self-contained frame-in/frame-out with no encoder or bitstream coupling,
directly measurable by vmafx, and exercises every part of the architecture: a
non-trivial Vulkan compute shader, the push-constant contract, the side-data
interop seam, and the FFmpeg patch-stack delivery. Denoise, FGS, and
optical-flow hints follow as later patches.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
|---|---|---|---|
| Temporal denoise first | Biggest BD-rate gain | Needs 3–5 frame VRAM history + temporal state — more moving parts before the architecture is proven | Deferred to a later step ([ADR roadmap](0104-ffmpeg-patch-stack.md)) |
| FGS param estimation first | Most novel "game-changer" | Hardest: denoise + param estimation + a bitstream filter (AV1 OBU / HEVC-VVC H.274 SEI); cross-cuts encoder + muxer | Too much surface for a first proof |
| Smart deband first | Self-contained, VMAF-measurable, exercises the full stack | Subjective gain can confuse a naive VMAF (mitigated, see research) | **Chosen** |

## Consequences

- **Positive**: a minimal, fully-verifiable slice; the deband filter is also the
  first producer of the interop banding section.
- **Negative**: dither raises naive frame-vs-source VMAF; the net win shows on
  the encode round-trip (documented, with amplitude discipline + a detail mask).
- **Neutral / follow-ups**: `vf_pelorus_analyze` to emit *measured* banding maps
  (deband currently emits a config-derived marker); denoise/FGS/MV next.

## References

- [docs/research/0101-smart-deband.md](../research/0101-smart-deband.md) — the algorithm digest.
- `libavfilter/vf_deband.c` — the flat-test the algorithm is built on.
- Source: `Q1.2` — flagship = "Smart deband (F3KDB)".
