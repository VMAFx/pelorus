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
| `PEL_SEC_BANDING` | `PelorusBandingSection` | `vf_pelorus_deband` / `_analyze` | `vf_libvmaf*` | banding risk, flat-area fraction, per-cell risk map offset |
| `PEL_SEC_VARIANCE` | `PelorusVarianceSection` | `vf_pelorus_analyze` | `vf_libvmaf*` | variance, edge density, texture energy, per-cell maps |
| `PEL_SEC_DENOISE` | `PelorusDenoiseSection` | `vf_pelorus_denoise` | telemetry | residual energy, applied strength, sigma estimate |
| `PEL_SEC_FILMGRAIN` | `PelorusFilmGrainSection` | `vf_pelorus_grain_estimate` | encoder + vmafx | film-grain params: AV1 AOM fields (→ `AV_FILM_GRAIN_PARAMS_AV1`) + a `grain_model` tag and H.274 mode scalars for HEVC/VVC (→ `AV_FILM_GRAIN_PARAMS_H274`) |
| `PEL_SEC_MOTION` | `PelorusMotionSection` | `vf_pelorus_mc` | `vf_libvmaf*`, autotune | global/peak motion, MV-field offset, scene-cut flag |
| `PEL_SEC_QPREPORT` | `PelorusQpReportSection` | closed-loop QSV reader (ADR-0119) | next pass, vmafx | encoder-honored: frame mean QP, PSNR Y/U/V, total bits, intra/inter/skipped counts, per-cell actual-QP + bits maps, `honored_fraction`, `report_source` |
| `PEL_SEC_MOTION_CONF` | `PelorusMotionConfSection` | `vf_pelorus_mc` | `vf_pelorus_denoise` (MC warp), autotune | per-cell motion confidence map (`uint8`, 0..255, low SAD ⇒ high) gating the ADR-0131 MC→denoise warp; offset/size + `conf_metric` |
| `PEL_SEC_COMPLEXITY` | `PelorusComplexitySection` | `vf_pelorus_analyze` | per-shot CRF steering (ADR-0132), autotune | per-frame complexity scalar `[0,1]` (texture + edge, folds in motion when `PEL_SEC_MOTION` is upstream), EMA-smoothed + scene-cut reset; `texture_energy`/`motion_component`/`has_scene_cut` |

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
edge, recovered from the frame size and the grid
(`pelorus_mc_cell_pitch`: the single `bsize` in 8..32 with
`ceil(W/b) == grid_cols` and `ceil(H/b) == grid_rows`). `PelorusMotionSection`
carries no block-size field in ABI 1.3; ABI 1.4 appends `block_size_log2`
(see [ABI 1.4](#abi-14-specified-not-implemented)), and this inference stays
as the fallback for 1.3 blobs. On small frames several block sizes can fit (a
96x64 frame with a 6x4 grid fits 16 to 19). When the `vf_pelorus_mc` default
(`bsize=16`) is one of them, it is assumed and a warning saying so is logged
once. If nothing fits, or the ambiguity excludes the default, motion
compensation is skipped for that frame and a warning is logged once.

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
cell grid).

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

## ABI 1.4 (specified, not implemented)

The shipped ABI is 1.3. ABI 1.4 is fixed in specification before any header
changes ([ADR-0174](../adr/0174-encoder-telemetry-abi-1-4.md)). The
implementing pull request bumps `PELORUS_ABI_MINOR` to 4 for three additions:

| Change | Struct and size | Specification |
|---|---|---|
| `PEL_SEC_ENC_TELEMETRY` (bit 8, section i): one normalised encoder-telemetry record per coded frame, optional row or block maps | `PelorusEncTelemetrySection`, 104 bytes | [encoder-telemetry.md](encoder-telemetry.md) (#86, #220, #221) |
| `PEL_SEC_ENCODE_RECORD` (bit 9, section j): SHA-256 digest of the canonical encode record plus a locator | `PelorusEncodeRecordSection`, 48 bytes | [encode-record.md](encode-record.md), [ADR-0175](../adr/0175-encode-provenance-record.md) (#81) |
| `PelorusMotionSection.block_size_log2` appended at the tail (0 = not reported) | 32 -> 36 bytes | ADR-0174 decision 9 (#218, BUG-035) |

A 1.4 reader detects a 1.3 motion section by its readable size
(`PEL_SD_FIELD_OK(got, PelorusMotionSection, block_size_log2)` is false) and
keeps the `pelorus_mc_cell_pitch` fallback above. VMAFx's re-vendor steps are
in [encoder-telemetry.md](encoder-telemetry.md#how-vmafx-re-vendors-abi-14).

## Stability rules (normative — see interop.h)

- **R1 Append-only**: new fields at the end of a section; new sections take a new
  bit. **R2**: never reorder, resize, remove, or repurpose. **R3**: every section
  is optional via `section_mask`; ignore unknown bits, tolerate absent ones.
  **R4**: read `min(producer_size, your_known_size)`. **R5**: little-endian wire,
  pointer-free. **R6**: `PELORUS_ABI_MINOR` bumps on additions; `MAJOR` never
  (additive evolution is forced by R1/R2).
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
