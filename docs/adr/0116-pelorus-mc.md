<!-- markdownlint-disable MD013 MD060 -->
# ADR-0116: vf_pelorus_mc_vulkan v1 — a standalone GPU motion-vector-hint producer (encode-speed, not quality)

- **Status**: Accepted
- **Date**: 2026-06-15
- **Deciders**: Lusoris
- **Tags**: motion, vulkan, interop, nvenc, encoder, roadmap

## Context

[ADR-0113](0113-optical-flow-mc.md) decided to build a GPU block-matching motion
estimator and framed its **v1** as *denoise-internal* — the MV field would feed
`vf_pelorus_denoise_vulkan`'s temporal warp. That ADR also recorded two findings
that shape this one: (1) two in-filter motion-compensated denoise attempts were
built and **measured to fail** (winner-take-all block matching gave −28% vs the
no-MC −34%; NLM-temporal over-blurred to VMAF 73–83) because a similarity metric
over raw, noisy pixels is swamped — that path is **reverted, not to be rebuilt**;
and (2) stock FFmpeg cannot inject MVs into any encoder, but the Pelorus patch
stack can reach NVENC's `NV_ENC_EXTERNAL_ME_HINT` API, which
[ADR-0114](0114-encoder-steering.md) Tier 3 confirms is the **only** vendor
external-ME-hint hook (AMF/QSV/Vulkan-Video expose none).

Building the denoise warp consumer first couples a large, unproven quality lever
to the estimator and re-opens the noise-limited failure mode (block-matching on
raw pixels matches grain as readily as motion). The independently shippable,
honestly-framed unit is the **producer**: a GPU motion estimator that emits a
per-block integer-pel MV field as interop side data. Its value is **encode
speed** — a fixed-function encoder ASIC can seed its motion search from our hints
and skip or shorten its own — not quality.

## Decision

We will ship `vf_pelorus_mc_vulkan` v1 as a **standalone MV-hint producer**: a GPU
block-matching estimator (one workgroup per block; cooperative SAD reduced in
shared memory; a predictor-seeded small-diamond descent transcribed from FFmpeg's
`libavfilter/motion_estimation.c`, `ff_me_search_epzs`/`_ds`) that fills the
already-reserved `PEL_SEC_MOTION` section (`interop.h`, bit `1u<<4`,
`PelorusMotionSection`, 32 bytes, ABI 1.0 — **no ABI bump**) with the frame
scalars and appends the dense `int16 (dx,dy)` per-block MV grid after the packed
section (the `vf_pelorus_analyze` map-payload pattern). The frame passes through
unchanged. The predictors are the zero MV, the previous frame's global-motion MV,
and the collocated previous-frame block MV (a persistent MV SSBO ping-ponged
frame to frame) — every seed is resolved **before** the dispatch, so there is no
cross-workgroup intra-frame neighbour race; intra-frame spatial predictors are
deliberately omitted for that reason. Block size and search range are AVOptions
(`bsize`, `search`) with device-agnostic defaults (16, 24) so the filter is a
product for any Vulkan GPU, not tuned to one box.

The NVENC ME-hint **consumer** (a `libavcodec/nvenc.c` patch reading
`PEL_SEC_MOTION` into `meExternalHints`) and the denoise warp consumer are both
**deferred follow-ups**, each gated on a measured win (ADR-0114 Tier 3 / ADR-0113).
The honest v1 claim is "the MV field is produced and plausible" (direction
verified on synthetic pans); the measured speedup belongs to the consumer PR.

### NVENC ME-hint consumer (done-code; speedup deferred)

