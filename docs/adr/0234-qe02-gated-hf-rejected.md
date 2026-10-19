<!-- markdownlint-disable MD013 MD060 -->
# ADR-0234: No edge-gated detail boost ahead of NVENC (QE-02 gate failed)

- **Status**: Accepted
- **Date**: 2026-10-19
- **Deciders**: Lusoris
- **Tags**: `quality`, `bench`, `nvenc`
- **Related**: [ADR-0142](0142-tune-auto-content-router.md), [ADR-0223](0223-noisy-source-bench-method.md)

## Context

[ADR-0142](0142-tune-auto-content-router.md) records that additive preprocessing (pre-sharpen, CAS) is a wash ahead of hardware encoders, while reductive preprocessing pays. Inventory item QE-02 ([0172-roadmap-inventory](../research/0172-roadmap-inventory.md)) questioned that for a narrower case: boost high-frequency detail only where NVENC's deadzone removes it, so it survives the encode. A global `cas=0.8` was already known to restore HF energy at a higher bitrate (maintainer notes behind the inventory row). QE-02 required an iso-quality net win, measured with the four-arm method of [ADR-0223](0223-noisy-source-bench-method.md) on at least four rate points, before anything is built. The maintainer chose SSIMULACRA2 as the deciding metric, with VMAF and CAMBI secondary.

## Decision

1. Pelorus does not build an additive detail boost, gated or global, as a pre-encode filter or as a `tune=auto` leg.
2. The ADR-0142 additive law covers edge-gated boosts as well, on the evidence of [research 0234](../research/0234-qe02-deadzone-gate.md):
   - Against `nvenc-tuned`, which uses the same encoder settings, the gated boost loses on five of six valid ladders. SSIMULACRA2 BD-rate ranges from +5.4 % to +74 % on `bbb`, `netflix-bar`, BarScene 1080p, TreesAndGrass and WalkingInStreet.
   - The only gain, −2.0 % on BarScene 1080p at CQ 30 to 42, does not replicate on TreesAndGrass (+5.4 %) or WalkingInStreet (+15.3 %).
   - The global control loses +52.2 % to +248.1 % against `nvenc-tuned`.
3. QE-02 closes. A new attempt needs a different mechanism and its own gate run, not a different mask.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Close QE-02 without a build (this) | Follows the gate the item set; keeps the filter set reductive | A clip class outside this corpus might still gain | **Chosen**; the loss holds at 360p and 1080p on four sources |
| Build a low-rate-only boost (CQ ≥ 30 at 1080p) | BarScene showed −2.0 % there | Two of three 1080p clips lose on the same ladder; a −2 % gain sits inside the spread between clips | Rejected |
| Widen the sweep (mask thresholds, `cas` strength, more clips) before deciding | Could find a narrow win | The gate already cut the global loss to a third and stayed above break-even; a narrow win would still need content detection that the router does not have | Deferred; needs a new hypothesis, not more tuning |
| Score with VMAF-NEG as well | Matches the original QE-02 text | The maintainer chose SSIMULACRA2 as decider and dropped VMAF-NEG for this gate | Not run |

## Consequences

- **Positive**: the `tune=auto` router stays reductive only, as ADR-0142 designed it; the QE-02 inventory row has a measured result.
- **Negative**: the result covers `hevc_nvenc` with the `nvenc-tuned` settings, 48-frame clips and a CPU prefilter; AV1 NVENC, QSV and longer clips are not measured.
- **Neutral / follow-ups**:
  - The four-arm record still names its fourth arm `pelorus-denoise+nvenc-tuned` and its reference `noisy-source` whatever the prefilter. Research 0234 states how these map for this run.
  - Two of the 1080p clips saturate NVENC near 15 Mbps at CQ 22 and 26, so the standard ladder gives no BD-rate on them.

## References

- Research: [docs/research/0234-qe02-deadzone-gate.md](../research/0234-qe02-deadzone-gate.md).
- Method: [ADR-0223](0223-noisy-source-bench-method.md), [research 0131](../research/0131-noisy-source-reference-run.md).
- Maintainer decision on the metric: ledger `pelorus-qe02-metric`, 2026-10-19 (paraphrased: SSIMULACRA2 decides, VMAF secondary, no VMAF-NEG).
