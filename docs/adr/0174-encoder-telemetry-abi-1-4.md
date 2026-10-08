<!-- markdownlint-disable MD013 MD060 -->
# ADR-0174: Interop ABI 1.4 adds a normalised encoder-telemetry section, the motion block size and an encode-record section in one minor bump, specified before the code

- **Status**: Proposed
- **Implementation**: pending (#86)
- **Date**: 2026-10-09
- **Deciders**: Lusoris
- **Tags**: interop, abi, telemetry, vmafx
- **Related**: [ADR-0103](0103-interop-sidedata-abi.md), [ADR-0119](0119-qp-feedback.md), [ADR-0122](0122-qp-feedback-csv-reader.md), [ADR-0172](0172-roadmap-milestone-map.md), [ADR-0175](0175-encode-provenance-record.md)

## Context

Issue #86 asks for one vendor-neutral description of what an encoder did per
frame and per block, read from several encoders through adapters. VMAFx needs
the layout in its interop mirror before `1.0.0-rc.4` (VMAFx/vmafx#2411): its
next re-vendor, right after its Praetor bump, takes the EUPL-1.2 relicence and
ABI 1.4 by moving the pin in `scripts/sync-pelorus-interop.sh` (VMAFx
comment on #218). Three more issues ride the same minor:

- #218 (BUG-035): `PelorusMotionSection` has no block size, so readers guess
  it from the frame size and the grid (`pelorus_mc_cell_pitch`,
  [interop-abi.md](../api/interop-abi.md), "Chained producers").
- #220 and VMAFx/vmafx#2271, VMAFx/vmafx#2510: the decode-side
  stream-metadata library in VMAFx shares its field names with this schema,
  and a test that loads both schemas enforces it.
- #221 and VMAFx/vmafx#2147: encoders driven outside FFmpeg must be able to
  fill the telemetry input, so it cannot carry FFmpeg types.

The ABI 1.1 section `PEL_SEC_QPREPORT` ([ADR-0119](0119-qp-feedback.md)) is
the closest existing shape and cannot be extended into this schema:

- Its per-cell QP map is `int8`, which cannot hold an AV1 `base_q_idx`
  (0..255).
- `avg_qp` carries no scale, so an AV1 and an HEVC value cannot be compared.
- `psnr_*` is "NaN/0 if absent" (`interop.h:312`), so a consumer cannot tell
  "not reported" from zero, which #86 acceptance item 3 forbids.
- The x265 fold (`pel_qp_report_from_x265_frames`) puts a whole GOP into one
  section, while #86 and VMAFx/vmafx#2271 need one record per frame.

What encoders expose differs widely (evidence in
[research 0174](../research/0174-abi-1-4-telemetry-provenance.md)).
FFmpeg's one cross-encoder channel is `AV_PKT_DATA_QUALITY_STATS`: a quality
factor, a picture type and optional SSE per packet. In FFmpeg n9.0.1,
`libaomenc` writes quality factor 0 there, which means "no QP", and
`libsvtav1` writes `headerPtr->qp * FF_QP2LAMBDA` in a scale this ADR has not
verified. The VMAFx mirror has two constraints of its own. It renders each
mirrored file with exactly one rewritten `#include "pelorus/..."`
(`render_vendor` in VMAFx `scripts/sync-pelorus-interop.sh`). Its manifest is
an exact path list (`scripts/ci/pelorus-mirror-paths.txt`), so a new file
needs a VMAFx change.

## Decision

We will publish ABI 1.4 as one minor bump with three additions. This ADR and
the specification pages fix their names, bits and layouts before any header
changes.

1. **One bump, three additions.** `PELORUS_ABI_MINOR` goes from 3 to 4 for:
   - (i) `PEL_SEC_ENC_TELEMETRY = 1u << 8`, struct
     `PelorusEncTelemetrySection`, 104 bytes, specified in
     [encoder-telemetry.md](../api/encoder-telemetry.md);
   - (j) `PEL_SEC_ENCODE_RECORD = 1u << 9`, struct
     `PelorusEncodeRecordSection`, 48 bytes, decided in
     [ADR-0175](0175-encode-provenance-record.md);
   - `uint8_t block_size_log2` plus `uint8_t _pad1[3]` appended to
     `PelorusMotionSection` after `_pad[3]`. The struct grows from 32 to 36
     bytes.
2. **Spec first, then code.** This change is documentation only; `interop.h`
   stays at 1.3. The implementing pull request adds the structs, the
   `_Static_assert` sizes (`== 104`, `== 48`, `== 36`), the two bits in
   `section_bit_valid`, the ABI 1.4 fixture cases and the minor bump in one
   commit. VMAFx pins that merge commit. A layout change after this ADR is
   accepted needs a new ADR.
3. **A new section, not a `PEL_SEC_QPREPORT` extension.** `PEL_SEC_QPREPORT`
   keeps its role as the ROI honoured-QP loop. Adapters may fill both.
4. **"Not reported" is a bit, never a value.** Each record carries a 64-bit
   `present_mask` with one `PEL_TLM_F_*` bit per optional field. The
   bit-to-field assignment is append-only, and a retired bit is never reused.
   A clear bit means the field is not reported: the writer stores zero and the
   reader ignores the value. Each adapter also publishes a static capability
   mask (`pel_tlm_adapter_caps(adapter, codec, &mask)`) of the bits it can
   fill. A record's mask must be a subset of its adapter's mask. In the JSON
   form, a field that is not reported is omitted, never written as `null` or
   `0`.
5. **QP: native value and normalised value.** `avg_qp` is the frame-mean QP in
   the scale named by `qp_scale` (H.26x `SliceQpY`, AV1 or VP9
   `base_q_idx`, AV1 encoder `--qp` 0..63). `avg_qp_norm` is the
   H.264-equivalent QP: `6 * log2(Qstep / 0.625)`, where `Qstep` is the
   frame's luma step size in H.264 units at 8 bits. This is the identity for
   the `SliceQpY` scale. The constants for the `base_q_idx` scales are a table
   delivered with the adapters. The #86 acceptance test (libx264, libx265 and
   libsvtav1 at matched step size agree within 1.0) gates that table.
6. **One record per frame, with optional maps on the encoder's own grid.** The
   record describes one coded frame. `granularity` (frame, row, block) says
   whether maps follow. The maps sit on the encoder's element grid
   (`map_cols` x `map_rows`, edge `1 << block_size_log2`). They are not folded
   onto the Pelorus analysis grid in the blob header, which has a different
   pitch. There are three optional maps, each with an 8-aligned blob-relative
   offset and a size:
   - `qp_map`: `int16` QP per element in the native scale, Q2 fixed point
     (`round(qp * 4)`, the quarter-pel convention of `mv_field`);
   - `bits_map`: `uint32` bits per element;
   - `mode_map`: `uint8` `enum pel_block_mode` per element.
7. **Bounds (HISS-02, HISS-03).** These limits hold:
   - `map_cols * map_rows <= PEL_TLM_MAP_MAX_ELEMS` (`1u << 20`). That covers
     8K at 8x8 (518 400 elements). The worst case is 7 340 032 map bytes per
     frame.
   - `block_size_log2` is 2..7 for row and block granularity and 0 for frame
     granularity.
   - Each map size equals elements x element size exactly.
   - Each map ends inside `total_size`.

   Readers check all of this before they form a pointer, or return
   `PEL_ERR_ABI`/`PEL_ERR_TRUNCATED`. The pack helper writes into a buffer
   the caller sizes once at initialisation
   (`pel_enc_telemetry_blob_size`). It allocates nothing per frame.
8. **The input does not assume FFmpeg (#221).** A new header,
   `pelorus/telemetry.h`, includes only `pelorus/interop.h`, which keeps
   VMAFx's one-include render rule. `PelorusEncTelemetryInput` is the section
   struct for the scalars plus three `const` map pointers. The adapters are
   plain C in libpelorus: the FFmpeg one parses the documented
   `AV_PKT_DATA_QUALITY_STATS` byte layout without FFmpeg headers, and the CSV
   and stat-file readers follow the x265 reader. The adapter `EXTERNAL` is
   filled by callers such as VMAFx's codec-adapter package.
9. **Motion block size (#218).** `vf_pelorus_mc` is the only
   `PEL_SEC_MOTION` writer (`vf_pelorus_analyze` packs variance, banding and
   complexity only, `vf_pelorus_analyze_vulkan.c:535-591`). It writes
   `log2(bsize)`: 3, 4 or 5. The value 0 means "not reported". A reader
   detects a 1.3 blob by its readable size
   (`PEL_SD_FIELD_OK(got, PelorusMotionSection, block_size_log2)` is false),
   not by the value. In that case it keeps the `pelorus_mc_cell_pitch`
   fallback.
10. **Field names.** The normative telemetry names are the JSON keys in the
    field registry `libpelorus/schema/telemetry-fields.json`, which lands with
    the code. Each key has a `PEL_TLM_F_<KEY>` bit in `interop.h`, and the
    registry marks it `shared` or `encoder_only`. VMAFx/vmafx#2271 uses the
    `shared` names, types and units unchanged. `picture_type` takes FFmpeg's
    `AVPictureType` numbers for I, P and B (1, 2, 3). Enums are written as
    lower-case names in JSON, as in VMAFx ADR-2073. The cross-walk and the
    parity test (#220) are specified in
    [encoder-telemetry.md](../api/encoder-telemetry.md).
11. **What the VMAFx mirror carries at RC4.** The ABI 1.4 re-pin changes only
    the existing ten mirrored files: `interop.h`, `interop.c`, the fixture,
    and `pelorus.h` for `PEL_ERR_MISMATCH` from ADR-0175. The new translation
    units (`telemetry.c`, `encode_record.c`, `sha256.c`) and the registry are
    not mirrored at RC4. The registry joins the mirror with
    VMAFx/vmafx#2271 (VMAFx 1.1) through a verbatim manifest row, because
    JSON cannot carry the C banner.
12. **Migration footer.** The implementing commit is additive and carries no
    `!`. Hard rule 1 still asks for a `Migration:` footer with before and
    after C. It names the motion size change and the reader-side guard:

    ```text
    Migration: PelorusMotionSection grows 32 -> 36 bytes (ABI 1.3 -> 1.4);
    two section bits are added (8 enc-telemetry, 9 encode-record).
    Before:
      _Static_assert(sizeof(PelorusMotionSection) == 32, "motion section ABI");
      bsize = pelorus_mc_cell_pitch(w, h, grid_cols, grid_rows);
    After:
      _Static_assert(sizeof(PelorusMotionSection) == 36, "motion section ABI");
      if (PEL_SD_FIELD_OK(got, PelorusMotionSection, block_size_log2) &&
          mo->block_size_log2 != 0)
          bsize = 1u << mo->block_size_log2;
      else
          bsize = pelorus_mc_cell_pitch(w, h, grid_cols, grid_rows);
    ```

## Alternatives considered

### Where the telemetry lives

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| New section bit 8 (chosen) | Clean per-frame semantics; scale, presence mask and `int16` maps from the start; `PEL_SEC_QPREPORT` keeps its ROI role | One more bit; adapters may write two sections | Chosen |
| Append fields to `PEL_SEC_QPREPORT` | No new bit | `int8` QP map and unscaled `avg_qp` stay; the "NaN/0 if absent" fields keep their ambiguity; GOP-aggregate and per-frame records share one bit | Two meanings on one bit ([ADR-0119](0119-qp-feedback.md) rejected the same for banding) |
| One section per encoder family | Each adapter's native fields | No normalisation, which is what #86 asks for; vmafx#2271 would need N name sets | Defeats the schema |

### "Not reported" against zero

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| 64-bit presence mask plus static adapter capability mask (chosen) | One rule for every type; the reader's test is one AND; maps to "key omitted" in JSON | 8 bytes per record; bits are append-only | Chosen |
| Sentinels (NaN for floats, `UINT32_MAX` for counts) | No extra field | Type-dependent; NaN fails comparisons silently; zero-filled pads read as "reported zero" | The ambiguity #86 removes |
| Capability record only, per adapter | Small | A field an adapter can fill but skipped for one frame (PSNR off) is still unknowable | Per-record truth needed |

### QP normalisation

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| H.264-equivalent QP on the step-size scale, native value kept (chosen) | Comparable across codecs where it matters (step size); identity for H.26x; test-gated | AV1/VP9 constants need a verified table | Chosen |
| Linear rescale to 0..1 by scale maximum | Trivial | Not step-size equivalent (H.264 30/51 = 0.59, AV1 120/255 = 0.47 for similar steps); fails #86 acceptance item 2 | Wrong measure |
| Native value only | No table | Every consumer converts; the #86 test has nothing to check | Pushes the work to every reader |

### Per-block payload

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Maps on the encoder grid, blob-relative and 8-aligned, bounded at 2^20 elements (chosen) | Lossless; same offset convention as `mv_field`; one bound check | Readers must validate offsets themselves (helper planned) | Chosen |
| Fold onto the Pelorus cell grid (`PEL_SEC_QPREPORT` style) | Aligns with analysis maps | Loses resolution; the fold rule differs per consumer | Lossy; the consumer can fold |
| Maps inside the section size | Self-contained | `pel_blob_find_section` returns at most the consumer's known size (R4), so a reader could not reach them | Breaks R4 access |

### Motion block size

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Append at the tail, 32 -> 36 bytes (chosen) | Literal R1; a 1.3 blob is detected by size through the existing `PEL_SD_FIELD_OK` | 4 bytes more per motion blob | Chosen |
| Consume `_pad[0]` (size stays 32) | No size change; `bump-abi` permits consuming reserved bytes | Absence is detectable only through the value 0, and only if every 1.3 producer zeroed its pad. `pel_blob_find_section` does not expose `struct_minor`. Renames a member the mirror compiles | Weaker absence signal than the size |

### Field-name parity source

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Pelorus-owned JSON registry, mirrored into VMAFx, plus a pinned snapshot of VMAFx's field list in Pelorus (chosen) | Both repos load the same registry; each direction is checked in the repo that changes; Python and C both read it | VMAFx needs a verbatim manifest row; VMAFx publishes its list in the same shape | Chosen |
| C X-macro header as the registry | Rides the existing mirror unchanged | VMAFx's stream-metadata tooling is not C; a second intra-Pelorus include in `interop.h` breaks the one-include render rule | Wrong consumer language |
| Each repo keeps its own schema; tests fetch the other at run time | No mirror change | Network in tests; unpinned | Fails the pinned-input rule (HISS-11) |

## Consequences

- **Positive**: VMAFx can mirror one fixed layout at RC4. Encoder
  telemetry gains a scale, a presence rule and a per-frame identity, so the
  decode-side track of VMAFx/vmafx#2271 and the encoder side share names.
  BUG-035 is closed by data, not by guessing from the frame size.
- **Negative**: the motion section grows by 4 bytes. Telemetry blobs with
  maps can reach about 7 MiB per frame at the bound. A new error code and two
  new bits widen the mirrored surface. The AV1 and VP9 normalisation constants
  stay unverified until the adapters land.
- **Neutral / follow-ups**: the implementing pull request (ABI code, fixture,
  `bump-abi` skill, `interop-abi-reviewer` sign-off); adapters and the
  `docs/metrics/` coverage matrix (#86 items 2-4, VMAFx 1.2); the parity test
  (#220) once VMAFx/vmafx#2271 publishes its field list; the mirror contract
  page (#223).

## Open questions

- VMAFx/vmafx#2271: confirm the `shared` names, and publish the field list in
  the registry shape with the `not_carried` list
  ([encoder-telemetry.md](../api/encoder-telemetry.md), "Field-name parity").
- VMAFx/vmafx#2271: codec names follow FFmpeg's (`h264`, `hevc`, `vvc`,
  `av1`, `vp9`); confirm or name the alternative before the bump merges.
- The transport of telemetry blobs outside a filtergraph (a sidecar file of
  blobs or attachment to decoded frames by `display_index`) is decided with
  the adapters (VMAFx 1.2).
- The AV1, VP9 and SVT-AV1 header `qp` scales and step-size constants are
  unverified (research 0174, items E3 and E4).

## References

- Issues #86, #218, #220, #221, #223; VMAFx/vmafx#2271, VMAFx/vmafx#2510,
  VMAFx/vmafx#2147, VMAFx/vmafx#2411.
- VMAFx ADR-1113 (pinned read-only mirror), ADR-1276 (re-pin on parser
  fixes), ADR-2073 (provenance record, JSON enum mapping).
- [Research 0174](../research/0174-abi-1-4-telemetry-provenance.md):
  verified evidence and the layout compile check.
- Source: `req` (task brief 2026-10-08, paraphrased): specify interop ABI
  1.4 for #86 and #81 so that VMAFx can mirror it before `1.0.0-rc.4`, and
  fold #218 into the same bump.
