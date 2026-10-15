<!-- markdownlint-disable MD013 MD060 -->
# ADR-0194: Freeze the Pelorus 1.0 public contract and use soversion 1

- **Status**: Accepted
- **Date**: 2026-10-15
- **Deciders**: Lusoris
- **Tags**: api, abi, ffmpeg, release, compatibility

## Context

Pelorus 1.0 needs one compatibility boundary that adopters can inspect before
they build against `libpelorus` or apply the FFmpeg patch stack. The project
already protects the `PelorusSideData` wire format with append-only rules and a
shared conformance fixture, and it separately freezes the deband options that
vmafx's autotuner uses. It has not yet defined the complete public surface for
all filters, metadata, headers, and exported functions.

Filter stance does not answer that compatibility question. "Proven" and
"experimental" describe the available quality evidence ([#154](https://github.com/VMAFx/pelorus/issues/154));
both kinds are already user-discoverable FFmpeg interfaces. Letting stance
control name stability would make an experimental-to-proven promotion a
compatibility event unrelated to the interface itself.

Patch filenames also need a boundary. The numbered files are a generated
delivery detail, while consumers apply a released stack in `series.txt` order.
Measured on 2026-10-15: VMAFx/vmafx at origin commit `f24730e` had no use of a
Pelorus patch by number; VMAFx/ffmpeg-patches at commit `fa58e72` applied the
Pelorus stack through `scripts/consumer_stacks.py` in `series.txt` order; a
GitHub code search found no other adopter.

## Decision

### Frozen surfaces

The 1.0 contract covers all of these surfaces, regardless of a filter's proven
or experimental stance:

- every header installed by `libpelorus_headers` in `libpelorus/meson.build`,
  every public declaration in those headers, every exported `pel_*` function,
  and the existing `pelorus_version*` functions;
- the names of all ten `vf_pelorus_*` filters and the `pelorus_fgs` bitstream
  filter: `pelorus_deband_vulkan`, `pelorus_analyze_vulkan`,
  `pelorus_denoise_vulkan`, `pelorus_grain_estimate_vulkan`,
  `pelorus_mc_vulkan`, `pelorus_dehalo_vulkan`, `pelorus_aa_vulkan`,
  `pelorus_deblock_vulkan`, `pelorus_borderfix_vulkan`, `pelorus_scenecut`, and
  `pelorus_fgs`;
- every AVOption name on those filters and every named `AV_OPT_TYPE_CONST`
  value accepted for an option;
- every encoder AVOption the patch stack adds to stock FFmpeg encoders:
  `pelorus_roi`, `pelorus_film_grain`, `pelorus_me_hints`, and `udu_sei`; an
  option's stance does not change this freeze;
- every `lavfi.pelorus.*` metadata key emitted by the canonical filter sources;
  and
- the tagged FFmpeg patch stack as a whole and `series.txt` as its replay order.

`libpelorus/frozen-surface.txt` lists every installed header, exported
function, filter name, AVOption name and constant, encoder AVOption, and
metadata key. It does not list types, struct fields, enum values, result codes
or macros: those are frozen by the append-only rule below and checked by the
`c-reviewer` and `interop-abi-reviewer` reviews and the interop fixture, not by
this checker.
`scripts/check-frozen-surface.py` derives the current surface from the installed
header list and canonical FFmpeg sources. A removed manifest entry, an unlisted
source addition, or an expired exemption fails `docs-check`. Additions are
compatible, but must be recorded with the checker's `--write` mode and reviewed.

Patch filenames and their `NNNN-` numbers are not part of the contract. Between
releases, patches may be renumbered, merged, or split. Consumers apply the
released `series.txt` in order; a retired number is recorded in the changelog.
A published tag and its stack remain immutable.

The installed library uses soversion `1`. Public functions, struct fields, enum
values, result codes, and macros in installed headers evolve append-only: an
existing declaration keeps its type, value, layout, and meaning. The interop
ABI keeps its stricter existing
append-only rules: fields and sections are appended, never reordered, resized,
removed, or repurposed; the shared fixture in `libpelorus/test/interop_test.c`
continues to prove older-reader compatibility ([ADR-0183](0183-sidedata-zero-free-carrier.md)).

### Deprecation and removal

Options, functions, filters, constants, metadata keys, and headers may be added
freely when the addition is backward compatible. A frozen name may be removed
only at a major release and only after at least one complete minor release in
which the name is marked deprecated in both the relevant documentation and the
changelog. Renaming is removal plus addition and follows the same rule.

Any breaking change after 1.0 requires a major version and soversion bump, a
conventional-commit subject containing `!`, and a `Migration:` footer with
before-and-after C examples where the C API changes (HISS-14).

### Exemption list

No names are exempt.

An exemption requires a superseding ADR entry for exactly one name, with a
reason and an expiry date, plus the matching `kind name expiry=YYYY-MM-DD
reason=...` entry in the manifest's `[exempt]` section. It suppresses only the
`removed` finding for that name. Once its date has passed, the checker fails it.

## Alternatives considered

- **Exempt experimental filters until their stance becomes proven.** Rejected:
  stance is a quality-evidence label, not an interface-stability label. It would
  leave already shipped names unsafe and couple compatibility to evidence work.
- **Freeze each numbered patch filename.** Rejected: numbers describe one
  generated decomposition of the stack. No measured consumer addressed patches
  by number; freezing them would prevent harmless renumbering, merging, and
  splitting without improving replay compatibility.

## Consequences

- Adopters have one manifest and one checker for the complete 1.0 name surface.
- Every new public name is allowed immediately but must enter the manifest in
  the same change; accidental removals and renames fail locally and in CI.
- Experimental filters keep the same compatibility promise as proven filters;
  their stance can change without renaming their interface.
- Patch-stack maintainers may reorganize generated patches between releases,
  while consumers retain deterministic replay through `series.txt`.
- The soversion change intentionally moves dynamic linking from
  `libpelorus.so.0` to `libpelorus.so.1`.

## References

- [Issue #152](https://github.com/VMAFx/pelorus/issues/152),
  [#154](https://github.com/VMAFx/pelorus/issues/154),
  [#155](https://github.com/VMAFx/pelorus/issues/155),
  [#157](https://github.com/VMAFx/pelorus/issues/157), and
  [#159](https://github.com/VMAFx/pelorus/issues/159).
- [ADR-0183](0183-sidedata-zero-free-carrier.md): append-only interop ABI 1.5 and
  zero-free carrier.
- [ADR-0185](0185-shared-ffmpeg-fix-series.md): tagged shared-series pin and
  ordered replay model.
- [Interop ABI](../api/interop-abi.md) and its conformance fixture,
  `libpelorus/test/interop_test.c`.
