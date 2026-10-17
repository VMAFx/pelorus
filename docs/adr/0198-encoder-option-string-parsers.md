<!-- markdownlint-disable MD013 MD060 -->
# ADR-0198: Option-string parsers for x264, x265, SVT-AV1 and libaom normalize pass-through CLI and FFmpeg parameters into canonical encode record parameters

- **Status**: Accepted
- **Implementation**: landed (#156: x264 and x265 in part 1, SVT-AV1 and libaom in part 2)
- **Date**: 2026-10-17
- **Deciders**: Lusoris
- **Tags**: provenance, encode-record, parsers, x264, x265, svt-av1, libaom
- **Related**: [ADR-0175](0175-encode-provenance-record.md)

## Context

Issue #81 introduced the encode provenance record ([ADR-0175](0175-encode-provenance-record.md)), which binds a clip's encoder settings, filter pipeline, input hash, and hardware path into a canonical JSON object whose SHA-256 digest is embedded in side data and SEI bitstreams. In ADR-0175, encoder parameters in `encoder.params` were defined as key-value pairs sorted by key with canonical scalar forms: integers as plain decimal text, booleans as `"true"` or `"false"`, reals as `"f64:<16 lower-case hex>"`, and other values verbatim.

However, real-world encoders receive options through diverse mechanisms. When encoding via CLI or FFmpeg's `libx264` and `libx265` wrappers, parameters are passed as free-form, colon-separated option strings (such as `-x264-params` and `-x265-params`, e.g. `crf=24:b-adapt=2:ref=4`). These option strings introduce significant variability:

1. **Multiple spellings and aliases**: Options have aliases (e.g. x264 `b-adapt` vs `b_adapt`, `frameref` vs `ref`, `bframe` vs `bframes`; x265 `ip-factor` vs `ipratio`, `pb-factor` vs `pbratio`).
2. **Negation flags**: Boolean options can be asserted as positive flags or negated using `no-` or `no` prefixes (e.g. `no-cabac` vs `cabac=0`).
3. **Flexible boolean spellings**: Encoders accept various boolean literals (`1`/`0`, `true`/`false`, `yes`/`no`), sometimes with differing case-sensitivity rules.
4. **Ordering**: Different callers order option pairs arbitrarily (`crf=24:b-adapt=2` vs `b-adapt=2:crf=24`).
5. **Overridden options**: Options can be specified more than once, with the last occurrence taking effect.

To ensure that identical encoder configurations produce identical canonical records and bitstream digests regardless of how options were spelled or ordered, issue #156 requires option-string parsers for x264 and x265 in libpelorus.

## Decision

We will implement dedicated option-string parsers for x264 and x265 in libpelorus (`pel_encode_options_parse` in `libpelorus/src/encode_options.c`) backed by static compile-time dialect tables derived directly from pinned upstream source code.

1. **Parser API and storage model**:
   - `pel_encode_options_parse(pel_encode_options_dialect d, const char *text, size_t len, PelorusEncodeOptions *out)`.
   - The caller allocates `PelorusEncodeOptions` (measured `sizeof(PelorusEncodeOptions)` = 278552, ~272 KiB; allocate on heap).
   - `out` contains fixed arrays `params` and `unknown_params` of `PelorusEncodeParam` sized to the record bounds (256 entries each), accompanied by internal pointer arrays and a 256 KiB character arena.
   - Pointers in `params` and `unknown_params` point directly into `out`'s internal arena. No heap memory is allocated by the library.
   - Outputs are sorted by key bytes in ascending order.
2. **Tokenizer and escaping**:
   - Input strings follow FFmpeg `av_dict_parse_string` and `av_get_token` rules: options delimited by `:`, key-value pairs delimited by `=`.
   - Single quotes (`'...'`) treat characters literally. Backslash (`\`) escapes the following character.
   - Leading/trailing whitespace around keys and values is trimmed.
   - Malformed inputs (empty key, missing `=`, empty value) return `PEL_ERR_INVALID`. An unterminated single quote is not an error: the rest of the string is copied literally, as FFmpeg `av_get_token` does (`libavutil/avstring.c`).
3. **Dialect tables and typing**:
   - Pinned sources: x264 at `b35605ace3ddf7c1a5d67a2eb553f034aef41d55` (`common/base.c: x264_param_parse`, 142 entries); x265 at `e444744c03978c1fb4e037168967020cf2648427` (`source/common/param.cpp: x265_param_parse`, 233 entries).
   - Each table maps canonical keys and aliases to their declared parameter type (`BOOL`, `INT`, `FLOAT`, `STRING`).
4. **Normalisation rules**:
   - Keys: underscores `_` convert to hyphens `-`; x265 strips leading `--`; aliases resolve to the primary canonical key.
   - Negations: `no-` or `no` prefix on known boolean options maps to the positive canonical key with inverted boolean value; the prefix is stripped before the name lookup, as both encoders do. The encoders also pass `"true"`/`"false"` to negated non-boolean options; the record does not type those and keeps them in `unknown_params` under the normalised key.
   - Values:
     - Boolean: `1`/`0`, `true`/`false`, `yes`/`no` normalise to `"true"` or `"false"` (x264 case-insensitive words; x265 case-sensitive matching upstream `atobool`).
     - Integer: parsed with base-0 `strtoll`, as both encoders' `strtol(str, &end, 0)` does. Upstream octal handling (`08` rejected by `strtol`) is preserved: rejected integers fail type validation. Valid integers format as plain decimal via `pel_encode_record_int()`.
     - Float: parsed with `strtod` and formatted as `"f64:<16 lower-case hex>"` via `pel_encode_record_f64()`.
     - Other: preserved verbatim after unescaping.
   - Duplicates: last occurrence wins across aliases and negation.
   - Unknown options and rejected values: unknown keys (under the normalised key) or known keys with values invalid for their type (under the canonical key) keep their value verbatim in `unknown_params`; the last occurrence wins.

### Part 2: SVT-AV1 and libaom

FFmpeg n9.0.2 hands both option strings straight to the encoder library: `libavcodec/libsvtav1.c:349` calls `svt_av1_enc_parse_parameter` for each `-svtav1-params` entry, and `libavcodec/libaomenc.c:1036` calls `aom_codec_set_option` for each `-aom-params` entry. The record follows those two functions, not the encoders' command-line front ends.

1. **Pinned sources**:
   - SVT-AV1 `v4.2.0` (`9292ec8e32bce26f781f277ec8739b53426c4300`): `Source/Lib/Globals/enc_settings.c: svt_av1_enc_parse_parameter` (lines 2125-2456); 121 entries in `libpelorus/src/encode_options_svtav1.c`.
   - libaom `v3.15.2` (`af3dc9aadc793c1b00edc003e3fc19a44fdeb574`): `av1/av1_cx_iface.c: encoder_set_option` (lines 4437-4937) with names from `av1/arg_defs.c` and parse helpers from `common/args_helper.c`; 122 entries in `libpelorus/src/encode_options_aom.c`.
2. **A dialect rules row per encoder** (`k_dialects` in `libpelorus/src/encode_options.c`): integer base, boolean case rule, underscore folding, `--` stripping and negation. x264 and x265 keep the part 1 behaviour.
   - SVT-AV1: names compare exactly (no `_` folding, no `--` stripping, no `no-` negation); aliases `w` → `width`, `h` → `height`, `q` → `qp`; integers base 0 (`strtol`/`strtoul`); booleans `1`/`0`, `true`/`false`, `yes`/`no` without regard to case; `crf`, `cqp` and `ac-bias` are reals.
   - libaom: the long name compares exactly (no aliases, no short names, no folding, no negation); integers base 10, as `arg_parse_uint_helper` and `arg_parse_int_helper` call `strtoul`/`strtol` with base 10; enum, rational, list and string options stay verbatim, because the enum helper accepts names and numbers and the record does not map one to the other.
3. **Unsigned type** (`PEL_OPT_TYPE_UINT`): both libraries reject a negative value for unsigned options (SVT-AV1 `str_to_uint`, libaom `arg_parse_uint_helper` through the `strtoul` sign check), so a negative value lands in `unknown_params`. Unsigned values up to 4294967295 are accepted; a larger value returns `PEL_ERR_RANGE`. The record does not check the narrower `uint8_t` and `int8_t` widths SVT-AV1 clamps to.
4. **Build-configuration gates**: options compiled in by default upstream are included (SVT-AV1 `enable-qm` under `CONFIG_ENABLE_QUANT_MATRIX`, libaom denoise options under `CONFIG_DENOISE`). libaom options behind `CONFIG_TUNE_VMAF` and `CONFIG_TUNE_BUTTERAUGLI`, both off by default, are left out; on such a build they land in `unknown_params` with their values intact.
5. **Dialect values**: `PEL_ENCODE_OPTIONS_SVTAV1` = 3 and `PEL_ENCODE_OPTIONS_AOM` = 4 append to the public enum; existing values do not move.

## Alternatives considered

### Implementation approach

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Static dialect tables in libpelorus (chosen) | Zero external dependencies; deterministic across platforms; fast binary search lookup; no GPL link contagion | Tables must be updated when upstream encoders add options | Chosen (#156) |
| Call `x264_param_parse` / `x265_param_parse` at runtime | 100% upstream fidelity; automatically tracks installed library version | Links libx264/libx265 into libpelorus; GPL copyleft contagion; requires encoders present at build and run time; impossible in headless filter pipelines without encoders | Violates hard rule 9 (no heavy dependencies) and dependency-free libpelorus contract |
| Keep option text verbatim without parsing | Zero maintenance; trivial implementation | Cannot normalize aliases, negation, or boolean spellings; identical encodes produce different digests; defeats provenance goal | Defeats issue #81 provenance repeatability |
| Parse into dynamic key-value map on heap | Dynamic capacity; smaller stack footprint | Allocates on heap; potential allocation failure in critical paths | Violates hard rule against library heap allocations |

## Consequences

- **Positive**:
  - Encodes using different option orderings or alternative spellings (e.g. `b_adapt=2` vs `b-adapt=2`, `ref=4` vs `frameref=4`) produce byte-identical `encode_record` JSON documents and matching SHA-256 digests.
  - Zero heap allocation and reentrant execution ensure safety in real-time filter pipelines.
  - Unknown options are never lost: they are preserved in `unknown_params` for downstream inspection.
- **Negative**:
  - Option tables must be periodically updated when upstream x264, x265, SVT-AV1 or libaom adds new options.
  - `PelorusEncodeOptions` is ~272 KiB (measured `sizeof(PelorusEncodeOptions)` = 278552), so callers should allocate it on the heap.
- **Neutral / follow-ups**:
  - NVENC requires no option string parser because its options are mapped directly via FFmpeg `AVOption`s.

## References

- Issue #81; Issue #156; [ADR-0175](0175-encode-provenance-record.md).
- Upstream x264 repository at `b35605ace3ddf7c1a5d67a2eb553f034aef41d55`: `common/base.c` (`x264_param_parse`).
- Upstream x265 repository at `e444744c03978c1fb4e037168967020cf2648427`: `source/common/param.cpp` (`x265_param_parse`).
- Upstream SVT-AV1 `v4.2.0` at `9292ec8e32bce26f781f277ec8739b53426c4300`: `Source/Lib/Globals/enc_settings.c` (`svt_av1_enc_parse_parameter`).
- Upstream libaom `v3.15.2` at `af3dc9aadc793c1b00edc003e3fc19a44fdeb574`: `av1/av1_cx_iface.c` (`encoder_set_option`), `av1/arg_defs.c`, `common/args_helper.c`.
- FFmpeg n9.0.2: `libavcodec/libsvtav1.c` and `libavcodec/libaomenc.c` (option pass-through).
- FFmpeg n9.0.2: `libavutil/dict.c` (`av_dict_parse_string`) and `libavutil/avstring.c` (`av_get_token`).
