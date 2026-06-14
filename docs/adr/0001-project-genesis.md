<!-- markdownlint-disable MD013 -->
# ADR-0001: Pelorus genesis — a Vulkan GPU pre-encode pipeline under the vmafx org

- **Status**: Accepted
- **Date**: 2026-06-14
- **Deciders**: Lusoris
- **Tags**: scope, org, vulkan, ffmpeg, agents

## Context

Hardware video encoders (NVENC/AMF/QSV) are fixed-function ASICs: fast and
power-efficient, but they take algorithmic shortcuts (rigid block partitioning,
tiny motion-search windows, rudimentary adaptive quantization, shallow B-frame
pyramids, no true 2-pass). The result is a measurable BD-rate gap to slow CPU
encoders (SVT-AV1, x265), most visibly as dark-scene banding and a "noise tax."
The lever that does not require new silicon is to turn the GPU's general-purpose
compute cores into an **intelligent pre-processor** that fixes those flaws in
VRAM *before* the encoder sees the pixels — debanding, temporal denoise,
film-grain synthesis, optical-flow motion hints — all zero-copy. These gains are
**codec-agnostic**: the deband/denoise/motion stages help HEVC (`hevc_nvenc`/
`hevc_qsv`/`hevc_vaapi`/`hevc_amf`, rivaling x265) and AV1 (`av1_nvenc`, …)
equally; only film-grain synthesis is codec-specific (AV1 = AOM, HEVC/VVC =
H.274 / SEI FGC), and Pelorus targets both. FFmpeg 8 ships the Vulkan compute
infrastructure (`vulkan_filter.h`, inline-GLSL filters) to build this in-tree.

The user already maintains **vmafx** (the VMAF fork), which is the natural
quality oracle for measuring such a pipeline — and which **dropped its own
Vulkan backend** (its ADR-0726). Pelorus therefore becomes the Vulkan home that
vmafx is not, a sibling repo under the same `vmafx` GitHub org.

## Decision

We will build **Pelorus**, a GPU pre-encode pipeline of Vulkan compute + FFmpeg
filters, hosted as `vmafx/pelorus`. It adapts the golusoris framework's repo
hygiene and adopts vmafx's stricter conventions (Meson/Ninja, Power-of-10 + SEI
CERT C, ADR culture, per-surface docs, changelog fragments, an
`ffmpeg-patches/` stack against n8.1.1). It is bidirectionally wired to vmafx
via a shared side-data ABI (data plane, [ADR-0103](0103-interop-sidedata-abi.md))
and vmafx's autotune loop (control plane, [ADR-0106](0106-autotune-control-plane.md)).
The first deliverable is a full scaffold plus one working flagship filter
(smart deband, [ADR-0102](0102-flagship-smart-deband.md)) with the remaining
techniques designed as stubs.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
|---|---|---|---|
| Add the filters inside the vmafx repo | One repo, shared CI | vmafx is a *measurement* tool; mixing a pre-encode pipeline muddies its scope, and vmafx deliberately removed Vulkan (ADR-0726) | Keep concerns separate; interop via a shared ABI instead |
| Standalone repo, no vmafx relationship | Maximum independence | Loses the quality-oracle loop that makes the gains measurable and tunable | Bidirectional interop is a core requirement |
| Out-of-tree libplacebo-style shader lib + thin generic FFmpeg wrapper | Decoupled from FFmpeg internals | Least zero-copy-native; diverges from vmafx's proven patch-stack model | Chose libpelorus + patch stack ([ADR-0104](0104-ffmpeg-patch-stack.md)) |

## Consequences

- **Positive**: a clean separation (Pelorus = transform, vmafx = measure) with
  a real, linkable contract between them; reuse of vmafx's autotune/MCP/server;
  zero-copy native via libavfilter Vulkan.
- **Negative**: two repos to keep in sync; a shared ABI that must stay
  append-only across independent release cadences.
- **Neutral / follow-ups**: adopt vmafx's doc-substance ([ADR-0100](0100-doc-substance-rule.md))
  and deep-dive-deliverables ([ADR-0108](0108-deep-dive-deliverables-rule.md))
  rules; stand up CI mirroring the local gate.

## References

- `.workingdir/*.md` — the design discussion the project derives from.
- vmafx ADR-0726 (Vulkan backend removed) — why Pelorus is the Vulkan home.
- Source: `req` — "create a vulkan filter and ffmpeg filters to power up gpu
  encoding … design the filter to be usable by vmafx and vice versa,
  bidirectional, and for encodes"; `req` — "i want that project to be hosted
  under my vmafx orga"; `Q1.1`/`Q1.2`/`Q1.3`/`Q1.4` (scope, flagship, delivery,
  interop popup answers).
