- Added interop ABI 1.4 (`PELORUS_ABI_MINOR` 4, append-only): the
  `PEL_SEC_ENC_TELEMETRY` section, one normalised encoder-telemetry record per
  coded frame with a presence bit per optional field and optional row or block
  maps; `pelorus/telemetry.h`, an input any encoder adapter fills without
  FFmpeg types, with validation and a non-allocating packer; the field
  registry `libpelorus/schema/telemetry-fields.json` and a VMAFx name-parity
  check that reports SKIP until VMAFx publishes its list
  ([ADR-0174](docs/adr/0174-encoder-telemetry-abi-1-4.md),
  `docs/api/encoder-telemetry.md`, `docs/metrics/encoder-telemetry.md`).
- Added the encode provenance record: `pel_encode_record_build`,
  `_canonicalize`, `_hash` and `_verify` produce and check the canonical JSON
  and the `sha256:` digest VMAFx stores as a score's `encode_record`, the
  `PEL_SEC_ENCODE_RECORD` section carries that digest and a locator, and
  `PEL_ERR_MISMATCH` (-8) reports a digest that differs
  ([ADR-0175](docs/adr/0175-encode-provenance-record.md),
  `docs/api/encode-record.md`).
- Added `block_size_log2` at the tail of `PelorusMotionSection` (32 -> 36
  bytes): `vf_pelorus_mc` writes its block edge and `vf_pelorus_denoise` uses
  it instead of guessing the edge from the grid, which fixes motion
  compensation on small frames such as `bsize=8` at 64x64 (#218, BUG-035).
  Readers of 1.3 blobs keep the inference.