The NVENC consumer is now implemented as the hand-maintained libavcodec diff
`ffmpeg-patches/files/nvenc-pelorus-me-hints.patch` (patch 0008, cumulative on
0007). A `-pelorus_me_hints` AVOption (default OFF; registered on `hevc_nvenc`
and `h264_nvenc`) parses the `PEL_SEC_MOTION` blob off each frame and translates
the per-block MV field into NVENC's external-ME-hint input:
`enableExternalMEHints` + `maxMEHintCountsPerBlock[0].numCandsPerBlk16x16 = 1` at
init, and per frame `NV_ENC_PIC_PARAMS::meExternalHints` +
`meHintCountsPerBlock`. **Block-geometry mapping**: NVENC's external-hint grid is
one entry per **16×16 block in raster order** for both H.264 (macroblock) and
HEVC, so the consumer sizes a `ceil(W/16) × ceil(H/16)` hint buffer and resamples
the producer's `bsize` grid onto it by sampling the producer cell covering each
16×16 block's center pixel. **L0/L1**: one **L0** candidate per block
(`dir = 0`, `partType = 0` for 16×16, `lastofPart = lastOfMB = 1`); **no L1** —
the producer is a single forward/previous-frame estimator, so `maxMEHintCounts
PerBlock[1]` stays zero. Each vector is clamped to the NVENC bitfield ranges
(mvx S12 `[-2048, 2047]`, mvy S10 `[-512, 511]`); the sign convention matches the
producer (`cur[pos] ≈ ref[pos + mv]`).

**Generality**: default off (zero behaviour change); compile-gated by a new
`NVENC_HAVE_EXTERNAL_ME_HINTS` macro (defined alongside `NVENC_HAVE_QP_MAP_MODE`
under the SDK-8.1 version check, the baseline at which these structs are stable);
the external-ME capability is runtime-probed via `NV_ENC_CAPS_SUPPORT_MEONLY_MODE`
(the closest proxy NVENC exposes) with graceful degrade + a one-shot warning; AV1
is skipped (NVENC's AV1 path uses the materially different
`NVENC_EXTERNAL_ME_SB_HINT` per-superblock struct, S12.2 fixed point — feeding
the integer-pel block grid there would be wrong, so the option no-ops for AV1).

**Blob parse — inline, no libavcodec→libpelorus link**: the consumer replicates
the minimum Pelorus-blob reader (UUID + `"PELOR1\0\0"` magic + `abi_major == 1`,
`section_mask` check, the `PelorusSectionDir` walk, and the `mv_field_offset` /
`mv_field_size` read) inline in `nvenc.c` rather than linking `libpelorus` into
all of `libavcodec` for a default-off feature. This mirrors the ROI/QSV patches
(which stay dependency-free by consuming only standard side data) and is safe
because the interop ABI is frozen append-only (interop.h R1–R6): an older inline
reader tolerates a newer producer (reads the bytes it knows, ignores the tail).
A breaking layout change to `PelorusSideData`/`PelorusSectionDir`/the motion
section offsets — forbidden by the ABI — is the only thing that would break it.

**Honest scope (unchanged)**: the value is **encode speed, not quality**. No
speedup number ships with this code: the MV field is wired into NVENC's
external-ME input and verified to **compile** against the ffnvcodec headers and
replay onto pristine n8.1.1, but a full nvenc build + on-hardware encode-speed
A/B (frames/s with vs without `-pelorus_me_hints`, same `-preset`/`-cq`, per
ADR-0111) was not run in-worktree and is the **documented follow-up**. The honest
claim is exactly: "MV field wired into NVENC's external-ME-hint input; measured
speedup deferred."

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
|---|---|---|---|
| Standalone MV-hint producer (this) | Independently shippable; honest scope (speed); fills the reserved interop plane; unblocks NVENC + denoise consumers behind one contract | No measured end-to-end win until a consumer lands | **Chosen** — smallest correct unit |
| Denoise-internal MC first (ADR-0113 v1) | One fewer side-data round-trip | Couples the estimator to an unproven quality lever; re-opens the noise-limited failure mode (block-match on raw pixels = grain) | Deferred to a consumer PR |
| Intra-frame spatial (left/top) EPZS predictors | Closer to the serial CPU EPZS; better MVs on coherent motion | Reading a neighbour block's result mid-dispatch is a cross-workgroup data race | Rejected — use temporal + global predictors only |
| `VK_NV_optical_flow` HW engine | Dense HW flow, fast | NVIDIA-only; zero in-tree usage; orchestration written blind | Later optional fast-path behind the same `PEL_SEC_MOTION` contract |
| CPU `vf_mestimate` side-data | No new GPU code | Breaks VRAM-resident zero-copy; CPU ME throughput | Rejected (ADR-0113) |

