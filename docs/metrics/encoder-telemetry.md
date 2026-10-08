<!-- markdownlint-disable MD013 MD060 -->
# Encoder telemetry: field names, the VMAFx check and the adapter contract

Interop ABI 1.4 carries what an encoder did per coded frame in one
normalised section, `PEL_SEC_ENC_TELEMETRY`. The layout, every bit and every
validation rule are on the API page,
[encoder-telemetry.md](../api/encoder-telemetry.md); the decision is
[ADR-0174](../adr/0174-encoder-telemetry-abi-1-4.md). This page answers three
operational questions: where the field names live, how the VMAFx name check
runs, and what a caller outside FFmpeg fills in.

The per-encoder coverage matrix (which adapter reports which field) arrives
with the adapters (#86 items 2 to 4). Today no adapter ships, so only
caller-filled records (`adapter = external`) carry fields.

## Where the field names live

The registry
[`libpelorus/schema/telemetry-fields.json`](../../libpelorus/schema/telemetry-fields.json)
lists all 31 keys with their bit, JSON type, C type, unit, range and whether
the decode side shares them (`decoder`: `shared` or `encoder_only`). The
`PEL_TLM_F_<KEY>` defines in
[`interop.h`](../../libpelorus/include/pelorus/interop.h) must match it bit for
bit. A rename is a change in three places: the registry, `interop.h` and the
API page.

## The VMAFx name check (issue #220)

VMAFx's decode-side stream metadata (VMAFx/vmafx#2271) uses the `shared` names
with the same type and unit. The check is
[`scripts/check-telemetry-field-parity.py`](../../scripts/check-telemetry-field-parity.py);
the fast suite runs it twice:

| Meson test | Command | Result today |
|---|---|---|
| `telemetry-field-parity` | `python3 -I scripts/check-telemetry-field-parity.py` | SKIP (exit 77): "VMAFx field list not pinned (VMAFx/vmafx#2271)", after the registry and `interop.h` checks pass |
| `telemetry-field-parity-self-test` | `python3 -I scripts/check-telemetry-field-parity.py --self-test` | pass: a planted failure for every rule is rejected, a matching pair passes |

**Pinned VMAFx revision: none yet.** VMAFx/vmafx#2271 has not published its
field list. When it does, its file is copied to
`libpelorus/test/fixtures/vmafx-stream-metadata-fields.json` with a `source`
member `VMAFx/vmafx@<40-hex sha>:<path>`, this paragraph names that revision,
and the first test stops skipping. A difference then fails with both names
printed, for example `missing in vmafx: frame_bytes` or
`type/unit differs: avg_qp`. An empty or malformed file exits 2 and is never a
pass.

## Filling a record outside FFmpeg (issue #221)

`pelorus/telemetry.h` includes only `pelorus/interop.h` and the C standard
headers; the telemetry test compiles it with no FFmpeg include path. A caller
such as VMAFx's codec-adapter package (VMAFx/vmafx#2147):

1. Fills `PelorusEncTelemetryInput.frame` with `adapter =
   PEL_TLM_ADAPTER_EXTERNAL`, the codec, `granularity`, and a
   `present_mask` bit for exactly the fields it has. A field it does not have
   keeps its bit clear and its value zero: that is "not reported", which is
   different from a reported zero.
2. Points `qp_map`, `bits_map` and `mode_map` at its own arrays for the maps
   it reports (`map_cols * map_rows` elements, at most 1 048 576). They are
   read during the call and never kept.
3. Sizes one buffer with `pel_enc_telemetry_blob_size()` for its largest frame
   and calls `pel_enc_telemetry_pack()` per frame. Nothing is allocated, and an
   invalid record returns `PEL_ERR_INVALID` or `PEL_ERR_RANGE` without
   writing.

`pel_qp_normalize()` fills `avg_qp_norm` for H.26x `slice_qp` values (the
identity). For AV1 and VP9 it returns `PEL_ERR_UNSUPPORTED` until the
step-size table is verified (research 0174, E3); leave `PEL_TLM_F_AVG_QP_NORM`
clear then.
