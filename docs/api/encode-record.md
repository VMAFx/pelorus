<!-- markdownlint-disable MD013 MD060 -->
# Encode provenance record

> [!IMPORTANT]
> **Status: the record, its digest, the API and the `PEL_SEC_ENCODE_RECORD`
> section (interop ABI 1.4) are implemented**
> ([ADR-0175](../adr/0175-encode-provenance-record.md), issue #81). The
> option-string parsers that fill `encoder.params` from x264, x265 and FFmpeg
> argument lists, and a producer that attaches the section, are follow-ups
> (#81 item 2).

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
| `encoder.unknown_params` (required, may be empty) | options the encoder's table does not know, verbatim (key and value text) |
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

- Keys are printable ASCII (0x21..0x7e without `"` and `\`), at most 64
  bytes, and unique per object. Byte order then equals RFC 8785's UTF-16
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
length (without the NUL) in `*out_len`.

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

## Tests

`libpelorus/test/encode_record_test.c` (Meson test `encode-record`; it reads
this page as its argument):

| Acceptance (#81) | Test |
|---|---|
| any single parameter change changes the hash | every encoder and filter parameter of the worked example, changed one at a time, gives a different digest |
| option order equal, value change different | the builder input lists the parameters out of key order and still produces the worked example byte for byte; value changes are the row above. Option-string order arrives with the parsers |
| unknown key kept and flagged; a dropped key fails | the unknown option sits in `unknown_params`; removing a known key, the unknown key or the `digest` member fails verification (`PEL_ERR_MISMATCH`, `PEL_ERR_ABSENT`) |
| round trip; a tampered field fails | build, verify, canonicalize again (a fixed point) pass; one changed character in a value fails |
| independent reproduction | the C digest of this page's JSON block, and of an embedded copy, equals the Python digest above; so does the `range` mutation; an escape and key-order case matches Python's bytes |

The same file checks SHA-256 against the FIPS 180-4 examples, one million
`a`, the padding boundaries 55 to 120 bytes and streamed chunks, and every
canonical-form bound on both sides of its limit. The section and
`pel_encode_record_digest_text()` are covered by the shared conformance
fixture `libpelorus/test/interop_test.c`.
