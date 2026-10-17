<!-- markdownlint-disable MD013 MD060 -->
# ADR-0204: libaom film grain table text format reader and writer

- **Status**: Accepted
- **Implementation**: landed (#82)
- **Date**: 2026-10-17
- **Deciders**: Lusoris
- **Tags**: grain, libaom, av1, api
- **Related**: [ADR-0149](0149-windows-utf8-paths.md)

## Context

Film grain synthesis in AV1 pipelines frequently uses parameter tables generated during pre-analysis and passed to downstream encoders such as `aomenc` or SVT-AV1. The reference implementation `libaom` defines a standard plain-text grain table format (`filmgrn1\n` magic, entry headers `E <start> <end> <apply> <seed> <update>`, and parameter blocks `p`, `sY`, `sCb`, `sCr`, `cY`, `cCb`, `cCr`).

Pelorus models film grain parameters in `PelorusFilmGrainSection` (mirroring FFmpeg's `AVFilmGrainAOMParams`). Issue #82 requires a parser and serializer for the libaom film grain table format in libpelorus so tools and filters can ingest and emit standard table files.

## Decision

1. **Memory-buffer API**: Provide `pel_grain_table_parse()` and `pel_grain_table_write()` operating purely on caller-supplied in-memory byte buffers and entry arrays (`PelorusGrainTableEntry`).
2. **Zero heap allocation**: The library performs no dynamic memory allocation (`malloc`, `calloc`, `realloc`, `free`). The caller provides storage capacity; if capacity is insufficient, `PEL_ERR_TRUNCATED` is returned; parse reports the entries read before the limit and write reports the bytes required.
3. **Strict range and syntax validation**: Reject invalid or out-of-range fields with `PEL_ERR_RANGE` rather than clamping. Validate points counts (0..14 Y, 0..10 Cb/Cr), scaling shifts (8..11), AR lags (0..3), AR shifts (6..9), multipliers (0..255), offsets (0..511), coordinates (0..255 with strictly monotonic x coordinates), and coefficients (-128..127). Syntax errors or invalid magic return `PEL_ERR_INVALID`.
4. **Byte-exact writer formatting**: Match libaom's reference writer byte-for-byte, including its space and newline quirks:
   - Header: `E %lld %lld %d %d %d\n`
   - Parameters: `\tp ...`, `\tsY %d  %d %d...` (two spaces before first point), `\tsCb %d %d %d...` (one space before first point), `\tsCr %d %d %d...`, `\tcY...`, `\tcCb...`, `\tcCr...\n`.
5. **No banned functions**: Avoid `sprintf`, `snprintf`, `strtok`, `atoi`. Implement dedicated bounded integer formatting and `strtoll` scanning with end-pointer validation.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Memory-buffer API with caller-supplied buffers (chosen) | Zero heap allocation; embeddable library contract; decoupled from I/O and filesystem; compatible with UTF-8 path handling | Caller manages memory buffers | Chosen |
| Link libaom internal `grain_table.c` | Reuses upstream C code directly | `aom_dsp/grain_table.c` is private to libaom and not exported in `libaom.so`; depends on `FILE*` and `setjmp`/`longjmp`; requires libaom dependency | Fails zero-dependency embeddable library contract; not exported |
| `FILE*`-based API (`pel_grain_table_read_file` / `write_file`) | Convenience for simple CLI utilities | Binds library to C stdio; conflicts with Windows UTF-8 path contract (ADR-0149); hides I/O error semantics from callers | Rejected: embeddable library principle, UTF-8 path rules (ADR-0149) |

## Consequences

- **Positive**: libpelorus can ingest and emit libaom-compatible film grain tables with zero heap allocations; the writer matches libaom's writer byte for byte, and `aomenc` 3.15.2 reads the test fixture into the expected bitstream parameters ([verification](../api/grain-table.md#verification)). Full input validation protects downstream encoders against corrupt table data; `aomenc` itself accepts a truncated table and emits a stream libdav1d refuses.
- **Negative**: Callers reading files from disk must perform file I/O into a buffer before calling `pel_grain_table_parse()`.
- **Neutral**: `PelorusFilmGrainSection` ABI is unchanged.

## References

- [Issue #82](https://github.com/VMAFx/pelorus/issues/82): Film grain table I/O.
- Upstream reference: [libaom grain_table.c](https://aomedia.googlesource.com/aom/+/refs/heads/main/aom_dsp/grain_table.c).
- [ADR-0149](0149-windows-utf8-paths.md): Windows UTF-8 path handling.
