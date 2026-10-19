<!-- markdownlint-disable MD013 MD060 -->
# ADR-0223: Noisy-Source Reference Method and Result-Record Validator

- **Status**: Accepted
- **Implementation**: partial: reference run done on an unpinned local clip (research [0131](../research/0131-noisy-source-reference-run.md)); a pinned noisy corpus clip is pending
- **Date**: 2026-10-18
- **Deciders**: Lusoris
- **Tags**: bench, metrics, validation, schema, nvenc
- **Related**: [ADR-0111](0111-benchmark-methodology.md), [ADR-0172](0172-roadmap-milestone-map.md), [ADR-0173](0173-pelorus-tester.md)

## Context

Prior Pelorus pre-encode benchmarks (such as v0.2 and v0.3) scored temporal denoise against a synthetic "clean" ground-truth reference, adding artificial noise to clean video and measuring fidelity recovery against the pre-noised source (`--clean-reference`). While clean-reference scoring measures artificial impairment stripping, it does not represent real-world broadcast, camera, or film workflows where the incoming source is naturally noisy and represents the authoritative capture.

Furthermore, earlier benchmarks evaluated Pelorus against default or partially-tuned hardware encoder baselines (e.g., `hevc_nvenc -preset p5` or `p7 -tune hq`), omitting modern hardware encoder temporal filtering (`tfLevel` / `-tf_level 4`) and Ultra High Quality tuning (`-tune uhq`). Competitive audit evidence (BEAT-1, C1, C2 in research 0172) identified this gap: claims that Pelorus pre-processing outperforms hardware encoders are unverified unless evaluated against the encoder's own native temporal filter and highest-quality tuning on the noisy source itself.

## Decision

We establish the noisy-source reference benchmark method and an automated result-record validator for all competitive hardware encoder claims:

1. **Source-referenced scoring**: The noisy source itself is the reference for quality scoring. Denoise and filtering are measured for rate-distortion efficiency directly against the input video.
2. **Metric hierarchy**: SSIMULACRA2 is the primary quality metric, which does not saturate on clean content and resists sharpening gaming. Standard VMAF (v1.0.16) and CAMBI banding index serve as secondary cross-checks.
3. **CQ-locked iso-bitrate ladders**: Benchmarks must use constant quality (CQ) locked ladders with at least 4 rate points per arm. Variable bitrate (VBR) targets are prohibited due to multipass rate-control overshoot. Single rate points and identical bitrates across arms are rejected as unusable for cubic Bjøntegaard delta (BD-rate) integration.
4. **Device attribution**: Hardware device identity (GPU model and driver) must be explicitly recorded per arm and per record.
5. **Four mandatory comparison arms**: Any claim comparing Pelorus against hardware encoding must record all four arms under identical source, resolution, and ladder conditions:
   - `nvenc-tuned`: hardware encoder tuned baseline (`-preset p7 -tune hq -spatial-aq 1 -temporal-aq 1 -aq-strength 8 -rc-lookahead 32 -multipass fullres -b_ref_mode middle -bf 4`; FFmpeg n9.0.2 refuses `-tf_level` with fewer than 4 B-frames, so every arm keeps the same GOP shape);
   - `nvenc-tflevel`: tuned baseline with native hardware temporal filtering (`-tf_level 4`);
   - `nvenc-uhq`: tuned baseline with ultra-high-quality tuning (`-tune uhq`);
   - `pelorus-denoise+nvenc-tuned`: Pelorus pre-denoise Vulkan filter feeding the tuned baseline.
6. **Comparability rule**: Any benchmark claim lacking all four mandatory arms or relying on synthetic-clean reference scoring is marked "not comparable (#131)". Historical documentation in `README.md` and `docs/development/bench-results.md` is relabelled accordingly without changing numerical records.
7. **Automated validator and schema**: Benchmark records must conform to JSON Schema `scripts/bench/record.schema.json` and pass validation by `scripts/bench/check-record.py` (standard library only, run with `python3 -I`). The fast test suite gates validator integrity via `--self-test` in `meson.build` (`test('bench-record')`).
8. **Synthetic fixture**: A hand-written reference fixture is provided at `scripts/bench/fixtures/record-four-arm.json` with synthetic values clearly identified.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Synthetic-clean reference only | Simple to synthesize with lavfi noise | Diverges from real capture; inflates apparent BD-rate by measuring divergence from unobserved clean ground truth | Unsound for real noisy content; fails peer review and vendor comparison |
| Two-arm comparison (`nvenc-tuned` vs `pelorus`) | Fewer encode passes per benchmark | Ignores native temporal filtering (`tf_level 4`) and UHQ tuning in modern NVENC SDKs | Incomplete baseline; fails competitive audit BEAT-1 |
| Third-party `jsonschema` library | Standard Python schema library | Adds external dependency not present in standard library; breaks `python3 -I` isolation | Violates stdlib-only policy and increases supply chain attack surface |

## Consequences

- **Positive**: Competitive claims are like-for-like against the best vendor baseline; machine-verifiable benchmark records prevent regressions; automated schema and domain gating.
- **Negative**: Benchmark runs require four full encode ladders per clip ($\ge 16$ encodes total), increasing benchmark execution time.
- **Neutral / follow-ups**: Historical documentation claims relabelled "not comparable (#131)"; future benchmark harness automation will emit conforming four-arm JSON records.

## References

- Issue: VMAFx/pelorus#131
- Research: [docs/research/0172-competitive-audit.md](../research/0172-competitive-audit.md) (section 5 BEAT-1, rows C1, C2)
- Prior ADR: [ADR-0111](0111-benchmark-methodology.md)
- Schema: `scripts/bench/record.schema.json`
- Validator: `scripts/bench/check-record.py`
- Reference run: [docs/research/0131-noisy-source-reference-run.md](../research/0131-noisy-source-reference-run.md)
