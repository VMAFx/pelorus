<!-- markdownlint-disable MD013 -->
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

Return codes (`pel_result`): `PEL_OK`, `PEL_ERR_ABSENT` (no Pelorus blob / section
not present — fall back to current behavior), `PEL_ERR_ABI` (major mismatch —
ignore the blob), `PEL_ERR_TRUNCATED`, `PEL_ERR_INVALID`.

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
