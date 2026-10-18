<!-- markdownlint-disable MD013 MD060 -->
# ADR-0210: libx265 user-data-unregistered SEI UUID wrap classification

- **Status**: Accepted
- **Implementation**: landed (#82)
- **Date**: 2026-10-18
- **Deciders**: Lusoris
- **Tags**: grain, fgs, h274, hevc, bsf, sei, udu, x265, interop
- **Related**: [ADR-0117](0117-grain-fgs-bsf.md), [ADR-0183](0183-sidedata-zero-free-carrier.md), [ADR-0206](0206-fgs-bsf-per-frame-bridge.md)

## Context

Per-frame film-grain synthesis in `pelorus_fgs` ([ADR-0206](0206-fgs-bsf-per-frame-bridge.md)) relies on encoder pass-through of Pelorus side-data sections via user-data-unregistered (UDU) SEI messages (`-udu_sei 1`).

In libx265, every release since 2.1 (commit `d4037be7a` in `source/encoder/sei.h`, `SEIuserDataUnregistered::writeSEI`) writes its own hard-coded UUID `2ca2de09-b517-47db-bb55-a4fe7fc2fc4e` as the outer `uuid_iso_iec_11578` before any caller payload. This behavior was measured and confirmed across libx265 releases 2.1, 3.0, upstream master commit `b81f650e2`, and the 4.3 distribution package (PR #371 research digest 0082; upstream report filed in x265 GitHub tracker issue 1000).

Consequently, when passing Pelorus side data into libx265 with `-udu_sei 1`, the SEI message arrives at `pelorus_fgs` wrapped as:

- Outer `uuid_iso_iec_11578`: x265 UUID `2ca2de09-b517-47db-bb55-a4fe7fc2fc4e`.
- SEI payload data: Pelorus blob UUID `e1d7c4a2-6b93-4f08-9a55-0f3c2db17e64` or carrier UUID `3f9b37b8-fd9a-4621-920e-9b78b55cf9b5` ([ADR-0183](0183-sidedata-zero-free-carrier.md)), followed by the standard Pelorus serialized image.

Under the initial `pelorus_fgs` implementation, only SEI messages whose outer UUID matched the Pelorus blob or carrier UUID were accepted. All x265-encoded streams therefore had their side data silently dropped.

## Decision

1. **Strict Allow-List Rule**:
   `pelorus_fgs` accepts exactly two valid forms and rejects everything else:
   - `PEL_UDU_DIRECT`: Outer UUID is the Pelorus blob UUID (`pelorus_sidedata_uuid`) or carrier UUID (`pelorus_carrier_uuid`).
   - `PEL_UDU_X265_WRAPPED`: Outer UUID is the libx265 UUID (`2ca2de09-b517-47db-bb55-a4fe7fc2fc4e`), `data != NULL`, `data_len > 16`, and `data[0..16)` is the Pelorus blob UUID or carrier UUID.
   - `PEL_UDU_NONE`: Any other outer UUID (including unknown outer UUIDs with nested Pelorus payloads), x265 outer UUID with non-Pelorus inner UUID, x265 outer UUID with truncated payload (`data_len <= 16`), or `NULL` outer pointer. Generic "skip 16 bytes and retry" logic is explicitly prohibited.

2. **Header-Only Helper (`pelorus_udu_wrap.h`)**:
   Following the precedent of `pelorus_sei_fit.h`, classification logic lives in `ffmpeg-patches/files/pelorus_udu_wrap.h`. It is plain C with standard library dependencies (`stdint.h`, `stddef.h`, `string.h`), zero dynamic allocations, and copies the UUID constants with mirror asserts in tests. It exposes `pel_udu_classify()`.

3. **Bitstream Filter Integration**:
   In `ffmpeg-patches/files/h265_pelorus_fgs_bsf.c` (`pel_fgs_read_sidedata`), the legacy `memcmp` check on `udu->uuid_iso_iec_11578` is replaced with `pel_udu_classify()`.
   - On `PEL_UDU_DIRECT`, payload processing continues unchanged (`raw_buf = outer UUID + data`, `payload_len = 16 + data_length`).
   - On `PEL_UDU_X265_WRAPPED`, `raw_buf = copy of data` (`payload_len = data_length`). The data already contains the leading Pelorus UUID, which unwraps directly via `pel_blob_unwrap()`.
   - Function complexity remains bounded ($\le 60$ LOC, 1024-iteration ceiling, `SIZE_MAX - 16` guard preserved).

4. **Published Vectors and Downstream Oracle Alignment**:
   The classification cases live once, in `ffmpeg-patches/test/fixtures/udu-wrap-vectors.txt` (one line per vector: name, outer UUID hex, data hex, expected class). The fast test `udu-wrap` (`ffmpeg-patches/test/udu_wrap_test.c`) replays every line and fails on a missing file, on fewer vectors than it was written for, or when a class has no vector. `vmafx` mirrors this exact classification in its side-data reader and replays the same file, so both readers are proven against one set.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Strict allow-list for x265 wrap (chosen) | Enables libx265 per-frame grain synthesis; prevents foreign SEI hijacking | Requires maintaining one well-known external UUID | Chosen |
| Generic offset-16 unwrap / retry | Decouples from specific third-party UUIDs | High risk: arbitrary foreign SEI payloads could match inner magic and inject grain or corrupt state | Refused: violates safety and isolation invariants |
| Patch libx265 upstream only | Pushes fix to source of truth | Years of existing deployed binaries and distro packages remain broken; zero control over adoption timeline | Refused: leaves current adopters without a functioning path |
| Add public API in libpelorus | Centralizes classification | Expands public libpelorus ABI surface for a quirk specific to FFmpeg/BSF SEI ingestion | Deferred: private header in patch stack is zero-cost and self-contained |

## Consequences

- **Positive**: HEVC encodes produced with libx265 and `-udu_sei 1` successfully bridge per-frame grain metadata into `pelorus_fgs`.
- **Negative**: Adds `pelorus_udu_wrap.h` to the FFmpeg patch tree and patch generation script.
- **Neutral**: When libx265 eventually offers verbatim UDU SEI pass-through, the wrapped classification path can be deprecated and retired.

## References

- Issue [#82](https://github.com/VMAFx/pelorus/issues/82): Film grain synthesis bitstream filter.
- PR #371 research digest 0082: measured evidence on libx265 UDU SEI UUID prefixing.
- Upstream report filed in x265 GitHub tracker issue 1000.
- [ADR-0183](0183-sidedata-zero-free-carrier.md): zero-free carrier form.
- [ADR-0206](0206-fgs-bsf-per-frame-bridge.md): per-frame film-grain bridge in pelorus_fgs.
- Usage: [docs/usage/grain-fgs-bsf.md](../usage/grain-fgs-bsf.md).
