<!-- markdownlint-disable MD013 -->
# ADR-0111: Pre-encode-filter benchmarks score against the clean ground truth

- **Status**: Accepted
- **Date**: 2026-06-14
- **Deciders**: Lusoris
- **Tags**: benchmark, methodology, bd-rate, tooling

## Context

Pelorus filters are a pre-encode pass whose value is a **BD-rate** improvement
against a normal hardware encode. To prove that, the bench must compare a bare
`hevc_nvenc`/`av1_nvenc` encode of the source against the same encoder fed a
Pelorus-filtered source, and report the bitrate delta at equal quality.

The first attempt scored both arms against the **encoder input** (the impaired
source — banded for deband, noisy for denoise). That inverts the objective: a
filter that removes the impairment then measures as *diverging* from the
reference, because VMAF reads removed banding/grain as "lost detail" and
penalises the very thing the filter exists to do. Deband consequently looked
like a wash and denoise looked like a regression, both spuriously.

The real-world scenario is the opposite. Capture arrives carrying sensor/film
grain (or 8-bit banding) that is perceptually unimportant and expensive to
encode; the deliverable is the **clean** picture underneath. So the clean signal
is the correct ground truth, not the impaired capture.

## Decision

Pre-encode-filter benchmarks score **both arms against the clean ground truth**,
while the impaired source remains the baseline encoder input and the filter's
input. `scripts/bench/run-bench.py` gains a `--clean-reference` flag that
decouples the VMAF reference from the encoder input (default unchanged:
reference == input, so existing calls are unaffected). The harness is pinned and
repeatable: `corpus.lock` records sha256-verified clips, `fetch-corpus.sh`
materialises raw YUV, and the RD ladder reports BD-rate (Bjøntegaard) plus a
CAMBI banding delta. `vmaf` is bounded by `--vmaf-timeout` because the build
hangs at 0% CPU *after* flushing its JSON; the harness reaps it and reads the
already-written result.

BD-rate is reported as the change of the Pelorus arm relative to baseline:
negative = fewer bits at equal quality = a win.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
|---|---|---|---|
| Score vs the clean ground truth (this) | Measures fidelity to the intended signal; reveals the real gain | Needs a known-clean reference (a clean source + injected impairment, or graded master) | **Chosen** |
| Score vs the impaired encoder input | No separate reference needed | Penalises the filter for removing the impairment; makes every restorative filter look like a loss | Rejected — it is the bug that masked the gain |
| No-reference metric (BRISQUE/NIQE) | No reference at all | Not bitrate-anchored; can't produce a BD-rate; noisy on synthetic content | Rejected for the headline metric |
| Subjective / MOS panel | Ground truth for perception | Not repeatable in CI; slow; out of scope for an automated gate | Deferred |

## Consequences

- **Positive**: the harness produces honest BD-rate numbers. With the clean
  reference, a temporal-denoise stand-in (CPU `atadenoise`, the same algorithm
  class as the planned `vf_pelorus_denoise_vulkan`) shows −42.94% BD-rate on
  high-motion BBB and −88.94% on a locked-off scene (both + seeded grain) — the
  encoder was spending a large fraction of its bitrate coding incoherent noise
  the clean deliverable does not contain.
- **Caveat documented**: the clean reference assumes the impairment is unwanted.
  Where grain is artistic intent the filter should not simply remove it — that
  is the film-grain-synthesis roadmap path (strip, then re-synthesise at decode).
  Gain magnitude scales with impairment level and operating point.
- **Correctness fix**: `bd_rate.py` computed `integ(baseline) − integ(pelorus)`,
  sign-flipping both BD-rate and BD-VMAF (a 50% saving read as ~+75%). Corrected
  to `integ(pelorus) − integ(baseline)` with a self-test (half-bitrate ⇒ −50%,
  identical ⇒ 0%, double ⇒ +100%).
- **Neutral / follow-ups**: deband's proper metric is banding (CAMBI /
  SSIMULACRA2), not VMAF BD-rate, since it trades a little VMAF (dither) for less
  banding; score it accordingly on real banded content. The stand-in result must
  be re-proven with the real Vulkan filter once it lands.

## References

- `scripts/bench/run-bench.py`, `scripts/bench/bd_rate.py`,
  `scripts/bench/corpus.lock`, `scripts/bench/fetch-corpus.sh`;
  [docs/development/benchmarking.md](../development/benchmarking.md),
  [docs/development/bench-results.md](../development/bench-results.md).
- [ADR-0102](0102-flagship-smart-deband.md) (deband), [ADR-0106](0106-autotune-control-plane.md) (the VMAF-oracle autotune this bench underpins).
- Source: `req` — "well i mean we need to compare against a normal gpu encode no?"
  and "well but we are looking for gains lol?" (the bench must contrast Pelorus
  against a bare GPU encode and surface the real BD-rate gain).
