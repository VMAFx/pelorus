<!-- markdownlint-disable MD013 -->
# Architecture Decision Records

Nygard-format ADRs for Pelorus. One file per decision,
`NNNN-kebab-case.md`, following [0000-template.md](0000-template.md). Reserve a
number atomically with `scripts/adr/next-free.sh --claim <slug>` before creating
the file. Land the ADR **before** the implementing commit; cite the user request
(`req`) or popup answer (`Q<r>.<q>`) in `## References`. Decisions are immutable
once Accepted — supersede with a new ADR rather than editing the body.

Numbering: project decisions start at `0001`; rules adopted from the vmafx
sibling keep vmafx's number (e.g. 0100, 0108) for an easy cross-walk.

| ADR | Title | Status |
|---|---|---|
| [0001](0001-project-genesis.md) | Pelorus genesis — Vulkan GPU pre-encode pipeline under the vmafx org | Accepted |
| [0100](0100-doc-substance-rule.md) | Every user-discoverable surface ships docs in the same PR | Accepted |
| [0102](0102-flagship-smart-deband.md) | Smart deband (f3kdb) is the flagship filter | Accepted |
| [0103](0103-interop-sidedata-abi.md) | Pelorus⇄vmafx interop via a UUID-keyed AVFrame side-data ABI | Accepted |
| [0104](0104-ffmpeg-patch-stack.md) | Delivery — libpelorus core + an FFmpeg patch stack | Accepted |
| [0105](0105-libpelorus-license.md) | libpelorus is BSD-2-Clause-Patent; FFmpeg files are LGPL-2.1 | Accepted |
| [0106](0106-autotune-control-plane.md) | Control plane — tune filter strength against VMAF via vmafx | Accepted |
| [0108](0108-deep-dive-deliverables-rule.md) | Fork-local PRs ship the deep-dive deliverables | Accepted |
| [0109](0109-analyze-filter.md) | vf_pelorus_analyze emits measured banding/variance via GPU readback | Accepted |
| [0110](0110-avoption-control-plane-contract.md) | Freeze the deband AVOption names + ranges as the vmafx-facing control-plane contract | Accepted |
| [0111](0111-benchmark-methodology.md) | Pre-encode-filter benchmarks score against the clean ground truth | Accepted |
| [0112](0112-temporal-denoise.md) | vf_pelorus_denoise — causal edge-preserving spatio-temporal denoise | Accepted |
| [0113](0113-optical-flow-mc.md) | vf_pelorus_mc — GPU motion estimation for MC-temporal denoise (+ gated NVENC ME hints) | Proposed |
| [0114](0114-encoder-steering.md) | Encoder-steering strategy — feed Pelorus GPU maps to every encoder (ROI today, QP-map patches, Vulkan quant-map) | Proposed |
| [0115](0115-grain-estimate.md) | vf_pelorus_grain_estimate — film-grain-synthesis parameter estimator (per-band HF-residual → AV1 AOM / H.274) | Accepted |
| [0116](0116-pelorus-mc.md) | vf_pelorus_mc_vulkan v1 — standalone GPU motion-vector-hint producer (encode-speed, not quality) | Accepted |
| [0117](0117-grain-fgs-bsf.md) | pelorus_fgs — H.274/HEVC Film Grain Characteristics SEI bitstream filter (FGS round-trip, HEVC leg) | Accepted |
| [0118](0118-nvenc-av1-filmgrain.md) | av1_nvenc film grain — carry the FGS estimate into NVENC hardware AV1 film-grain synthesis | Accepted |
| [0119](0119-qp-feedback.md) | Closed-loop QP feedback — read the encoder's honored QP/bits back into the interop ABI (PEL_SEC_QPREPORT, ABI 1.1) | Accepted |
| [0120](0120-libaom-steering.md) | libaom ROI steering — honor AV_FRAME_DATA_REGIONS_OF_INTEREST via AOME_SET_ROI_MAP | Accepted |
| [0121](0121-svtav1-steering.md) | SVT-AV1 ROI steering — honor AV_FRAME_DATA_REGIONS_OF_INTEREST via the SVT-AV1 segment map | Accepted |
| [0122](0122-qp-feedback-csv-reader.md) | Runnable QP-feedback reader — fold x265 `--csv` per-frame stats into PEL_SEC_QPREPORT (SDK-free closed loop) | Accepted |
