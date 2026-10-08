- Added the interop ABI 1.4 specification ahead of the code, so VMAFx can
  mirror one fixed layout: a normalised encoder-telemetry section with a
  presence mask that separates "not reported" from zero, an encode-record
  section, and a block size in the motion section
  ([ADR-0174](docs/adr/0174-encoder-telemetry-abi-1-4.md),
  `docs/api/encoder-telemetry.md`). The shipped ABI stays 1.3.
- Added the encode provenance record specification: canonical JSON whose
  `sha256:` digest is the exact form VMAFx stores as a score's encode record,
  with a worked example any party can recompute
  ([ADR-0175](docs/adr/0175-encode-provenance-record.md),
  `docs/api/encode-record.md`).
