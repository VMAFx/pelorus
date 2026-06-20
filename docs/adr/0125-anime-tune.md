<!-- markdownlint-disable MD013 MD060 -->
# ADR-0125: The anime "tune" — a documented GPU pre-encode composition, not a new filter

- **Status**: Accepted
- **Date**: 2026-06-20
- **Deciders**: Lusoris
- **Tags**: anime, tune, pipeline, deband, dehalo, aa, analyze, roi, encoder-steering, docs, capstone

## Context

Animation is the content class where a fixed-function hardware encoder loses the
most to a slow CPU encoder, and it loses for a *structural* reason: an anime
frame is two extremes side by side — large, nearly-flat color regions and hard,
high-contrast line-art — and a fixed-function encoder mishandles **both**. The
flats band (the encoder's cheap intra-frame variance AQ starves exactly the
smooth gradients that contour); the lines ring and alias (compression and prior
processing leave bright/dark "halos" around the line-art and stair-step
"jaggies" along it). Each of those is a *distinct* artefact with a *distinct*
fix, and Pelorus already has — or has in flight — a GPU filter for each one:

| Anime artefact | Where it lives | Pelorus filter | Status |
|---|---|---|---|
| Banding on the flats | flat color regions | `vf_pelorus_deband_vulkan` (smart f3kdb) | working (ADR-0102) |
| Encoder *starves* the flats | flat color regions | `vf_pelorus_analyze_vulkan roi=1` → ROI map → encoder steering | working (ADR-0109 / ADR-0114) |
| Ringing / "halos" on lines | line-art edges | `vf_pelorus_dehalo_vulkan` | in flight (ADR-0123, PR #17) |
| Jaggies / aliasing on lines | line-art edges | `vf_pelorus_aa_vulkan` (warp AA + line-darkening) | in flight (ADR-0124, PR #18) |

The project's stated headline is to make Pelorus *the* GPU pre-encode filter
wonder for anime. With the per-artefact filters built (or building), the open
question is not "what filter do we still need" but "how do we present the
*composition* of the ones we have" — i.e. what is the recommended end-to-end
anime tune, and is that tune a new piece of code or a documented chain of the
existing pieces?

The forces: the four filters are already independent, already composable in one
zero-copy Vulkan filtergraph, and already each carry their own AVOptions, ADR,
and per-surface docs. The deband leg additionally needs **10-bit headroom** for
its dither to survive quantization (the v0.1 8-bit-wash lesson — at 8-bit and
low bitrate the encoder quantizes the dither away and re-bands, so the banding
reduction never reaches the decoded output; see bench-results.md v0.1 and
research 0101). The ROI-steering leg needs **constant-QP and the encoder's own
AQ off**, because the encoder's variance-AQ overrides the delta-QP map and VBR
redistribution erodes the perceptual win (ADR-0114 Tier 1, bench-results.md
v0.5).

## Decision

We will ship the anime tune as a **documented composition** of the existing
filters plus encoder ROI steering — a recommended `tune=anime` filtergraph and a
user guide ([docs/usage/anime.md](../usage/anime.md)) — **not** as a new
meta-filter and **not** as a code-level `tune=` AVOption on a wrapper. The
recommended chain is:

```text
ffmpeg -init_hw_device vulkan -i in.mkv -vf "
  format=yuv420p10le,hwupload,
  pelorus_analyze_vulkan=roi=1,
  pelorus_dehalo_vulkan=blur=2:darkstr=1:brightstr=1,
  pelorus_aa_vulkan=depth=8:blur=2:darkstr=0.3,
  pelorus_deband_vulkan=range=15:thry=0.02:dither=bluenoise:dynamic=1:protect=1,
  hwdownload,format=p010le
" -c:v hevc_nvenc -pelorus_roi 1 -preset p5 -rc constqp -qp 22 out.mkv
```

The ordering is principled (and content-tunable — the guide says so):

1. **`analyze roi=1` first.** It is a pass-through producer that measures the
   frame and emits the `AV_FRAME_DATA_REGIONS_OF_INTEREST` map over the
   banding-prone flat tiles. Running it first measures the *source* before any
   stage alters it, and the ROI blob then rides every downstream frame to the
   encoder via `av_frame_copy_props`.
2. **`dehalo` then `aa`** clean the line-art. Dehalo runs *before* aa so the
   anti-aliasing warps lines that have already had their ringing removed —
   warping a still-haloed edge would smear the halo along the line.
3. **`deband` last** so its freshly-injected dither is not disturbed by the aa
   warp that would otherwise run after it. Debanding the flats is the final
   pixel operation before download.
4. **10-bit (`yuv420p10le` → `p010le`)** end to end so the deband dither
   survives quantization (the v0.1 8-bit-wash lesson).
5. **ROI steering** (`-pelorus_roi 1`) under **constant-QP with the encoder's own
   AQ off**, as the ROI work requires, so the encoder honors the flat-ROI map
   and spends its bits on the flats.

We choose a *documented chain over a code "tune" option* for one decisive
reason: **the filters are already independent and composable, so a meta-filter
would only duplicate them.** A `tune=anime` wrapper (whether a new filter or an
AVOption on one) would re-implement the same four passes behind one name, freeze
their relative ordering and parameters into code, and force every per-content
adjustment through a recompile — when the exact same effect is one filtergraph
string the user can read, reorder, and retune. The composition *is* the
deliverable; presenting it well is a docs problem, not a code problem.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
|---|---|---|---|
| **Documented `tune=anime` chain of existing filters + ROI steering** (chosen) | Zero new code; reuses four already-built, already-documented, already-composable filters; every stage and parameter is visible and retunable in the filtergraph; honest about ordering being content-tunable | Users must copy a longer `-vf` string; no single magic flag | **Chosen** — the composition is the value; a wrapper would only hide it |
| Single monolithic `vf_pelorus_anime_vulkan` filter | One name, one filter | Non-composable: bakes dehalo+aa+deband+analyze into one pass, duplicating four existing shaders; can't reorder or drop a stage per content; every tweak is a recompile; violates the one-technique-per-filter design | Rejected — duplicates existing filters, destroys composability |
| Code-level `tune=anime` AVOption on a wrapper filter | A single flag | Over-engineering: it is just a stored filtergraph behind an option; freezes ordering/params in C; still has to call the same four filters; adds a maintenance surface (the wrapper) for zero capability the chain lacks | Rejected — over-engineered for what a documented chain already does |
| CPU VapourSynth anime chain (dehalo/aa/deband scripts) via `hwdownload` | Mature, well-known anime scripts exist | Breaks zero-copy (round-trips every frame through system RAM and the CPU), throwing away the entire reason Pelorus exists; CPU pre-processing at CPU speed/power is precisely the thing this project replaces | Rejected — defeats the zero-copy GPU premise |

## Consequences

- **Positive.** The headline ("Pelorus is the GPU pre-encode wonder for anime")
  becomes a concrete, copy-pasteable recommendation backed by four
  single-responsibility filters, each with its own ADR/docs/benchmark. No new
  code, no new maintenance surface, no frozen ordering — users read the chain,
  understand *why* each stage is there, and retune per source. Each leg targets
  a distinct anime artefact (banding / starved flats / ringing / jaggies), so
  the composition addresses the full anime failure mode of a fixed-function
  encoder.
- **Build-verification status, honestly.** The composition is sound and each leg
  is independently build-verified: `analyze`/`deband`/the ROI steering are
  working and benchmarked (analyze-ROI: **−47% banding at iso-bitrate and
  iso-VMAF, auto-detected**, bench v0.5; NVENC ROI: **−41% banding**, bench v0.5;
  x265 ROI: **−47% banding**, bench v0.5). `dehalo` (ADR-0123, PR #17) and `aa`
  (ADR-0124, PR #18) land on sibling branches; until those PRs merge, this
  capstone's links to their ADRs and metrics docs are dead by construction (this
  ADR depends on #17 + #18).
- **Negative / the documented follow-up.** **No composed end-to-end number is
  claimed.** The per-leg wins above are real and measured *individually*; the
  full-chain anime BD-rate / SSIMULACRA2 / CAMBI proof against a *clean anime
  ground truth* is the explicit follow-up. SSIMULACRA2 and CAMBI (not VMAF
  BD-rate) are the right metrics here — deband/dehalo/aa trade a little
  frame-vs-source VMAF for lower banding/ringing/aliasing, exactly as the
  benchmark methodology (ADR-0111) and the v0.1 deband lesson document. Until
  that run exists, the tune is presented as a *recommended, build-verified
  composition*, and the guide tells users to tune the parameters to their source
  and prove the result with SSIMULACRA2 + CAMBI.
- **Neutral / follow-ups.** Land the anime corpus + the composed-chain benchmark
  (clean-referenced, SSIMULACRA2 + CAMBI) once #17/#18 merge; revisit whether a
  per-content *preset table* (TV anime vs film vs CG) is worth documenting after
  the corpus exists.

## References

- [ADR-0102](0102-flagship-smart-deband.md) (smart deband), [ADR-0109](0109-analyze-filter.md) (the ROI map producer), [ADR-0111](0111-benchmark-methodology.md) (clean-reference + the right metric per filter), [ADR-0114](0114-encoder-steering.md) (encoder ROI steering, the −41%/−47% proofs), [ADR-0123](0123-dehalo.md) (`vf_pelorus_dehalo_vulkan`, PR #17), [ADR-0124](0124-aa.md) (`vf_pelorus_aa_vulkan`, PR #18).
- [docs/usage/anime.md](../usage/anime.md) (the user-facing guide), [docs/usage/ffmpeg.md](../usage/ffmpeg.md) (the zero-copy pipeline + ROI steering), [docs/development/bench-results.md](../development/bench-results.md) (v0.1 deband 8-bit-wash lesson; v0.4–v0.7 ROI proofs), [docs/research/0101-smart-deband.md](../research/0101-smart-deband.md) (10-bit dither-survival).
- Source: `req` — the user directed that the anime tune ship as a pure docs/ADR capstone that ties the existing GPU anime filters into one recommended "wonder" pipeline, as a documented composition rather than a new meta-filter, since the filters are independent and composable and a wrapper would only duplicate them.
