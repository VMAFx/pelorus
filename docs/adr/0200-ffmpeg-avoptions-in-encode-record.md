<!-- markdownlint-disable MD013 MD060 -->
# ADR-0200: The encode record stores FFmpeg encoder AVOptions under the `ffmpeg:` prefix, verbatim

- **Status**: Accepted
- **Implementation**: landed (#352)
- **Date**: 2026-10-17
- **Deciders**: Lusoris
- **Tags**: provenance, encode-record, ffmpeg, avoptions
- **Related**: [ADR-0175](0175-encode-provenance-record.md), [ADR-0198](0198-encoder-option-string-parsers.md)

## Context

[ADR-0198](0198-encoder-option-string-parsers.md) normalises the option strings `-x264-params`, `-x265-params`, `-svtav1-params` and `-aom-params` into encode record parameters. Most encoder settings in an FFmpeg command line are not in those strings: `-preset`, `-crf`, `-b:v`, `-g` and every rate-control option of the NVENC, QSV, VAAPI and Vulkan encoders are FFmpeg AVOptions. Without them the record of a hardware encode holds no encoder settings, and two software encodes that differ only in `-preset` get the same digest.

An AVOption can also set the same encoder field as an option-string key: FFmpeg's `libx264` sets `crf` from the AVOption and then applies `-x264-params crf=...`, which wins. Recording both would give `-crf 30 -x264-params crf=20` a different digest from `-x264-params crf=20`, although the encoder runs with the same settings.

## Decision

1. **API**: `pel_encode_options_from_ffmpeg(encoder, opts, nb_opts, out)` in `libpelorus/src/encode_avoptions.c` takes the encoder name and the AVOption name/value pairs the host passed to the encoder, with stream specifiers already stripped (`b`, not `b:v`). The option-string entry (`x264-params` and the others) is parsed with the ADR-0198 parser and its keys stay bare. Each other option is stored as `ffmpeg:<name>` with its value verbatim: no unit conversion, no `k`/`M` suffix expansion, no alias folding.
2. **Known and unknown names**: a name in the encoder's table goes to `params`; any other name goes to `unknown_params`, still as `ffmpeg:<name>`, so it still counts in the digest. Tables cover 16 encoders: `libx264`, `libx265`, `libsvtav1`, `libaom-av1` and the NVENC, QSV, VAAPI and Vulkan encoders for H.264, HEVC and AV1. Any other encoder returns `PEL_ERR_UNSUPPORTED`.
3. **Generated tables**: `scripts/encode-options/avoption-probe.c` prints the AVOptions of `AVCodecContext` and of each encoder's private class from the installed FFmpeg (n9.0.2). `scripts/encode-options/gen-avoption-tables.py` keeps every non-constant private option plus every `AVCodecContext` option whose field the encoder's FFmpeg source reads as `avctx-><field>`, and writes `libpelorus/src/encode_avoptions_tables.c` with the sha256 of each input in its header. Counts: libx264 79, libx265 35, libsvtav1 20, libaom-av1 76; NVENC 67/69/65, QSV 73/72/44, VAAPI 38/38/34, Vulkan 35/35/32 (H.264/HEVC/AV1).
4. **Ignored host settings**: `threads`, `thread_type`, `gpu`, `passlogfile`, `stats`, `x265-stats`, `debug`, `dump_separator`, `err_detect`, `export_side_data` and `max_pixels` are not recorded. They set the thread layout, a device index, a stats-file path (the second pass reads the file content; the name is a host path) or diagnostics, not encoder settings.
5. **Clash pairs**: an AVOption is dropped only when the option string holds a valid value for a key the encoder applies after the AVOption, to a superset of the same fields. Six pairs are proven from the FFmpeg n9.0.2 and pinned encoder sources, and nothing applied after the string sets those fields again:

   | Encoder | AVOption = string key | AVOption applied | String applied |
   | --- | --- | --- | --- |
   | libx264 | `crf`, `qp` | `libx264.c:1123-1128` | `x264_param_parse` at `libx264.c:1383`; x264 `common/base.c:1313-1322` |
   | libx265 | `crf`, `qp` | `libx265.c:431-443` | `x265_param_parse` at `libx265.c:520`; x265 `common/param.cpp:857-866` |
   | libsvtav1 | `crf` | `libsvtav1.c:256-258` (qp, rate-control mode) | `svt_av1_enc_parse_parameter` at `libsvtav1.c:348`; SVT-AV1 `enc_settings.c:1499` (qp, rate-control mode, aq mode, offset) |
   | libaom-av1 | `cpu-used` | `AOME_SET_CPUUSED` at `libaomenc.c:983` | `aom-params` at `libaomenc.c:1035`; libaom `av1_cx_iface.c:4474` |

   SVT-AV1 `qp` is not a pair: the AVOption also sets `aq_mode` to 0 (SVT-AV1 4 and later) and the string `qp` does not, so both stay. An invalid string value (it lands in `unknown_params`) does not drop the AVOption, because the encoder rejects that string entry. Names that only look alike (x264 `aq-mode`) stay in both forms until a test proves the pair.
6. **Collision guard, order and bounds**: an AVOption name containing `:`, an empty name or value, or an option-string key starting with `ffmpeg:` returns `PEL_ERR_INVALID`, so the two namespaces cannot meet. A repeated name keeps its last value, as FFmpeg's `AVDictionary` does; this includes the option-string entry. Keys above 64 bytes (names above 57 bytes) and values above 1024 bytes return `PEL_ERR_RANGE`. On any error both arrays are empty.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| `ffmpeg:` prefix, verbatim value (chosen) | No collision with option-string keys; a value reads back as typed; no unit logic to keep in step with FFmpeg | `-b:v 5M` and `-b:v 5000000` give different digests | Chosen (decision `pelorus-352-avopt-keys`) |
| Bare AVOption names merged with option-string keys | Shorter keys | x264 `crf` from the AVOption and from the string become one key with two sources; silent overwrite | Hides which source set a value |
| Convert values to canonical numbers (`5M` to `5000000`) | Equal settings, equal digest | Re-implements `av_opt_set` parsing per type, unit and named constant; drifts with FFmpeg | Extra code with its own failure modes |
| Record every AVOption, no ignore list | Nothing omitted | `threads` and the stats path change per host; the same encode gets another digest on another machine | Defeats repeatable provenance |
| Hand-written tables | No generator | 16 encoders, about 800 names; easy to miss one | Generated tables carry their input hashes |

## Consequences

- **Positive**: hardware encodes get encoder settings in the record; `-preset` and `-crf` change the digest; `-crf 30 -x264-params crf=20` and `-x264-params crf=20` give the same digest.
- **Negative**: values are not canonicalised, so equal settings spelled differently (`5M`, `5000k`) give different digests. Aliases are not folded (`me_method` and `motion-est` stay two names). libx264's older option string `x264opts` is stored verbatim as `ffmpeg:x264opts`, not parsed, so an AVOption it overrides stays in the record; the digest still follows its content. The `avctx-><field>` grep can list a field an encoder reads but ignores; such a name is still recorded, which only costs a key. Tables are regenerated by hand when the FFmpeg pin moves.
- **Neutral**: the caller strips stream specifiers and passes only the options meant for the encoder.

## References

- Issue #352; decision `pelorus-352-avopt-keys` (2026-10-17).
- FFmpeg n9.0.2: `libavcodec/options_table.h`, `libavcodec/libx264.c`, `libavcodec/libx265.c`, `libavcodec/libsvtav1.c`, `libavcodec/libaomenc.c`, `libavcodec/nvenc*.c`, `libavcodec/qsvenc*.c`, `libavcodec/vaapi_encode*.c`, `libavcodec/vulkan_encode*.c`, `libavcodec/hw_base_encode*.c`.
- x264 `b35605ace3ddf7c1a5d67a2eb553f034aef41d55`, x265 `e444744c03978c1fb4e037168967020cf2648427`, SVT-AV1 `v4.2.0`, libaom `v3.15.2` (pins of [ADR-0198](0198-encoder-option-string-parsers.md)).
