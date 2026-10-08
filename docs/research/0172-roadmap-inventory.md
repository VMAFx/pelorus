<!-- markdownlint-disable MD013 MD024 MD033 MD060 -->

# Research 0172: roadmap item inventory

Item inventory behind [ADR-0172](../adr/0172-roadmap-milestone-map.md): every open piece of work found on 2026-10-08, grouped by area, with a stable id (`REL-01`, `QE-12`, ...). Roadmap issues cite these ids as `INV <id>`. Sources named `maintainer notes` are the maintainer's private backlog, which is not part of the repository; the claims taken from it are marked `(U)` for outside readers.

**Base.** `origin/master` `420c19a`; release v0.2.2, ABI 1.3, FFmpeg n9.0.2, 18 patches. Read through `git show origin/master:<path>` and `gh` read calls.

**Keys.** Size: S up to 1 day, M 2 to 5 days, L 1 to 3 weeks, XL over 3 weeks or several pull requests; sizes are estimates, not sourced. "derived" in the source column marks a gap found by this inventory without earlier ticket text. vmafx phase: RC4 is before vmafx 1.0.0-rc.4 (VMAFx/vmafx#2411), 1.1 and 1.2 are vmafx milestones, `-` is no vmafx link. The milestone and issue numbers in the issue column are those of the forge at that time; [the roadmap](../roadmap.md) is current.

## REL: Release engineering and 1.0 definition

| id | item | source | size | dependency | vmafx-phase | issue |
| --- | --- | --- | --- | --- | --- | --- |
| REL-01 | Cut v0.3.0: 35 changelog.d fragments (bug wave BUG-001..033, FFmpeg 9.0.2, Praetor 492a00f9, SLSA L3, Windows UTF-8). ABI 1.3 unchanged. Behaviour change: `pelorus_fgs` defaults to RDD 5 model 0 (ADR-0155) | maintainer notes; changelog.d/*; ADR-0155 | S | REL-03, REL-02 rehearsal | - (vmafx mirror already at 11e183e) | none |
| REL-02 | Prove release publish hand-off + attestation signer at first real tag; rehearsal run 37805309705 proved build/attest/cosign/SBOM only | ADR-0169:100-108 ("Negative"); maintainer notes | S | REL-01 | - | none |
| REL-03 | Merge Renovate #97 (download-artifact v8.0.2) and #98 (upload-artifact v7.0.2) when >= 3 days old (2026-10-10), re-run release rehearsal | maintainer notes; ADR-0169 (five pinned actions need updates) | S | date | - | #97, #98 |
| REL-04 | Define 1.0 contract: README says "Public API and the interop ABI may evolve before v1.0.0". Decide what freezes (libpelorus public headers pelorus.h, deband.h, denoise.h, interop.h; AVOption names; lavfi.pelorus.* metadata keys; patch stack numbering) and the soversion plan (meson soversion '0') | README.md:193-194; libpelorus/meson.build (soversion '0'); interop.h R1-R6 | M | decisions D1, D2 | - | none |
| REL-05 | Re-title/re-plan forge milestones: `0.1 — First release` still open though v0.1.0..v0.2.2 shipped; epics #92/#93 target 0.1/0.2 while maintainer notes plans v0.3.0 then 1.0. Add v0.3.0 and 1.0 milestones; move #94 | forge milestones 1,2; #92 body; maintainer notes | S | decision D3 | - | #92, #93, #94 |
| REL-06 | #92 epic closure: sole child #65 is closed (auto-closed by #76); "Done when" items (hermetic replay, fixture descriptors checked, gates green on all supported platforms) need verdict, then close or fold into new epic | #92 body; maintainer notes 15:50Z | S | REL-05 | - | #92 |
| REL-07 | Promote or demote each filter status label before 1.0: dehalo "Built (tuning pending)", deblock "Built (tuning pending)", aa "Built (defaults unproven)", scenecut "Built (BD-rate A/B pending)", grain_estimate "Estimator built", fgs "Working (static model)". 1.0 should not ship unproven labels without a documented stance | README.md Modules table lines 84-92 | M | QE-03..QE-09, decision D4 | - | none |
| REL-08 | Release cadence and LTS policy: ruleset `praetor-main-protection` covers `master` + `lts-*`; no lts branch policy documented. Also semver rule for patch stack vs library version | maintainer notes; cut-release skill | S | decision D2 | - | none |
| REL-09 | README "Landed so far" row + maintainer notes) | CLAUDE.md "Delivery"; render-changelog skill | S | REL-01 | - | none |

## QE: Quality evidence and BD-rate (claims currently unproven)

| id | item | source | size | dependency | vmafx-phase | issue |
| --- | --- | --- | --- | --- | --- | --- |
| QE-01 | Scenecut BD-rate A/B: cut-aligned GOPs vs periodic IDR on multi-shot content, RD ladder, ADR-0111 method. Also tune mc SAD threshold; optional min-GOP guard | ADR-0126:75-86; README.md:90,140; docs/metrics/scenecut.md:102 | M | QE-12 corpus | - | none |
| QE-02 | Deadzone-survival detail preconditioning: iso-bitrate net-win gate (VMAF-NEG + SSIMULACRA2) before any build | maintainer notes | M (gate), L (build) | decision D5 (contradicts ADR-0142:19-21, additive = wash) | - | none |
| QE-03 | Anime composed-chain proof (analyze roi + dehalo + aa + deband) vs clean anime ground truth; SSIMULACRA2 + CAMBI + edge-region VMAF-NEG | ADR-0125:118-132; docs/usage/anime.md:115 | L | QE-12 corpus (clean anime), FLT-01 | - | none |
| QE-04 | Dehalo quality proof + content tuning of `edge` | ADR-0123:91-95; ADR-0163:137-139; docs/metrics/dehalo.md:121-124 | M | FLT-01, QE-12 | - | none |
| QE-05 | aa perceptual tuning + SSIMULACRA2/edge VMAF-NEG proof | ADR-0124:100-104; docs/metrics/aa.md:98 | M | QE-12 | - | none |
| QE-06 | Deblock tuning per prior codec (`bsize` presets) + re-transcode BD-rate proof | ADR-0127:86-91; docs/metrics/deblock.md:94-97 | M | QE-12 (re-transcode corpus) | - | none |
| QE-07 | Borderfix dirty-border BD-rate note (optional, not a correctness gate) | ADR-0128:87-91; docs/metrics/borderfix.md:113-119 | S | QE-12 | - | none |
| QE-08 | Grain: on-HW grain-match + BD-rate proof for `pelorus_fgs` and `nvenc_pelorus_film_grain`; calibration rerun after FFmpeg bump (`gen-h274-grain-calibration.py --check`) | ADR-0117:112; ADR-0118:126-131; ADR-0155:98-99; ADR-0161:119-121; docs/usage/grain-fgs-bsf.md:198 | M | INT-05 | 1.2 (scoring kit vmafx#2144) | #82 |
| QE-09 | MC-warp denoise BD-VMAF demo on BBB high-motion segment | ADR-0131:66-71 | S | QE-12 | - | none |
| QE-10 | QSV ROI BD-rate on Intel HW (no numbers claimed anywhere) and per-vendor iso-bitrate ROI BD-rate (v0.6 table is CQP mechanism demo only) | docs/development/bench-results.md:273-295; ADR-0114:66,107,123 | M | HW-04 | - | none |
| QE-11 | NVENC ME-hint speed/BD-rate retest. Quarter-pel + confidence field now exist (docs/metrics/mc.md:44), so maintainer notes "defer until half-pel" is stale; BUG-031 fixed. Speedup never measured | ADR-0116:100-102,160-166; maintainer notes | M | HW (4090) | - | none |
| QE-12 | Corpus: pinned BBB URL dead since 2026-08-30 (fetch-corpus cannot cold-start; re-pin resets baseline history). Needs clean-reference sets: anime, real banding (10-bit), re-transcode, multi-shot, high-motion. Private PVE media cannot enter corpus.lock | docs/development/benchmarking.md:75-83; scripts/bench/corpus.lock; maintainer notes ("CORPUS UNBLOCKED" block) | L | decision D6 | 1.0.0/1.1 benchmark stage (vmafx#2067 note: "no perf claims before VMAFx benchmark stage") | none |
| QE-13 | `tune=auto` per-leg iso-bitrate validation (grainy -> denoise, textured, re-encode -> deblock/demosquito, fades) | ADR-0142:94; maintainer notes | L | FLT-06 | - | none |
| QE-14 | CAMBI-align the banding detector (single-scale variance vs multi-scale CAMBI); fixes v0.10 SVT honest negative | maintainer notes; ADR-0133:59 | M | QE-12 (real banding) | - | none |
| QE-15 | Benchmark methodology publication: consolidate measured negatives (ADR-0132/0135/0141 rejected) and positives into one evidence page for the 1.0 claim set | docs/development/bench-results.md; ADR-0111 | M | QE-01..QE-11 | - | none |

## ENC: Encoder steering coverage (features x encoders)

Current matrix (from README.md:146-165, docs/usage/ffmpeg.md:81-140, docs/backends/*, patch series): ROI/qpmap on NVENC (0004, proven -41% banding), QSV (0005, HW-unproven), Vulkan h264/hevc/av1 (0009, 4090 positive deltas only, RADV disabled), libaom (0012, inert upstream), SVT-AV1 (0013, +CAMBI -1.5%). ME hints NVENC only (0008). Film grain NVENC AV1 (0011), HEVC static SEI BSF (0010). x264/x265/VAAPI/D3D12 use stock FFmpeg ROI. No AMF patch. No consolidated encoder x feature x GPU matrix exists in docs (HW-06).

| id | item | source | size | dependency | vmafx-phase | issue |
| --- | --- | --- | --- | --- | --- | --- |
| ENC-01 | NVIDIA Vulkan quantization maps accept only non-negative deltas, so negative-offset ROI has no effect. Decide: shift map / document / vendor-gated shift | maintainer decision Q-001; ADR-0166:96; README.md:154-157 | M | decision D7 | - | none |
| ENC-02 | RADV: offers only R32_SINT map without transfer/storage usage; needs host-written LINEAR fill path with layout handling | ADR-0166:81,96; maintainer notes | L | none | - | none |
| ENC-03 | Vulkan delta maps under CBR/VBR (spec allows; probe gates to CQP) | maintainer notes; ADR-0166 | M | ENC-01 | - | none |
| ENC-04 | AMD AMF: no steering patch (ROI, qp feedback `PEL_QPSRC`, grain). ADR-0114 step 6 names AMF block-QP feedback. Decide scope for 1.0 | patch series (no amf); ADR-0114:175; #86 body (BlockQpFeedback) | L | HW (AMD VCE/VCN dGPU), decision D8 | 1.2 (#86 adapters) | #86 |
| ENC-05 | libaom ROI inert: `AOME_SET_ROI_MAP` returns INVALID_PARAM on stock 3.14.1; becomes live with no code change when upstream wires it. Track + retest per libaom release | docs/backends/libaom-roi.md:63-72; ADR-0120:59-82 | S | upstream | - | none |
| ENC-06 | SVT-AV1 film grain leg (`--fgs-table` library interface) | ADR-0121:155-172 | M | INT-05 | 1.2 | #82 |
| ENC-07 | Hardware AV1 grain legs: QSV, AMF, VAAPI; explicit chroma grain params | ADR-0118:130-132 | L | HW; ENC-12 | 1.2 | #82 |
| ENC-08 | `pelorus_fgs` H.264 and VVC legs; per-frame time-varying HEVC model via side-data channel; per-band -> multi-interval FGC mapping; full H.274 component tables | ADR-0117:96,112-116; ADR-0161:125; series.txt roadmap comment; docs/usage/grain-fgs-bsf.md:48,73,200 | L | INT-05 | 1.2 | #82 |
| ENC-09 | `-pelorus_roi` for `av1_qsv` (not registered) and `av1_amf`/AMF in general | docs/usage/ffmpeg.md ("does not add -pelorus_roi to av1_qsv") | M | HW (Arc B-series) | - | none |
| ENC-10 | QSV per-block stat extraction (`mfxEncodeBlkStats`/`mfxExtEncodedUnitsInfo`) -> `PEL_SEC_QPREPORT`; hardware-blocked | ADR-0122:86; docs/metrics/qp-feedback.md:142-150; ADR-0114:175 | L | HW-04 | 1.2 (#86) | #86 |
| ENC-11 | NVENC / Vulkan QP-feedback readers (`PEL_QPSRC_NVENC`, `PEL_QPSRC_VULKAN` reserved) | interop.h:132-133 | L | ENC-10 pattern; INT-01 | 1.2 | #86 |
| ENC-12 | Estimator: explicit chroma grain; full per-lag AR (Yule-Walker) fit; `ar_coeffs_y[0]` diagonal-tap semantics; multi-interval H.274 mapping from the 8 bands; frame-size guard above DCI 8K | ADR-0115:62,77,109; ADR-0161:119-127 | L | none | - | none |
| ENC-13 | Scenecut: min-GOP guard, mc SAD threshold tuning | ADR-0126:81-84; docs/metrics/scenecut.md:97 | S | QE-01 | - | none |
| ENC-14 | Encoder-side adaptive quantisation interplay: NVENC AQ overrides map (AQ-off required), complexity-driven aqStrength lever needs small fork patch (marked strategic) | docs/development/bench-results.md ROI caveat; maintainer notes (note ADR-0132 rejected per-shot CRF, only layer-1 complexity scalar shipped) | L | decision D5 | - | none |

## INT: Interop, telemetry and vmafx coupling

| id | item | source | size | dependency | vmafx-phase | issue |
| --- | --- | --- | --- | --- | --- | --- |
| INT-01 | Normalised encoder telemetry schema spec (per-frame/per-block, ABI 1.4 append-only, capability record "not reported" vs 0). Acceptance includes `_Static_assert` layout lock + conformance fixture + vmafx mirror re-sync | #86 body; maintainer notes; Q-007; vmafx-delta Q2(a) | L | REL-01 (v0.3.0 first) | RC4 spec (vmafx#2411 mirror carries it); vmafx#2271/#2510/#2142/#2143 | #86 |
| INT-02 | #86 adapters: FFmpeg `QUALITY_STATS`, x265 CSV (exists), SVT-AV1 `--stat-file`, libaom, VVenC log, hardware feedback structs; fixture corpus per encoder | #86 body | XL | INT-01 | 1.2 | #86 |
| INT-03 | Encode provenance record: canonical JSON + SHA-256, `pel_encode_record_{build,canonicalize,hash,verify}`, option-string parsers, side-data section carrying hash + locator. vmafx already reserved `encode_record` (sha256, 64 hex; ADR-2073, ABI 0.1.5) | #81 body; vmafx-delta Q1, Q3 | L | REL-01 | RC4 schema; adapters 1.1 (vmafx#2142, #2159) | #81 |
| INT-04 | ROI / delta-QP intent: `PelorusRoiIntent` section, mapping functions with `pel_roi_map_loss`, `tools/pelorus_roi_conformance` harness (honoured fraction, AQ flattening) | #80 body | L | INT-01, INT-08 | 1.2 (vmafx#2067, #2147, #2142) | #80 |
| INT-05 | Film-grain interchange: AV1 <-> H.274 conversion with fidelity estimate, libaom grain-table I/O, inline per-frame bridge from `PEL_SEC_FILMGRAIN` to SEI BSF, scoring recipe (score decoded output, not recon) | #82 body; ADR-0161:94-95 (append H.274 values to FILMGRAIN deferred) | L | ENC-08 | 1.2 (vmafx#2144, #2067 item 6) | #82 |
| INT-06 | Quality-window stream `PEL_SEC_QUALITY` + zone/segment emitter + time-alignment helper + reference loop with flat-CRF baseline (must not become QP steering, ADR-0132/0142) | #83 body | L | INT-01; vmafx window scores #2138 (RC4 WP4) | 1.2, no perf claims before vmafx benchmark stage (vmafx#2067, #2147, #2238) | #83 |
| INT-07 | Recon-frame handoff: `pel_recon_caps` per encoder, plain-struct descriptor, recon-vs-decode byte conformance | #84 body | L | INT-01; vmafx#1723 import API | descriptor with RC4 import API; adapters 1.2 | #84 |
| INT-08 | Bit-exact filter mode (exploratory): classify filters exact / deterministic / tolerance-bound, CPU reference for exact class, strict-FP flag, parity gate. Known: Vulkan division/sqrt precision gives +-1 code cross-vendor (ADR-0163:133-136); BUG-027 denoise tile mismatch on 4090 fixed | #85 body; ADR-0163:133-136 | XL | HW matrix (HW-01) | investigate 1.1; exact class 1.2 (vmafx#2148, #2142) | #85 |
| INT-09 | LCEVC enhancement-layer reader -> per-frame records in #86 schema | #87 body | L | INT-01 | blocks vmafx#2251 (also #2147, #2142) | #87 |
| INT-10 | BUG-035: `PelorusMotionSection` has no block-size field; consumers assume default on small frames. Append `block_size_log2` (QPREPORT already has it, interop.h:336) in the ABI 1.4 bump | pre-release bug list BUG-035; interop.h | S | rides INT-01 | RC4 mirror | none |
| INT-11 | `honored_fraction`: `pel_qp_report_from_blocks` still sets 0 (interop.c:359); x265 CSV reader computes frame-level sign agreement (qp_report_csv.c:496-579). Wire per-block path, define null-map = undefined (not 1.0), gate tuner wins on it | maintainer notes; interop.c:359; qp_report_csv.c:578; #80 AC1 | M | ENC-10, INT-04 | 1.2 (vmafx#2142 honouring record) | #80 |
| INT-12 | vmafx consumer gaps: vmafx reads BANDING, VARIANCE, COMPLEXITY only (perceptual_weight.c; ADR-1118, ADR-1120). No consumer for QPREPORT, MOTION_CONF, DENOISE, FILMGRAIN, MOTION. Needs explicit user go (cross-repo) | maintainer notes; vmafx-delta Q2 | M (vmafx side) | decision D9 | 1.2 | none |
| INT-13 | Field-name parity with vmafx#2271 bitstream-facts track, enforced by a test that loads both schemas | vmafx-delta Q2(d); vmafx#2510 | M | INT-01 | 1.1 (#2271) | #86 |
| INT-14 | Pelorus-enabled ffmpeg for vmafx live prefilter test (vmafx#2586 T-PREFILTER-LIVE-ENCODE-UNTESTED): provide reproducible build recipe or artifact | vmafx-delta Q2 | M | PLT-01 | 1.1 | none |
| INT-15 | vmafx#1455 libgpudispatch extraction: move `vf_pelorus_*_vulkan.c` GPU dispatch onto shared lib after the lib lands (vmafx-led) | vmafx-delta Q2 | L | vmafx lib first | vmafx "Code health" milestone | none |
| INT-16 | Mirror contract process: any change to the ten mirrored files must be on master before vmafx re-pins (vmafx ADR-1276); keep SHA reachable; document in libpelorus/AGENTS.md | vmafx-delta Q6 | S | none | ongoing | none |
| INT-17 | Reserved/deferred ABI items: per-cell map payloads (analyze, ADR-0103:50, ADR-0109:47), `PEL_DENOISE_FLAG_AUTO_SIGMA` + `MOTION_COMP` flags (ADR-0112:69), `bits_cell`/`qp_cell` maps (qp-feedback.md:25,35) | cited | M | INT-01 | - | none |
| INT-18 | Autotune loop on vmafx side: per-shot search seeded by `noise_sigma`/`global_banding_risk`, multi-metric objective (VMAF-NEG + SSIMULACRA2 + CAMBI) vs gaming | maintainer notes; ADR-0106 | L | INT-11 | 1.2 | none |

## HW: Hardware validation

| id | item | source | size | dependency | vmafx-phase | issue |
| --- | --- | --- | --- | --- | --- | --- |
| HW-01 | PR #68 Intel validation: `git merge origin/master` (32 files, state UNKNOWN), re-run `vulkan-format-matrix.sh PELORUS_VALIDATE=1` on 4090 / A380 / RADV to confirm `precise` fixes neutral (denoise/dehalo tile=1, aa fast=1 at 10/12-bit); reviewer routing (vulkan-shader, ffmpeg-patch, doc, pr-body); reserved ADR-0150 | maintainer notes; PR #68 body; #69 item 2-3 | M | none | - | #68 |
| HW-02 | B580 and UHD 770 speed rows (second Intel machine only) | PR #68 "Not done"; maintainer notes | M | second Intel machine stable | - | #68 |
| HW-03 | UHD 770 `aa fast=1` residual (research note on #68 branch) | maintainer notes; PR #68 commit 16fd7d9 | S | HW-02 | - | #68 |
| HW-04 | QSV dense ROI (patch 0005) on B580 under Linux iHD: Windows VPL 2.17/2.15 clears `EnableMBQP`; A380 has low-power-encode bug (fixed only on Battlemage) | maintainer notes; #69 item 4; bench-results.md:358-365 | M | Linux box with B580 | - | none |
| HW-06 | One consolidated encoder x feature x GPU validation matrix doc (ROI, delta-QP, ME hints, grain, scenecut, qpmap x NVENC/QSV/Vulkan/libaom/SVT/AMF/VAAPI x 4090/A380/B580/UHD770/RADV/ANV) with evidence pointers; today scattered over README, bench-results.md, docs/backends/* | derived (docs/backends has 3 files; no matrix found) | M | HW-01..04 | 1.2 (#80 AC4 "encoder matrix") | #80 |
| HW-07 | Automate hardware matrix: `vulkan-format-matrix.sh` and runtime proofs run manually on workstation; CI is GPU-less. Self-hosted runner or recorded-evidence gate | derived; maintainer notes (manual runs) | L | decision D10 | - | none |
| HW-08 | Vulkan encode on ANV and AMD dGPU; Intel Windows drivers expose no `VK_KHR_video_encode_queue`; Intel drivers 101.9033/101.7092 need `disable_multiplane=1` (and `linear_images=1` on UHD 770) | #69 "Open work 3"; PR #68 commit e689a8d | M | HW-01 | - | #68 |
| HW-09 | AMD dGPU / AMF hardware: only iGPU RADV in inventory (Ryzen 9950X3D); AMF steering and RADV qpmap proof need AMD dGPU or VCN video encode | maintainer notes; ENC-02, ENC-04 | M | hardware | - | none |

## FLT: Filters and algorithms

| id | item | source | size | dependency | vmafx-phase | issue |
| --- | --- | --- | --- | --- | --- | --- |
| FLT-01 | BUG-034: dehalo removes <= 1.3% halo energy (steep flanks protected as line-art); feathered FineDehalo-style mask reaching the flank pixel (gate at nine neighbours) | pre-release bug list BUG-034; ADR-0163:60,97,137 | M | none | - | none |
| FLT-02 | Edge/flat segmentation primitive ("luma mask") feeding aggressive deband + protecting line-art; foundation of `tune=anime` | maintainer notes step 1 | M | decision D5 | - | none |
| FLT-03 | `tune=auto` router: detection enablers (`noise_sigma`, `dark_frac`, `edge_temporal_excess`), per-shot classifier with hysteresis keyed on `has_scene_cut`, filter/preset. ADR-0142 Status still Proposed | ADR-0142:94-96; maintainer notes | XL | QE-13, at least one validated content-general filter | - | none |
| FLT-04 | Demosquito (motion-gated temporal mosquito corrector for the edge band denoise refuses): premise check first | maintainer notes (#15) | M (check) + L | QE-12 | - | none |
| FLT-05 | Fadecomp (fade/dissolve luma pre-normalisation; weightp auto-off with B-frames); reversibility risk, premise check 1 h no GPU | maintainer notes (#4) | M | none | - | none |
| FLT-06 | Denoise: forward motion-compensated taps and explicit cadence classifier (both deferred); pyramidal LK flow for MC (deferred) | ADR-0137:108-113; ADR-0113:82 | L | none | - | none |
| FLT-07 | Borderfix `mode` knob (mirror/fixed-colour), auto dirty-band width detection | ADR-0128:89-92 | S | use case | - | none |
| FLT-08 | Chroma processing for dehalo/deblock/aa (hook exists via `planes`, luma default) and chroma reconstruction / HDR tone prep (parked, content-niche) | ADR-0123:75; ADR-0127:70; maintainer notes | M | none | - | none |
| FLT-09 | GPU perf leads: aa Sobel ALU (drop sqrt via gx^2+gy^2 vs thr^2, verify bit-exactness; or hoist 4x redundant recompute); mc REF-window cache (measurement-blocked). Refuted/closed: subgroup reductions, fp16, aa tiling, mc cur-block cache | maintainer notes blocks | M | HW-01 timing stability | - | none |
| FLT-10 | HISS-04 split of analyze `coalesce_roi`/`attach_roi`/`attach_stats` salvage (unlanded diff in archive/worktrees-20261003/fix-vulkan-hiss04-910e.diff) | maintainer notes | S | ENG-02 | - | none |
| FLT-11 | Closed negatives (do not re-litigate without new evidence): per-shot CRF (ADR-0132), perceptual-AQ map (ADR-0135; revival needs structure-tensor/CSF discriminator), deband wide-reach (ADR-0141), neural dehalo/deblock out of scope | ADR-0132, 0135, 0141, 0123:74, 0127:68 | - | - | - | none |

## PLT: Platform, packaging, distribution

| id | item | source | size | dependency | vmafx-phase | issue |
| --- | --- | --- | --- | --- | --- | --- |
| PLT-01 | Distribution story for 1.0: releases ship patch tarball + SHA256SUMS + SBOM (release-build.yml); libpelorus has pkg-config + `install_headers` + soversion '0' (libpelorus/meson.build:32-45); no distro packages, no prebuilt Pelorus-enabled FFmpeg, no container for end users (only dev container `ghcr.io/vmafx/pelorus-dev`) | libpelorus/meson.build; ADR-0169; ADR-0153:97 | L | decision D11 | 1.1 (vmafx#2586 needs pelorus ffmpeg) | none |
| PLT-02 | Windows: native MSYS2 UCRT64 fast-suite CI exists (ci.yml:168); FFmpeg-with-patches on Windows only hand-built (#68 dfb22bf, `build-and-run.sh` MSYS2 port); `check-build-config.py --self-test` git fixtures skipped on Windows (MSYS2 git expands `{commit}`); fetch-corpus harness Linux-only | ADR-0149:122-149; meson.build:65-72,139-156; maintainer notes; PR #68 | M | HW-01 | - | #68 |
| PLT-03 | Supported-platform statement for 1.0 (Linux x86_64 Vulkan 1.3+ drivers; Windows; macOS/MoltenVK not addressed anywhere); #92 "green on all supported platforms" lacks a definition | #92 body; derived | S | decision D12 | - | #92 |
| PLT-04 | Docs portal: facet `docs:seo-portal` declared; docs gate = `make docs-lint docs-figures` (Node 22+); no published site found in repo | .standards.yaml:15; CLAUDE.md gates | M | decision D13 | - | none |
| PLT-05 | Build reproducibility/hermeticity: replay uses pinned FFmpeg SHA; hermetic `git am`; owner-only x265 fixture | ADR-0148; ADR-0144 | S | none | - | #65 (closed) |

## ENG: Engineering health and governance

| id | item | source | size | dependency | vmafx-phase | issue |
| --- | --- | --- | --- | --- | --- | --- |
| ENG-01 | FFmpeg-tree clang-tidy lane over 13 `ffmpeg-patches` TUs inside the replay build (`bear` compile db; profile accepting FFmpeg idioms; declare in `.standards.yaml clang_tidy.lanes`; delete 13 exceptions). HARD DEADLINE 2027-01-06; no renewal tooling (praetor#798) | #94; ADR-0168:80-83,111; maintainer notes | M | none | - | #94 |
| ENG-02 | HISS baseline 51 burn-down: 28 HISS-01 (27 in ffmpeg-patches/files, 1 in ffmpeg-patches/test), 21 HISS-04 (18 ffmpeg-patches/files, 1 ffmpeg-patches/test, 2 scripts), 2 HISS-07 (scripts). Zero libpelorus entries. Hot functions: init_filter, filter_frame, denoise_dispatch, mc_dispatch, attach_* | .standards-baseline.json (origin/master, 51) | XL | ENG-01 (shares the TUs) | - | none |
| ENG-03 | HISS-18 workflow-trigger audit: 6 WARN findings | docs/research/0153-praetor-full-adoption.md:62; praetor-delta-20261008.md | S | none | - | none |
| ENG-04 | 6 `bugprone-signed-bitwise` advisory tidy warnings (3 in libpelorus/src) + 1 `readability-function-size` in `pelorus_sidedata_test.c`; not in `WarningsAsErrors` so exit 0; HISS-10 says warning hygiene exits clean | ADR-0168:128-133 | S | none | - | none |
| ENG-05 | Re-render dev-container bundle on the published image digest (Q-004) | ADR-0153:100-101; Q-004 | S | image published from master | - | none |
| ENG-06 | Flip ADR statuses: Proposed on master = 0142, 0153, 0155, 0161, 0163, 0166, 0168, 0169, 0170. All but 0142 describe merged work; policy says Accepted once the implementing PR merges | docs/adr/README.md rows 57-71; ADR files | S | none | - | none |
| ENG-07 | #56 leftovers: AGENTS.md HISS-01/04 overclaim; duplicate `ubuntu-26.04` rule in `.github/actionlint.yaml`; `.codex` roles hand-maintained; repo-auditor/gatekeeper persona vs praetor#175 (closed) | maintainer notes; #69 item 5 | S | none | - | none |
| ENG-08 | Doc fix: `libpelorus/AGENTS.md:66` names `_wfopen`, code uses `_wfsopen` (edit AGENTS.md, then `standardsctl compile-context`) | maintainer notes | S | none | - | none |
| ENG-10 | Praetor pin drift: repo pin 492a00f9; installed `praetorctl` moved to 1ab5c99f outside session (hooks call PATH binary); vmafx pins 7458a220; periodic re-pin cadence needed; locked `praetor-docs.yml` lacks `persist-credentials: false` and push branch filter (upstream issue not filed) | maintainer notes; vmafx-delta Q4; maintainer notes | S per re-pin, M if breaking | none | - | none |
| ENG-11 | Classic branch protection still present alongside ruleset `praetor-main-protection` (#24738112); decide to remove | maintainer notes | S | decision D14 | - | none |
| ENG-12 | Adopt VMAFx `.clang-tidy` profile (modernize-*, C23, 59 findings on mirrored files per PR #78) as a Pelorus CI lane, and/or copy vmafx Renovate settings (catch-all weekly group, `minimumReleaseAge`, vulnerability alerts; mind ADR-1251 draftPR+automerge deadlock) | vmafx-delta Q5, Q6, Open Q4/Q6 | M | decision D15 | - | #3 |
| ENG-13 | REUSE/SPDX adoption (vmafx has REUSE.toml; Pelorus gates SKIP; praetor#896 P0 blocks top-level SPDX-Package keys) | vmafx-delta Q4; praetor-delta | M | decision D15 | - | none |
| ENG-14 | HISS script standard (vmafx ADR-1142): no discarded failures, strict mode, curl limits, watchdog; verify remaining `scripts/` and `ffmpeg-patches/*.sh` | vmafx-delta Q6; HISS-07 baseline rows | M | ENG-02 | - | none |
| ENG-15 | Praetor local audit last check (`git-hooks`): praetor#175 closed 2026-10-06; hook install stays explicit (`make hooks-install`); re-verify local `make verify-all` exit 0 after #67 | CLAUDE.md; praetor-delta | S | none | - | none |
| ENG-16 | Dependabot/Renovate dashboard #3 hygiene and the 3-day release-age rule for actions; checker shape guards (ADR-0170) | ADR-0170; #3 | S | none | - | #3 |

## UPS: Upstream FFmpeg and upstream reports

| id | item | source | size | dependency | vmafx-phase | issue |
| --- | --- | --- | --- | --- | --- | --- |
| UPS-01 | FFmpeg rebase cadence: base n9.0.2 (build-config.env, Renovate tracks FFmpeg tags). Each bump: regenerate 18 patches, replay full series, re-check rebase-sensitive contracts (H.274 calibration table, `vkfmt`/FF_VK_STRUCT_EXT, `fftools/ffmpeg_enc.c` force_key_frames). n9.1 release status unverified | build-config.env; docs/rebase-notes.md:38-75,157; ADR-0144 | L per rebase | upstream releases | - | none |
| UPS-02 | File Intel upstream reports (repros on orphan branch `handover/office-2026-09-30`, `shw-scratch/`): drivers 101.9033/101.7092 `VK_EXT_host_image_copy` multi-planar corruption + `FormatProperties3` zeroing; no encode queue | maintainer notes; #69 | S | none | - | #69 |
| UPS-03 | File FFmpeg n9.0.2 reports: `vkfmt_from_pixfmt2` pNext reuse; `vulkan_transfer_host` VUID-09230; QSV fixed-pool `hwupload` 0x80070057; no Vulkan<->D3D11 mapping on Windows | maintainer notes; #69 | S | none | - | #69 |
| UPS-04 | File NVIDIA 615.71 `av1_vulkan` stream defects (VUID 10350/10291; libdav1d rejects) | maintainer notes; ADR-0166:96 | S | none | - | none |
| UPS-05 | File FFmpeg native `av1dec` biased chroma film-grain values (libdav1d unbiased) -> double bias with `-pelorus_film_grain` | maintainer notes | S | none | - | none |
| UPS-06 | Praetor: locked `praetor-docs.yml` defects; paperclip hardcodes `main` (praetor#71 commented); exception renewal tooling (praetor#798) | maintainer notes | S | none | - | none |
| UPS-07 | Upstream-ability of the patch stack: no stated plan to submit any patch to FFmpeg or encoders (libaom ROI, scenecut, NVENC qpDeltaMap). Decide | derived | M | decision D16 | - | none |

## DOC: Documentation for 1.0

| id | item | source | size | dependency | vmafx-phase | issue |
| --- | --- | --- | --- | --- | --- | --- |
| DOC-01 | Schema pages: `docs/metrics/` telemetry field table + per-encoder coverage matrix; provenance schema + worked example; grain model comparison; conformance harness pages | #86, #81, #82, #80 acceptance criteria | M | INT-01..INT-05 | RC4 for #86/#81 | #80-#86 |
| DOC-02 | Per-surface doc pass for all 10 filters + BSF + 6 encoder patches against ADR-0100/0108 (doc-reviewer) before 1.0; reconcile "claimed vs measured" statements | ADR-0100, ADR-0108; docs/metrics/* | M | REL-07 | - | none |
| DOC-03 | Stale text sweep: `ffmpeg-patches/series.txt` roadmap comment (qp-feedback-qsv-reader), `docs/rebase-notes.md` "Unreleased" headings after v0.3.0, README "Pre-1.0 (v0.2.2)" line, ADR-0116/0131 follow-up statements, maintainer notes :257 (integer-pel) | cited | S | REL-01 | - | none |

## Decisions the inventory raised

Answered on 2026-10-08; the outcomes are in [ADR-0172](../adr/0172-roadmap-milestone-map.md).

| id | decision | related items |
| --- | --- | --- |
| D1 | What "1.0" means: library semver 1.0 with frozen headers vs product-level; what is excluded (filters labelled experimental) | REL-04, REL-07 |
| D2 | ABI stance: stay at 1.x append-only through 1.0 (ABI minor 4, 5... for #86/#81/#83/#80) vs consolidate; soversion bump; lts branch policy | REL-04, REL-08, INT-01 |
| D3 | Milestone scheme: rename `0.1`/`0.2` to v0.3.0 / 0.x.. / 1.0, align with vmafx 1.0.0 / 1.1 / 1.2 | REL-05, REL-06 |
| D4 | Rule for filters with unproven quality labels at 1.0 (ship as experimental, hold, or require proof) | REL-07, QE-03..QE-07 |
| D5 | Drop or keep additive-sharpen (QE-02), RC-competing ideas, anime luma mask, `aqStrength` lever given ADR-0142's "additive = wash" law | QE-02, FLT-02, ENC-14 |
| D6 | Corpus: re-pin BBB (resets baseline) or add a second pinned corpus; policy for private media | QE-12 |
| D7 | Q-001: NVIDIA negative-delta policy (shift map / document / vendor-gated) | ENC-01, ENC-03 |
| D8 | AMD AMF in or out of 1.0 scope | ENC-04, ENC-09, HW-09 |
| D9 | Explicit go for vmafx read-side of QPREPORT / MOTION_CONF / DENOISE / FILMGRAIN / MOTION | INT-12 |
| D10 | Hardware CI (self-hosted GPU runner) vs recorded-evidence gate | HW-07 |
| D11 | Distribution: prebuilt FFmpeg, containers, distro packages, or source-only | PLT-01, INT-14 |
| D12 | Supported platforms list (Linux, Windows, macOS?) | PLT-03 |
| D13 | Publish a docs portal (facet already declared) | PLT-04 |
| D14 | Remove classic branch protection now that ruleset is live | ENG-11 |
| D15 | Adopt VMAFx tidy profile, Renovate parity, REUSE | ENG-12, ENG-13 |
| D16 | Upstream-submission plan for the patch stack | UPS-07 |

## Defects found in existing text

1. Eight ADRs that describe merged work were still `Proposed` (0153, 0155, 0161, 0163, 0166, 0168, 0169, 0170); only 0142 is genuinely Proposed.
2. Epic #92 listed #65 as its only child and #65 was closed; #94 sat on the milestone of a release that had already shipped.
3. `README.md` "Pre-1.0 (v0.2.2)" and the `ffmpeg-patches/series.txt` roadmap comment predate the bug wave.
4. `docs/development/benchmarking.md:75`: the corpus URL has been dead since 2026-08-30; the fetch now fails loudly (BUG-022) but a cold start is still impossible.
