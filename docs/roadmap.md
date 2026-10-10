<!-- markdownlint-disable MD013 MD060 -->
# Roadmap

Pelorus plans its work on the forge and keeps this page as the map. Work lives in
issues, the order lives here, and the reasons live in
[ADR-0172](adr/0172-roadmap-milestone-map.md).

- **Milestones**: [all milestones](https://github.com/VMAFx/pelorus/milestones).
- **Epics**: issues labelled
  [`epic`](https://github.com/VMAFx/pelorus/issues?q=is%3Aissue+label%3Aepic).
  Each epic lists its children as native sub-issues. This page links the epic and
  states its child count on 2026-10-08; it does not copy the children.
- **Labels**: `area:*` (subsystem), `priority:P0` to `P3`, `release-blocker`,
  `blocked`, `needs-decision`, `vmafx-ask` (a request from VMAFx/vmafx). The
  list is `.config/labels.yaml`.
- **Evidence**: the research digests `docs/research/0172-*.md` hold the audits
  behind the items, and `INV`, `VS`, `ZC`, `TP`, `CO`, `CC` and `P1` in issue
  bodies refer to them.

Pelorus is the pre-encode half of a pair. The sibling
[VMAFx/vmafx](https://github.com/VMAFx/vmafx) is the quality oracle and
autotune control plane, and the two meet in the `PelorusSideData` interop ABI
([ADR-0103](adr/0103-interop-sidedata-abi.md)). Milestones that vmafx depends on
say which vmafx phase they serve.

## Where to start

- **Using Pelorus now**: the current release candidate is v0.4.0-rc.2 with
  interop ABI 1.5, and the latest final release is v0.3.0; see the
  [README](../README.md).
- **Checking what ships next**: [0.4](#04-telemetry-schemas-and-tester-kit)
  becomes final as v0.4.0 after its candidates.
- **Contributing**: take an issue from the lowest open milestone, preferring
  `priority:P0` and `release-blocker`. [CONTRIBUTING](../CONTRIBUTING.md) has
  the rules.

## Releases

| Milestone | Theme | vmafx phase | Epics |
| --- | --- | --- | --- |
| [0.3](https://github.com/VMAFx/pelorus/milestone/1) | Hardened release | none | [#92](https://github.com/VMAFx/pelorus/issues/92) |
| [0.4](https://github.com/VMAFx/pelorus/milestone/2) | Telemetry schemas and tester kit | `1.0.0-rc.4` | [#93](https://github.com/VMAFx/pelorus/issues/93), [#108](https://github.com/VMAFx/pelorus/issues/108) |
| [0.5](https://github.com/VMAFx/pelorus/milestone/3) | Zero-copy and live integration | 1.1 | [#193](https://github.com/VMAFx/pelorus/issues/193), [#195](https://github.com/VMAFx/pelorus/issues/195) |
| [0.6](https://github.com/VMAFx/pelorus/milestone/4) | Conformance and encoder feedback | 1.2 | [#196](https://github.com/VMAFx/pelorus/issues/196) |
| [0.7](https://github.com/VMAFx/pelorus/milestone/5) | Multi-backend compute | `libgpudispatch` | [#176](https://github.com/VMAFx/pelorus/issues/176) |
| [0.8](https://github.com/VMAFx/pelorus/milestone/6) | Codec and encoder breadth | codec adapters | [#177](https://github.com/VMAFx/pelorus/issues/177) |
| [1.0](https://github.com/VMAFx/pelorus/milestone/7) | Stable contract | 1.0.0 | [#182](https://github.com/VMAFx/pelorus/issues/182), [#189](https://github.com/VMAFx/pelorus/issues/189), [#194](https://github.com/VMAFx/pelorus/issues/194) |
| [1.1](https://github.com/VMAFx/pelorus/milestone/8) | Content-adaptive tuning | none | [#112](https://github.com/VMAFx/pelorus/issues/112) |
| [1.2](https://github.com/VMAFx/pelorus/milestone/9) | Integrations and packaging | none | [#113](https://github.com/VMAFx/pelorus/issues/113) |
| [2.0](https://github.com/VMAFx/pelorus/milestone/10) | Breaking changes | none | [#114](https://github.com/VMAFx/pelorus/issues/114) |
| [2.1](https://github.com/VMAFx/pelorus/milestone/11) | Rust interop crate | 2.3 (Rust P2) | [#115](https://github.com/VMAFx/pelorus/issues/115) |
| [2.2](https://github.com/VMAFx/pelorus/milestone/12) | Rust host runtime | 2.5 (Rust P4) | [#116](https://github.com/VMAFx/pelorus/issues/116) |
| [3.0](https://github.com/VMAFx/pelorus/milestone/13) | Host C removed | 3.0 (Rust P6) | [#118](https://github.com/VMAFx/pelorus/issues/118) |

Release candidates (`vX.Y.Z-rc.N`, from 0.4) are published as prereleases and
are never `latest` ([#108](https://github.com/VMAFx/pelorus/issues/108)).

## 0.3 Hardened release

Outcome: v0.3.0 ships the bug wave (BUG-001 to BUG-035), the FFmpeg n9.0.2
hardening, Praetor `492a00f9` governance, SLSA Build Level 3 provenance and the
EUPL-1.2 relicence.

Exit criteria:

- v0.3.0 is published by `release.yml`.
- The downloaded assets pass `gh attestation verify` (signer
  `release-build.yml`), `cosign verify-blob` and `sha256sum -c`.
- `reuse lint` is green and no Proposed ADR describes merged work.
- Hooks and the Makefile run the engine pinned at `PRAETOR_REF`, not the one on
  `PATH`.

Epic [#92](https://github.com/VMAFx/pelorus/issues/92), 8 children. ADRs:
[0155](adr/0155-fgs-bsf-rdd5-profile.md),
[0161](adr/0161-grain-estimate-rounding-and-h274-mapping.md),
[0163](adr/0163-dehalo-gate-and-pull.md),
[0166](adr/0166-vulkan-qpmap-activation.md),
[0168](adr/0168-praetor-engine-492a00f.md),
[0169](adr/0169-release-provenance-slsa3.md),
[0170](adr/0170-renovate-shape-guards.md),
[0171](adr/0171-eupl-relicense.md).

## 0.4 Telemetry schemas and tester kit

Outcome: interop ABI 1.4 carries the schemas vmafx needs before its
`1.0.0-rc.4` (VMAFx/vmafx#2411), and Pelorus ships its first tester images and
first release candidate (v0.4.0-rc.1).

Exit criteria:

- ABI 1.4 is append-only, with `_Static_assert` layout locks and an extended
  conformance fixture.
- Telemetry field names match vmafx's stream-metadata schema (VMAFx/vmafx#2271)
  by test, and the vmafx mirror is re-pinned at the release tag.
- NVIDIA and Intel tester images are published with the `-source` image, SBOM
  and provenance.
- v0.4.0 is released after the release candidate.

Epics: [#93](https://github.com/VMAFx/pelorus/issues/93) telemetry and
provenance schemas, 8 children;
[#108](https://github.com/VMAFx/pelorus/issues/108) tester kit and release
candidates, 13 children. Design:
[tester programme](research/0172-tester-programme.md).

## 0.5 Zero-copy and live integration

Outcome: decode, Pelorus, encoder and vmafx scoring stay on the GPU on every
supported Linux path, and the interchange pieces vmafx 1.1 consumes exist.

Exit criteria:

- A zero-copy chain test is green on NVIDIA and on Intel or AMD Linux.
- No documented recipe uses `hwupload` or `hwdownload` except for software
  encoders.
- LCEVC metadata ([#87](https://github.com/VMAFx/pelorus/issues/87)) lands.
- The tester Windows zip and arm64 image are published; the MoltenVK spike has
  a verdict.

Epics: [#193](https://github.com/VMAFx/pelorus/issues/193) 100 percent
zero-copy pipeline, 7 children;
[#195](https://github.com/VMAFx/pelorus/issues/195) interchange and live
integration, 12 children. Break list and hop matrix:
[zero-copy audit](research/0172-zero-copy-audit.md).

## 0.6 Conformance and encoder feedback

Outcome: every steering signal Pelorus sends is measured as honoured, encoders
report back what they did, and vmafx consumes it.

Exit criteria:

- The conformance harness ([#80](https://github.com/VMAFx/pelorus/issues/80))
  reports the honoured fraction per encoder.
- Quality-window stream, recon-frame handoff and bit-exact filter mode
  ([#83](https://github.com/VMAFx/pelorus/issues/83),
  [#84](https://github.com/VMAFx/pelorus/issues/84),
  [#85](https://github.com/VMAFx/pelorus/issues/85)) land.
- QP feedback readers exist for QSV, NVENC and Vulkan, and the exact-class
  parity gate runs.
- vmafx reads the remaining interop sections (`QPREPORT`, `MOTION_CONF`,
  `DENOISE`, `FILMGRAIN`, `MOTION`).
- Encoder adapters fill the ABI 1.4 sections that v0.4.0-rc.1 shipped: stats
  adapters for libx264, libx265 and libsvtav1
  ([#263](https://github.com/VMAFx/pelorus/issues/263)), NVENC, QSV and Vulkan
  Video adapters ([#264](https://github.com/VMAFx/pelorus/issues/264)), and
  encode-record option-string parsers
  ([#265](https://github.com/VMAFx/pelorus/issues/265)). They moved here from
  0.4 to follow VMAFx's 1.1 and 1.2 adapter phase.

Epic [#196](https://github.com/VMAFx/pelorus/issues/196), 18 children.

## 0.7 Multi-backend compute

Outcome: Pelorus filters run natively on CUDA, SYCL/oneAPI, HIP/ROCm and Metal
through vmafx's `libgpudispatch`, which removes every Vulkan interop hop, with a
CPU reference and an exact-twin contract. Work starts when VMAFx/vmafx#1455
lands.

Exit criteria:

- Each backend passes the parity gate against the CPU reference.
- The tester images cover each backend.
- Frames reach vmafx's matching backend without interop.

Epic [#176](https://github.com/VMAFx/pelorus/issues/176), 10 children. Scope
source: [vmafx scope gap](research/0172-vmafx-scope-gap.md).

## 0.8 Codec and encoder breadth

Outcome: steering, film grain and telemetry cover the codecs and encoders vmafx
tunes.

Exit criteria: VVC, VP9, H.264 grain, SVT-AV1 and hardware AV1 grain legs, AMF,
VAAPI and VideoToolbox steering each ship with conformance numbers.

Epic [#177](https://github.com/VMAFx/pelorus/issues/177), 10 children.

## 1.0 Stable contract

Outcome: what adopters depend on is frozen. The contract is the public headers,
AVOption and filter names (including per-backend names), `lavfi.pelorus.*` keys,
the append-only interop ABI and soversion 1. Every filter is labelled proven or
experimental. Windows is supported, a prebuilt attested GPL-3.0-or-later FFmpeg
is published, AMF steering ships, the docs portal is live and licensing is
complete. macOS is listed only if the 0.5 spike passes.

Exit criteria:

- The 1.0 contract ADR is Accepted and `soversion` is 1.
- Every filter carries a proven or experimental label with its stance.
- A CI job proves each supported platform.
- Licence provenance, credits and notices are in every artifact, and the patent
  review in [#194](https://github.com/VMAFx/pelorus/issues/194) has a decision recorded before any quality claim.

Epics: [#182](https://github.com/VMAFx/pelorus/issues/182) frozen contract, 5
children; [#189](https://github.com/VMAFx/pelorus/issues/189) platforms,
distribution and docs, 6 children;
[#194](https://github.com/VMAFx/pelorus/issues/194) licensing and notices, 4
children (1 closed).

## After 1.0

The post-1.0 milestones are epics with candidate checklists. Children are
created when a milestone becomes the next one.

| Milestone | Outcome | Epic |
| --- | --- | --- |
| 1.1 Content-adaptive tuning | `tune=auto` router, luma mask, demosquito, fade compensation, denoise motion taps, learned pre-processor study, per-shot autotune with vmafx | [#112](https://github.com/VMAFx/pelorus/issues/112) |
| 1.2 Integrations and packaging | GStreamer element, HandBrake recipe, VapourSynth and OBS feasibility, package channels, filter metrics export | [#113](https://github.com/VMAFx/pelorus/issues/113) |
| 2.0 Breaking changes | ABI 2 consolidation, soversion 2, removal of deprecated options and names | [#114](https://github.com/VMAFx/pelorus/issues/114) |
| 2.1 Rust interop crate | Dependency-free crate for the interop ABI with conformance vectors shared with the C fixture, so vmafx can drop the C mirror | [#115](https://github.com/VMAFx/pelorus/issues/115) |
| 2.2 Rust host runtime | Host dispatch on `libgpudispatch-rs` and libpelorus host code in Rust behind the C ABI; FFmpeg-tree files stay C | [#116](https://github.com/VMAFx/pelorus/issues/116) |
| 3.0 Host C removed | Host C deleted outside a named FFmpeg-tree exception list | [#118](https://github.com/VMAFx/pelorus/issues/118) |

2.1 to 3.0 follow vmafx's Rust phases; see
[post-1.0 and Rust coupling](research/0172-post-1-0-rust-coupling.md).

## Ongoing buckets

Rolling milestones that never close. Each pass must leave a measurable
improvement.

| Bucket | Purpose | Epic |
| --- | --- | --- |
| [Proven filters](https://github.com/VMAFx/pelorus/milestone/14) | Measured BD-rate evidence, including negative results, on a public corpus with noisy-source references and the encoders' own temporal-filter baselines. No filter claims to beat an encoder before the method item lands. Method and claims ladder: [competitive audit](research/0172-competitive-audit.md) | [#123](https://github.com/VMAFx/pelorus/issues/123), 11 children |
| [Code health and governance](https://github.com/VMAFx/pelorus/milestone/15) | HISS baseline, tidy findings, exceptions before expiry (the FFmpeg-tree tidy lane, [#94](https://github.com/VMAFx/pelorus/issues/94), is due 2027-01-06), Praetor and Renovate drift | [#126](https://github.com/VMAFx/pelorus/issues/126), 8 children |
| [Upstream and rebases](https://github.com/VMAFx/pelorus/milestone/16) | FFmpeg rebases, upstream bug reports, upstream zero-copy interop work, patch submissions | [#129](https://github.com/VMAFx/pelorus/issues/129), 4 children |
| [Research and competitive watch](https://github.com/VMAFx/pelorus/milestone/17) | Quarterly competitor and patent refresh. Ideas wait here until a premise check passes and they move to a milestone | [#121](https://github.com/VMAFx/pelorus/issues/121), checklist |

## How to propose a change

1. **A new work item or a move between milestones** is triage: open or edit the
   issue, set its milestone and `area:*` and `priority:*` labels. No ADR needed.
2. **A change to the map** (add, remove, rename, reorder or re-scope a milestone
   or bucket) needs a new ADR that supersedes or amends
   [ADR-0172](adr/0172-roadmap-milestone-map.md) and an edit to this page in the
   same change. Reserve the number with `scripts/adr/next-free.sh --claim <slug>`.
3. **Keep the names equal** on the forge, in epic titles and here; update the
   forge milestone in the same change.
4. **Check the ADR index** ([adr/README.md](adr/README.md)) and add a changelog
   fragment under `changelog.d/`.
