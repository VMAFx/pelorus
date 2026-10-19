<!-- markdownlint-disable MD013 MD060 -->
# Encode provenance record

> [!IMPORTANT]
> **Status: the record, its digest, the API, the `PEL_SEC_ENCODE_RECORD`
> section (interop ABI 1.4), and option-string parsers for x264, x265,
> SVT-AV1 and libaom, and FFmpeg encoder AVOptions for 16 encoders are
> implemented**
> ([ADR-0175](../adr/0175-encode-provenance-record.md),
> [ADR-0198](../adr/0198-encoder-option-string-parsers.md),
> [ADR-0200](../adr/0200-ffmpeg-avoptions-in-encode-record.md), issues #81,
> #156, #352). NVENC, QSV, VAAPI and Vulkan encoders have no option string:
> their settings are discrete FFmpeg AVOptions, recorded as `ffmpeg:<name>`.

## In one paragraph

An encode record says how a clip was encoded: the encoder and its normalised
settings, the input, the Pelorus filter chain with each filter's validated
parameters, and the hardware path. It is a small JSON document with one
canonical byte form and one SHA-256 digest. The digest has the form
`sha256:` followed by 64 lower-case hex digits, which is exactly what VMAFx
stores in a score's provenance (`encode_record`, VMAFx ADR-2073). Side data
carries only the digest and a locator, so it can ride every frame and, through
user-data-unregistered SEI, the bitstream.

## Members

All scalars are JSON strings ([canonical form](#canonical-form)). Required
members are marked.

| Member | Content |
|---|---|
| `schema` (required) | `pelorus/encode-record/1` |
| `encoder.name` (required) | encoder as the host names it, for example `libx265`, `hevc_nvenc` |
| `encoder.version` (required) | the encoder's own version string |
| `encoder.codec` (required) | FFmpeg codec name: `h264`, `hevc`, `vvc`, `av1`, `vp9` |
| `encoder.params` (required, may be empty) | known options, normalised: one spelling per option, typed values |
| `encoder.unknown_params` (required, may be empty) | options the encoder's table does not know: normalised key, verbatim value text |
| `input.digest` | `sha256:` and 64 hex digits of the input file bytes |
| `input.id` | an opaque caller identifier, when hashing the input is not possible |
| `filters[]` (required, may be empty) | the pre-encode chain in order; each entry has `name` (for example `pelorus_deband_vulkan`), `library` (`libpelorus` or the host library), `version`, `params`, `unknown_params` |
| `hardware.path` | `cpu`, `vulkan`, `nvenc`, `qsv`, `amf`, `vaapi` or `videotoolbox` |
| `hardware.device`, `hardware.driver` | device and driver as the API reports them |
| `elapsed_ns` | wall time of the encode in nanoseconds; outside the digest |
| `digest` | the computed digest; outside the digest |

At least one of `input.digest` and `input.id` is present. A new member in a
later `record_major` 1 revision is covered by the digest like any other: the
canonicaliser does not interpret the schema.

## Canonical form

The rules are VMAFx ADR-2073 item 2 (RFC 8785, keys in byte order, no
whitespace, strings escaping only `"`, `\` and control characters), with
restrictions that make every conforming writer, including Python's
`json.dumps`, produce the same bytes:

| Value | Canonical text (always a JSON string) |
|---|---|
| integer | plain decimal: `"16"`, `"-3"`; no `+`, no leading zeros |
| boolean | `"true"`, `"false"` |
| real (`float` or `double`) | `"f64:"` and the 16 lower-case hex digits of the IEEE-754 binary64 bits; a `float` widens to `double` exactly; `-0.0` becomes `+0.0`; NaN and infinity are rejected |
| per-plane value | an array of the strings above |
| string or enum | the UTF-8 text; invalid UTF-8 is rejected |

- Keys are printable ASCII (0x21..0x7e without `"` and `\`), 1 to 64
  bytes (an empty key is rejected), and unique per object. Byte order then equals RFC 8785's UTF-16
  order.
- Escapes are `\"`, `\\`, `\b`, `\t`, `\n`, `\f`, `\r`, and lower-case
  `\u00xx` for other control characters. `/`, DEL and non-ASCII characters
  are written as they are.
- Bounds (HISS-02): text at most 65 536 bytes, at most 64 filters, at most
  256 entries per `params` object, nesting depth at most 4, values at most
  1024 bytes. The canonicaliser does not read the schema, so it applies them
  generically: every object has at most 256 members, every array at most 64
  entries, at most 4 containers nest below the root object (the worked
  example's `thr` array is the fourth), keys are at most 64 bytes and string
  values at most 1024 bytes once decoded. The input text is bounded too.

**Normalisation.** Each encoder's parameter table maps aliases to one
spelling (for example x264 `b-adapt` and `b_adapt`) and declares each known
key's type. Option order does not matter, because `params` is an object. A
repeated option takes its last value. Filter `params` are the validated
contract values from `deband.h`, `denoise.h` and so on, not the option text.

## Digest

```text
digest = "sha256:" + hex(SHA-256(canonical(record without "digest" and "elapsed_ns")))
```

The digest is lower-case hex, 71 characters in all. It is the exact string
`vmafx_context_set_encode_record()` accepts (VMAFx
`core/include/vmafx/provenance.h`).

### Worked example

```json
{
  "digest": "sha256:b0201cec9782a58a59541103a7757fbd8221f67bd45a32f3b6a8d7f054d244bc",
  "elapsed_ns": "8123456789",
  "encoder": {
    "codec": "hevc",
    "name": "libx265",
    "params": {"aq-mode": "3", "bframes": "4", "crf": "f64:4038000000000000", "preset": "slow"},
    "unknown_params": {"some-new-option": "1"},
    "version": "4.1"
  },
  "filters": [
    {
      "library": "libpelorus",
      "name": "pelorus_deband_vulkan",
      "params": {
        "range": "16",
        "thr": ["f64:3fd6666660000000", "f64:3fc99999a0000000", "f64:3fc99999a0000000", "f64:0000000000000000"]
      },
      "unknown_params": {},
      "version": "0.3.0"
    }
  ],
  "hardware": {"device": "example GPU", "path": "vulkan"},
  "input": {"digest": "sha256:6d7233c7036e18b055f7861cd3c9f534093477b15efe8f58a80fecce4993c2ae"},
  "schema": "pelorus/encode-record/1"
}
```

`crf` 24.0 is `f64:4038000000000000`. The deband `thr` values are `float`
0.35, 0.2, 0.2 and 0, widened to `double`. The `input.digest` is the SHA-256
of the bytes `example input bytes`. The canonical text without `digest` and
`elapsed_ns` is 596 bytes, and its digest is the `digest` above. Changing
`range` to `"15"` gives
`sha256:f603fbf21c0423dbc7a353da631239283f88e1239c8a913707369441a39cb804`.
Any party can recompute both with Python:

```python
import hashlib, json, sys

record = json.load(open(sys.argv[1], encoding="utf-8"))
body = {k: v for k, v in record.items() if k not in ("digest", "elapsed_ns")}
text = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
print("sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest())
```

**Duplicate keys.** The canonical form has unique keys, so the canonicaliser
and the builder reject a repeated key. "A repeated option takes its last
value" is the job of the option-string parser that fills `params`.

## Side-data section

`PEL_SEC_ENCODE_RECORD = 1u << 9` (section j, ABI 1.4), struct
`PelorusEncodeRecordSection`, 48 bytes, 4-byte aligned, no implicit padding
(research 0174, E1). APPEND-ONLY after `_pad`.

| Offset | Type | Member | Meaning |
|---:|---|---|---|
| 0 | `uint8_t[32]` | `digest` | raw SHA-256 of the canonical text |
| 32 | `uint32_t` | `locator_offset` | blob-relative offset of the locator, UTF-8, no NUL |
| 36 | `uint32_t` | `locator_size` | locator bytes, 0..4096; 0 means no locator |
| 40 | `uint8_t` | `digest_alg` | 1 = SHA-256; 0 is invalid |
| 41 | `uint8_t` | `locator_kind` | 0 none, 1 path relative to the media file, 2 URI |
| 42 | `uint8_t` | `record_major` | 1 |
| 43 | `uint8_t[5]` | `_pad` | reserved, zero |

A producer attaches the section at least to the first frame of a stream. A
consumer uses the first valid section it reads. The producer places the
locator after the packed sections at an 8-aligned blob-relative offset, like
every map, so `pel_blob_map(blob, len, locator_offset, locator_size,
locator_size, 1, &p)` validates it; a reader also checks `locator_size <=
PEL_ENCODE_RECORD_LOCATOR_MAX` (4096). `pel_encode_record_digest_text()`
(in the mirrored `interop.c`) turns the section into the 71-character text.
A VMAFx reader then calls:

```c
char text[PEL_DIGEST_TEXT_SIZE]; /* 72: "sha256:" + 64 hex + NUL */
if (pel_encode_record_digest_text(sec, got, text, sizeof text) == PEL_OK)
    (void)vmafx_context_set_encode_record(ctx, text, NULL);
```

FFmpeg passes frame side data of type `AV_FRAME_DATA_SEI_UNREGISTERED` into
the bitstream when the encoder option `udu_sei` is set. In FFmpeg n9.0.1 that
option exists on `libx264`, `libx265`, `h264_nvenc` and `hevc_nvenc` (research
0174, E5). The digest then survives a remux and reaches any decoder.

## API

Header [`pelorus/encode_record.h`](../../libpelorus/include/pelorus/encode_record.h);
implementation `libpelorus/src/encode_record.c` with the private SHA-256
`libpelorus/src/sha256.c`. All functions return `pel_result`, write into
caller buffers, allocate nothing, keep no pointer they were given and hold no
global state, so concurrent calls on distinct buffers are safe. The
canonicaliser uses an explicit stack of five frames (about 15 KiB of stack),
never recursion. Text outputs are NUL-terminated: `cap` must hold the text
plus one byte, and a short buffer returns `PEL_ERR_RANGE` with the needed
length (without the NUL) in `*out_len`. `buf = NULL` with `cap = 0` is a size
query with the same result, as for `pel_blob_pack_into`; `NULL` with a
non-zero `cap` is `PEL_ERR_INVALID`.

| Function | Result |
|---|---|
| `pel_encode_record_build(const PelorusEncodeRecordInput *in, char *buf, size_t cap, size_t *out_len)` | canonical text with `digest` filled |
| `pel_encode_record_canonicalize(const char *json, size_t len, char *buf, size_t cap, size_t *out_len)` | canonical text of any record; `PEL_ERR_INVALID` on a duplicate key, a non-string scalar, invalid UTF-8 or a bound overrun |
| `pel_encode_record_hash(const char *json, size_t len, uint8_t digest[32])` | the digest of the record |
| `pel_encode_record_verify(const char *json, size_t len, const uint8_t expected[32])` | `PEL_OK`, or `PEL_ERR_MISMATCH` (new, -8) when the embedded `digest` or `expected` (if non-NULL) differs; `PEL_ERR_ABSENT` when the record has no `digest` member and `expected` is NULL, since there is nothing to verify against |
| `pel_encode_record_int(int64_t v, char out[24])`, `pel_encode_record_f64(double v, char out[24])` | the canonical scalar texts: plain decimal, and `f64:` with 16 hex digits (`-0.0` as `+0.0`; `PEL_ERR_RANGE` for NaN or infinity) |
| `pel_encode_record_digest_text(const PelorusEncodeRecordSection *s, size_t got, char *out, size_t cap)` | `sha256:` text; `PEL_ERR_INVALID` on `digest_alg != 1` or `got` shorter than the section |

SHA-256 is a libpelorus port of VMAFx `core/src/vmafx/sha256.c` (FIPS 180-4,
VMAFx commit `c3fdc200`, same holder and licence: Lusoris, EUPL-1.2). It is not
mirrored back into VMAFx.

`pel_encode_record_build()` takes a `PelorusEncodeRecordInput`: the encoder's
name, version and codec (required), its `params` and `unknown_params` as
arrays of `PelorusEncodeParam` (`key`, one scalar text or an array of them),
`input_digest` and/or `input_id`, the `filters` in chain order
(`PelorusEncodeFilter`: `name`, `library`, `version`, `params`,
`unknown_params`), the optional `hardware_path` (one of the seven names
above), `hardware_device`, `hardware_driver`, and `elapsed_ns` with
`has_elapsed_ns`. Parameters may come in any order: the builder sorts them.
It checks what the canonicaliser cannot know: required members, the
`input.digest` form and the `hardware.path` name.

## Option-string parsers

Header [`pelorus/encode_options.h`](../../libpelorus/include/pelorus/encode_options.h);
implementation `libpelorus/src/encode_options.c` with dialect tables in
`libpelorus/src/encode_options_x264.c`, `libpelorus/src/encode_options_x265.c`,
`libpelorus/src/encode_options_svtav1.c` and `libpelorus/src/encode_options_aom.c`
([ADR-0198](../adr/0198-encoder-option-string-parsers.md), issue #156).

The option-string parser parses and normalises CLI and FFmpeg pass-through
parameter strings (`-x264-params`, `-x265-params`, `-svtav1-params`,
`-aom-params`) into arrays of
`PelorusEncodeParam` ready for `pel_encode_record_build()`.

### Parser API

```c
typedef enum pel_encode_options_dialect {
    PEL_ENCODE_OPTIONS_X264 = 1,
    PEL_ENCODE_OPTIONS_X265 = 2,
    PEL_ENCODE_OPTIONS_SVTAV1 = 3,
    PEL_ENCODE_OPTIONS_AOM = 4,
} pel_encode_options_dialect;

typedef struct PelorusEncodeOptions {
    PelorusEncodeParam params[PEL_ENCODE_RECORD_MAX_MEMBERS];
    uint32_t nb_params;
    PelorusEncodeParam unknown_params[PEL_ENCODE_RECORD_MAX_MEMBERS];
    uint32_t nb_unknown_params;
    /* private value pointer slots and character arena */
} PelorusEncodeOptions;

pel_result pel_encode_options_parse(pel_encode_options_dialect d,
                                    const char *text, size_t len,
                                    PelorusEncodeOptions *out);
```

The caller allocates `out` (allocate on heap; ~272 KiB, measured `sizeof(PelorusEncodeOptions)` = 278552). All pointers in
`out->params` and `out->unknown_params` reference memory within `out`'s own
storage buffer. The function performs zero heap allocation, uses no global
state, and produces no standard library logging. Concurrent calls on distinct
`PelorusEncodeOptions` instances are reentrant and thread-safe.

Outputs in both `out->params` and `out->unknown_params` are sorted in ascending
byte order by key (deterministic across all calls).

### Input syntax

The parser accepts FFmpeg key-value lists where options are separated by `:` and
key-value pairs by `=`:

- Quoting: single quotes (`'...'`) treat enclosed characters literally.
  Backslash (`\`) escapes the next character (for instance `\:`, `\=`, `\\`).
- Leading and trailing whitespace around keys and values is stripped.
- An empty input string or `len == 0` returns `PEL_OK` with zero parameters.
- Malformed inputs return `PEL_ERR_INVALID`: empty keys (e.g. `=3`), missing
  `=` (e.g. `crf`), or empty values (e.g. `crf=`). Unterminated quotes have the
  remainder copied literally, and trailing backslashes are kept literally,
  matching FFmpeg's `av_dict_parse_string` and `av_get_token` behavior.
- Out-of-bounds parameters return `PEL_ERR_RANGE`: keys exceeding 64 bytes,
  values exceeding 1024 bytes, integers with $|v| > \text{INT32\_MAX}$, more
  than 256 parameters, or arena exhaustion.

These syntax rules are the same for all four dialects, because every dialect
uses the one tokenizer in `libpelorus/src/encode_options.c`. An escaped `:` stays
inside its value: x265 `sar=16\:9:crf=20` gives two parameters, `sar` = `16:9`
and `crf`, where a split on every `:` would give three. A string with a 64-byte
key and a 1024-byte value builds a record that verifies; one more value byte
returns `PEL_ERR_RANGE`. `libpelorus/test/encode_options_test.c` checks these
cases and that a record built from a string with one key dropped fails
`pel_encode_record_verify` against the digest of the full string.

FFmpeg encoder AVOptions (`-preset`, `-crf` and the hardware encoders'
rate-control options) go through `pel_encode_options_from_ffmpeg`; see
[FFmpeg encoder AVOptions](#ffmpeg-encoder-avoptions).

### Normalisation rules per dialect

The rules below describe x264 and x265. SVT-AV1 and libaom differ where their
libraries differ; see [SVT-AV1 and libaom](#svt-av1-and-libaom).

1. **Key normalisation**:
   - Underscores (`_`) are translated to hyphens (`-`).
   - x265: leading `--` prefixes are stripped.
   - Keys are matched case-sensitively against upstream option tables.
   - Known aliases are mapped to canonical keys (for example, x264 `frameref`
     becomes `ref`, `bframe` becomes `bframes`; x265 `ip-factor` becomes
     `ipratio`, `pb-factor` becomes `pbratio`).
   - Negation prefixes: a `no-` or `no` prefix on a known boolean option maps
     to the base canonical key with the inverted boolean value, as upstream
     strips the prefix before the name lookup (x265 `no-tskip-fast=1` gives
     `tskip-fast=false`). Both encoders also accept the
     prefix on non-boolean options and pass them `"true"` or `"false"`
     (x264 `common/base.c:913-919`, x265 `source/common/param.cpp:937-946`);
     the record does not type those, so `no-b-adapt=1` lands in
     `unknown_params` as `no-b-adapt`.
2. **Value normalisation**:
   - **Boolean**: x264 accepts `1`/`0`, `true`/`false`, `yes`/`no` (case-insensitive words);
     x265 accepts `1`/`0`, `true`/`false`, `yes`/`no` (case-sensitive, matching upstream `atobool`).
     Normalised to `"true"` or `"false"`.
   - **Integer**: parsed via base-0 `strtoll` (accepts decimal, hex `0x`, octal `0`),
     as both encoders do (`strtol(str, &end, 0)` in x264 `common/base.c:856` and
     x265 `source/common/param.cpp:1551`).
     Parsing assumes the C locale (ASCII digits). Values with $|v| > \text{INT32\_MAX}$
     return `PEL_ERR_RANGE`. Strings beginning with `0` followed by non-octal digits
     (such as `08`) fail conversion matching upstream `strtol` and are rejected as malformed for that type.
     Valid integers are formatted as plain decimal canonical strings via `pel_encode_record_int()`.
   - **Float / Real**: parsed via `strtod` in the C locale (period decimal point)
     and formatted as canonical `"f64:<16 lower-case hex>"` via `pel_encode_record_f64()`.
   - **Other types** (enums, filenames, lists, ratios, "auto"): preserved verbatim as unescaped text.
3. **Duplicate keys**:
   - When a key appears multiple times (including across aliases or negation prefixes),
     the last occurrence wins (e.g. `b_adapt=2:b-adapt=1` yields `b-adapt=1`).
4. **Unknown and rejected parameters**:
   - Keys not recognized in the dialect table land in `unknown_params` under the normalised key
     (`_` → `-`, x265 leading `--` stripped) with the value verbatim, so `foo_bar=1:foo-bar=2`
     yields one entry `foo-bar=2`.
   - Known keys whose values fail validation for their declared type also land in `unknown_params`
     under their canonical key with the value verbatim (because the underlying encoder rejects
     them, meaning they did not take effect); a later valid value for that key replaces the entry.

### SVT-AV1 and libaom

FFmpeg passes each `-svtav1-params` entry to `svt_av1_enc_parse_parameter`
(`libavcodec/libsvtav1.c:349`) and each `-aom-params` entry to
`aom_codec_set_option` (`libavcodec/libaomenc.c:1036`); the parsers follow
those functions.

| Rule | SVT-AV1 | libaom |
|---|---|---|
| Key match | exact; no `_` folding, no `--` strip | exact long name; no `_` folding, no `--` strip |
| Aliases | `w` → `width`, `h` → `height`, `q` → `qp` | none |
| Negation prefix | none (`no-enable-overlays` is unknown) | none |
| Booleans | `1`/`0`, `true`/`false`, `yes`/`no`, any case | no boolean options; flags are unsigned integers |
| Integers | base 0 (`lp=0x10` gives `16`) | base 10 (`cpu-used=010` gives `10`; `0x4` is rejected) |
| Unsigned options | negative value rejected to `unknown_params` | negative value rejected to `unknown_params` |
| Reals | `crf`, `cqp`, `ac-bias` as `f64:<hex>` | none |
| Verbatim | `keyint`, `tbr`, `mbr`, `rc`, `profile`, enums, colour options, lists, file names | enums (`tune=ssim`), rationals, lists, file names |

Unsigned values accept 0 to 4294967295; a larger value returns `PEL_ERR_RANGE`.
The record does not check the narrower 8-bit widths SVT-AV1 clamps to. libaom
options behind `CONFIG_TUNE_VMAF` and `CONFIG_TUNE_BUTTERAUGLI` (off by default)
are not in the table and land in `unknown_params`.

### Field table

| Input | Destination | Key normalisation | Value normalisation |
|---|---|---|---|
| Known key, valid value | `params` | canonical key (`_` → `-`, alias mapped, `no-` stripped) | typed canonical form (`true`/`false`, plain decimal, `f64:<hex>`, or unescaped string) |
| Unknown key | `unknown_params` | normalised key (`_` → `-`, x265 `--` stripped) | verbatim unescaped value |
| Known key, invalid value | `unknown_params` | canonical key | verbatim unescaped value |

### Coverage table

| Dialect | Key count | Upstream source pin | Source file & function |
|---|---|---|---|
| x264 (`PEL_ENCODE_OPTIONS_X264`) | 142 (123 base + 19 aliases) | VideoLAN x264 `b35605ace3ddf7c1a5d67a2eb553f034aef41d55` | `common/base.c: x264_param_parse` |
| x265 (`PEL_ENCODE_OPTIONS_X265`) | 233 (225 base + 8 aliases) | MulticoreWare x265 `e444744c03978c1fb4e037168967020cf2648427` | `source/common/param.cpp: x265_param_parse` |
| SVT-AV1 (`PEL_ENCODE_OPTIONS_SVTAV1`) | 121 (118 base + 3 aliases) | SVT-AV1 `v4.2.0` / `9292ec8e32bce26f781f277ec8739b53426c4300` | `Source/Lib/Globals/enc_settings.c: svt_av1_enc_parse_parameter` |
| libaom (`PEL_ENCODE_OPTIONS_AOM`) | 122 | libaom `v3.15.2` / `af3dc9aadc793c1b00edc003e3fc19a44fdeb574` | `av1/av1_cx_iface.c: encoder_set_option` |

## FFmpeg encoder AVOptions

Implementation `libpelorus/src/encode_avoptions.c` with generated name tables
in `libpelorus/src/encode_avoptions_tables.c`
([ADR-0200](../adr/0200-ffmpeg-avoptions-in-encode-record.md), issue #352).

```c
typedef struct PelorusEncodeOption {
    const char *key;   /* AVOption name, no stream specifier: "b", not "b:v" */
    const char *value; /* as passed to the encoder */
} PelorusEncodeOption;

pel_result pel_encode_options_from_ffmpeg(const char *encoder,
                                          const PelorusEncodeOption *opts,
                                          uint32_t nb_opts,
                                          PelorusEncodeOptions *out);
```

The host passes the encoder name and the options it gave the encoder. Example
(link with `-lpelorus`):

```c
#include <pelorus/encode_options.h>
#include <stdio.h>
#include <stdlib.h>

int main(void)
{
    static const PelorusEncodeOption x264[] = {
        {"preset", "slow"}, {"crf", "30"}, {"x264-params", "crf=20:ref=4"}, {"threads", "8"},
    };
    static const PelorusEncodeOption nvenc[] = {{"rc", "vbr"}, {"cq", "21"}, {"b", "5M"}};
    PelorusEncodeOptions *out = malloc(sizeof(*out));
    uint32_t i;

    if (!out || pel_encode_options_from_ffmpeg("libx264", x264, 4, out) != PEL_OK) {
        return 1;
    }
    for (i = 0; i < out->nb_params; i++) { /* crf=f64:4034000000000000 (20.0), ffmpeg:preset=slow, ref=4 */
        printf("%s=%s\n", out->params[i].key, out->params[i].values[0]);
    }
    if (pel_encode_options_from_ffmpeg("hevc_nvenc", nvenc, 3, out) != PEL_OK) {
        return 1;
    }
    for (i = 0; i < out->nb_params; i++) { /* ffmpeg:b=5M, ffmpeg:cq=21, ffmpeg:rc=vbr */
        printf("%s=%s\n", out->params[i].key, out->params[i].values[0]);
    }
    free(out);
    return 0;
}
```

`libsvtav1` with `svtav1-params` and `libaom-av1` with `aom-params` work the
same way as `libx264` with `x264-params`.

The result fills the same `PelorusEncodeOptions` as the option-string parser,
with the same storage rules: the caller allocates `out` (on the heap, about
272 KiB); every key and value is copied into `out`'s own arena, so `opts` and
its strings need to live only for the call and `out` stays valid after the
caller frees them. The function allocates nothing, uses no global state and
logs nothing; calls on distinct `out` instances are reentrant and thread-safe.
Both arrays are sorted by key:

| Input | Destination | Key | Value |
|---|---|---|---|
| option-string entry (`x264-params`, `x265-params`, `svtav1-params`, `aom-params`) | per the [field table](#field-table) | bare, normalised by the dialect | normalised by the dialect |
| AVOption in the encoder's table | `params` | `ffmpeg:<name>` | verbatim, no unit conversion (`5M` stays `5M`) |
| AVOption not in the table | `unknown_params` | `ffmpeg:<name>` | verbatim |
| host setting: `threads`, `thread_type`, `gpu`, `passlogfile`, `stats`, `x265-stats`, `debug`, `dump_separator`, `err_detect`, `export_side_data`, `max_pixels` | not recorded | | |
| AVOption of a clash pair, string holds a valid value for its key | not recorded; the string value encodes | | |

Clash pairs, where the encoder applies the option string after the AVOption to
the same fields: libx264 `crf` and `qp`, libx265 `crf` and `qp`, libsvtav1
`crf`, libaom-av1 `cpu-used`. ADR-0200 lists the source lines. So
`-crf 30 -x264-params crf=20` and `-x264-params crf=20` give the same record.
SVT-AV1 `qp` is not a pair: the AVOption also turns adaptive quantisation off.
When the string value is invalid (`crf=abc`), the AVOption stays in `params`.
libx264's older option string `x264opts` is not parsed: it is stored verbatim
as `ffmpeg:x264opts`, and an AVOption it overrides stays in the record. The
digest still changes with its content.

A repeated name keeps its last value, as FFmpeg's dictionary does. Errors leave
both arrays empty:

| Return | Cause |
|---|---|
| `PEL_ERR_UNSUPPORTED` | encoder not in the table list |
| `PEL_ERR_INVALID` | NULL argument; empty name or value; a name with `:`; an option-string key starting with `ffmpeg:` |
| any code of `pel_encode_options_parse` | the option-string entry failed to parse (for example `x264-params` = `crf` without `=`); passed through unchanged |
| `PEL_ERR_RANGE` | key above 64 bytes (name above 57 bytes), value above 1024 bytes, or the record bounds |

Encoders and table sizes (FFmpeg n9.0.2): libx264 79, libx265 35, libsvtav1
20, libaom-av1 76; `h264_nvenc` 67, `hevc_nvenc` 69, `av1_nvenc` 65;
`h264_qsv` 73, `hevc_qsv` 72, `av1_qsv` 44; `h264_vaapi` 38, `hevc_vaapi` 38,
`av1_vaapi` 34; `h264_vulkan` 35, `hevc_vulkan` 35, `av1_vulkan` 32. A table
holds each private option of the encoder and each `AVCodecContext` option the
encoder's source reads. Regenerate after an FFmpeg pin bump:

```sh
cc -o avoption-probe scripts/encode-options/avoption-probe.c -lavcodec -lavutil
./avoption-probe libx264 libx265 libsvtav1 libaom-av1 h264_nvenc hevc_nvenc \
    av1_nvenc h264_qsv hevc_qsv av1_qsv h264_vaapi hevc_vaapi av1_vaapi \
    h264_vulkan hevc_vulkan av1_vulkan > avoptions.tsv
python3 -I scripts/encode-options/gen-avoption-tables.py avoptions.tsv \
    /path/to/ffmpeg n9.0.2 libpelorus/src/encode_avoptions_tables.c
```

The FFmpeg patch-stack replay lints `avoption-probe.c` under the `ffmpeg-tree`
`clang-tidy` lane against the pinned static n9.0.2 prefix
([ADR-0233](../adr/0233-probe-ffmpeg-tree-tidy-lane.md)).

`pel_encode_options_from_ffmpeg` and `PelorusEncodeOption` are an additive
public API in `pelorus/encode_options.h`: library symbols follow the 0.x
library version, the interop ABI (`PELORUS_ABI_MINOR`) and the
`PEL_SEC_ENCODE_RECORD` section do not change. New fields of
`PelorusEncodeOption` would only be appended.

## Tests

`libpelorus/test/encode_record_test.c` (Meson test `encode-record`; it reads
this page as its argument) and `libpelorus/test/encode_options_test.c` (Meson
test `encode-options`):

| Acceptance (#81) | Test |
|---|---|
| any single parameter change changes the hash | every encoder and filter parameter of the worked example, changed one at a time, gives a different digest |
| option order equal, value change different | the builder input lists the parameters out of key order and still produces the worked example byte for byte; value changes are the row above. Option-string order arrives with the parsers |
| unknown key kept and flagged; a dropped key fails | the unknown option sits in `unknown_params`; removing a known key, the unknown key or the `digest` member fails verification (`PEL_ERR_MISMATCH`, `PEL_ERR_ABSENT`) |
| round trip; a tampered field fails | build, verify, canonicalize again (a fixed point) pass; one changed character in a value fails |
| independent reproduction | the C digest of this page's JSON block, and of an embedded copy, equals the Python digest above; so does the `range` mutation; an escape and key-order case matches Python's bytes |
| FFmpeg AVOptions (#352) | `test_avopt_*` in `encode_options_test.c`: tables sorted, unique and within key bounds; one changed AVOption changes the digest; host settings ignored for every encoder; each clash pair gives the string-only digest, an invalid string value keeps the AVOption; an unproven pair keeps both; known and unknown names verbatim, last wins; every error code with zeroed output; key 64/65 and value 1024/1025 bytes; last option-string entry wins |

The same file checks SHA-256 against the FIPS 180-4 examples, one million
`a`, the padding boundaries 55 to 120 bytes and streamed chunks, and every
canonical-form bound on both sides of its limit. The section and
`pel_encode_record_digest_text()` are covered by the shared conformance
fixture `libpelorus/test/interop_test.c`.
