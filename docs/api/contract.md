<!-- markdownlint-disable MD013 -->
# Pelorus 1.0 public contract

Pelorus 1.0 freezes the names that applications, FFmpeg command lines, and
metadata readers depend on. A filter's proven or experimental stance describes
quality evidence only; it does not weaken this compatibility promise. The
decision is [ADR-0194](../adr/0194-stable-contract-1-0.md).

The exact list lives in
[`libpelorus/frozen-surface.txt`](../../libpelorus/frozen-surface.txt). The
repository gate rebuilds the current list from the source and fails when a name
is removed or when a new name has not been recorded.

## What is frozen

- Headers installed by `libpelorus/meson.build`, all public declarations in
  them, exported `pel_*` functions, and the existing `pelorus_version*`
  functions. The C API grows append-only: existing prototypes, struct layouts,
  enum and result-code values, macros, and documented meanings do not change.
  The manifest lists headers and exported functions only; declarations inside
  the headers are held by review and the interop fixture, not by the checker.
- All ten `vf_pelorus_*` filter names and the `pelorus_fgs` bitstream-filter
  name.
- Every AVOption name and named enum/flags constant on those filters. Constants
  appear in the manifest as `filter.option=constant`.
- Every encoder AVOption the patch stack adds to stock FFmpeg encoders:
  `pelorus_roi`, `pelorus_film_grain`, `pelorus_me_hints`, and `udu_sei`. An
  option's stance does not change this freeze.
- Every emitted `lavfi.pelorus.*` metadata key.
- A released FFmpeg patch stack as a whole and its `series.txt` replay order.
  Individual `NNNN-` patch filenames are not frozen.

The `PelorusSideData` wire ABI retains its additional append-only rules and
conformance fixture; see [interop ABI](interop-abi.md).
The non-wire parameter and version APIs have a runnable introduction in
[C API parameter contracts](parameter-contracts.md).

## Add a compatible surface

Additions are allowed. Add the header, function, filter, option, constant, or
metadata key in its canonical source, then regenerate the manifest:

```bash
python3 -I scripts/check-frozen-surface.py --write
python3 -I scripts/check-frozen-surface.py
```

Review the manifest diff. Document the new surface and add a changelog fragment
in the same change. `--write` preserves the `[exempt]` section; it does not make
an unreviewed addition part of the contract by itself.

## Deprecate or remove a name

Mark a frozen name deprecated in its user documentation and in the changelog.
It must remain available for at least one complete minor release after that
notice. Removal is allowed only at a major release; a rename counts as removal
plus addition. A breaking change also needs a major version and soversion bump,
a conventional-commit `!`, and a `Migration:` footer. C API migrations include
before-and-after snippets.

## Exemptions

The exemption list is empty. A future exception needs a superseding ADR that
names exactly one surface, its reason, and an expiry date, plus a matching line
under `[exempt]`:

```text
kind name expiry=YYYY-MM-DD reason=why this one name is temporarily exempt
```

Kinds are `header`, `symbol`, `filter`, `avoption`, `encoder-avoption`, and
`metadata-key`. An exemption suppresses only a missing-source (`removed`)
finding for that name.
After the expiry date the gate fails until the exception is removed or replaced
by a newly decided one.

## Moving from soversion 0

The 1.0 contract changes the dynamic-link SONAME from `libpelorus.so.0` to
`libpelorus.so.1`. An executable linked against soversion 0 does not
automatically load soversion 1: rebuild or relink it against the 1.x library.
Systems may co-install the two SONAMEs, but Pelorus does not promise a maintained
soversion-0 compatibility package. The source transition is additive; code that
uses only the frozen public headers should rebuild without source changes.

## Patch-stack consumers

Apply the released filenames in `ffmpeg-patches/series.txt` order. Patch files
may be renumbered, merged, or split between releases, and a retired number is
not reused silently: the release changelog records it. Never infer ordering or
identity from a patch number outside its tagged release.
