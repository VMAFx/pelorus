<!-- markdownlint-disable MD013 MD060 -->
# ADR-0175: The encode provenance record is canonical JSON whose SHA-256 digest VMAFx binds unchanged, and side data carries only the digest and a locator

- **Status**: Proposed
- **Implementation**: pending (#81)
- **Date**: 2026-10-09
- **Deciders**: Lusoris
- **Tags**: interop, abi, provenance, vmafx
- **Related**: [ADR-0103](0103-interop-sidedata-abi.md), [ADR-0174](0174-encoder-telemetry-abi-1-4.md), [ADR-0169](0169-release-provenance-slsa3.md)

## Context

Issue #81 asks for a structured, hashable record of how a clip was encoded:
the encoder and its normalised settings, the input, the Pelorus filter chain
with each filter's validated parameters and library version, the hardware
path, and the wall time. A quality score must be able to point at it. VMAFx
has already reserved the slot. Its provenance record
([VMAFx ADR-2073](https://github.com/VMAFx/vmafx/blob/master/docs/adr/2073-vmafx-provenance-record.md),
item 6) carries `encode_record`, set through
`vmafx_context_set_encode_record()`. That function accepts only `sha256:`
followed by 64 lower-case hex digits (`valid_encode_record`, VMAFx
`core/src/vmafx/provenance.c`). VMAFx's own digest is the SHA-256 of an
RFC 8785 text without `digest` and `elapsed_ns`, and its tests reproduce it
with Python's
`json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)`.
VMAFx/vmafx#2159 signs that digest, so the encode-side digest must be
reproducible by a third party with the same tools.

Encoders embed free-text option strings, and those strings differ per encoder
(#81 evidence). FFmpeg n9.0.1 passes user-data-unregistered SEI from frame
side data into the bitstream when `udu_sei` is set on `libx264`, `libx265`,
`h264_nvenc` and `hevc_nvenc`
([research 0174](../research/0174-abi-1-4-telemetry-provenance.md), item E5).
A Pelorus blob on the encoder's input frames can therefore reach a decoder,
including VMAFx's reader (patch 0017).

## Decision

We will define the encode record as canonical JSON with one digest that VMAFx
takes unchanged. Side data carries only that digest and a locator.

1. **Record.** The record is a UTF-8 JSON object with schema
   `pelorus/encode-record/1` and these members: `schema`, `encoder` (`name`,
   `version`, `codec`, `params`, `unknown_params`), `input` (`digest` and/or
   `id`), `filters` (an array in chain order, each with `name`, `library`,
   `version`, `params`, `unknown_params`), `hardware` (`path`, `device`,
   `driver`), `elapsed_ns` and `digest`. The member table is in
   [encode-record.md](../api/encode-record.md).
2. **Canonical form.** The rules are ADR-2073 item 2: RFC 8785, keys in byte
   order, no whitespace. Strings escape only `"`, `\` and control characters
   (`\b \t \n \f \r`, all others as lower-case `\u00xx`). Three restrictions
   make RFC 8785 and the Python reference produce the same bytes:
   - Every scalar is a JSON string. There are no numbers, booleans or `null`.
     An integer is plain decimal text. A boolean is `"true"` or `"false"`. A
     real is `f64:` followed by the 16 lower-case hex digits of its IEEE-754
     binary64 bits. A `float` widens to `double` exactly, `-0.0` is written as
     `+0.0`, and NaN or infinity is rejected. This matches VMAFx's "16 hex
     digits of the IEEE-754 bits" convention for score digests. An array value
     (a per-plane parameter) is an array of such strings.
   - Keys are printable ASCII (0x21..0x7e without `"` and `\`), so byte order
     equals RFC 8785's UTF-16 order.
   - Invalid UTF-8 is rejected with `PEL_ERR_INVALID`. VMAFx replaces it with
     U+FFFD, but a record Pelorus builds never contains it, so the two texts
     cannot diverge.
3. **Digest.** The digest is `sha256:` followed by the 64 lower-case hex
   digits of SHA-256 over the canonical text of the record without its
   top-level `digest` and `elapsed_ns` members. This is the exact string
   `vmafx_context_set_encode_record()` accepts. The wall time stays in the
   record and out of the digest, as `elapsed_ns` does in ADR-2073.
4. **Normalisation.** Each encoder has a parameter table that defines its known
   keys, their aliases (for example x264 `b-adapt` and `b_adapt`) and their
   types. Option order does not matter, because `params` is an object. A
   repeated key takes its last value, as the encoders do. An unknown key is
   kept verbatim in `unknown_params`, which is how it is flagged. It is never
   dropped. Filter parameters are the validated contract values
   (`deband.h`, `denoise.h` and so on), not the option text.
5. **Side data.** `PEL_SEC_ENCODE_RECORD = 1u << 9` (section j,
   [ADR-0174](0174-encoder-telemetry-abi-1-4.md)) holds
   `PelorusEncodeRecordSection`, 48 bytes: `uint8_t digest[32]` (raw
   SHA-256), `uint32_t locator_offset`, `uint32_t locator_size` (UTF-8, no
   NUL, at most 4096 bytes, blob-relative), `uint8_t digest_alg` (1 =
   SHA-256), `uint8_t locator_kind` (0 none, 1 path relative to the media,
   2 URI), `uint8_t record_major` (1) and `uint8_t _pad[5]`. A producer
   attaches it at least to the first frame. A consumer takes the first valid
   section of a stream. The record itself never rides in side data.
6. **API.** The functions are named after #81:
   - `pel_encode_record_build` builds the canonical text with the digest
     filled in;
   - `pel_encode_record_canonicalize` takes any JSON record and returns the
     canonical text, or `PEL_ERR_INVALID` on duplicate keys, a non-string
     scalar, invalid UTF-8 or a bound overrun;
   - `pel_encode_record_hash` computes the digest;
   - `pel_encode_record_verify` returns `PEL_OK` or the new
     `PEL_ERR_MISMATCH = -8`, appended to `pel_result`.

   All of them write into caller buffers and allocate nothing. The mirrored
   `interop.c` gains `pel_encode_record_digest_text()`, which formats the
   section's digest as the 71-character `sha256:` text. A VMAFx reader passes
   that text to `vmafx_context_set_encode_record()` and needs no SHA-256 code
   from Pelorus.
7. **SHA-256.** libpelorus gains `src/sha256.c`, a port of VMAFx
   `core/src/vmafx/sha256.c` (FIPS 180-4; same holder, EUPL-1.2) with `pel_`
   names and its test vectors. The port is not mirrored back into VMAFx, so
   each repository keeps one implementation.
8. **Bounds (HISS-02).** A record is at most 65 536 bytes, with at most 64
   filters, 256 parameters per object, a nesting depth of 4, keys of at most
   64 bytes and values of at most 1024 bytes. The canonicaliser rejects
   anything beyond these limits rather than truncating it.

## Alternatives considered

### Canonical form

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| RFC 8785 subset, all scalars strings (chosen) | Same rules and the same Python reference as VMAFx ADR-2073; readable; the digest goes straight into `encode_record` and into the VMAFx/vmafx#2159 signature | Reals are shown as bit patterns | Chosen |
| RFC 8785 with JSON numbers | Natural numbers | Needs ECMAScript shortest-number formatting in C; Python `json.dumps` differs from it for exponents (`1e-07` against `1e-7`), so the reference no longer matches | Two implementations disagree |
| Deterministic CBOR (RFC 8949 section 4.2) | Compact | A second encoding beside VMAFx's JSON; needs a CBOR library in each verifier | ADR-2073 rejected it for the same record family |
| Hash the raw encoder option string | No parser | Option order and spelling change the hash; carries no filter chain | Fails #81 acceptance items 1 and 2 |

### Reals inside the digested text

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| `f64:` plus 16 hex digits of the binary64 bits (chosen) | Exact; trivial in C (`memcpy` plus `snprintf`) and Python (`struct.pack(">d", v).hex()`); same convention as VMAFx score digests | Not human-readable | Chosen |
| `%.17g` decimal text | Readable-ish | Relies on correctly rounded `printf` in every C library; `0.35` prints as `0.34999999999999998` | Platform risk |
| Shortest round-trip decimal | Readable | Needs a Ryu-class algorithm or a 17-step print-and-parse loop that depends on `strtod` rounding | More code for readability only |

### Side-data content

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Digest plus locator (chosen) | 48 bytes plus the locator; fits the SEI path; the record stays a separate, signable file | The consumer must fetch the record to read it | Chosen (#81 item 3) |
| Full record inline | Self-contained | Up to 64 KiB per frame; SEI bloat; a second copy to keep consistent | Too large per frame |
| Container tag only, no section | No ABI change | Lost on remux and raw streams; VMAFx reads side data, not tags | Does not reach the VMAFx reader |

### Digest on the wire

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Raw 32 bytes plus a mirrored text helper (chosen) | Fixed size; one formatting implementation, shared through the mirror | One helper call | Chosen |
| 72-byte `sha256:` text | Directly usable | Larger fixed field; text validation in every reader | Validation duplicated |

### SHA-256 source

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Port VMAFx's FIPS 180-4 implementation (chosen) | Already reviewed and tested; same holder and licence; no new dependency (hard rule 9) | The same algorithm exists in both repositories | Chosen; the copies never meet in one build |
| OpenSSL or libcrypto | Maintained | A dependency for every vendoring consumer; libpelorus is dependency-free | Breaks the dependency-free invariant |
| A third-party public-domain file | Small | New provenance to review; the VMAFx copy is already reviewed | No gain over the port |

## Consequences

- **Positive**: one digest binds the encode to a VMAFx score with no new
  VMAFx code beyond reading the section. The digest survives a remux when
  `udu_sei` carries the blob into the bitstream. Any party with Python can
  recompute it.
- **Negative**: reals are not readable in the record. The tooling and the
  documentation decode them. libpelorus gains a SHA-256 and a JSON
  canonicaliser to maintain.
- **Neutral / follow-ups**: option-string parsers for x264, x265 and FFmpeg
  argument lists (#81 item 2, VMAFx 1.1); a producer filter or tool that
  writes the section; SEI carriage tests per encoder; the C2PA assertion on
  the VMAFx side (VMAFx/vmafx#2159).

## Open questions

- VMAFx: confirm that `vmafx_context_set_encode_record()` should receive the
  digest of the record as Pelorus defines it here (no VMAFx wrapper object
  around it).
- Maintainer: the frame policy of the producer (first frame only, or every key
  frame) is decided with the producer. This ADR fixes only the consumer rule
  "first valid section".

## References

- Issue #81; VMAFx/vmafx#2142, VMAFx/vmafx#2159.
- VMAFx ADR-2073 items 2 and 6; `core/include/vmafx/provenance.h`
  (`vmafx_context_set_encode_record`); `core/src/vmafx/sha256.c`.
- RFC 8785, JSON Canonicalization Scheme: <https://www.rfc-editor.org/rfc/rfc8785>.
- FIPS 180-4, Secure Hash Standard.
- [Research 0174](../research/0174-abi-1-4-telemetry-provenance.md): the
  worked example's digest, recomputed with the Python reference.
- Source: `req` (task brief 2026-10-08, paraphrased): specify the #81 canonical
  JSON and SHA-256 so that they match ADR-2073's digest definition exactly
  (64 hex digits, the same canonicalisation).
