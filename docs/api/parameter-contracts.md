<!-- markdownlint-disable MD013 -->
# C API parameter and version contracts

Start with `<pelorus/pelorus.h>` for the library version and `pel_result` error
codes. Include `<pelorus/deband.h>` or `<pelorus/denoise.h>` when constructing a
validated parameter block for the corresponding FFmpeg filter. These installed
headers are part of the [Pelorus 1.0 public contract](contract.md).

## Use a parameter block

Initialize the whole struct with its default function, adjust fields, then
validate before handing the values to a host integration:

```c
#include <stdio.h>

#include <pelorus/deband.h>
#include <pelorus/denoise.h>

int main(void)
{
    PelorusDebandParams params;
    PelorusDenoiseParams denoise;
    const char *field = NULL;
    pel_result result;

    pel_deband_params_default(&params);
    params.range = 21;
    result = pel_deband_params_validate(&params, &field);
    if (result != PEL_OK) {
        fprintf(stderr, "%s: %s\n", field != NULL ? field : "params",
                pel_result_str(result));
        return 1;
    }
    pel_denoise_params_default(&denoise);
    denoise.n_prev = 2;
    result = pel_denoise_params_validate(&denoise, &field);
    if (result != PEL_OK) {
        fprintf(stderr, "%s: %s\n", field != NULL ? field : "params",
                pel_result_str(result));
        return 1;
    }
    printf("range=%d n_prev=%d\n", params.range, denoise.n_prev);
    return 0;
}
```

Compile it against the installed package:

```bash
cc parameter-example.c -o parameter-example $(pkg-config --cflags --libs libpelorus)
./parameter-example
```

`pel_deband_params_default()` and `pel_denoise_params_default()` write every
field of caller-owned storage and allocate nothing. They ignore a null pointer.
The corresponding `*_validate()` function reads the struct without retaining
it. It returns `PEL_OK`, `PEL_ERR_INVALID` for a null parameter block, or
`PEL_ERR_RANGE` for a field outside the header's documented bounds.

The optional `what` output receives a pointer to an immutable library-owned
field name on error and `NULL` on success. The caller must not free or modify
that string. Passing `NULL` for `what` is supported. Calls on distinct or shared
read-only parameter blocks are thread-safe; the functions use no mutable global
state.

The default functions provide conservative starting values, not a quality
guarantee for every source. FFmpeg-facing option names and the vmafx autotune
subset are documented in [control-plane contract](control-plane.md).

## Deband parameters

Per-plane arrays use `{Y, Cb, Cr, A}` order. Thresholds and grain amplitudes are
normalized to full range, independent of bit depth.

| Field | Default | Accepted value | Meaning |
| --- | --- | --- | --- |
| `range` | `15` | `1..31` | reference-sampling radius in pixels |
| `thr[4]` | `{0.012, 0.012, 0.012, 0.012}` | each `0..0.25` | flat-test threshold per plane |
| `grain[4]` | `{0.006, 0, 0, 0}` | each `0..0.4` | synthesized grain amplitude per plane |
| `softness` | `0.5` | `0..1` | blend width; zero is a hard switch |
| `detail_thr` | `0.06` | `0..0.25` | activity threshold for detail protection |
| `sample_mode` | `PEL_DEBAND_SAMPLE_SQUARE` | one sample-mode enum | reference-tap topology |
| `blur_mode` | `PEL_DEBAND_BLUR_AVERAGE` | one blur-mode enum | flat-test comparison mode |
| `dither_mode` | `PEL_DEBAND_DITHER_BLUENOISE` | one dither-mode enum | grain/dither generator |
| `planes` | `0xF` | `uint32_t` bitmask | physical planes selected by the host; not range-checked here |
| `flags` | `DYNAMIC_GRAIN \| PROTECT_DETAIL` | `uint32_t` bitmask | optional behaviors below; not range-checked here |
| `out_depth` | `0` | `0`, `8`, `10`, `12`, or `16` | output depth; zero inherits the input |

Sample modes are `COLUMN=1` (two vertical taps), `SQUARE=2` (four rotated
taps), `ROW=3` (two horizontal taps), and `SQUARE_ROT=4` (square taps with
per-frame rotation). Blur modes are `AVERAGE=0` and `ALLREFS=1`. Dither modes
are `NONE=0`, `BAYER8=1`, and `BLUENOISE=2`.

The flag bits are `DYNAMIC_GRAIN` (re-seed each frame), `PROTECT_DETAIL` (gate
debanding on textured regions), and `COUPLING` (require all planes to agree).

## Denoise parameters

Per-plane arrays again use `{Y, Cb, Cr, A}` and normalized full-range values.

| Field | Default | Accepted value | Meaning |
| --- | --- | --- | --- |
| `sigma_s[4]` | `{0.03, 0.04, 0.04, 0.03}` | each `0..0.5` | spatial range sigma per plane |
| `sigma_t[4]` | `{0.05, 0.05, 0.05, 0.05}` | each `0..0.5` | temporal gate bandwidth per plane |
| `strength[4]` | `{0.30, 0.20, 0.20, 0.30}` | each `0..1` | dry/wet strength per plane |
| `blend` | `0.6` | `0..1` | spatial-to-temporal blend |
| `temporal_decay` | `0.8` | `0..1` | trust falloff for older frames |
| `temporal_cut` | `0.10` | `0..0.5` | temporal walk cutoff for motion or scene changes |
| `patch_radius` | `1` | `0..3` | spatial search radius; zero is temporal-only |
| `n_prev` | `3` | `0..PEL_DENOISE_MAX_PREV` (`4`) | previous frames retained |
| `denoiser_id` | `PEL_DENOISER_BILATERAL_TEMPORAL` | `NONE` or `BILATERAL_TEMPORAL` | algorithm identifier reported to interop consumers |
| `planes` | `0xF` | `uint32_t` bitmask | physical planes selected by the host; not range-checked here |
| `flags` | `TEMPORAL \| PROTECT_DETAIL` | `uint32_t` bitmask | optional behaviors below; not range-checked here |

The denoiser identifiers are `NONE=0` and `BILATERAL_TEMPORAL=1`. Flag bits are
`TEMPORAL` (enable temporal samples), `MOTION_COMP` (reserved),
`PROTECT_DETAIL` (dampen filtering on detail), and `AUTO_SIGMA` (reserved).

## Version and result helpers

`pelorus_version()` returns `PELORUS_VERSION_INT`, packed as
`(major << 16) | (minor << 8) | patch`. `pelorus_version_string()` returns the
compiled library's static version string. `pel_result_str()` returns a static,
never-null English description for a result code, including an unknown code.
The returned strings remain owned by the library and require no cleanup.

These helpers allocate nothing, perform no I/O, and are safe to call
concurrently. Compare the numeric version when code needs ordering; use the
string for diagnostics because release candidates include their suffix only in
the string.

## Stability

The functions, structures, enums, result codes, and macros described here are
stable from the 1.0 contract. Compatible additions are allowed. Removing or
changing an existing declaration follows the contract's minor-release
deprecation period and major-release migration rule.
