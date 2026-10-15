<!-- markdownlint-disable MD013 -->
# ADR-0196: Publish per-filter JSON knob contracts and an AVOption drift gate for vmafx FilterAdapter generation

- **Status**: Accepted
- **Date**: 2026-10-15
- **Deciders**: Lusoris
- **Tags**: interop, autotune, vmafx, ffmpeg, docs

## Context

[ADR-0110](0110-avoption-control-plane-contract.md) froze the ten autotune-relevant `AVOption` names, types, and ranges of `vf_pelorus_deband_vulkan` in [docs/api/control-plane.md](../api/control-plane.md) so that VMAFx (pronounced *vee-mafx*, from Video Multi-Method Assessment Fusion extended; the sibling perceptual quality and autotune project) could bind its `vmaf-tune` optimizer against a stable control-plane surface ([ADR-0106](0106-autotune-control-plane.md)). At the time, Pelorus (pronounced *puh-LOR-uhs*, from the nautical pelorus bearing instrument that keeps a vessel on course) shipped a single filter, so ADR-0110 deferred a machine-readable schema until the filter inventory grew.

Pelorus now ships ten video filters (`pelorus_aa_vulkan`, `pelorus_analyze_vulkan`, `pelorus_borderfix_vulkan`, `pelorus_deband_vulkan`, `pelorus_deblock_vulkan`, `pelorus_dehalo_vulkan`, `pelorus_denoise_vulkan`, `pelorus_grain_estimate_vulkan`, `pelorus_mc_vulkan`, and `pelorus_scenecut`) plus the `pelorus_fgs` bitstream filter (`h265_pelorus_fgs_bsf.c`), and six frame-writing Vulkan filters (`pelorus_aa_vulkan`, `pelorus_borderfix_vulkan`, `pelorus_deband_vulkan`, `pelorus_deblock_vulkan`, `pelorus_dehalo_vulkan`, and `pelorus_denoise_vulkan`) share pool options via `PEL_VK_POOL_OPTIONS` in `ffmpeg-patches/files/pelorus_vulkan_pool.h` ([ADR-0184](0184-vulkan-drm-modifier-output-pools.md)). As recorded in [docs/research/0172-vmafx-scope-gap.md](../research/0172-vmafx-scope-gap.md) gap G7, VMAFx's `FilterAdapter` protocol (VMAFx ADR-1116, `pkg/prefilter`) is ready to generate prefilter adapters beyond deband, but still hard-codes only `pelorus_deband_vulkan` because the other filters lack published, machine-checkable knob contracts.

Without a checked contract per filter, VMAFx must either duplicate C `AVOption` bounds by hand across ten filters or parse FFmpeg help text at runtime, and any option rename, range tightening, or enum constant change in `ffmpeg-patches/files/` can silently desynchronize the optimizer from the patch stack.

## Decision

We will publish **one frozen JSON knob contract per filter and bitstream filter under `docs/api/knobs/<filter>.json`** (`schema_version: 1`), keyed by the canonical FFmpeg filter or bitstream-filter `.p.name`, and enforce parity against the C `AVOption` tables with `scripts/check-filter-knob-contracts.py` in `make docs-check`:

1. **Per-filter JSON layout (`docs/api/knobs/<filter>.json`)**: one file per filter is chosen over a monolithic `docs/api/knobs.json` so that diffs isolate to the touched filter, missing or stale filter contracts fail by filename, and VMAFx `FilterAdapter` generators can load individual filter contracts on demand.
2. **Option schema and `effect_class` taxonomy**: each option record carries stable keys (`name`, `type`, `min`, `max`, `default`, `unit`, `constants`, `effect_class`, `tunable`, `help`). `type` normalizes `AV_OPT_TYPE_*` into `int`, `int64`, `double`, `float`, `bool`, `string`, `flags`, or `const-enum` (when an `AV_OPT_TYPE_INT` option has associated `AV_OPT_TYPE_CONST` rows sharing `.unit`). Every option is classified in `EFFECT_CLASS_MAP` in `scripts/check-filter-knob-contracts.py` into one of four `effect_class` values:
   - `strength` (`tunable: true`) — continuous or discrete perceptual-intensity, threshold, or grain/dither mode knobs that `vmaf-tune` may sweep;
   - `shape` (`tunable: false`) — algorithm-geometry or model-selection choices (`sample`, `blur`, `patch`, `prev`, `bsize`, `search`, `cell`, `model`, `left`/`right`/`top`/`bottom`) fixed per run outside the inner search loop;
   - `topology` (`tunable: false`) — pipeline, hardware-pool (`tiling`, `drm_modifiers` from `PEL_VK_POOL_OPTIONS`), execution-path (`fast`, `tile`, `mc`, `lookahead`), plane-mask (`planes`, `components`), or bitstream/keyframe plumbing (`force_idr`, `persistence`, `skip_existing`) options;
   - `reporting` (`tunable: false`) — data-plane side-data emission switches (`meta`, `roi`, `maps`, `native`).
