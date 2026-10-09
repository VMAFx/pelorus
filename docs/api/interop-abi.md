<!-- markdownlint-disable MD013 MD040 MD060 -->
# libpelorus interop ABI

The Pelorus⇄vmafx data-plane contract. Public header:
[`pelorus/interop.h`](../../libpelorus/include/pelorus/interop.h); implementation
`libpelorus/src/interop.c`. Decision: [ADR-0103](../adr/0103-interop-sidedata-abi.md).

**ABI stability tag: stable, append-only from v0.1.0.** Both Pelorus and vmafx
link or vendor the same `interop.c`. A shared conformance fixture
(`libpelorus/test/interop_test.c`) gates both repos.

## What it is

A flat, pointer-free, self-describing per-frame blob. In an FFmpeg filtergraph
it rides each `AVFrame` as `AV_FRAME_DATA_SEI_UNREGISTERED`, prefixed by the
16-byte project UUID `e1d7c4a2-6b93-4f08-9a55-0f3c2db17e64`, so it round-trips
the graph (`av_frame_copy_props` propagates side data) and never collides with
other unregistered SEIs. Inside, an 8-byte magic `"PELOR1\0\0"`, an ABI version,
a section directory, and the present sections.

In a bitstream the blob can also arrive in its zero-free carrier form (ABI 1.5,
[below](#zero-free-carrier-form-abi-15)): NVENC writes it so, under the UUID
`3f9b37b8-fd9a-4621-920e-9b78b55cf9b5`. A reader of decoded frames passes every
entry through `pel_blob_unwrap()` first.

## Layout

```
[16-byte UUID] [PelorusSideData header (48)] [PelorusSectionDir dir[count] (16*count)]
               [section payloads, each 8-byte aligned]
```

`dir[i].offset` is relative to the header start (magic[0]); a section pointer is
`blob + 16 + dir[i].offset`. Section payload starts are padded to 8 bytes so a
consumer can cast the returned pointer to the struct without an unaligned access.

## Sections

| Bit | Struct | Writer | Reader | Carries |
|---|---|---|---|---|
| `PEL_SEC_BANDING` | `PelorusBandingSection` | `vf_pelorus_deband` / `_analyze` | `vf_libvmaf*` | banding risk, flat-area fraction, per-cell risk map (`uint8`; `_analyze` only, [analysis maps](#analysis-maps-vf_pelorus_analyze)) |
| `PEL_SEC_VARIANCE` | `PelorusVarianceSection` | `vf_pelorus_analyze` | `vf_libvmaf*` | variance, edge density, texture energy, per-cell variance (`float`) and edge (`uint8`) maps ([analysis maps](#analysis-maps-vf_pelorus_analyze)) |
| `PEL_SEC_DENOISE` | `PelorusDenoiseSection` | `vf_pelorus_denoise` | telemetry | residual energy, applied strength, sigma estimate |
| `PEL_SEC_FILMGRAIN` | `PelorusFilmGrainSection` | `vf_pelorus_grain_estimate` | encoder + vmafx | film-grain params: AV1 AOM fields (→ `AV_FILM_GRAIN_PARAMS_AV1`) + a `grain_model` tag and H.274 mode scalars for HEVC/VVC (→ `AV_FILM_GRAIN_PARAMS_H274`) |
| `PEL_SEC_MOTION` | `PelorusMotionSection` | `vf_pelorus_mc` | `vf_libvmaf*`, autotune | global/peak motion, MV-field offset, scene-cut flag |
| `PEL_SEC_QPREPORT` | `PelorusQpReportSection` | closed-loop QSV reader (ADR-0119) | next pass, vmafx | encoder-honored: frame mean QP, PSNR Y/U/V, total bits, intra/inter/skipped counts, per-cell actual-QP + bits maps, `honored_fraction`, `report_source` |
| `PEL_SEC_MOTION_CONF` | `PelorusMotionConfSection` | `vf_pelorus_mc` | `vf_pelorus_denoise` (MC warp), autotune | per-cell motion confidence map (`uint8`, 0..255, low SAD ⇒ high) gating the ADR-0131 MC→denoise warp; offset/size + `conf_metric` |
| `PEL_SEC_COMPLEXITY` | `PelorusComplexitySection` | `vf_pelorus_analyze` | per-shot CRF steering (ADR-0132), autotune | per-frame complexity scalar `[0,1]` (texture + edge, folds in motion when `PEL_SEC_MOTION` is upstream), EMA-smoothed + scene-cut reset; `texture_energy`/`motion_component`/`has_scene_cut` |
| `PEL_SEC_ENC_TELEMETRY` (ABI 1.4) | `PelorusEncTelemetrySection` | encoder adapters through `pelorus/telemetry.h` (ADR-0174) | vmafx, a later pass | one record per coded frame: QP in its native scale plus an H.264-equivalent value, bits, picture type, flags, PSNR/SSIM, area shares, optional row or block maps on the encoder's grid; a `present_mask` bit per optional field ([encoder-telemetry.md](encoder-telemetry.md)) |
| `PEL_SEC_ENCODE_RECORD` (ABI 1.4) | `PelorusEncodeRecordSection` | an encode producer (ADR-0175) | vmafx provenance (`encode_record`) | raw SHA-256 of the canonical encode record plus a locator of the record file ([encode-record.md](encode-record.md)) |

`PEL_SEC_QPREPORT` (ABI 1.1) is the **read-back** half of encoder steering: unlike
sections (a)–(e), which carry *pre-encode* GPU measurements pushed *to* the encoder,
it carries the encoder's *actual* post-encode QP/bit decisions read *back* so a later
pass or vmafx can verify the requested ROI / delta-QP map (ADR-0114) was honored. It
is fed by the vendor-neutral reader stub `pel_qp_report_from_blocks` (libpelorus links
no encoder SDK). The runnable reader is the x265 `--csv` path (`pel_x265_csv_parse` +
`pel_qp_report_from_x265_frames`, ADR-0122): SDK-free per-frame stats that populate the
section with measured honored-QP/bits/PSNR + a `honored_fraction`, demonstrated by
`tools/pelorus_qp_report`. The QSV per-block reader is the HW follow-up. See
[docs/metrics/qp-feedback.md](../metrics/qp-feedback.md),
[ADR-0119](../adr/0119-qp-feedback.md), and [ADR-0122](../adr/0122-qp-feedback-csv-reader.md).

**Single-writer invariant**: Pelorus is the only writer. vmafx reads; if it ever
needs to annotate back it attaches its *own* distinct-UUID blob, never edits ours.
The writers are `pel_blob_pack` and `pel_blob_pack_into` in the mirrored
`interop.c`, and `pel_enc_telemetry_pack` in `telemetry.c`; VMAFx code must not
call them to emit a blob under the Pelorus UUID. A VMAFx-origin annotation, telemetry
included, uses VMAFx's own UUID (VMAFx ADR-1113 keeps the mirror read-only).
Filling and validating a `PelorusEncTelemetryInput` is reading the contract,
not writing a Pelorus blob.

## API

```c
/* Producer (vf_pelorus_*) */
PelorusSideData meta = { .frame_pts = pts, .producer_id = PEL_FOURCC('P','L','R','S'),
                         .plane_layout = PEL_LAYOUT_420, .bit_depth = 10 };
PelorusBandingSection band = { .global_banding_risk = r, ... };
PelorusPackSection secs[] = { { PEL_SEC_BANDING, &band, sizeof band } };
uint8_t *blob; size_t len;
if (pel_blob_pack(&meta, secs, 1, &blob, &len) == PEL_OK) {
    AVBufferRef *buf = av_buffer_create(blob, len, pel_sd_free, NULL, 0);
    av_frame_new_side_data_from_buf(frame, AV_FRAME_DATA_SEI_UNREGISTERED, buf);
}

/* Consumer (vmafx vf_libvmaf*) */
const void *p; size_t got;
if (pel_blob_find_section(sd->data, sd->size, PEL_SEC_BANDING,
                          sizeof(PelorusBandingSection), &p, &got) == PEL_OK) {
    const PelorusBandingSection *b = p;   /* read up to `got` bytes (R4) */
    /* upweight error in flat/banding-prone, low-variance cells */
}
```

`pel_blob_pack` allocates the blob (`calloc`); free with `pel_blob_free`, or hand
it to an `AVBufferRef` with a free callback that calls `pel_blob_free` (do **not**
`av_free` it — allocator mismatch). `pel_blob_find_section` returns a pointer
into the blob (no copy) and the readable byte count `min(producer, consumer)`.

### Chained producers (several blobs on one frame)

`av_frame_new_side_data_from_buf` appends; it never merges or replaces. A frame
that passed through several Pelorus producers (for example `pelorus_mc`, then
`pelorus_analyze`, then `pelorus_denoise`) therefore carries one
`AV_FRAME_DATA_SEI_UNREGISTERED` entry per producer, each with its own header and
grid. `av_frame_get_side_data` returns only the first one.

Pelorus's FFmpeg consumers (`pelorus_analyze`, `pelorus_denoise` motion
compensation, `pelorus_scenecut`) use `pelorus_sd_find_section`
(`ffmpeg-patches/files/pelorus_sidedata.h`): scan all entries **newest first**
and take the section from the newest valid Pelorus blob that contains it. Read
the grid (`grid_cols`/`grid_rows`) from that same blob, never from another one.
Every field read is guarded by the readable size (R4): a section written by an
older, shorter producer is read only as far as `got` covers
(`PEL_SD_FIELD_OK(got, Type, field)`).

`pelorus_denoise` maps the motion grid back to pixels with the producer's block
edge (`pelorus_mc_block_pitch` in `pelorus_sidedata.h`). Since ABI 1.4
`vf_pelorus_mc` names it: `block_size_log2` is 3, 4 or 5 for `bsize` 8, 16 or
32, and 0 ("not reported") for the other edges the option accepts, such as 12.
A named edge must reproduce the grid, or motion compensation is skipped for
that frame. A 1.3 motion section (32 bytes, detected by its readable size) or
the value 0 falls back to inference from the frame size and the grid
(`pelorus_mc_cell_pitch`: the single `bsize` in 8..32 with
`ceil(W/b) == grid_cols` and `ceil(H/b) == grid_rows`). On small frames
several block sizes can fit (a 96x64 frame with a 6x4 grid fits 16 to 19).
When the `vf_pelorus_mc` default (`bsize=16`) is one of them, it is assumed
and a warning saying so is logged once. If nothing fits, or the ambiguity
excludes the default (`bsize=8` on a 64x64 frame fits 8 and 9), motion
compensation is skipped for that frame and a warning is logged once.

### Analysis maps (vf_pelorus_analyze)

`vf_pelorus_analyze_vulkan` fills the map fields `PelorusBandingSection` and
`PelorusVarianceSection` have carried since ABI 1.0
([ADR-0177](../adr/0177-analyze-per-cell-maps.md)). The layout and the field
types are unchanged, so the maps took no minor bump and a reader of any minor
can read them. `vf_pelorus_deband` still writes a banding section with an
empty grid and no map.

**Grid.** The header's `grid_cols` x `grid_rows` is the cell grid of the frame:
cells are `cell` x `cell` luma pixels (`cell` option, a power of two in 8..64,
default 32), row-major from the top-left, `grid_cols = ceil(W / cell)` and
`grid_rows = ceil(H / cell)`. The last column and row are partial when the
frame size is not a multiple of the cell; their values cover only the pixels
inside the frame. A frame smaller than one cell is a 1x1 grid. The filter
refuses a grid above 2^20 cells (`8192x8192` at `cell=8` is the largest) when
the link is configured. A reader that needs the pixel pitch takes the one power
of two in 8..64 that reproduces both dimensions; when `grid_cols` and
`grid_rows` are both 1 the single cell covers the whole frame.

**Maps** (`grid_cols * grid_rows` elements each):

| Field pair | Element | Value |
| --- | --- | --- |
| `PelorusBandingSection.cell_data_offset` / `_size` | `uint8` | round(255 x banding score); the score in [0, 1] is the one `roi=1` steers by: the larger of the fine per-cell score and the coarse inter-cell score ([ADR-0133](../adr/0133-analyze-coarse-banding-cambi.md)) |
| `PelorusVarianceSection.var_cell_offset` / `_size` | `float` | luma variance of the cell in the [0, 1] sample domain (at most 0.25); the grid mean is `global_variance` |
| `PelorusVarianceSection.edge_cell_offset` / `_size` | `uint8` | round(255 x edge density of the cell); the grid mean / 255 is `edge_density` within rounding |

The maps follow the packed sections in that order, each at the next 8-aligned
blob-relative offset, and `total_size` ends at the last map byte. Every map
passes `pel_blob_map(blob, len, offset, size, grid_cols * grid_rows,
elem_size, &ptr)` (the ABI 1.4 helper; a reader on 1.3 headers applies the
same checks itself); read the `float` map with `memcpy` unless the blob base is
8-aligned (R5). With `maps=0` all six fields are 0 and the blob is the
scalar-only one; a reader then uses the frame scalars, as it does for a blob
from an older Pelorus.

```c
const void *p, *band = NULL;
size_t got;
if (pel_blob_find_section(sd->data, sd->size, PEL_SEC_BANDING, sizeof(PelorusBandingSection),
                          &p, &got) == PEL_OK && got == sizeof(PelorusBandingSection)) {
    PelorusBandingSection b;
    memcpy(&b, p, sizeof(b));
    if (b.cell_data_size != 0 &&
        pel_blob_map(sd->data, sd->size, b.cell_data_offset, b.cell_data_size,
                     (uint32_t)grid_cols * grid_rows, 1, &band) == PEL_OK) {
        /* band[i] / 255.0 is the banding score of cell i */
    }
}
```

Each frame through the filter costs 6 bytes per cell plus at most 14 bytes of
alignment: 12 KiB at 1920x1080 and 48 KiB at 3840x2160 with `cell=32`. An
encoder with `udu_sei=1` writes the blob into the bitstream, so pass `maps=0`
there when the maps are not needed downstream. `hevc_nvenc` writes a blob that
does not fit its per-picture SEI limit without the maps: their offsets and
sizes are zero, as in a `maps=0` blob, so read maps from the frame side data in
the filter graph, not from an HEVC NVENC stream
([ADR-0181](../adr/0181-hevc-nvenc-sei-header-budget.md)). Both NVENC encoders
write the blob as its zero-free carrier
([ADR-0183](../adr/0183-sidedata-zero-free-carrier.md)): flat content gives maps
that are mostly zero bytes, which NVENC otherwise writes truncated.

### QP-report reader stub (closed loop)

`pel_qp_report_from_blocks` folds an encoder's per-block actual-QP grid onto the
shared cell grid and fills a `PelorusQpReportSection` for packing. It takes an
abstract `PelorusQpReportInput` (no oneVPL/NVENC types) so libpelorus links no
encoder SDK — vmafx vendors `interop.c` unchanged. See
[docs/metrics/qp-feedback.md](../metrics/qp-feedback.md) for the QSV mapping and
the working-ABI-vs-documented-stub status.

**Ownership / lifetime**: `out_section` and `qp_cell_out` are caller-owned and
caller-allocated; the function writes into them and retains no reference (it
allocates nothing). `qp_cell_out` must hold `grid_cols*grid_rows` bytes or the
call returns `PEL_ERR_RANGE`. **Thread-safety**: the function holds no global or
static state, so concurrent calls on non-aliased buffers are safe.

Return codes (`pel_result`): `PEL_OK`, `PEL_ERR_ABSENT` (no Pelorus blob / section
not present — fall back to current behavior), `PEL_ERR_ABI` (major mismatch —
ignore the blob), `PEL_ERR_TRUNCATED`, `PEL_ERR_INVALID`, `PEL_ERR_RANGE`
(`pel_qp_report_from_blocks` only — the `qp_cell_out` buffer is smaller than the
cell grid). ABI 1.4 appends `PEL_ERR_MISMATCH` (-8): a recomputed encode-record
digest differs from the expected one (`pel_encode_record_verify`). A consumer
that switches on `pel_result` maps an unknown value to its generic error.

### ABI 1.4 helpers in `interop.c`

| Function | Purpose |
|---|---|
| `pel_blob_pack_into(meta, sections, nb, buf, cap, &len)` | `pel_blob_pack` into a caller buffer, no allocation; `cap` 0 asks for the length (`PEL_ERR_RANGE`, `len` set) |
| `pel_blob_map(blob, len, offset, size, elem_count, elem_size, &ptr)` | locate a map a section references: framing, `size == elem_count * elem_size` (64-bit), 8-aligned offset, no overlap with the header or `dir[]` (`offset >= header_size + section_count * 16`; each `PEL_ERR_ABI`), inside `total_size` (`PEL_ERR_TRUNCATED`) |
| `pel_encode_record_digest_text(sec, got, text, cap)` | the 71-character `sha256:` text VMAFx stores, from a `PelorusEncodeRecordSection`; `cap >= PEL_DIGEST_TEXT_SIZE` (72) |

The writer-side telemetry contract (`telemetry.c`) and the encode-record
canonicaliser (`encode_record.c`, `sha256.c`) are separate units that the
VMAFx mirror does not carry at RC4 (ADR-0174 decision 11). The shared fixture
therefore calls only `interop.c`.

### x265 CSV reader (the runnable closed loop, ADR-0122)

`pel_x265_csv_parse` reads an x265 `--csv --csv-log-level 2` file into per-frame
`PelorusX265Frame` rows (locating the `Type/POC/QP/Bits/PSNR` columns by header
name); `pel_qp_report_from_x265_frames` folds those rows into a frame-stats-only
`PelorusQpReportSection` (bit-weighted mean QP, summed bits, bit-weighted PSNR,
`qp_valid = 0`) and computes `honored_fraction` against an optional requested
per-frame QP array. This is the SDK-free, end-to-end-runnable readback surface
(no libx265 link — pure CSV parsing); see
[docs/metrics/qp-feedback.md](../metrics/qp-feedback.md) for the narrative and the
measured demonstrator output.

**Stability**: both functions are public libpelorus API and follow the same
stability tag as the rest of this header — append-only; signatures do not change
incompatibly. They are *consumer* helpers and touch no wire layout (the
`PELORUS_ABI_MINOR` is unchanged by their addition).

**Path encoding** ([ADR-0149](../adr/0149-windows-utf8-paths.md)): `path` is a
NUL-terminated **UTF-8** string on every platform, never the Windows ANSI code
page. On POSIX the bytes go to `fopen` unchanged (byte-for-byte the earlier
behavior: no validation, so a non-UTF-8 byte name still opens). On Windows the
reader converts the path strictly to UTF-16 (`MultiByteToWideChar` with
`MB_ERR_INVALID_CHARS`) and opens it with `_wfopen`, so the result does not depend
on the active code page. It adds no `\\?\` prefix: to exceed `MAX_PATH`, pass an
extended-length path such as `\\?\C:\very\long\x265.csv` (backslashes, absolute),
or opt the host process into long paths. A path ends at its first NUL; there is
no length argument, so an embedded NUL cannot be expressed. Windows hosts that
receive paths as UTF-16 (`wmain`, `CommandLineToArgvW`, the shell) convert them
with `WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, ...)` first, as the
`pelorus_qp_report` tool does.

**Ownership / lifetime**: `out_frames` (parse) and `frames` + `out_section`
(fold) are caller-owned and caller-allocated; the functions write into them and
retain no reference. `pel_x265_csv_parse` opens and closes the file itself and
uses bounded stack buffers; on Windows only, it also makes one temporary heap
copy of the path in UTF-16 (at most 64 KiB), which it frees before returning on
every path. `pel_qp_report_from_x265_frames` allocates nothing.
`requested_qp`, when non-NULL, must hold `nb` entries in the same order as
`frames`.

**Thread-safety**: neither function holds global or static state, so concurrent
calls are safe as long as buffers do not alias. `pel_x265_csv_parse` opens,
reads and closes a private `FILE *` of its own (`fopen` on POSIX, `_wfopen` on
Windows), so two concurrent calls on the same *path* are safe (independent
streams); the caller must not share one open `FILE *` across them (the function
never accepts one). The Windows UTF-16 path copy is allocated and freed within
the one call and is never shared between calls.

Return codes (`pel_result`): `pel_x265_csv_parse` returns `PEL_OK`,
`PEL_ERR_INVALID` (NULL args / `cap == 0`; on Windows also a path that is not
well-formed UTF-8), `PEL_ERR_ABSENT` (the file cannot be opened, or no header
with recognizable `QP` + `Bits` columns), `PEL_ERR_NOMEM` (Windows only: the
UTF-16 path copy could not be allocated), `PEL_ERR_TRUNCATED` (a read error
mid-file), or `PEL_ERR_RANGE` (more frame rows than `cap` — `out_count` is
clamped to `cap`, the parsed rows are still usable).
`pel_qp_report_from_x265_frames` returns `PEL_OK` or `PEL_ERR_INVALID` (NULL
`frames`/`out_section`, or `nb == 0`).

What `pel_x265_csv_parse` leaves in `*out_count` depends on where it stopped:

| Result | `*out_count` |
| --- | --- |
| `PEL_ERR_INVALID` from argument validation (NULL `path`/`out_frames`/`out_count`, `cap == 0`) | not written (the caller's value is kept) |
| `PEL_ERR_INVALID` (ill-formed UTF-8), `PEL_ERR_ABSENT`, `PEL_ERR_NOMEM` | 0 |
| `PEL_ERR_TRUNCATED` | the rows parsed before the read error (they are in `out_frames`) |
| `PEL_ERR_RANGE` | `cap` |
| `PEL_OK` | the rows parsed |

Treat any result other than `PEL_OK` and `PEL_ERR_RANGE` as a failed read.
ADR-0149 added two results a caller may not have branched on before, both on
Windows only: `PEL_ERR_INVALID` for an ill-formed UTF-8 path and
`PEL_ERR_NOMEM`.

On Windows the path checks run in a fixed order, so the result depends only on
the bytes, never on the code page or locale:

| Check (Windows) | Result | `errno` |
| --- | --- | --- |
| more than 98301 bytes (3 × 32767; cannot be any Windows path), not decoded | `PEL_ERR_ABSENT` | `ENAMETOOLONG` |
| ill-formed UTF-8: stray/truncated sequence, overlong form, encoded surrogate, > U+10FFFF | `PEL_ERR_INVALID` | `EILSEQ` |
| more than 32767 UTF-16 code units | `PEL_ERR_ABSENT` | `ENAMETOOLONG` |
| UTF-16 copy allocation fails | `PEL_ERR_NOMEM` | `ENOMEM` |
| `_wfopen` fails (missing, denied, bad name, …) | `PEL_ERR_ABSENT` | as set by `_wfopen` |

On POSIX, every `fopen` failure is `PEL_ERR_ABSENT` with `errno` as `fopen` set
it, exactly as before ADR-0149. `errno` is diagnostic only: the `pel_result` is
the contract.

## Conformance fixture files (ADR-0148)

The shared fixture (`libpelorus/test/interop_test.c`, which VMAFx vendors as
`core/test/test_pelorus_interop.c`) writes a few small files into the test's
working directory: `pelorus_x265_csv_test.csv` for the x265 CSV reader, and
`pelorus_fixture_*.tmp` for its own file-safety regression. Every one is
created exclusively and owner-only, and removed before the test returns.

| Property | POSIX | Windows |
|---|---|---|
| Creation | `open(O_CREAT\|O_EXCL\|O_NOFOLLOW\|O_CLOEXEC, 0600)` | `CreateFileA(CREATE_NEW, FILE_FLAG_OPEN_REPARSE_POINT)` |
| Access from the first open | mode `0600`; asserted under `umask(0)` | protected DACL `D:P(A;;FA;;;OW)`, owner only; nothing inherited |
| Existing file, link, or dangling link at the name | refused (`EEXIST`); target untouched | refused (`ERROR_FILE_EXISTS`); target untouched |
| Link cases in the regression | always run | need Developer Mode or `SeCreateSymbolicLinkPrivilege`; skipped with a note otherwise |

If a killed run leaves one of these files behind, the next run fails with
`exclusive create refused (stale file from an aborted run?)`. The test never
deletes a file it did not create, so remove the named file by hand.

Build requirements for a consumer compiling the fixture: on glibc the body
needs POSIX.1-2008 declarations. The Pelorus target passes
`-D_POSIX_C_SOURCE=200809L`; VMAFx's `_GNU_SOURCE` also works. On Windows it
links `advapi32`, which Meson's default `c_winlibs` provides; MSVC also gets it
through `#pragma comment(lib, "advapi32.lib")`. After a Pelorus change to the
fixture, VMAFx re-pins `PELORUS_VENDOR_SHA` and re-vendors with
`scripts/sync-pelorus-interop.sh --update`. The rendered mirror is byte-identical
to the Pelorus body except for the include rewrite; see
[research digest 0148](../research/0148-owner-only-exclusive-test-fixtures.md).

## ABI 1.4

ABI 1.4 ([ADR-0174](../adr/0174-encoder-telemetry-abi-1-4.md)) shipped in
v0.4.0-rc.1, after ABI 1.3 in v0.3.0; ABI 1.5 adds the
[zero-free carrier form](#zero-free-carrier-form-abi-15). The 1.4 minor bump
covers three additions, each locked by `_Static_assert` on its size and on
every member offset:

| Change | Struct and size | Specification |
|---|---|---|
| `PEL_SEC_ENC_TELEMETRY` (bit 8, section i): one normalised encoder-telemetry record per coded frame, optional row or block maps | `PelorusEncTelemetrySection`, 104 bytes | [encoder-telemetry.md](encoder-telemetry.md) (#86, #220, #221) |
| `PEL_SEC_ENCODE_RECORD` (bit 9, section j): SHA-256 digest of the canonical encode record plus a locator | `PelorusEncodeRecordSection`, 48 bytes | [encode-record.md](encode-record.md), [ADR-0175](../adr/0175-encode-provenance-record.md) (#81) |
| `PelorusMotionSection.block_size_log2` appended at the tail (0 = not reported) | 32 -> 36 bytes | ADR-0174 decision 9 (#218, BUG-035) |

A 1.4 reader detects a 1.3 motion section by its readable size
(`PEL_SD_FIELD_OK(got, PelorusMotionSection, block_size_log2)` is false) and
keeps the `pelorus_mc_cell_pitch` fallback above. A 1.3 consumer reading a 1.4
blob gets 32 readable bytes (R4) and never sees the field. VMAFx's re-vendor
steps are in [encoder-telemetry.md](encoder-telemetry.md#how-vmafx-re-vendors-abi-14).

Migration for a reader of the motion section (the commit's `Migration:`
footer):

```c
/* Before (ABI 1.3) */
_Static_assert(sizeof(PelorusMotionSection) == 32, "motion section ABI");
bsize = pelorus_mc_cell_pitch(w, h, grid_cols, grid_rows);
/* After (ABI 1.4) */
_Static_assert(sizeof(PelorusMotionSection) == 36, "motion section ABI");
if (PEL_SD_FIELD_OK(got, PelorusMotionSection, block_size_log2) &&
    mo->block_size_log2 != 0)
    bsize = 1u << mo->block_size_log2;
else
    bsize = pelorus_mc_cell_pitch(w, h, grid_cols, grid_rows);
```

## Zero-free carrier form (ABI 1.5)

`PELORUS_ABI_MINOR` is 5 ([ADR-0183](../adr/0183-sidedata-zero-free-carrier.md),
[research 0183](../research/0183-sidedata-zero-free-carrier.md)). No section,
field or bit changes; ABI 1.5 adds a second way to carry the same blob in a
bitstream.

**Why.** `h264_nvenc` and `hevc_nvenc` write a user data unregistered SEI
truncated when its emulation prevention bytes exceed `ceil(P / 3) + 3` for a
`P`-byte payload, and the decoder drops it without an error (issue #284). Zero
runs cause emulation prevention, and the analyze maps of flat content are
mostly zeros. A payload without any zero byte needs no emulation prevention.

**Layout.**

```
[16-byte carrier UUID 3f9b37b8-fd9a-4621-920e-9b78b55cf9b5] [COBS(image)]
```

`image` is the blob without its UUID: the header, `dir[]`, the sections and the
maps, exactly as `pel_blob_pack` lays them out. COBS (Cheshire and Baker, 1999)
without a frame delimiter: the encoding is a sequence of blocks, each a code
byte `c` (1 to 255) followed by `c - 1` non-zero bytes. Every block except a
full one (`c = 255`) and the last one stands for one zero byte after its data.
A full block that ends the image is not followed by an empty block. The result
holds no zero byte and is one byte longer than the image, plus one byte per
254 bytes without a zero (`PEL_CARRIER_MAX_LEN(n)` bounds a carrier of an
`n`-byte blob). The carrier UUID has no zero byte either.

Known answer (the conformance fixture checks it): the header-only image
`50 45 4c 4f 52 31 00 00 01 00 05 00 30 00 00 00 00 00 00 00 00 00 30 00 40 e2
01 00 00 00 00 00 00 0a 10 00 09 00 00 00 50 4c 52 53 00 00 00 00` encodes, after
the carrier UUID, to `07 50 45 4c 4f 52 31 01 02 01 02 05 02 30 01 01 01 01 01
01 01 01 02 30 04 40 e2 01 01 01 01 01 01 03 0a 10 02 09 01 01 05 50 4c 52 53 01
01 01 01`.

| Function | Purpose |
|---|---|
| `pel_blob_carrier_encode(blob, len, out, cap, &out_len)` | blob to carrier into a caller buffer, no allocation; `PEL_ERR_ABSENT` for a payload that is not a Pelorus blob (write it unchanged); `cap` 0 asks for the length (`PEL_ERR_RANGE`, `out_len` set) |
| `pel_blob_unwrap(data, len, scratch, cap, &blob, &blob_len)` | either form to the blob: a blob comes back as `data` itself (no copy, `scratch` untouched), a carrier is decoded into `scratch` (`cap >= len` always suffices) as blob UUID + image; `PEL_ERR_ABSENT` for any other UUID |

The decoder is strict and allocates nothing. It returns `PEL_ERR_ABI` for a zero
byte, a block that runs past the end, or an empty final block after a full one
(a form the encoder never writes, so a payload decodes to one image and
encodes back to the same bytes), and `PEL_ERR_TRUNCATED` when the image is
shorter than the 48-byte header. Every other framing check stays where it is:
`pel_blob_find_section()` and `pel_blob_map()` on the unwrapped blob, so a
carrier cut on a block boundary reports `PEL_ERR_TRUNCATED` there like a short
blob. Decoding a 49 KB carrier took at most 0.17 ms on a debug build.

**Writers.** Pelorus filters keep attaching the blob, and in-graph consumers
read it in place as before. `h264_nvenc` and `hevc_nvenc` (patch 0022) write
every Pelorus blob as its carrier; QSV (patch 0019), Vulkan Video (patch 0020)
and the software encoders write the blob, which they carry intact. Pelorus is
still the only writer: VMAFx must not call `pel_blob_carrier_encode` on its
own data.

**Readers.** Call `pel_blob_unwrap()` on every `AV_FRAME_DATA_SEI_UNREGISTERED`
entry a decoder exports, then read sections as before:

```c
/* Before (ABI 1.4): only the blob form */
if (pel_blob_find_section(sd->data, sd->size, PEL_SEC_BANDING,
                          sizeof(PelorusBandingSection), &p, &got) == PEL_OK) { ... }

/* After (ABI 1.5): either form; scratch holds at least sd->size bytes */
const uint8_t *blob;
size_t len;
if (pel_blob_unwrap(sd->data, sd->size, scratch, scratch_cap, &blob, &len) == PEL_OK &&
    pel_blob_find_section(blob, len, PEL_SEC_BANDING,
                          sizeof(PelorusBandingSection), &p, &got) == PEL_OK) { ... }
```

A reader built on ABI 1.4 sees a carrier as an unregistered SEI with a foreign
UUID: `pel_blob_is_present()` returns 0 and the frame has no Pelorus data for
it (R3). It never misreads a carrier. VMAFx re-pins to the release that ships
ABI 1.5 and adds the call ([mirror contract](mirror-contract.md)); until then
it scores frames from NVENC streams unweighted, the same result as the
truncated SEI it replaces on flat content.

## Stability rules (normative — see interop.h)

- **R1 Append-only**: new fields at the end of a section; new sections take a new
  bit. **R2**: never reorder, resize, remove, or repurpose. **R3**: every section
  is optional via `section_mask`; ignore unknown bits, tolerate absent ones.
  **R4**: read `min(producer_size, your_known_size)`. **R5**: little-endian wire,
  pointer-free. **R6**: `PELORUS_ABI_MINOR` bumps on additions, a new carrier
  form included (ABI 1.5); `MAJOR` never (additive evolution is forced by
  R1/R2).
- Adding a field/section: bump `PELORUS_ABI_MINOR`, extend the conformance
  fixture, document the new field here. Changing meaning: mint a **new** section
  bit; leave the old bit reserved.

## Control plane (autotune)

Separately versioned. Pelorus exposes filter strengths as `AVOption`s and the
banding/variance sections as perceptual-weighting input; vmafx's `libvmaf_tune`
/ `vmafx-server` `/v1/score` / `vmaf-mcp` drive the VMAF-in-the-loop search. The
autotune-relevant deband `AVOption`s are frozen as a stable contract in
[control-plane.md](control-plane.md) ([ADR-0110](../adr/0110-avoption-control-plane-contract.md)).
See [ADR-0106](../adr/0106-autotune-control-plane.md) and
[docs/usage/ffmpeg.md](../usage/ffmpeg.md).
