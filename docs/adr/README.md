<!-- markdownlint-disable MD013 MD060 -->
# Architecture Decision Records

Nygard-format ADRs for Pelorus. One file per decision,
`NNNN-kebab-case.md`, following [0000-template.md](0000-template.md). Reserve a
number atomically with `scripts/adr/next-free.sh --claim <slug>` before creating
the file; run `git fetch origin` first to see remote branches, as the allocator
scans every local worktree and every local branch tip offline. Land the ADR
**before** the implementing commit; cite the user request (`req`) or popup answer (`Q<r>.<q>`) in `## References`. Decisions are immutable
once Accepted — supersede with a new ADR rather than editing the body.

Status follows the merge: an ADR is `Proposed` until its implementing PR
merges, then flips to `Accepted` in that PR or the next docs sweep. A
`Proposed` ADR must carry the header line
`- **Implementation**: pending (#<open issue or epic>)`; one without it counts
as merged but unflipped. `scripts/adr/check-status.py` enforces this in
`make docs-check` and the CI `docs` job; `--self-test` plants the failing cases.
The table below is generated: `scripts/adr/check-status.py --write` renders each
row from the ADR's H1 title and status line, and the check refuses a table that
differs from that rendering.

Numbering: project decisions start at `0001`; rules adopted from the vmafx
sibling keep vmafx's number (e.g. 0100, 0108) for an easy cross-walk.