### Consumer-side alternatives

| Option | Pros | Cons | Why not chosen |
|---|---|---|---|
| Inline minimal blob reader in `nvenc.c` (this) | No libavcodec→libpelorus link for a default-off feature; matches the dependency-free ROI/QSV patches; zero footprint when off | Duplicates ~40 lines of ABI-aware parse; must track interop.h if its framing structs ever grow (forbidden by the append-only ABI) | **Chosen** — smallest blast radius |
| Link `libpelorus` into `libavcodec` (configure `require_pkg_config`) | Single source of the parse logic | Makes every NVENC build depend on libpelorus even with the option off; invasive configure surgery | Rejected — too heavy for a niche default-off lever |
| Map every producer block to its own NVENC sub-partition (16×8/8×8) | Finer hint granularity | Producer emits one MV per `bsize` block, not per sub-block; no extra information to give | Rejected — one L0 16×16 candidate carries all we have |
| Populate L1 hints too | Bidirectional seed | Producer is forward/previous-frame only; no L1 vectors exist | Rejected — L0 only |

## Consequences

- **Positive**: a self-contained, honestly-scoped filter that fills the reserved
  motion interop plane for vmafx and unblocks both the NVENC ME-hint consumer and
  a future denoise warp consumer behind one stable side-data contract; no ABI
  bump (the section was reserved at ABI 1.0); zero-copy, codec-agnostic producer.
- **Negative / honest scope**: no measured BD-rate or speed win ships with v1 —
  that is the consumer PR's job; block-MV magnitude on partially-flat content
  under-reads the true global motion (the mean is diluted by aperture-ambiguous
  blocks), so consumers should weight by per-block SAD / use `motion_magnitude_p95`
  rather than the raw mean.
- **Risks / honesty**: block-matching on raw pixels matches grain as readily as
  motion (ADR-0113) — acceptable for an encoder **search seed**, which is why this
  does **not** feed the denoiser; over-claiming an encode-speed or quality number
  before the consumer is measured is the failure mode to avoid (state plainly:
  "MV field produced + direction-verified" only).

## References

- [ADR-0113](0113-optical-flow-mc.md) (the motion-estimation strategy + the
  MC-failure data this v1 deliberately does not rebuild),
  [ADR-0114](0114-encoder-steering.md) Tier 3 (NVENC external ME hints are the
  only vendor hook; the consumer is producer-gated),
  [ADR-0111](0111-benchmark-methodology.md) (how a speed/quality claim must be
  measured before it ships).
- `libavfilter/motion_estimation.c` (`ff_me_search_epzs` / `ff_me_search_ds`,
  SAD cost), `libpelorus/include/pelorus/interop.h` (`PEL_SEC_MOTION`,
  `PelorusMotionSection`), `/usr/include/ffnvcodec/nvEncodeAPI.h`
  (`enableExternalMEHints`, `meExternalHints`, `NV_ENC_EXTERNAL_ME_HINT`,
  `NVENC_EXTERNAL_ME_HINT_COUNTS_PER_BLOCKTYPE`, `NV_ENC_CAPS_SUPPORT_MEONLY_MODE`).
- Consumer patch: `ffmpeg-patches/files/nvenc-pelorus-me-hints.patch` (patch
  0008), `docs/usage/ffmpeg.md` ("Encoder motion-search seeding"),
  `docs/rebase-notes.md` (patch 0008 entry).
- Source: `req` — build the motion estimator as the hardest roadmap item, one
  solid first implementation: the Vulkan compute block-match pass producing a
  per-block integer-pel MV field emitted as Pelorus MV-hint side data, framed
  honestly as encode speed (the NVENC ME-hint consumer) rather than quality, with
  the consumer patch a documented follow-up.
- Source: `req` — implement the NVENC external ME-hint consumer as the gated
  Tier-3 follow-up to the merged mc producer: read the `PEL_SEC_MOTION` MV field
  and feed it to NVENC so the ASIC can skip/shorten its own motion search; frame
  the value as encode speed, not quality, everywhere; the option is default off,
  the capability is runtime-probed with graceful degrade, and the measured
  speedup is deferred (do not fabricate a number).