3. **Authoritative deband table parity**: the frozen and excluded tables for `pelorus_deband_vulkan` in [docs/api/control-plane.md](../api/control-plane.md) remain authoritative under [ADR-0110](0110-avoption-control-plane-contract.md); `scripts/check-filter-knob-contracts.py` parses `docs/api/control-plane.md` and asserts that `docs/api/knobs/pelorus_deband_vulkan.json` agrees with its frozen (`strength` / `tunable: true`) vs excluded (`tunable: false`) split, types, bounds, and defaults.
4. **Drift gate (`scripts/check-filter-knob-contracts.py`)**: stdlib Python (`python3 -I`) parses `ffmpeg-patches/files/*.c`, expands `PEL_VK_POOL_OPTIONS` from `ffmpeg-patches/files/pelorus_vulkan_pool.h`, resolves named `#define` and `enum` constants per source file (the `.c` file first, then the shared headers it includes under `ffmpeg-patches/files/` and `libpelorus/include/`, failing with exit code `2` on unresolved or conflicting constant definitions), supports `--write` to regenerate contracts and `--self-test` with planted defects, and runs in `make docs-check`.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Per-filter JSON contracts `docs/api/knobs/<filter>.json` + C `AVOption` drift gate (this ADR) | Machine-readable for VMAFx `FilterAdapter` codegen; per-filter diffs; catches range/default/enum/missing-filter drift in `make docs-check` without building FFmpeg; keeps `AVOption` in C as source of truth | Adds 11 small JSON files and a stdlib Python parser to maintain | **Chosen** |
| Single monolithic `docs/api/knobs.json` for all filters | Single file to open | Larger merge conflicts when unrelated filter PRs touch options; harder to spot a missing or retired filter by path alone | Rejected — per-filter files map 1:1 to `.p.name` and keep PR diffs scoped |
| Hand-written Markdown tables per filter (extending ADR-0110's deband table to all 11 units without JSON) | Human-readable in prose | Not directly consumable by VMAFx `FilterAdapter` generators without scraping Markdown; error-prone across 90+ options | Rejected — Markdown prose in `control-plane.md` summarizes and links the contracts, while JSON carries the machine contract |
| Runtime introspection via `ffmpeg -h filter=<name>` in CI or VMAFx | Uses FFmpeg's own option printer | Requires a full patched FFmpeg + Vulkan build just to read static option metadata; `ffmpeg -h` output omits `effect_class` / `tunable` semantics and is not an offline artifact VMAFx can vendor or read at codegen time | Rejected — violates the fast CPU-only docs gate requirement and provides no `effect_class` split |

## Consequences

- **Breaking vs backward-compatible contract changes**:
  - **Breaking** — renaming, removing, narrowing the `[min, max]` range of, changing the `type` of, or removing/renumbering an enum constant of any knob with `tunable: true` (`effect_class: "strength"`), or flipping a knob from `tunable: true` to `false`. Requires a coordinated two-repository PR (Pelorus + VMAFx `FilterAdapter` / `vmaf-tune`) and a note in `docs/rebase-notes.md`.
  - **Backward-compatible** — widening `[min, max]`, adding a new `tunable: true` knob, or changing `tunable: false` (`shape`, `topology`, `reporting`) knobs. Can land in Pelorus alone as long as any new option's `effect_class` is added to `EFFECT_CLASS_MAP` in `scripts/check-filter-knob-contracts.py` and `python3 -I scripts/check-filter-knob-contracts.py --write` updates `docs/api/knobs/<filter>.json` in the same commit.
- **Positive**: VMAFx workstream G7 can generate `FilterAdapter` definitions for all Pelorus filters from `docs/api/knobs/*.json`, filtering on `tunable: true` for the optimizer search space and exposing `shape`/`topology`/`reporting` options as fixed adapter configuration. Any C `AVOption` edit in `ffmpeg-patches/files/` that drifts from `docs/api/knobs/*.json` or `docs/api/control-plane.md` fails `make docs-check` immediately.
- **Negative**: `EFFECT_CLASS_MAP` in `scripts/check-filter-knob-contracts.py` is the source of truth for option classification, so adding a new Pelorus filter or a new `AVOption` row in `ffmpeg-patches/files/` requires adding its `effect_class` entry in `scripts/check-filter-knob-contracts.py` before running `python3 -I scripts/check-filter-knob-contracts.py --write`.

## References

- [ADR-0106](0106-autotune-control-plane.md) — VMAFx control-plane coupling.
- [ADR-0110](0110-avoption-control-plane-contract.md) — frozen `vf_pelorus_deband_vulkan` control-plane contract.
- [ADR-0184](0184-vulkan-drm-modifier-output-pools.md) — shared `PEL_VK_POOL_OPTIONS` (`tiling`, `drm_modifiers`).
- [docs/api/control-plane.md](../api/control-plane.md) — control-plane contract and per-filter JSON schema.
- [docs/research/0172-vmafx-scope-gap.md](../research/0172-vmafx-scope-gap.md) — gap G7 (`FilterAdapter` protocol across all Pelorus filters).
- Issue: [#158](https://github.com/VMAFx/pelorus/issues/158).
