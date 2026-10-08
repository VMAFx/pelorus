<!-- markdownlint-disable MD013 MD024 MD033 MD060 -->

# Research 0172: post-1.0 milestones, Rust coupling and licensing pointers

Evidence behind the post-1.0 milestones (1.1, 1.2, 2.0, 2.1, 2.2, 3.0) and the licensing items of [ADR-0172](../adr/0172-roadmap-milestone-map.md). Part A points at vmafx's licensing decisions (the Pelorus relicence itself is [ADR-0171](../adr/0171-eupl-relicense.md)); part B lists vmafx's post-1.0 milestones and its Rust migration phases; part C lists Praetor's milestones; part D lists Pelorus-native post-1.0 candidates.

**Sources.** vmafx `origin/master` (`git show`, `grep`), `gh` read calls on `VMAFx/vmafx` and `cordanaLLM/praetor`, Praetor `origin/main` `3a766f2d5`, Pelorus `origin/master` `420c19a`. All read 2026-10-08.

## A. vmafx licensing pointers (paths on vmafx origin/master)

1. EUPL-1.2 switch
   - docs/adr/1250-eupl-fork-relicense.md (Accepted 2026-10-05; veto 4 = vendored Pelorus files untouched; "REUSE gate out of scope" line is stale: gate exists, see 2)
   - docs/adr/1699-root-licence-files-eupl.md (LICENSE = EUPL text, NOTICE = Netflix BSD-2-Clause-Patent; one root licence file)
   - docs/adr/1255-spdx-residual-identifier-correction.md; docs/adr/1036-licensing-spdx-svm-copyright.md (bad SPDX id history)
   - docs/adr/1560-python-package-licence-union.md; docs/adr/2673-chart-licence-kubernetes-schemas.md (manifest licence = union of shipped files)
