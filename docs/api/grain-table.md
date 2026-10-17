<!-- markdownlint-disable MD013 -->
# Film Grain Table I/O API

libpelorus provides a pure C parser and serializer for the libaom film-grain table text format. This enables Pelorus filters, tools, and downstream encoders to exchange film-grain parameters without external dependencies or heap allocations.

- Header: [`pelorus/grain_table.h`](../../libpelorus/include/pelorus/grain_table.h)
- Implementation: [`libpelorus/src/grain_table.c`](../../libpelorus/src/grain_table.c)
- Decision: [ADR-0204](../adr/0204-libaom-grain-table-io.md)
- Upstream reference: [libaom `grain_table.c`](https://aomedia.googlesource.com/aom/+/refs/heads/main/aom_dsp/grain_table.c)

## Format overview

The libaom film-grain table format is a line-oriented plain-text format:

1. **Header magic**: The file must begin with `filmgrn1\n`.
2. **Entry headers**: Each entry begins with an `E` line:

   ```text
   E <start_time> <end_time> <apply_grain> <random_seed> <update_parameters>
   ```

   - `start_time`, `end_time`: 64-bit signed timestamps in libaom encoder ticks, 10 MHz (`TICKS_PER_SEC` in libaom `av1/encoder/encoder.h`; `av1_cx_iface.c` converts each frame pts to these ticks before the table lookup). `end_time >= start_time`.
   - `apply_grain`: `0` or `1`.
   - `random_seed`: `0..65535`.
   - `update_parameters`: `0` or `1`. If `0`, no parameter lines follow; the entry carries only its interval, `apply_grain` and `random_seed`.
3. **Parameter block** (present only when `update_parameters == 1`):
   - `\tp <ar_coeff_lag> <ar_coeff_shift> <grain_scale_shift> <scaling_shift> <chroma_scaling_from_luma> <overlap_flag> <cb_mult> <cb_luma_mult> <cb_offset> <cr_mult> <cr_luma_mult> <cr_offset>`
   - `\tsY <num_y_points>  <x0> <y0> <x1> <y1> ...` (note libaom writer quirk: two spaces before first point coordinates).
   - `\tsCb <num_cb_points> <x0> <y0> ...`
   - `\tsCr <num_cr_points> <x0> <y0> ...`
   - `\tcY <coeff0> <coeff1> ...` (`num_y_coeffs = 2 * lag * (lag + 1)`)
   - `\tcCb <coeff0> <coeff1> ...` (`num_uv_coeffs = 2 * lag * (lag + 1) + 1`, so one coefficient at `lag == 0`)
   - `\tcCr <coeff0> <coeff1> ...`

## Coded value mapping

`PelorusGrainTableEntry` holds timestamps, `update_parameters`, and a `PelorusFilmGrainSection` structure (interop ABI 1.0+). The table uses libaom's coded integer ranges, which map to Pelorus fields (matching FFmpeg `AVFilmGrainAOMParams` semantics) as follows:

| libaom table token | Table range | Pelorus field | Pelorus range | Mapping / formula |
| --- | --- | --- | --- | --- |
| `start_time` | `int64` | `entry.start_time` | `int64` | Verbatim timestamp |
| `end_time` | `int64` | `entry.end_time` | `int64` | Verbatim timestamp (`>= start_time`) |
| `apply_grain` | `0..1` | `params.apply` | `0..1` | Verbatim flag |
| `random_seed` | `0..65535` | `params.seed` | `0..65535` | Verbatim seed |
| `update_parameters` | `0..1` | `entry.update_parameters` | `0..1` | Verbatim flag |
| `ar_coeff_lag` | `0..3` | `params.ar_coeff_lag` | `0..3` | Verbatim lag |
| `ar_coeff_shift` | `6..9` | `params.ar_coeff_shift` | `6..9` | Verbatim shift |
| `grain_scale_shift` | `0..3` | `params.grain_scale_shift` | `0..3` | Verbatim shift |
| `scaling_shift` | `8..11` | `params.scaling_shift` | `8..11` | Verbatim shift |
| `chroma_scaling_from_luma` | `0..1` | `params.chroma_scaling_from_luma` | `0..1` | Verbatim flag |
| `overlap_flag` | `0..1` | `params.overlap_flag` | `0..1` | Verbatim flag |
| `cb_mult` | `0..255` | `params.uv_mult[0]` | `-128..127` | `params.uv_mult[0] = coded - 128` |
| `cr_mult` | `0..255` | `params.uv_mult[1]` | `-128..127` | `params.uv_mult[1] = coded - 128` |
| `cb_luma_mult` | `0..255` | `params.uv_mult_luma[0]` | `-128..127` | `params.uv_mult_luma[0] = coded - 128` |
| `cr_luma_mult` | `0..255` | `params.uv_mult_luma[1]` | `-128..127` | `params.uv_mult_luma[1] = coded - 128` |
| `cb_offset` | `0..511` | `params.uv_offset[0]` | `-256..255` | `params.uv_offset[0] = coded - 256` |
| `cr_offset` | `0..511` | `params.uv_offset[1]` | `-256..255` | `params.uv_offset[1] = coded - 256` |
| `sY` points | 0..14 points, `x`: 0..255, `y`: 0..255 | `params.num_y_points`, `params.y_points[i][0..1]` | same | Verbatim coordinates; `x` strictly increasing |
| `sCb` points | 0..10 points, `x`: 0..255, `y`: 0..255 | `params.num_uv_points[0]`, `params.uv_points[0][i][0..1]` | same | Verbatim coordinates; `x` strictly increasing |
| `sCr` points | 0..10 points, `x`: 0..255, `y`: 0..255 | `params.num_uv_points[1]`, `params.uv_points[1][i][0..1]` | same | Verbatim coordinates; `x` strictly increasing |
| `cY` coeffs | `-128..127` | `params.ar_coeffs_y[i]` | `-128..127` | Verbatim signed 8-bit integer |
| `cCb` coeffs | `-128..127` | `params.ar_coeffs_uv[0][i]` | `-128..127` | Verbatim signed 8-bit integer |
| `cCr` coeffs | `-128..127` | `params.ar_coeffs_uv[1][i]` | `-128..127` | Verbatim signed 8-bit integer |
| (model) | N/A | `params.grain_model` | `PEL_GRAIN_AOM` (1) | Set to `PEL_GRAIN_AOM` on parse |
| (H.274 fields) | N/A | `params.h274_*`, `params.limit_output_range` | `0` | Zeroed on parse; ignored on write |

## Error handling

The API returns `pel_result`:

| Code | Value | Meaning |
| --- | --- | --- |
| `PEL_OK` | 0 | Operation completed successfully. |
| `PEL_ERR_INVALID` | < 0 | Invalid syntax, missing magic `filmgrn1\n`, truncated line, non-digit token, or NULL argument. |
| `PEL_ERR_RANGE` | < 0 | Field value outside legal bounds, monotonic order violated, or `end_time < start_time`. Rejected without clamping. |
| `PEL_ERR_TRUNCATED` | < 0 | Output array or string buffer capacity too small. On parse, `*count` reports entries parsed before truncation. On write, `*written` reports total buffer bytes required (excluding NUL). |

## Verification

- `libpelorus/test/grain_table_test.c` (fast suite, `grain-table`): parse of the two-entry fixture, byte-exact write-back, every range rule planted once in parse and write, bad magic, short lines, output-array and buffer capacity at every length below the required size with a guard byte, and the lag 0, empty-table, seed 65535 and offset 0 and 511 boundaries.
- libaom reads the fixture: `aomenc --film-grain-table=libpelorus/test/fixtures/grain/two-entries.tbl` (libaom 3.15.2) encodes a stream whose film grain parameters, decoded by `ffprobe -export_side_data film_grain` (libdav1d), carry the fixture's lag, shifts, overlap flag, 14 luma and 10 + 10 chroma points and AR coefficients, with `uv_mult`, `uv_mult_luma` and `uv_offset` equal to 0 for coded 128, 128 and 256. This confirms the coded-to-Pelorus mapping above. The seed differs per frame because the libaom encoder advances it.
- aomenc does not reject a malformed table: a table cut off inside `sY` still encodes with exit status 0, and libdav1d then refuses the stream (`point_y_value[i] out of range`). The strict validation here stops such a table before it reaches an encoder.

## Usage example

```c
#include <pelorus/grain_table.h>
#include <stdint.h>
#include <stdlib.h>

void process_table(const char *table_text, size_t text_len)
{
    PelorusGrainTableEntry entries[128];
    size_t count = 0;

    pel_result r = pel_grain_table_parse(table_text, text_len, entries, 128, &count);
    if (r != PEL_OK) {
        /* Handle syntax or range error */
        return;
    }

    /* Inspect or manipulate entries */
    for (size_t i = 0; i < count; i++) {
        entries[i].params.seed = (uint16_t)((entries[i].params.seed + 1) & 0xFFFF);
    }

    /* Serialize back to libaom text format */
    size_t needed = 0;
    r = pel_grain_table_write(entries, count, NULL, 0, &needed);
    if (r == PEL_ERR_TRUNCATED && needed > 0) {
        char *buf = malloc(needed + 1);
        if (buf) {
            size_t written = 0;
            r = pel_grain_table_write(entries, count, buf, needed + 1, &written);
            if (r == PEL_OK) {
                /* buf now contains byte-exact libaom table text */
            }
            free(buf);
        }
    }
}
```
