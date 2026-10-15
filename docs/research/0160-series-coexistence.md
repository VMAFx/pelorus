<!-- markdownlint-disable MD013 -->
# FFmpeg series coexistence measurement

**Checked:** 2026-10-15T17:58:11+02:00.

**Question:** Can the shared FFmpeg fixes, the sibling vmafx series, and the
Pelorus series apply cumulatively without an overlapping hunk?

## Inputs

| Input | Fingerprint |
| --- | --- |
| FFmpeg | tag `n9.0.2`; peeled commit `946fcce07b6dcd0331c8cc609192aeff5e1924f8` from `build-config.env` |
| Shared fixes | release `v0.2.0`, commit `d958384a13297fc009c74f4eb5080b9d83db3a74`, tarball sha256 `1f16f72e77bbe4ca14f9dc8712ae98165bfd1e45694e4c0d608fef02bac1db05`; 8 applied patch bodies checked byte-for-byte against that commit |
| vmafx | checkout `0836e067d1c3c4b393f23737eafb50b5fd54d02c`; `ffmpeg-patches/series.txt` sha256 `f020820b09d02700ab94b75e3b8663806eba8cd6fde33a3da0dbfa7e54555a77`; 21 patches |
| Pelorus | base `1770b85cbbabc8aeab5ce72f77d7e8cf96f13b05`; `ffmpeg-patches/series.txt` sha256 `2712c7ad34d3fd8294926f48abfd548a373f19a31bedcac002d0d9a510700984`; 22 patches |

The vmafx series and shared series came from operator-supplied read-only Git
checkouts; their locations are runtime inputs, not encoded in the checker. For
the shared override, the checker compared `base.env`, `series.txt`, and all
eight `.patch` files with pinned commit `d958384a`. The vmafx series is an
unpinned and unverified input (unlike the pinned, checksum-verified shared
series); the checker records its commit and dirty state in the output. FFmpeg
was checked only through detached worktrees created under this repository's
`build/` directory.

## Result

| Order | Result | First failing patch | Measured collision |
| --- | --- | --- | --- |
| shared → vmafx → Pelorus | unsupported, exit 1 | Pelorus `0001-add-vf_pelorus_deband_vulkan.patch` | vmafx `0002-add-vmaf_pre-filter.patch` and Pelorus 0001 both change `configure` at old line 7457 |
| shared → Pelorus → vmafx | unsupported, exit 1 | vmafx `0002-add-vmaf_pre-filter.patch` | the same pair and `configure` hunk in reverse |

No apply order is adopted, so this change makes no architecture decision and
adds no ADR. The revisit trigger is any vmafx or Pelorus series-content change;
rerun `ffmpeg-patches/test/series-coexistence.sh` with the new series directory.
The checker must return 0 before a combined FFmpeg build is supported.

## Common touched files

The vmafx stack touches 14 files and Pelorus touches 54. Their file-set
intersection contains five paths:

| File | vmafx patch group | Pelorus patch group | Observed before stop |
| --- | --- | --- | --- |
| `configure` | filter/backend registration patches 0002–0006, 0008, 0010–0013 | filter registration patches 0001–0003, 0006–0007, 0009–0010, 0014–0018 | overlapping hunk; first failure in both orders |
| `libavfilter/Makefile` | filter patches 0002, 0005–0006, 0008, 0013 | filter patches 0001–0003, 0006–0007, 0014–0018 | common file; no failure reached because `configure` stopped first |
| `libavfilter/allfilters.c` | filter patches 0002, 0005–0006, 0008, 0013 | filter patches 0001–0003, 0006–0007, 0014–0018 | common file; no failure reached |
| `libavcodec/libaomenc.c` | vmafx 0007 | Pelorus 0012 | common file; no failure reached |
| `libavcodec/libsvtav1.c` | vmafx 0007 | Pelorus 0013 | common file; no failure reached |

“Common file” does not assert that every listed hunk overlaps. The gate reports
only numeric old/new hunk-range intersections associated with a cumulative
`git apply --check` failure.

## Reader contract

The vmafx series reader uses exactly:

- FFmpeg side-data type `AV_FRAME_DATA_SEI_UNREGISTERED`;
- reader entry point `vmaf_set_perceptual_sidedata`;
- flat header type `PelorusSideData`.

The compiled header probes measured producer ABI 1.5 and reader ABI 1.3.
`PELORUS_MAGIC_STR`, ABI major 1, `PELORUS_SIDEDATA_UUID_LEN`, every member
offset in the 48-byte `PelorusSideData` header and 16-byte directory, all eight
reader-visible section identifiers, and every consumed member of the banding,
variance, and complexity sections match. The `pelorus_sidedata_uuid`
initializer also matches. Minor 3 remains compatible with the raw
in-filtergraph ABI 1.5 blob because the checked fields are append-only and the
reader ignores unknown sections. It does not recognize ABI 1.5's separate
zero-free carrier UUID or decode COBS; encoded carrier input is unsupported and
unmeasured here. The call-flow check proves that the distorted frame's
`AV_FRAME_DATA_SEI_UNREGISTERED` buffer and size reach
`vmaf_set_perceptual_sidedata`, and its negative fixture substitutes another
buffer and must fail. A second negative fixture removes the call in a later
revision and proves that only the final cumulative source is accepted. The
reader check ran even though no combined tree reached the post-apply state: the
checker retained a separate shared-plus-vmafx cumulative tree for the FFmpeg
call site and compiled both projects' headers directly. Because both cumulative
orders collide at `configure:7457` before any build can take place, the `BUILD=1`
path and the reader-ABI compilation-failure path have no executed evidence on
real trees; those paths are verified via synthetic `--self-test` fixtures.

## Commands and evidence

| Command | Result |
| --- | --- |
| `bash ffmpeg-patches/test/series-coexistence.sh --self-test` | exit 0; two non-overlap controls accepted; planted overlap rejected in both orders; context failure rejected; final-tree reader removal rejected; synthetic reader ABI positive control accepted, planted failing compiler (`CC=false`) rejected; bad `JOBS`/`BUILD` inputs rejected |
| `SHARED_SERIES_DIR=/path/to/shared-series FFMPEG_REPO=/path/to/ffmpeg VMAFX_SERIES_DIR=/path/to/vmafx/ffmpeg-patches BUILD=0 JOBS=4 bash ffmpeg-patches/test/series-coexistence.sh` | exit 1; both orders unsupported; reader contract passes; `BUILD=1` unexecuted on real trees |

Raw log: `.workingdir/evidence/0160-series-coexistence-real.log`, sha256
`e68ed3e62a285d6eba11e21ab1c80f4c987b2a5ac8a80ebb0cb3b98e94077bc3`, 133
lines. This path is local evidence, not a published project dependency.