2. REUSE.toml / LICENSES policy
   - REUSE.toml:1-10 (default table first: path ["*",".*","*/**",".*/**"], precedence closest, EUPL-1.2), overrides after (precedence override)
   - LICENSES/*.txt (15 texts incl. BSD-2-Clause-Patent, EUPL-1.2, LGPL-2.1-or-later, GPL-2.0-or-later)
   - CI: Makefile:159-165 `lint-reuse` (reuse==6.2.0), .github/workflows/lint-and-format.yml:103, .pre-commit-config.yaml:355-366 (reuse-lint + test-reuse-compliance)
   - issue VMAFx/vmafx#2580 (open, Ongoing): REUSE compliance every tracked file; generated/vendored via REUSE.toml annotations
   - docs/credits.yaml + docs/adr/2485-vmafx-credits-page.md (scripts/docs/check-credits.py)
3. Licence provenance gate + AI-tool copyright rule
   - docs/adr/1474-relicense-helper-headers-and-ci-check.md (required check "Licence Provenance"; scripts/dev/relicense_fork_files.py --check; scripts/ci/check_licence_metadata.py)
   - docs/adr/0861-vmafx-copyright-policy-drop-anthropic.md (AI tool not a copyright holder; credit stays in Co-Authored-By trailers, :13, :48)
   - docs/adr/0105-copyright-handling-dual-notice.md (header rule)
4. EUPL source-duty / embedding page
   - docs/licensing.md (Embedding VMAFx in another product table; "What the published packages carry"; Checking the licence metadata); ADR-1685 referenced there (no dual licence)
   - docs/adr/1513-production-artifact-licensing.md, 1503, 1517 (notices + `<tag>-source` images), 1578 (withdrawn rc images)
5. Vendored Pelorus interop files in vmafx today
   - REUSE.toml:327-331: path ["core/src/interop/pelorus*"], precedence closest, SPDX-FileCopyrightText "2026 Lusoris", SPDX-License-Identifier BSD-2-Clause-Patent (note: glob does not cover core/include/libvmaf/pelorus/*.h or core/test/test_pelorus_interop.c; those rely on in-file header prose "BSD+Patent" which is a deprecated spelling, not an SPDX tag)
   - .config/lint-exceptions.d/spdx.toml:7-55: 9 per-file SPDX-header exceptions (4 headers, 4 .c under core/src/interop, core/test/test_pelorus_interop.c), reason "add the line in pelorus and re-vendor", all `expires = 2026-12-31`
   - docs/credits.yaml:1140-1150: id pelorus, relation vendored, license BSD-2-Clause-Patent, paths core/src/interop/
   - docs/adr/1113-vendor-pelorus-interop-abi.md (pinned read-only mirror, banner + include rewrite only), 1120, 1276 (pin by full commit), .github/AGENTS.d/pelorus-mirror.md, scripts/sync-pelorus-interop.sh
   - IMPACT of Pelorus -> EUPL: vmafx REUSE.toml:327-331, credits.yaml:1140, spdx.toml exceptions, ADR-1250 veto 4 table row ("terms decided in VMAFx/pelorus") all need the same-day re-vendor/update; mirror header string embedded in sync script (ADR-1474:58). Mirrored headers currently carry prose "BSD+Patent": Pelorus should ship SPDX lines in the originals (vmafx exceptions expire 2026-12-31).
   - Related: #1455 libgpudispatch is EUPL-1.2 and "sibling repositories adopt it as EUPL-1.2" (plan comment 2026-10-07, Q-144): Pelorus FFmpeg filter .c files stay LGPL-2.1+ (FFmpeg compat); a shared GPU dispatch lib under EUPL cannot be linked into FFmpeg-tree LGPL files without a licence-compat decision. Flag for ADR.
   - Precedent for FFmpeg patch licence in vmafx: REUSE.toml:47-95 patches 0001-0018 override LGPL-2.1-or-later ("2000-2026 the FFmpeg developers" + Lusoris), 0019 `LGPL-2.1-or-later AND GPL-2.0-or-later`.
6. Prebuilt FFmpeg licence-flavour stance
   - docs/adr/1514-go-and-node-image-licensing.md (decision 2: FFmpeg built WITHOUT --enable-nonfree, published GPL-3.0-or-later, `-source` image holds FFmpeg tree + series + configure line)
   - docker/Dockerfile.node:28 comment, :304-312 configure (`--enable-gpl --enable-version3 --enable-libvmaf --enable-libx264 --enable-libx265 --enable-libvpx --enable-libdav1d --enable-libsvtav1 ...`; no NVENC/AMF/VPL flags there); docker/AGENTS.md:79 "never --enable-nonfree"; docs/development/docker-production.md:402
   - docs/licensing.md:145 (published statement); :262 + docs/adr/1578 (rc.1/rc.2 vmafx-node package deleted: image built --enable-nonfree = not redistributable)
   - Still nonfree in non-published recipes: Dockerfile:160, Dockerfile.ffmpeg:37 (`--enable-nonfree --enable-cuda-nvcc`), dev/Containerfile:1119 (dev only)
   - Verified against FFmpeg n9.0.2 `configure` (gh api): HWACCEL_LIBRARY_NONFREE_LIST = cuda_nvcc, cuda_sdk; EXTERNAL_LIBRARY_NONFREE_LIST = decklink, libfdk_aac, libmpeghdec; `enabled gpl` + any nonfree list item dies (configure:4833-4834). libvpl on HWACCEL_LIBRARY_LIST, not on a nonfree list.
   - SDK header licences: intel/libvpl = MIT (gh api license endpoint, verified). AMD AMF repo LICENSE.txt: GitHub reports NOASSERTION; text opens with "Notice Regarding Standards" (no codec patent licence) -> read full text before AMF work. FFmpeg/nv-codec-headers: GitHub API 404 / raw LICENSE 404 here; licence UNVERIFIED (training memory says MIT).
   - Praetor adopter requirements (docs/guides/licensing-gates.md @ praetor main 3a766f2d5): markers `REUSE.toml` or `LICENSES/` at root trigger (1) adoption writes `.github/workflows/reuse.yml` (fsfe/reuse-action@v6, required check in rendered ruleset) + `reuse-lint` lefthook job (reuse 6.x); (2) audit "REUSE.toml annotation order" (default table FIRST, overrides after; array-of-[[annotations]] tables only, no inline arrays/dotted keys); (3) audit "root licence": `LICENSE` = regular file (not symlink) with exact text of declared `LICENSES/<id>.txt` (declared = id of last whole-tree annotation); other licence-named root files need `root-license-notice` exception in .standards.yaml (path, reason, expiry <=90 days). NOTICE is not a licence name. Glob bound 2^24 steps; `reuse-annotation-order` exception for oversize.
   - praetor#823 (open, Ongoing code health: umbrella; "later gates": SPDX header presence, manifest licence field, container licence/SBOM), #892 (closed PR: gates), #896 (CLOSED 2026-10-08T16:16Z completed, fixed by 1ab5c99ff / PR #898; the brief of that day said open). Pelorus pin 492a00f9 (.github/workflows/standards-gate.yml:24) does NOT contain 1ab5c99ff (git merge-base check) -> adopting REUSE.toml with SPDX-PackageName keys requires re-pin to >= 1ab5c99ff first, or omit the keys. #812 (closed PR: credits gate, praetor's own repo only: "adopt does not yet write credits into adopted repositories"); #850 (open, Adopter blockers: caveman skill points to docs/credits.md adopters lack; vmafx built its own, ADR-2485).
   - Pelorus today (at survey time): no SPDX headers, no REUSE.toml, no LICENSES/, no third-party notices. Gates SKIP until markers exist; adding REUSE.toml turns three gates on at once (migration note in #892).

## B. vmafx post-1.0 milestones (gh api repos/VMAFx/vmafx/milestones, 2026-10-08)

| ms | title | open | description (short) |
| --- | --- | --- | --- |
| 1.1 (#2) | Integrations & live quality | 54 | OBS plugin, encoder sign-off #2148, vmaf-tune adapters #2147, reference kit #2144, integrator pack/CRA #2146, signed provenance #2159 |
| 1.2 (#3) | Encoder feedback, embedding & platforms | 19 | zero-copy encoder feedback #2067, CUDA aarch64 #2164, Arm #2156, iOS #2246 / Android #2247 / WASM #2248; ALSO Rust P0b-P0e #2569-#2572 |
| 1.3 (#4) | New metrics | 12 | butteraugli #2165, colour VDP #2167, NR neural #2166, own NR models epic #2505, extractor SDK #2337 |
| 1.4 (#8) | Metric A/B, best mix & more data | 9 | A/B harness #2240, corpus #2241, vmaf-tune Go ADR impls #2526-#2529 |
| 1.5 (#9) | Next model generation | 2 | retrain #2242, panel-aware #2262 |
| 2.0 (#5) | Breaking changes | 6 | libvmaf.h compat removal #2536/#1254, default changes #2538-#2540; #2537 (C++23) closed, superseded by Rust epic |
| 2.1 Rust P1a (#10) |  |  | #2573 default-model extractors + SIMD |
| 2.2 Rust P1b (#11) |  |  | #2574 remaining CPU extractors + SIMD |
| 2.3 Rust P2 (#12) |  |  | #2575 engine, model loading, predict, pooling, generated ABI shells |
| 2.4 Rust P3 (#13) |  |  | #2576 CLI/tools/MCP/ONNX host; #2581 host-integration glue |
| 2.5 Rust P4+P5 (#14) |  |  | #2577 GPU host runtimes on libgpudispatch; #2578 kernel verdicts + exception-list gate |
| 3.0 (#15) |  |  | Host C/C++ removed: #2579 P6; epic #2567 |
| Ongoing (#6, #7) |  |  | Models & benchmarks (#2395, #2168, #1255, #827); Code health (#2580 REUSE, #1256, #1237, #1443 Praetor adoption) |

Rust migration facts (docs/adr/2478-rust-core-migration.md, Accepted 2026-10-08; research digest 2479):

- Pelorus/interop is NOT mentioned in ADR-2478, research-2479, or epic #2567 (grep: zero hits). The ten mirrored files are host C under core/src/interop + core/include/libvmaf/pelorus; ADR-2478 end state: "no host C or C++ file remains outside the exception list"; exception list class 2 = "out-of-tree host glue for hosts with no supported Rust route (FFmpeg filters, VLC, OBS)". The mirror is neither. DERIVED: it falls under P2 (engine/predict/ABI shells, 2.3) or P3 or 3.0 deletion; vmafx has made no decision. Decision needed: (a) vmafx keeps a C mirror on the exception list (ADR-1113 byte-identity guard retained), (b) Pelorus ships a Rust interop crate vmafx vendors/depends on, (c) vmafx ports parser to Rust against the shared conformance fixture. ADR-1113 rejected hand-reimplementation (two parsers of a frozen wire format) -> (c) conflicts with ADR-1113 unless the fixture is shared and enforced.
- FFI boundary vmafx side: `core/api/vmafx.toml` single definition, generator `scripts/codegen/vmafx-api.py` gets Rust emitter (#2569, 1.2); public C ABI exported from Rust; headers stay generated; cbindgen kept for extractor ABI until shim gone. Oracle contract: C layer stays differential oracle until layer C deleted; hashed per-frame fixtures (#2572). New host components from 1.3 on are Rust-first (Q-233). Toolchain pin 1.98.1.
- Pelorus-visible consequence: FFmpeg filter/patch C stays C (FFmpeg exception, #2581 line 9 "FFmpeg patch series stays C"); the libvmaf FFmpeg filters read Pelorus side-data via patch 0017 (vmafx ffmpeg-patches/0017-libvmaf-read-pelorus-sidedata.patch).
- libgpudispatch (#1455, milestone 1.0.0, RC5 sub-epic A, plan 2026-10-07): extract in C for RC5 (env tokenizer -> picture pool -> per-backend common/dispatch/stubs -> consumers), vmafx is extraction source; rewritten in Rust in P4 (#2577, 2.5) behind same C ABI (ADR-2478 Q-226); licence EUPL-1.2 for siblings (Q-144); Depends-On cordanaLLM/praetor#605. Pelorus ask: INT-15 (move vf_pelorus_*_vulkan.c GPU dispatch onto lib after it lands; note issue text says Vulkan filter boilerplate shared with template is a separate duplication, comment 2026-09-22 scopes fork as two-way vmafx<->template). Pelorus is a CONSUMER, timeline RC5 (before 1.0.0 final) + not before.

vmafx items needing Pelorus deliverables (issue, milestone, ask):

| vmafx issue | ms | Pelorus ask | Pelorus item / issue |
| --- | --- | --- | --- |
| #2271 bitstream facts track | 1.1 | field names shared with encoder telemetry schema; test loading both schemas | INT-01/INT-13, pelorus#86 |
| #2510 ADR one session engine + stream-metadata lib | 1.1 | same field-name parity | INT-13 |
| #2251 LCEVC layer-aware quality | 1.1 | LCEVC enhancement-layer reader -> #86 schema (blocks #2251) | INT-09, pelorus#87 |
| #2147 vmaf-tune adapter interface (non-FFmpeg encoders) | 1.1 | encoder adapters/telemetry, ROI intent | INT-02/INT-04, pelorus#86/#80 |
| #2144 vendor-neutral reference kit | 1.1 | scoring recipe for grain interchange | INT-05, pelorus#82 |
| #2148 encoder sign-off epic | 1.0/1.1 | quality-window stream, provenance | INT-06 (#83), INT-03 (#81) |
| #2146 integrator pack (SBOM, signatures, licence notices, CRA mapping) | 1.1 | DERIVED: Pelorus prebuilt FFmpeg container needs matching notices/SBOM pattern (ADR-1513/1514) | PLT-01 |
| #2067 embedding + encoder feedback epic; #2514 encoder feedback API and recon-frame input | 1.2 | recon-frame handoff descriptor + adapters | INT-07 (#84), INT-06 (#83) |
| #2142 provenance on every score | 1.0.0 | encode provenance record (`encode_record` sha256 reserved, ADR-2073, ABI 0.1.5) | INT-03 (#81) |
| #2586 T-PREFILTER-LIVE-ENCODE-UNTESTED | Ongoing | Pelorus-enabled ffmpeg artifact/recipe | INT-14 / PLT-01 |
| #2411 rc.4 release PR | RC4 | ABI 1.4 spec (#86) time-boxed to RC4; mirror pinned 11e183e | INT-01, INT-16 |
| #1455 libgpudispatch | 1.0.0 | consumer migration after lib lands; EUPL licence seam | INT-15 |
| #2569 Rust emitter, #2572 oracle fixtures | 1.2 | DERIVED: ask Pelorus to expose conformance fixture as data (hashed vectors) rather than C test | derived |
| #2575 Rust P2 (2.3) | 2.3 | DERIVED: decision on mirror form (see above) | derived |
| #2580 REUSE compliance | Ongoing | mirror files need SPDX in Pelorus originals (spdx.toml exceptions expire 2026-12-31) | ENG-13 |

## C. Praetor milestones (cordanaLLM/praetor; titles + numbers)

1.0 Measure and route cheap (16): #904 CI run-minutes budget report; #889 EPIC cache-stable context; #888 EPIC cheap lanes enforced; #880 EPIC benchmark evidence format; #879 EPIC efficiency ledger; #871 benchmark evidence names backend that ran; #870 efficiency ledger per landed unit; #860 compile-context fail on AGENTS.md without cache-band markers; #858 dispatch hook not enforcing routed lane; #853 cache-stable compiled context + MCP responses; #852 model router pins stale models (no alias routing); #752 nested AGENTS.md as generated index; #375 register budget root only; #236 register block bytes depend on compiler build; #162 projections carry no compiler version; #73 300-line budget no override.
1.1 Facts before inference (21): #897 ledger fact-hit ratios; #894 dedupe struct-shape clones; #882 EPIC gate outputs and evidence; #881 EPIC knowledge and checks first; #876 outside-hardware report ingest format; #875 dedupe counters per release; #874 JUnit XML gate output; #873 HISS applicability matrix per language; #872 generated capability table; #825 HISS-10 count warnings in CI logs; #811 nested AGENTS.md lint gaps; #810 `gate receipt --pr`; #755 hermetic git in tests; #607 cross-repo payload schema decoder check; #599 post-baseline HISS findings; #240 evidence tier; #211, #198, #182, #161 (HISS-19 outside Go), #105 (HISS-21 portability matrix incl. compile-only GPU legs).
1.2 One planning graph across repos (14): #883 EPIC planning graph; #877 org-wide agent rule distribution; #837 epics/milestones published once, never reconciled (planning-sync emission); #605 copied-script drift across adopters (libgpudispatch Depends-On); #299/#298/#297/#296 `needs epic` defects; #207, #203, #158, #107, #92, #66 vendored artifacts drift.
1.3 Budgets compiled into every harness (8): #884 EPIC declared budgets; #200, #183, #176, #171, #167, #147, #144.
Ongoing: Radar (#885 EPIC radar digest, #878 radar module licence gate); Code health (53 open); Adopter blockers (60 open; #850 credits ref, #896/#175 closed).
Pelorus absorption (DERIVED from titles): 1.0 #852/#858 router alias + lane enforcement + #870/#879 ledger (Pelorus QE evidence maps to #880/#871 "result names backend that ran" = no-silent-fallback rule); 1.1 #874 JUnit, #873/#105 HISS applicability + compile-only GPU legs (Pelorus CI is GPU-less, HW-07), #825 warning ceiling (ENG-04); 1.2 #837 planning-sync emission (Pelorus milestone rename D3), #605/#66 drift of mirrored vmafx/pelorus files; 1.3 #884 budgets.

## D. Pelorus-native post-1.0 candidates (beyond vmafx coupling)

Mission text: README/CLAUDE.md "reduce fixed-function encoder BD-rate gap"; docs/principles.md has no non-goal section (grep: none) so non-goals come from ADRs: ADR-0132 per-shot CRF rejected, ADR-0135 perceptual-AQ rejected, ADR-0141 deband wide-reach rejected, ADR-0123:74/0127:68 neural dehalo/deblock out of scope; ADR-0142 "additive pre-sharpen = wash".
Each item: source | rationale | rough phase.

- tune=auto router: ADR-0142 (Proposed), inventory FLT-03/QE-13 | closes loop from analyze metadata (noise_sigma, banding risk) to filter choice; needs per-leg iso-bitrate proof first | 1.1-1.2
- Filter quality proofs and content legs (anime chain, dehalo flank mask, deblock presets, demosquito, fadecomp): QE-01..QE-14, FLT-01..FLT-08 | 1.0 labels some filters unproven; proof campaign needs corpus QE-12 | 1.1
- Grain breadth: VVC/H.264 legs, SVT-AV1 `--fgs-table`, HW AV1 grain (QSV/AMF/VAAPI), explicit chroma/AR fit, time-varying HEVC model: ENC-06/07/08/12, ADR-0117/0118/0161, pelorus#82 | codec breadth of the one codec-spanning feature | 1.2
- AMF and Windows encoder steering beyond 1.0 baseline (qp feedback, grain, ROI `av1_amf`), VAAPI, QSV block-stats: ENC-04/09/10/11, HW-04/09 | needs AMD dGPU + Arc HW; 1.0 scope per D8 | 1.1-1.2
- ABI/interop evolution: ABI 1.4 telemetry, encode record, ROI intent, quality stream, recon handoff (INT-01..INT-11; pelorus#80-#87) | pre-1.0 append-only; ABI 2.0 consolidation (derived: reorder/resize/repurpose permitted once, soversion 2, removal of reserved/deprecated sections after vmafx 2.0 drops libvmaf.h compat #2536) | 1.1-1.3 appends; 2.0 consolidation derived
- Rust: derived. (a) Rust crate for libpelorus interop (pack/parse/conformance) if vmafx 2.3 (P2) wants a non-C mirror (see B decision); (b) cbindgen/C ABI from Rust, keeping soversion 1; (c) filters stay C (FFmpeg) and go on a Pelorus exception list per Praetor practice. Rationale: HISS-01..09 are C-centric; Rust removes pointer-arithmetic class (HISS-09). Phase 2.x, gated on vmafx decision
- VVenC/VVC encoder adapters and telemetry (INT-02 lists VVenC log); AV2 readiness (derived: AV2 spec/libavm tracking, grain model, steering patches) | codec scope statement in mission is codec-agnostic; AV2 unverified status, research digest first | 1.3-2.0
- Vulkan video decode zero-copy front end (derived; ADR-0166 covers encode; decode path currently via hwaccel) and Vulkan<->D3D11/D3D12 mapping on Windows (UPS-03: none in FFmpeg n9.0.2) | Windows distribution goal | 1.2
- Other backends: D3D12 compute / Metal-MoltenVK (derived; PLT-03 shows macOS unaddressed; ADR-0114 mentions d3d12va only as encoder) | mission is Vulkan compute zero-copy; non-Vulkan backend would break rule 4 single shader source (GLSL->SPIR-V at build time) -> needs ADR and likely stays out; MoltenVK as support statement only | 2.0 decision
- Upstreaming the patch stack: UPS-07 (D16) | reduces rebase burden (UPS-01 per FFmpeg bump); candidates: libaom ROI wiring (ENC-05), NVENC qpDeltaMap, scenecut, Vulkan fixes UPS-03/05 | continuous from 1.1
- Distribution maturity beyond 1.0: distro packages, multi-arch container, signed SBOM per image, Windows installers, docs portal PLT-01/04 | 1.1
- Bit-exact filter mode / cross-vendor determinism (INT-08, ADR-0163:133-136, vmafx#2144) | feeds vmafx reference kit | 1.2-1.3
- Engineering health ratchet: HISS baseline 51 burn-down, FFmpeg-tree tidy lane (ENG-01 deadline 2027-01-06), Renovate/Praetor re-pin cadence | rolling

### Proposed milestone themes (X.Y — Theme)

1.1 — Evidence & content-adaptive filters: tune=auto router (FLT-03); filter quality proofs (QE-01..07); corpus + methodology publication (QE-12, QE-15); anime/dehalo/deblock tuning; hardware matrix doc/CI (HW-06/07)
1.2 — Encoder telemetry & interchange: ABI 1.4 telemetry (INT-01, #86); encode provenance (INT-03, #81); ROI intent (INT-04, #80); quality stream (INT-06, #83); recon handoff (INT-07, #84); LCEVC reader (INT-09, #87)
1.3 — Codec & vendor breadth: grain legs VVC/H.264/SVT-AV1/HW AV1 (ENC-06..08, #82); AMF/VAAPI/QSV steering (ENC-04/09/10); VVenC adapters; AV2 readiness study; NVIDIA negative-delta policy (ENC-01..03)
1.4 — Platform & distribution: Windows FFmpeg container/installer, Vulkan<->D3D mapping, decode zero-copy front end, distro packages, docs portal growth, upstream patch submissions (UPS-07)
2.0 — Contract consolidation: ABI 2.0 / soversion 2 consolidation of reserved sections, removal of deprecated AVOptions, decision on non-Vulkan backends, Rust interop crate (if chosen), mirror-contract change with vmafx 2.x
Ongoing — Upstream tracking & engineering health: FFmpeg rebase cadence (UPS-01), upstream reports (UPS-02..05), HISS/tidy ratchet, Praetor/Renovate cadence, REUSE/credits upkeep

## Open questions / unverified

- nv-codec-headers licence unverified (404 via API); AMF LICENSE.txt full text not read beyond opening notice.
- No Pelorus statement exists in vmafx Rust ADR/epic; mirror fate is derived, not sourced.
- praetor#896 closed, not open; Pelorus pin lacks fix.
- Rust phases/dates are milestone labels only (no calendar dates in milestones).
