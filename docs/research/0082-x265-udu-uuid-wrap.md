<!-- markdownlint-disable MD013 MD060 -->
# libx265 wraps the Pelorus user data SEI in its own UUID

**Date:** 2026-10-18

**Context:** issue [#82](https://github.com/VMAFx/pelorus/issues/82), [ADR-0206](../adr/0206-fgs-bsf-per-frame-bridge.md). The FGS smoke used to say that libx265 4.3 drops the Pelorus SEI. That was a misdiagnosis.

**Scope:** x265 source history from `bitbucket.org/multicoreware/x265_git` (master `b81f650e2`), source builds of tags 2.1, 3.0 and master (shared library, no assembly), the CachyOS package x265 4.3-1.1 (`X265_BUILD` 217), FFmpeg `n9.0.2` plus the Pelorus stack.

Raw logs and the reproducer (local, not committed): `.workingdir/evidence/x265-udu/` (`FINDINGS.md`, `source-builds.txt`, `udu.c`, `x265build.sh`).

## Verdict

libx265 keeps the SEI but writes its own UUID `2ca2de09-b517-47db-bb55-a4fe7fc2fc4e` in front of the payload it receives. FFmpeg's `-udu_sei 1` passes the whole side data, Pelorus UUID included, so the stream carries the x265 UUID, then the Pelorus UUID, then the blob. Decoders export the x265 UUID, and no Pelorus reader finds the blob. This is not a 4.3 regression: every x265 release since 2.1 behaves this way.

## Method

1. Read the writer. `SEIuserDataUnregistered::writeSEI` in x265 `source/encoder/sei.h` writes `m_uuid_iso_iec_11578` (defined in `sei.cpp`) and then `m_userData`. `FrameEncoder` sends every `USER_DATA_UNREGISTERED` entry of `x265_picture.userSEI` through that class. The API has no field for a caller's UUID. The UUID write came in with the API itself, in commit `d4037be7a` ("sei: add arbitrary user SEI", 2016-01-07); the first tag that contains it is 2.1.
2. Read the caller. FFmpeg `libavcodec/libx265.c` (n9.0.2, lines 816-844) copies each `AV_FRAME_DATA_SEI_UNREGISTERED` side data whole into an `x265_sei_payload` of type `USER_DATA_UNREGISTERED`. FFmpeg master (`e9b3c55fe03`) is unchanged in this area.
3. Reproduce without FFmpeg. `udu.c` passes one 24-byte payload (a 16-byte marker UUID plus 8 bytes) on each of 4 pictures and parses every SEI message of the output. It counts the marker at payload offset 0 and at offset 16.
4. Reproduce through FFmpeg. A stream carrying the Pelorus carrier SEI was decoded and encoded again with `-c:v libx265 -udu_sei 1`.

## Results

| x265 | `X265_BUILD` | marker at offset 0 | marker at offset 16 |
| --- | --- | --- | --- |
| 2.1 (source) | 95 | 0 of 4 | 4 of 4 |
| 3.0 (source) | 169 | 0 of 4 | 4 of 4 |
| master `b81f650e2` (source) | 216 | 0 of 4 | 4 of 4 |
| 4.3 (package) | 217 | 0 of 4 | 4 of 4 |

Through FFmpeg, every picture's type 5 payload starts with `2ca2de09...` followed by `3f9b37b8...`, the carrier UUID.

## Consequences

- `ffmpeg-patches/test/fgs-sidedata-smoke.sh` classifies the libx265 probe as kept, wrapped or missing and skips with the cause it found. Its `--self-test` plants each form, and it fails when the `wrapped` or `kept` rule is switched off.
- Reading the wrapped form in `pelorus_fgs` (an allow-list of exactly this one form) follows in its own change with an ADR.
- The x265 API cannot carry a caller's UUID, so nothing FFmpeg sends today avoids the wrap. The upstream report goes through the fleet's review before anything is posted.