| ADR | Title | Status |
|---|---|---|
| [0001](0001-project-genesis.md) | Pelorus genesis — a Vulkan GPU pre-encode pipeline under the vmafx org | Accepted |
| [0100](0100-doc-substance-rule.md) | Every user-discoverable surface ships human-readable docs in the same PR | Accepted |
| [0102](0102-flagship-smart-deband.md) | Smart deband (f3kdb) is the flagship filter | Accepted |
| [0103](0103-interop-sidedata-abi.md) | Pelorus⇄vmafx interop via a UUID-keyed AVFrame side-data ABI | Accepted |
| [0104](0104-ffmpeg-patch-stack.md) | Delivery — libpelorus core + an FFmpeg patch stack | Accepted |
| [0105](0105-libpelorus-license.md) | libpelorus is BSD-2-Clause-Patent; FFmpeg filter files are LGPL-2.1 | Superseded by [0171](0171-eupl-relicense.md) |
| [0106](0106-autotune-control-plane.md) | Control plane — tune Pelorus filter strength against VMAF via vmafx | Accepted |
| [0108](0108-deep-dive-deliverables-rule.md) | Fork-local PRs ship the deep-dive deliverables | Accepted |
| [0109](0109-analyze-filter.md) | vf_pelorus_analyze emits measured banding/variance via GPU readback | Accepted |
| [0110](0110-avoption-control-plane-contract.md) | Freeze the deband AVOption names + ranges as the vmafx-facing control-plane contract | Accepted |
| [0111](0111-benchmark-methodology.md) | Pre-encode-filter benchmarks score against the clean ground truth | Accepted |
| [0112](0112-temporal-denoise.md) | vf_pelorus_denoise — causal edge-preserving spatio-temporal denoise | Accepted |
| [0113](0113-optical-flow-mc.md) | vf_pelorus_mc — GPU motion estimation feeding MC-temporal denoise (and, gated, NVENC ME hints) | Accepted |
| [0114](0114-encoder-steering.md) | Encoder-steering strategy — feed Pelorus GPU maps to every encoder we can | Accepted |
| [0115](0115-grain-estimate.md) | vf_pelorus_grain_estimate — film-grain-synthesis parameter estimator | Accepted |
| [0116](0116-pelorus-mc.md) | vf_pelorus_mc_vulkan v1 — a standalone GPU motion-vector-hint producer (encode-speed, not quality) | Accepted |
| [0117](0117-grain-fgs-bsf.md) | pelorus_fgs — H.274 / HEVC film-grain SEI bitstream filter | Accepted |
| [0118](0118-nvenc-av1-filmgrain.md) | av1_nvenc film grain — carry the FGS estimate into NVENC hardware AV1 film-grain synthesis | Accepted |
| [0119](0119-qp-feedback.md) | Closed-loop QP feedback — read the encoder's honored QP/bits back into the interop ABI | Accepted |
| [0120](0120-libaom-steering.md) | libaom-av1 ROI steering via AOME_SET_ROI_MAP (SW-AV1 leg of Tier 1) | Accepted |
| [0121](0121-svtav1-steering.md) | SVT-AV1 ROI steering — honor AV_FRAME_DATA_REGIONS_OF_INTEREST via the SVT-AV1 segment map | Accepted |
| [0122](0122-qp-feedback-csv-reader.md) | A runnable QP-feedback reader — fold x265 `--csv` per-frame stats into PEL_SEC_QPREPORT | Accepted |
| [0123](0123-anime-dehalo.md) | vf_pelorus_dehalo_vulkan — anime/2D dehalo + dering | Accepted |
| [0124](0124-anime-aa.md) | vf_pelorus_aa — anime warp anti-aliasing + line-darkening | Accepted |
| [0125](0125-anime-tune.md) | The anime "tune" — a documented GPU pre-encode composition, not a new filter | Accepted |
| [0126](0126-scenecut-idr.md) | vf_pelorus_scenecut — scene-cut → forced IDR (vendor-neutral encoder steering) | Accepted |
| [0127](0127-deblock.md) | vf_pelorus_deblock_vulkan — re-encode deblock/dering | Accepted |
| [0128](0128-borderfix.md) | vf_pelorus_borderfix_vulkan — dirty-line / border repair | Accepted |
| [0129](0129-inline-glsl-chroma-passthrough-fix.md) | Runtime-only GPU defects — on-device validation as a release gate | Accepted |
| [0130](0130-mc-subpel-quarterpel.md) | vf_pelorus_mc — sub-pel MV refinement + quarter-pel grid units | Accepted |
| [0131](0131-mc-denoise-warp.md) | MC→denoise motion-compensated warp + per-block confidence | Accepted |
| [0132](0132-per-shot-crf-steering.md) | Per-shot complexity-budget CRF steering (design) | Rejected |
| [0133](0133-analyze-coarse-banding-cambi.md) | Coarse inter-tile banding scale for analyze ROI (CAMBI alignment) | Accepted |
| [0134](0134-denoise-shared-mem-tile.md) | Shared-memory tiling of the denoise NLM spatial window (opt-in) | Accepted |
| [0135](0135-analyze-perceptual-aq-map.md) | Source-side perceptual bit-allocation map for analyze — REJECTED on measurement | Rejected |
| [0136](0136-analyze-frame-metadata.md) | analyze emits per-frame stats as FFmpeg frame metadata | Accepted |
| [0137](0137-denoise-forward-lookahead-cadence.md) | Forward-lookahead (bidirectional) temporal denoise — cadence-aware | Accepted |
| [0138](0138-bug-hunt-remediation.md) | Adversarial bug-hunt remediation — 9 confirmed defects fixed | Accepted |
| [0139](0139-dehalo-shared-mem-tile.md) | Shared-memory tiling of the dehalo box-blur window (opt-in) | Accepted |
| [0140](0140-aa-sobel-mag-hoist.md) | Hoist the redundant aa sobel-mag into shared memory (opt-in `fast`) | Accepted |
| [0141](0141-deband-wide-band-reach.md) | Wide-band reach for deband (multi-radius / extended range) — REJECTED on measurement | Rejected |
| [0142](0142-tune-auto-content-router.md) | `tune=auto` — content-adaptive pre-encode router (design) + the grain-sigma detection enabler | Proposed |
| [0143](0143-ffmpeg-9-migration.md) | FFmpeg 9 migration — precompiled SPIR-V replaces the runtime inline-GLSL shader model | Accepted |
| [0144](0144-ffmpeg-pin-and-ci-runner-policy.md) | Pin FFmpeg by release and commit; run CI on Ubuntu 26.04 native Vulkan packages | Accepted; amended by [0149](0149-windows-utf8-paths.md) |
| [0145](0145-praetor-governance-adoption.md) | Adopt Praetor as a governance ratchet | Accepted; engine pin and baseline superseded by [0154](0154-praetor-engine-repin.md) |
| [0146](0146-qsv-roi-frame-ownership.md) | QSV dense ROI maps are per-frame and contract-gated | Accepted |
| [0147](0147-vulkan-sample-domain-and-components.md) | Normalize Vulkan arithmetic to the sample domain and preserve plane components | Accepted |
| [0148](0148-owner-only-exclusive-test-fixtures.md) | Conformance-fixture files are exclusive, owner-only, and never follow links | Accepted |
| [0149](0149-windows-utf8-paths.md) | libpelorus path arguments are UTF-8; a native Windows CI leg proves it | Accepted |
| [0151](0151-renovate-mirrors-checker-toolchain-pins.md) | CI toolchain pins that the build-config checker copies get a checker-validated Renovate regex manager | Accepted; enforcement claim narrowed by [0152](0152-renovate-guard-enforcement-scope.md) |
| [0152](0152-renovate-guard-enforcement-scope.md) | The build-config checker validates the mirroring Renovate manager, not every `renovate.json` setting | Accepted |
| [0153](0153-praetor-full-adoption.md) | Adopt Praetor's dev container, Git hooks, and branch ruleset | Accepted |
| [0154](0154-praetor-engine-repin.md) | Re-pin the Praetor engine to `0af07a73` | Accepted; engine pin superseded by [0168](0168-praetor-engine-492a00f.md) |
| [0155](0155-fgs-bsf-rdd5-profile.md) | pelorus_fgs defaults to the SMPTE RDD 5 profile and rejects unwritable models at init | Accepted |
| [0161](0161-grain-estimate-rounding-and-h274-mapping.md) | The grain estimator rounds its fixed-point sums and maps to H.274 model 0 through a calibration against FFmpeg's synthesizer | Accepted |
| [0163](0163-dehalo-gate-and-pull.md) | Dehalo harm-fix: ring gate in edge-step units and the DeHalo_alpha MaskedMerge + Repair pull | Accepted |
| [0166](0166-vulkan-qpmap-activation.md) | Patch 0009 enables the Vulkan quantization-map extension and follows the driver's map contract | Accepted |
| [0168](0168-praetor-engine-492a00f.md) | Re-pin the Praetor engine to `492a00f9` | Accepted |
| [0169](0169-release-provenance-slsa3.md) | Build, attest, and sign releases in a reusable workflow (SLSA Build Level 3) | Accepted |
| [0170](0170-renovate-shape-guards.md) | The build-config checker validates the shape of the setup-go and actionlint pins; a ci.yml regex manager tracks actionlint | Accepted |
| [0171](0171-eupl-relicense.md) | Pelorus is EUPL-1.2; the files that become part of FFmpeg stay LGPL-2.1-or-later | Accepted |
| [0172](0172-roadmap-milestone-map.md) | Roadmap to 1.0 and beyond: milestone map 0.3 to 3.0 and four Ongoing buckets | Accepted |
| [0173](0173-tester-programme.md) | Tester programme: artifacts are evidence for a master commit, licence rules bind before first publication | Accepted |
| [0174](0174-encoder-telemetry-abi-1-4.md) | Interop ABI 1.4 adds a normalised encoder-telemetry section, the motion block size and an encode-record section in one minor bump, specified before the code | Accepted |
| [0175](0175-encode-provenance-record.md) | The encode provenance record is canonical JSON whose SHA-256 digest VMAFx binds unchanged, and side data carries only the digest and a locator | Accepted |
| [0176](0176-rc-process-and-tester-publish.md) | Release candidates are `vX.Y.Z-rc.N` prereleases gated on candidate legs; tester images publish only from a dispatched, approved workflow | Accepted |
| [0177](0177-analyze-per-cell-maps.md) | vf_pelorus_analyze fills the ABI 1.0 banding, variance and edge map fields on a power-of-two cell grid, packed FFmpeg-side | Accepted |
| [0178](0178-tester-artifact-licence-record.md) | Every tester image file has a recorded licence, a gate fails the build on an unrecorded one, and the tester CI runs only on image inputs and never publishes from a schedule | Accepted |
| [0179](0179-dev-image-licence-record.md) | The pelorus-dev image starts from plain Ubuntu, records every file it holds, and fails its build on an unrecorded one | Accepted |
| [0180](0180-tester-vendor-images.md) | Tester images per kit: NVIDIA ships no NVIDIA file and builds NVENC from the MIT headers only, Intel ships Debian's media stack with the Expat-licensed non-free media driver, and a report without its device names the missing docker option | Accepted |
| [0181](0181-hevc-nvenc-sei-header-budget.md) | hevc_nvenc writes Pelorus side data within NVENC's 1024-byte header limit, without the per-cell maps when they do not fit | Accepted |
| [0182](0182-vulkan-qpmap-fill-paths.md) | Patch 0009 writes the QP map in the advertised format, by a fill path the encode queue family can record | Accepted |
| [0183](0183-sidedata-zero-free-carrier.md) | NVENC writes Pelorus side data in a zero-free COBS carrier under its own UUID (interop ABI 1.5) | Accepted |
| [0184](0184-vulkan-drm-modifier-output-pools.md) | Opt-in DRM-format-modifier output pools so Pelorus frames map to VAAPI and QSV without hwdownload | Accepted |
| [0185](0185-shared-ffmpeg-fix-series.md) | Take generic FFmpeg fixes from the shared fix series, as a pinned and verified release | Accepted |
| [0186](0186-scenecut-on-hardware-frames.md) | pelorus_scenecut runs on hardware frames, not after hwdownload | Accepted |
| [0187](0187-praetor-engine-7cf9cbb-engine-launcher.md) | Re-pin the Praetor engine to `7cf9cbb2`, run it through the engine's launcher, and take the engine's harness block | Accepted |
| [0188](0188-named-test-skips.md) | A skipped test passes only when it is named with its reason | Accepted |
| [0189](0189-named-nvenc-smoke-skip.md) | The patch-stack replay passes the NVENC smoke's skip only when the caller names its reason | Accepted |
| [0190](0190-gitleaks-every-ref.md) | CI scans every branch and tag for credentials with a pinned gitleaks | Accepted |
| [0191](0191-nvenc-hop-derived-vulkan-device.md) | Derive Vulkan device from decoder CUDA device for the NVENC hop | Accepted |
| [0192](0192-series-v0.2.0-forced-keyframe-smoke.md) | Pin the shared series at v0.2.0 and gate the forced-keyframe hang with a GPU smoke | Accepted |
| [0193](0193-filter-stance-labels.md) | Label every filter with its evidence stance | Accepted |
| [0195](0195-ai-tool-not-copyright-holder.md) | AI coding tools are not copyright holders; file headers must agree with REUSE annotations | Accepted |
| [0196](0196-filter-knob-contracts.md) | Publish per-filter JSON knob contracts and an AVOption drift gate for vmafx FilterAdapter generation | Accepted |
| [0197](0197-vmafx-interop-mirror-host-c-exception.md) | Place VMAFx interop mirror on ADR-2478 host-C exception list | Accepted |
| [0198](0198-encoder-option-string-parsers.md) | Option-string parsers for x264, x265, SVT-AV1 and libaom normalize pass-through CLI and FFmpeg parameters into canonical encode record parameters | Accepted |
| [0199](0199-patch-branch-tester-evidence.md) | Tester images may publish a commit of a maintained `lts-*` patch branch | Accepted |
| [0200](0200-ffmpeg-avoptions-in-encode-record.md) | The encode record stores FFmpeg encoder AVOptions under the `ffmpeg:` prefix, verbatim | Accepted |
| [0201](0201-vulkan-input-format-allow-list.md) | Pelorus Vulkan filters accept a measured list of input layouts and refuse the rest by name | Accepted |
| [0202](0202-vulkan-plane-size-spec-constants.md) | Pelorus Vulkan shaders take the frame size from specialization constants, not imageSize() | Accepted |
| [0203](0203-arm64-ci-lane.md) | Native arm64 CI lane for fast suite, patch replay, and lavapipe functional validation | Accepted |
| [0204](0204-libaom-grain-table-io.md) | libaom film grain table text format reader and writer | Accepted |
| [0205](0205-aom-h274-grain-conversion.md) | AV1 (AOM) and H.274 film grain parameter conversion with fidelity estimate | Accepted |
| [0206](0206-fgs-bsf-per-frame-bridge.md) | Per-frame film-grain bridge in the pelorus_fgs bitstream filter | Accepted |
| [0207](0207-grain-scoring-decoded-output.md) | Score the decoded grain-synthesised picture, never the reconstruction | Accepted |
| [0208](0208-arm64-tester-image.md) | Build the generic tester image natively on arm64 | Accepted |
| [0209](0209-zero-copy-vendor-legs.md) | Zero-copy chain vendor legs and encoder frames assertion | Accepted |
| [0210](0210-fgs-x265-udu-wrap.md) | libx265 user-data-unregistered SEI UUID wrap classification | Accepted |
| [0211](0211-grain-synth-conformance.md) | Film grain synthesis conformance against reference decoders | Accepted |
| [0212](0212-grain-scoring-av1-libdav1d.md) | Grain scoring covers AV1 through libdav1d only | Accepted |
| [0213](0213-windows-replay-tester-zip.md) | Windows replay of FFmpeg patch stack and tester zip artifact | Accepted |
| [0214](0214-quality-window-section.md) | Quality Window Section, Rational PTS Alignment, and Encoder Zone Emitters (Interop ABI 1.6) | Accepted |
| [0215](0215-quality-threshold-loop.md) | Quality-Threshold Closed-Loop Controller and Reference Harness | Accepted |
| [0216](0216-h274-grain-quantization.md) | H.274 Film Grain Synthesis Floor Quantization Modeling | Accepted |
| [0217](0217-av1-grain-round-quantization.md) | AV1 Film Grain Synthesis Round Quantization Modeling | Accepted |
| [0218](0218-sei-fit-includes-nvenc-sei.md) | The SEI Fit Helper Includes the Series nvenc_sei.h | Superseded by [0236](0236-sei-fit-merged-form.md) |
| [0219](0219-adr-allocator-sees-worktrees-and-branches.md) | ADR Allocator Discovers Worktrees and Local Branch Tips | Accepted |
| [0220](0220-codeql-code-scanning.md) | In-Repo Pinned CodeQL Workflow for Code Scanning | Accepted |
| [0221](0221-vulkan-export-contract.md) | Pelorus output pools carry their export facts in FFmpeg's frames and device contexts | Accepted |
| [0222](0222-device-local-storage-buffers.md) | Shader-read storage buffers are device-local, held by a source check | Accepted |
| [0223](0223-noisy-source-bench-method.md) | Noisy-Source Reference Method and Result-Record Validator | Accepted |
| [0224](0224-ffmpeg-tree-tidy-lane.md) | FFmpeg-tree clang-tidy lane in patch stack replay | Accepted |
| [0225](0225-deband-step-gated-dither.md) | Deband dither only on code steps, taps kept inside the plane, bench pin on ordered dither | Accepted |
| [0226](0226-dehalo-feathered-mask.md) | Dehalo feathered mask for the steep halo flank, with line-art guards | Accepted |
| [0227](0227-av1-grain-calibration.md) | The grain estimator maps to AV1 through a calibration against FFmpeg's AV1 synthesizer and refuses frames above DCI 8K | Accepted |
| [0228](0228-bench-corpus-categories.md) | Bench corpus categories with pinned, licence-checked sets | Accepted |
| [0229](0229-grain-dual-gate.md) | The grain estimator measures every band at two edge gates and takes the wide one only where the band behaves like grain | Accepted |
| [0230](0230-grain-gate-chain.md) | The grain estimator extends the dual gate to a chain of three gates and maps gated bands to AV1 through a gated calibration | Accepted |
| [0231](0231-split-vulkan-qpmap-enabler.md) | Split patch 0009 into a libavutil enabler (0009a) and the Pelorus encode path (0009b) | Accepted |
| [0232](0232-bench-corpus-parked-category.md) | A bench corpus category can be parked on an issue | Accepted |
| [0233](0233-probe-ffmpeg-tree-tidy-lane.md) | avoption-probe joins the FFmpeg-tree clang-tidy lane | Accepted |
| [0234](0234-qe02-gated-hf-rejected.md) | No edge-gated detail boost ahead of NVENC (QE-02 gate failed) | Accepted |
| [0235](0235-docker-build-push-actions.md) | Pinned Docker Actions for Dev Container Image Pipeline | Accepted |
| [0236](0236-sei-fit-merged-form.md) | The SEI Fit Helper Keeps Its Own Rules on FFmpeg's Merged Form | Accepted |
| [0237](0237-tonemap-stage-placement.md) | HDR Tone-Map Stage Runs Before the Pelorus Filters | Accepted |
| [0238](0238-agent-context-reference-split.md) | Move the agent reference tables out of `AGENTS.md` | Accepted |
| [0239](0239-semgrep-float-identity-rule.md) | Adopt the VMAFx float-identity Semgrep rule in test suites | Accepted |
| [0240](0240-doc-recipes-run-verbatim.md) | Run documented zero-copy recipes verbatim as hardware tests | Accepted |
| [0241](0241-suppression-marker-binding.md) | Bind every nosemgrep and nosec marker to its start line, rule id and reason | Accepted |
| [0242](0242-msvc-clang-cl-ci-leg.md) | Build and test libpelorus with cl.exe and clang-cl in CI | Accepted |
| [0243](0243-ruff-zizmor-local-gate.md) | Run pinned Ruff and zizmor in the local gate | Accepted |
| [0244](0244-python-argparse-gate.md) | Parse every Python command line with argparse | Accepted |
| [0245](0245-changelog-render-at-landing.md) | Changelog render at landing | Proposed |
| [0246](0246-meson-testlog-credential-scrub.md) | Meson testlog credential scrub and scanner | Proposed |
| [0247](0247-interop-abi-history-fixtures.md) | Persisted interop blobs from released ABI writers and readers | Accepted |
