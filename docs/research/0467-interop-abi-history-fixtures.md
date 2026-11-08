<!-- markdownlint-disable MD013 -->
# Interop ABI history fixtures and released reader/writer compatibility

**Date:** 2026-11-08

**Decision:** [ADR-0247](../adr/0247-interop-abi-history-fixtures.md).

**Scope:** libpelorus interop ABI wire format across Git release tags (v0.1.0 through v0.4.1) and HEAD (PELORUS_ABI_MINOR 0, 3, 4, 5, 6). Evaluates writer byte stability, forward and backward compatibility across 12 reader binaries, carrier encapsulation under ADR-0183, and error detection on corrupted offsets.

## Verdict

Persisted binary fixtures under `libpelorus/test/fixtures/interop-history/` ensure immutable byte contracts for released ABI writers. All 12 readers (11 release tags + HEAD) successfully parse all 7 fixtures (plain and carrier), yielding an 84-cell clean matrix (rc 0). Pre-ABI 1.5 readers ignore carrier-encapsulated blobs cleanly (`pel_blob_is_present` returns 0; section lookups return non-`PEL_OK`). Intentional byte corruption at offsets 144 (banding risk float) and 74 (section directory size) reliably triggers validation failures (`PEL_ERR_TRUNCATED` or corrupted values).

## Measurements

### Release tag to ABI minor mapping

| Tag | PELORUS_ABI_MINOR | Notes |
| --- | --- | --- |
| v0.1.0 | 0 | Initial interop ABI (plain format only) |
| v0.2.0 | 3 | Adds QP report, motion-confidence and complexity sections (minors 1 and 2 never reached a tag) |
| v0.2.1 | 3 | Identical ABI to v0.2.0 |
| v0.2.2 | 3 | Identical ABI to v0.2.0 |
| v0.3.0 | 3 | Identical ABI to v0.2.0 |
| v0.4.0-rc.1 | 4 | Adds encoder-telemetry and encode-record sections |
| v0.4.0-rc.2 | 5 | Adds the zero-free carrier form (ADR-0183) |
| v0.4.0-rc.3 | 5 | Identical ABI to v0.4.0-rc.2 |
| v0.4.0-rc.4 | 5 | Identical ABI to v0.4.0-rc.2 |
| v0.4.0 | 5 | GA release with ABI 1.5 |
| v0.4.1 | 5 | Patch release on ABI 1.5 |
| HEAD | 6 | Adds the quality-window section (ADR-0214); no tag yet |

### Writer byte identity per minor

- **Minor 0 (plain):** `v0.1.0` emits `abi-1.0.bin` (560 bytes, SHA-256 `6bb5c1d82d655547309c0941be100ad3ac11ba15ad067c6eb5f78b3a84bfafd8`).
- **Minor 3 (plain):** `v0.2.0`, `v0.2.1`, `v0.2.2`, and `v0.3.0` agree byte for byte on `abi-1.3.bin` (720 bytes, SHA-256 `a074b65b57e78cb518d6db9614baca7bad2ef4773d97f1accccbdbe2380e67cd`).
- **Minor 4 (plain):** `v0.4.0-rc.1` emits `abi-1.4.bin` (936 bytes, SHA-256 `b2d428cff7098474caa4d6969dda3445f3c37bd5cb2179fd7f8bb72d154040d4`).
- **Minor 5 (plain & carrier):** `v0.4.0`, `v0.4.0-rc.2`, `v0.4.0-rc.3`, `v0.4.0-rc.4`, and `v0.4.1` agree byte for byte on `abi-1.5.bin` (936 bytes, SHA-256 `01fc224bc800defaedfcb14c8160d042d4e918eba60651ce04f2e678eb65e7e2`) and `abi-1.5-carrier.bin` (937 bytes, SHA-256 `71efa1b8bda098c1a67434e8be11dd6da760bf877d71a2f0403e7859aa3a2fc5`).
- **Minor 6 (plain & carrier):** `HEAD` emits `abi-1.6.bin` (1048 bytes, SHA-256 `7737063db2456f3c62ce47b32b42eefae260a22a42830b47b1925847352ac14d`) and `abi-1.6-carrier.bin` (1049 bytes, SHA-256 `199e7b030ed16ff413cfa26246d67943deb04c7586bbc8c5dbf2ee6c90370ba9`).

### Reader x fixture matrix

Exit code of `abi_history read <fixture>` for each reader binary; `c` marks the carrier form. All 84 cells exit 0.

| Reader | 1.0 | 1.3 | 1.4 | 1.5 | 1.5c | 1.6 | 1.6c |
| --- | --- | --- | --- | --- | --- | --- | --- |
| v0.1.0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| v0.2.0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| v0.2.1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| v0.2.2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| v0.3.0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| v0.4.0-rc.1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| v0.4.0-rc.2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| v0.4.0-rc.3 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| v0.4.0-rc.4 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| v0.4.0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| v0.4.1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| HEAD | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

### Pre-1.5 reader behaviour on carrier blobs

Readers built against versions with `PELORUS_ABI_MINOR < 5` (tags `v0.1.0` through `v0.4.0-rc.1`) inspect incoming blobs. When presented with carrier-wrapped fixtures (`abi-1.5-carrier.bin` and `abi-1.6-carrier.bin`), the carrier UUID prefix does not match the plain blob magic. Consequently:

- `pel_blob_is_present(buf, len)` evaluates to 0.
- `pel_blob_find_section(buf, len, ...)` returns non-`PEL_OK` (`PEL_ERR_ABI` or `PEL_ERR_NOT_FOUND`).

These older readers safely ignore carrier blobs rather than misinterpreting wrapped bytes as section data.

### Planted corruption probes

- **Offset 144 (float corruption):** Directory entry 0 payload for banding starts at blob offset 128 (file offset 144 after 16-byte UUID prefix). The first field is float `global_banding_risk = 0.25f`. Flipping bit 0 corrupts the IEEE 754 float representation, triggering an immediate value check rejection in the reader.
- **Offset 74 (size overflow):** Directory entry 0 size field byte 2. Under rule R4, readers clamp small size increments to their known size. However, modifying byte 2 increases section size to 65560 bytes, exceeding the blob's total size of 544 bytes, which causes the parser to fail closed with `PEL_ERR_TRUNCATED`.

## Limits

- Fixtures are produced by `libpelorus/test/abi_history/` (`abi_history.c`, `abi_history_fill.c`, `abi_history_pack.c`, `abi_history_check.c`) compiled against historical release tag headers and `interop.c` sources, rather than full FFmpeg filter pipeline runs.
- External reader implementations such as `vmafx` are not directly driven by this matrix suite; `vmafx` mirrors `interop_test.c` for its ABI verification.
- Tag sources are compiled without `-Werror` (emitting informational note lines) because historical release sources cannot be retroactively fixed for new compiler warnings.
- The 1.6 fixtures are provisional: no release tag writes minor 6 yet, so an ABI bump before the next release removes them (ADR-0247). Released minors' fixtures never leave.
- The fixture `abi_minor` header byte is verified via SHA-256 and byte identity in `check-interop-abi-history.py`, rather than a dedicated header-field check inside the Meson read test.

## Evidence

- `python3 -I scripts/check-interop-abi-history.py --self-test` (passes the meta-check, 10 planted defects, and 3 positive cases: clean copy, unlisted tag note, superseding ABI bump).
- `python3 -I scripts/check-interop-abi-history.py` (passes 84-cell reader matrix with exit code 0).
