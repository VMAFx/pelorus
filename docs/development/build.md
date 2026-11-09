<!-- markdownlint-disable MD013 -->
# Building Pelorus

## libpelorus (the core)

Meson + Ninja.

```bash
meson setup build
ninja -C build
meson test -C build --suite=fast      # interop ABI + UTF-8 paths + shader-compile checks
ninja -C build install                # install lib + headers + pkg-config
```

Every libpelorus parameter that names a file is UTF-8 on every platform
([ADR-0149](../adr/0149-windows-utf8-paths.md)); see
[docs/api/interop-abi.md](../api/interop-abi.md#x265-csv-reader-the-runnable-closed-loop-adr-0122).

Options (`meson_options.txt`):

| Option | Default | Effect |
| --- | --- | --- |
| `tests` | true | build + register the libpelorus test suite |
| `shaders` | true | compile the standalone reference `.comp` shaders to SPIR-V (needs glslang) |
| `tools` | true | build the libpelorus CLI demonstrators (`pelorus_qp_report`; not installed) |

### SEI fit tests

`ffmpeg-patches/files/pelorus_sei_fit.h` keeps the `hevc_nvenc` SEI rules
Pelorus decides with: the 1024-byte non-VCL limit with its 256-byte reserve,
FFmpeg's 512-byte payload limit, the emulation-prevention count and the
truncation rule ([ADR-0236](../adr/0236-sei-fit-merged-form.md)). The fast
suite checks them without an FFmpeg tree or the shared series. `sei-fit` must
pass. `sei-fit-planted-reserve`, `-payload`, `-epb`, `-truncated` and
`-charge` build the same test against a copy of the header with that one rule
changed (`scripts/plant-header.py`); their checks must fail, and such a
planted build exits 0 only then (1 when nothing caught the plant), so the
tester kit runs them like any fast test. The `core`, `sanitizers`
and `windows` CI jobs pass `--require` for all six to
`scripts/check-test-skips.py`, so a skip or a missing test fails the job.

### Telemetry-adapter locale probes

The encoder-telemetry text adapters parse dot-decimal numbers through a
call-local C locale. Meson checks `newlocale`, `freelocale` and `strtod_l` at
configure time on POSIX systems, and `_create_locale`, `_free_locale` and
`_strtod_l` on Windows UCRT. A missing symbol stops configuration with the
symbol and platform in the error. Linux probes define `_GNU_SOURCE`, matching
the feature-test macro used by the adapter translation unit.

Non-Windows builds link libm; Meson carries that dependency into the installed
pkg-config metadata so static consumers receive it through `Libs.private`.
The Linux `core` CI job installs locale data and generates `de_DE.UTF-8` before
the fast suite. `pelorus:enc-telemetry-adapters-locale` then changes
`LC_NUMERIC` to a comma-decimal locale and verifies that both adapters still
parse dot decimals. Local systems without `de_DE.UTF-8`, `de_DE.utf8` or
`fr_FR.UTF-8` emit the named skip recorded in
[`.config/test-skips.json`](../../.config/test-skips.json); the ordinary
`pelorus:enc-telemetry-adapters` test still runs all other adapter checks.

### Host-pipeline integration test

```bash
meson test -C build --suite=integration --print-errorlogs
```

`pelorus:host-pipeline` (`libpelorus/test/integration/`: `main` in
`host_pipeline_test.c`, one unit per stage; also in the `fast` suite) runs the
producer and the consumer side of the side-data path in one process, through
the public headers only:

1. **Encode record and film grain.** The producer builds the provenance JSON
   for an x265 option string and a deband filter, hashes it into an
   encode-record section, reads a film-grain section from
   `libpelorus/test/fixtures/grain/libaom-3.15.2-fgtest1.tbl`, packs both
   into one blob and wraps it in the SEI carrier. The consumer unwraps the
   carrier, checks the blob byte for byte and its header fields, verifies the
   JSON against the record digest and converts the grain from AV1 to H.274
   and back. The whole film-grain section, as parsed and again as unwrapped,
   must equal the values of the decoded stream below, which the test writes
   out itself instead of taking them from the parser.
2. **Encoder telemetry.** Each of the 10 frames of
   `libpelorus/test/fixtures/telemetry/x265-4.3-qp32.csv` goes through
   telemetry pack, carrier and unwrap; the unwrapped blob must equal the
   packed one byte for byte, and its telemetry section field for field the
   section the CSV parser produced.

Negative cases must fail with the named code: a flipped JSON or digest byte
(`PEL_ERR_MISMATCH`), a 0x00 byte inside the COBS stream or a carrier cut
inside its first COBS block (`PEL_ERR_ABI`), a foreign UUID, a carrier shorter
than the UUID or a header-only blob searched for an encode record
(`PEL_ERR_ABSENT`), and an 8-byte encode-record section (`PEL_ERR_INVALID`).
Carrier encode, unwrap scratch and telemetry pack each return `PEL_ERR_RANGE`
one byte below the required size and succeed at it.

The grain conversion is lossy, so the test bounds each fidelity loss at the
loss measured on this fixture times 1.2, rounded up: AV1 to H.274 below 0.117,
H.274 to AV1 below 0.136, descriptor comparison below 0.226 and luma sigma
error below 0.091. Each loss must also be finite and above zero: a NaN sigma
drops out of the loss maximum and would read as zero loss.

The table is frame 0 of `ffmpeg-patches/test/fixtures/grain-av1-fgs.ivf`
(libaom 3.15.2 with `film-grain-test=1`,
[ADR-0212](../adr/0212-grain-scoring-av1-libdav1d.md)), decoded by libdav1d
with `-export_side_data film_grain` and printed by `showinfo`. The exported
`uv_mult` and `uv_mult_luma` are converted back to coded values by adding 128,
`uv_offset` by adding 256, and `pel_grain_table_write` writes the result once
more for canonical spacing. Every field matches the decoded stream except
`limit_output_range`, which the table format does not carry.

These files are also the tracked test under `**/integration/**` that the
`native-gpu-systems` profile's integration test level needs; `praetorctl audit`
reports the level as a gap without it.

## Software Vulkan lane (Mesa lavapipe)

The `lavapipe` job of `.github/workflows/ci.yml` runs the real shaders of the
patch stack on Mesa lavapipe, so a runner without a GPU still executes all
ten filters with the Khronos validation layer on. Its result is functional
evidence on software Vulkan, never GPU evidence; the research digest has the
measurements and the reasoning
([research 0228](../research/0228-lavapipe-spike.md)). On a draft pull request
it stops on purpose, like the other hosted gates.

To reproduce it locally, with `lvp_icd.json` pointing at your lavapipe (the
replay script fetches the shared series, so it needs what
[the patch stack section](#the-shared-ffmpeg-fix-series-comes-first) lists):

```bash
FFMPEG_REPO=/absolute/path/to/ffmpeg KEEP_DIR=$PWD/kept ffmpeg-patches/test/build-and-run.sh
export VK_DRIVER_FILES=/usr/share/vulkan/icd.d/lvp_icd.json PELORUS_VALIDATE=1 \
  LD_LIBRARY_PATH=$PWD/kept/lib FFMPEG_BIN=$PWD/kept/ffmpeg
ffmpeg-patches/test/vulkan-lavapipe-guard.sh     # device, extensions, features
ffmpeg-patches/test/vulkan-lavapipe-filters.sh   # ten filters, zero VUIDs
ffmpeg-patches/test/vulkan-lavapipe-filters.sh --soak  # 3000-frame soak, side data, leaks
ffmpeg-patches/test/vulkan-format-matrix.sh
ffmpeg-patches/test/vulkan-lavapipe-report.sh    # software_vulkan, functional
```

The `--soak` step (issue #466) runs each config of the script's `SOAK_TABLE`
twice at 320x180 under validation, 300 frames and then 3000 frames. The table
holds the nine compute filters, `pelorus_denoise_vulkan` with and without its
causal frame ring, and the `pelorus_mc_vulkan` to `pelorus_denoise_vulkan`
side-data chain. The source is cut with `trim`, so every frame drains through
the graph at end of stream. Each run needs an executable coreutils `timeout`
on `PATH`. A config fails when:

- `bench: maxrss` grows by more than `SOAK_RSS_MIB` (default 16 MiB) from the
  short to the long run. On lavapipe, device memory is host memory, so a frame
  that is never unreferenced grows RSS. The floor is
  `SOAK_RSS_MIB` × 1024 / 2700 frames, about 6 KiB per frame; a planted config
  that keeps every frame grew by 276300 KiB;
- an ffmpeg run passes `SOAK_TIMEOUT` (default 600 s; SIGKILL follows
  `SOAK_KILL_AFTER` seconds later), which is how a frame or pool leak that
  blocks the graph shows;
- the validation layer reports any VUID, including Vulkan objects still alive
  when the device is destroyed;
- `showinfo` counts a different number of frames, misses the last frame, or
  counts, on any single frame, a different number of Pelorus UUID or AV1
  film-grain side-data entries than the table states.

Each soak run passes `-nostats`. Without it, ffmpeg's main thread writes a
carriage-return progress line while the filter thread writes one showinfo
side-data line in several log calls. A progress write between those calls cuts
the line, and that frame counts no entry (CI run 42494841487). A soak run whose
output holds a carriage return fails with `progress line in ffmpeg output`. The
fast-suite test `showinfo-nostats` (`scripts/check-showinfo-nostats.py`)
refuses any tracked ffmpeg command that adds showinfo without `-nostats`, and
checks each command of a chain on its own.

Run-to-run maxrss noise on lavapipe measured up to about 8 MiB (7760 KiB),
inside the 16 MiB budget. Heap leaks below the floor need a LeakSanitizer build. The soak
is software Vulkan evidence only. The NVENC side-data path has no soak:
`ffmpeg-patches/test/nvenc-udu-sei-smoke.sh` needs an NVIDIA host, caps
`FRAMES` at 64 per case and does not run in CI. `SOAK_CONFIGS=denoise_ring,mc`
narrows a local run to the named configs. CI runs the script defaults:
`scripts/check-build-config.py` rejects any `SOAK_` setting in
`.github/workflows/ci.yml`. `--self-test` plants every
failure above in a fake ffmpeg and requires each one to fail for its own
reason.

`KEEP_DIR` must be an absolute path that does not exist yet. The guard names
every missing device extension from
`ffmpeg-patches/test/vulkan-required-extensions.txt`; FFmpeg 9 itself treats
them as optional and would fall back silently.

## Native arm64 lane (Linux aarch64)

The `arm64` job of `.github/workflows/ci.yml` runs on native `ubuntu-26.04-arm`
GitHub-hosted runners ([ADR-0203](../adr/0203-arm64-ci-lane.md), issue #170).

| Scope | Platform coverage |
| --- | --- |
| What runs | Native `meson setup --werror build && ninja -C build`, fast suite (including side-data round-trip serialization against the x86-64 known-answer test byte vectors in `libpelorus/test/interop_test.c`), FFmpeg patch stack replay, Mesa lavapipe software Vulkan validation of all ten filters, the 3000-frame filter and side-data soak under validation, format matrix, and missing-device `no_device` non-failure tester report assertion |
| What does not run | No NEON SIMD kernels exist in Pelorus; no arm64 GPU evidence (lavapipe provides software functional evidence only) |
| Architecture endianness | Big-endian architectures are unsupported. The Pelorus interop ABI wire format is strictly little-endian ([interop ABI](../api/interop-abi.md#stability-rules-normative--see-interoph), [ADR-0183](../adr/0183-sidedata-zero-free-carrier.md)) |

## Windows (MSYS2 UCRT64)

libpelorus and its fast suite build natively on Windows with the MSYS2 UCRT64
toolchain (MinGW-w64 GCC on the Universal CRT). CI runs this in the `windows`
job of `.github/workflows/ci.yml` on a pinned `windows-2025` image
([ADR-0149](../adr/0149-windows-utf8-paths.md)). To reproduce it locally, open
a UCRT64 shell (`C:\msys64\ucrt64.exe`) and run:

```bash
pacman -S --needed mingw-w64-ucrt-x86_64-gcc mingw-w64-ucrt-x86_64-meson \
  mingw-w64-ucrt-x86_64-ninja mingw-w64-ucrt-x86_64-python \
  mingw-w64-ucrt-x86_64-glslang mingw-w64-ucrt-x86_64-shaderc
git config --global core.autocrlf false   # build the same bytes as Linux
meson setup build && ninja -C build
meson test -C build --suite=fast --print-errorlogs
meson test -C build path-utf8 --verbose   # the UTF-8 path contract transcript
```

From PowerShell, set `$env:MSYSTEM='UCRT64'` and `$env:CHERE_INVOKING='1'`, then
run `C:\msys64\usr\bin\bash.exe -lc '<commands>'`.

CI installs with `update: false`: the packages come from the sync database
bundled in the MSYS2 base archive of the pinned `setup-msys2` release, not
from a fresh `pacman -Sy`. A local MSYS2 that is kept up to date can therefore
run newer packages than CI. The job's receipt step prints `pacman -Q` for the
six packages, so the two can be compared.

Windows-specific behavior of the suite:

- **`path-utf8`** asserts the Windows half of the UTF-8 path contract. It
  creates a directory + file named with Latin-1, CJK, and an astral emoji
  through `_wmkdir` and an exclusive `_wopen`, then reads them back through
  `pel_x265_csv_parse`'s UTF-8 path. It checks that ill-formed UTF-8 returns
  `PEL_ERR_INVALID` with `errno == EILSEQ`, that an over-long path returns
  `PEL_ERR_ABSENT` with `errno == ENAMETOOLONG`, and that a `\\?\` path past
  `MAX_PATH` opens. On POSIX the same test asserts the literal `fopen`
  pass-through instead. The test avoids the C11 `"wx"` open mode, which the
  legacy `msvcrt.dll` runtime rejects, so it also passes with a non-UCRT
  MinGW-w64 GCC; UCRT64 remains the toolchain CI tests.
- **Shader compile checks** discard their SPIR-V to `NUL`, not `/dev/null`: a
  MinGW `glslangValidator` would otherwise write a real `\dev\null` file.
- **`build-config-sync`** runs `scripts/check-build-config.py` without
  `--self-test` on Windows. The self-test's Git fixtures model the Linux-only
  FFmpeg replay; MSYS2 argument globbing and Python's CRLF text mode break them,
  and the Linux jobs run the full self-test on every PR.
- **`suppression-markers`** (the tree scan) is not registered on Windows: it
  lists files with `git ls-files`, and the MSYS2 leg has no git on `PATH`.
  `suppression-markers-self-test` runs the planted cases there.
- **`python-empty-except`** and **`python-argv`** (the tree scans) are not
  registered on Windows for the same reason: they list tracked files with
  `git ls-files`. `python-empty-except-self-test` and `python-argv-self-test`
  run their planted cases there.

## Windows (cl.exe and clang-cl)

The `windows-msvc` job of `.github/workflows/ci.yml` builds libpelorus, the
tools and the tests with cl.exe and with clang-cl under `--werror`, then runs
the fast suite ([ADR-0242](../adr/0242-msvc-clang-cl-ci-leg.md)). You need
Visual Studio 2022 with the C++ workload, LLVM on `PATH` for the clang-cl leg,
and `bash` on `PATH`: `meson.build` requires it at setup (Git for Windows
provides it). Give each compiler its own build directory, since Meson fixes the
compiler of a directory at its first setup.

From Git Bash:

```bash
python -m pip install --require-hashes -r .config/msvc-ci/requirements.txt
CC=cl meson setup --vsenv --werror build-cl -Ddefault_library=static -Dshaders=false
meson compile -C build-cl
meson test -C build-cl --suite=fast --print-errorlogs
```

From PowerShell:

```powershell
python -m pip install --require-hashes -r .config/msvc-ci/requirements.txt
$env:CC = 'cl'
meson setup --vsenv --werror build-cl -Ddefault_library=static -Dshaders=false
meson compile -C build-cl
meson test -C build-cl --suite=fast --print-errorlogs
```

For the other leg, set the compiler to `clang-cl` and use `build-clang-cl`. The library is static here: libpelorus
carries no `__declspec(dllexport)` annotations, and cl.exe and clang-cl export
no symbol from a DLL without them. The reference shaders compile in the Linux
and MSYS2 jobs. icx-cl is not in CI.

The FFmpeg patch-stack scripts below remain Linux-only.

## FFmpeg filters (the patch stack)

Requires `libpelorus` installed (visible to `pkg-config`) plus a Vulkan loader +
SPIR-V compiler, and `cosign` plus an authenticated `gh` for the shared series
below.

```bash
cd ffmpeg-patches
FFMPEG_REPO=/absolute/path/to/ffmpeg ./generate.sh           # regenerate patches
FFMPEG_REPO=/absolute/path/to/ffmpeg ./test/build-and-run.sh # apply + build + smoke
```

Both scripts read the exact tag and peeled commit from root `build-config.env`,
verify that the qualified tag resolves to that commit, and work in an isolated
run-owned Git worktree. The committed `*.patch` files are the artifact; edit
the sources under `files/` and regenerate. Patch replay supplies its own
ephemeral committer identity and neutralizes caller signing, hooks, and diff
ordering, so the same command works on a clean CI runner and a configured
developer workstation.

`build-and-run.sh` ends with five GPU smokes and one CPU smoke: the
`hevc_nvenc` SEI smoke, the NVDEC → Vulkan → NVENC hop smoke
(`test/nvenc-cuda-hop-smoke.sh`, nine filter chains on the derived Vulkan device,
[ADR-0191](../adr/0191-nvenc-hop-derived-vulkan-device.md)), the documented
zero-copy recipes smoke (`test/doc-recipes-smoke.py`, runs the verbatim boundary-table
recipes across CUDA, Vulkan, VAAPI and QSV, [ADR-0240](../adr/0240-doc-recipes-run-verbatim.md)),
the Vulkan forced-keyframe smoke (`test/vulkan-forced-keyframe-smoke.sh`, 60 Vulkan HEVC
encodes with `-force_key_frames 2`, a hang fails it,
[ADR-0192](../adr/0192-series-v0.2.0-forced-keyframe-smoke.md); its header
lists `INPUT`, `RUNS`, `OUTPUT_ROOT` and `--self-test`), the
`pelorus_fgs` sidedata smoke (`test/fgs-sidedata-smoke.sh`: grain estimator,
HEVC encoder with `-udu_sei 1`, then `pelorus_fgs=source=sidedata`; one FGC SEI
per access unit, the cancel after grain stops, the 10-bit refusal and the
options-mode pass-through,
[ADR-0206](../adr/0206-fgs-bsf-per-frame-bridge.md)), the grain score smoke
(`test/grain-score-smoke.sh`: grain scoring recipe verifies decoded
grain-synthesised output vs grain-free reconstruction and strips FGC SEI,
[ADR-0207](../adr/0207-grain-scoring-decoded-output.md)), and the grain score AV1 smoke
(`test/grain-score-av1-smoke.sh`: AV1 via libdav1d,
[ADR-0212](../adr/0212-grain-scoring-av1-libdav1d.md)). The first
two need an NVIDIA driver and a Vulkan device; the third needs a Vulkan device
with HEVC video decode and encode; the fourth needs a Vulkan device plus
a working `hevc_nvenc` or libx265 (GPL builds only; the filter unwraps the UUID
every libx265 since 2.1 writes in front of the user data SEI,
[ADR-0210](../adr/0210-fgs-x265-udu-wrap.md)), and it also runs a libx265 case
when the build has libx265 (`ENCODER=libx265` runs every case on it);
the fifth needs an HEVC decoder and the `psnr` filter; the sixth needs the `libdav1d` decoder and the `psnr` filter. A host that lacks one makes a smoke skip,
and the replay then fails unless `NVENC_SMOKE_SKIP` (SEI smoke),
`NVENC_HOP_SMOKE_SKIP` (hop smoke), `KEYFRAME_SMOKE_SKIP` (keyframe smoke),
`FGS_SMOKE_SKIP` (grain smoke), `GRAIN_SCORE_SMOKE_SKIP` (grain score smoke)
`GRAIN_SCORE_AV1_SMOKE_SKIP` (grain score AV1 smoke)
and `DOC_RECIPES_SKIP` (documented zero-copy recipes,
[ADR-0240](../adr/0240-doc-recipes-run-verbatim.md))
name the reason each smoke prints, so a GPU host whose driver or device went
missing cannot pass by accident
([ADR-0189](../adr/0189-named-nvenc-smoke-skip.md)). On a machine without an
NVIDIA driver or Vulkan video:

```bash
NVENC_SMOKE_SKIP="Cannot load libcuda.so.1" \
  NVENC_HOP_SMOKE_SKIP="Cannot load libcuda.so.1" \
  KEYFRAME_SMOKE_SKIP="no Vulkan device with HEVC video" \
  FGS_SMOKE_SKIP="no HEVC encoder" \
  GRAIN_SCORE_SMOKE_SKIP="no HEVC decoder" \
  DOC_RECIPES_SKIP="no recipe device: cannot generate input clips" \
  FFMPEG_REPO=/absolute/path/to/ffmpeg ./test/build-and-run.sh
```

### Windows replay (MSYS2 UCRT64)

`ffmpeg-patches/test/build-and-run.sh` also runs under MSYS2 UCRT64 bash ([ADR-0213](../adr/0213-windows-replay-tester-zip.md)). Under MSYS2, the script automatically detects the environment (`$MSYSTEM == "UCRT64"`), sets `FFMPEG_EXE="$WORKTREE/ffmpeg.exe"`, prepends `$PRIVATE_PREFIX/bin` to `PATH` (for runtime resolution of `pelorus.dll`), and strips carriage returns (`\r`) from filter and BSF registration stdout checks. When `KEEP_DIR` is set on Windows, `build-and-run.sh` copies `ffmpeg.exe` and `$PRIVATE_PREFIX/bin/*.dll` directly into `$KEEP_DIR`. The dedicated path-filtered workflow `.github/workflows/windows-tester.yml` uses this mode on `windows-2025` runners to replay the patch stack and package the `pelorus-tester-windows-x64` artifact.

### The shared FFmpeg fix series comes first

Pelorus's 23 patches do not apply to stock FFmpeg. They apply on top of the
shared FFmpeg fix series, a separate release of
[VMAFx/ffmpeg-patches](https://github.com/VMAFx/ffmpeg-patches) that holds the
FFmpeg fixes every VMAFx project needs
([ADR-0185](../adr/0185-shared-ffmpeg-fix-series.md)). You do not download it
by hand: `generate.sh`, `test/build-and-run.sh` and
`test/qsv-roi-regression.sh` call one script, which fetches the pinned
release, verifies it and unpacks it. The tester workflow runs the same script
on the runner, and the image build checks the pin again with `--unpack`:

```bash
scripts/fetch-ffmpeg-series.sh /absolute/path/to/new-dir   # prints new-dir/series
scripts/fetch-ffmpeg-series.sh --self-test                 # the verifier refuses planted defects
scripts/fetch-ffmpeg-series.sh --unpack TARBALL NEW_DIR    # image build only: sha256 pin, then unpack
```

What you need for the first two: network access, `cosign` on `PATH`, and
`gh auth login` or a `GH_TOKEN`.

| Variable | Use |
| --- | --- |
| `GH_TOKEN` | credential of `gh attestation verify`; CI passes `github.token` |
| `FFMPEG_SERIES_FROM` | directory with the four release files, taken instead of a download and verified the same way |
| `CURL_MAX_TIME` | download deadline per attempt in seconds, default 120 |
| `CURL_STALL_TIME` | seconds without progress before aborting an attempt, default 30 |
| `CURL_RETRY_WINDOW` | retry window in seconds, default 2 * `CURL_MAX_TIME` |

The script fails, and removes its output, when any of these does not hold:

| Check | Command it runs | What it proves |
| --- | --- | --- |
| sha256 pin | `sha256sum` against `FFMPEG_SERIES_SHA256` | the tarball is the pinned one, byte for byte |
| signature | `cosign verify-blob --bundle SHA256SUMS.sigstore.json`, identity `release-build.yml@refs/tags/<tag>` | the series repository's release workflow signed the checksums |
| checksums | `sha256sum --check --strict SHA256SUMS` | the tarball is the one the signed checksums list |
| provenance | `gh attestation verify`, source ref and commit pinned | the tarball was built from `FFMPEG_SERIES_COMMIT` at that tag on a GitHub-hosted runner |
| base | `base.env` of the tarball against `build-config.env` | the series was made for the pinned FFmpeg release |

The script also refuses a tarball whose members leave its top-level directory
or are not plain files, and a `series.txt` that lists a missing patch. The fast
test `fetch-ffmpeg-series-fail-closed` (`scripts/test-fetch-ffmpeg-series.py`)
plants those defects and the sha256 and base ones without network; the
signature and provenance defects are planted by `--self-test`, which the
`ffmpeg-stack` CI job runs.

Apply order, wherever FFmpeg is patched: the series in its own `series.txt`
order, then `ffmpeg-patches/series.txt`. To do it by hand, in an FFmpeg
checkout of `FFMPEG_COMMIT`:

```bash
series="$(scripts/fetch-ffmpeg-series.sh "$PWD/../series-release")"
grep -Ev '^(#|$)' "$series/series.txt" | sed "s#^#$series/patches/#" | xargs git -C /path/to/ffmpeg am --3way
grep -Ev '^(#|$)' ffmpeg-patches/series.txt | sed "s#^#$PWD/ffmpeg-patches/#" | xargs git -C /path/to/ffmpeg am --3way
```

`build-config.env` holds the pin: `FFMPEG_SERIES_REPO` (the series
repository, `VMAFx/ffmpeg-patches`), `FFMPEG_SERIES_TAG`, `FFMPEG_SERIES_COMMIT`
(the commit the tag names) and `FFMPEG_SERIES_SHA256` (of the tarball). To
move to another series release, change the last three together from that
release's notes, run `scripts/fetch-ffmpeg-series.sh --self-test` (it
downloads the release, so it needs the network, `cosign` and `gh` too),
regenerate twice and replay. Number 0021 of the Pelorus stack is retired: that
fix is series patch 0001 now.

### Apply order with the vmafx series

The sibling vmafx project (the VMAF extension and orchestration layer,
pronounced “vee-maf-ex”) ships another FFmpeg patch series. Check it against
the Pelorus stack before combining the projects:

```bash
FFMPEG_REPO=/absolute/path/to/ffmpeg \
VMAFX_SERIES_DIR=/absolute/path/to/vmafx/ffmpeg-patches \
ffmpeg-patches/test/series-coexistence.sh
```

The input is the `ffmpeg-patches/` directory inside a full vmafx checkout: the
checker reads patch names and numbers from its `series.txt` and compiles the
vendored interop header and source under the sibling `core/` directory. A
series-only extraction is insufficient. `VMAFX_SERIES_DIR` is an unverified,
unpinned input (unlike the pinned, checksum-verified shared series); the
checker records its commit and dirty state in its output. The checker creates
detached worktrees at the FFmpeg tag pinned by `build-config.env`, fetches and
verifies the shared series, then measures both cumulative orders:

1. shared fixes → vmafx → Pelorus (preferred candidate);
2. shared fixes → Pelorus → vmafx (reverse measurement).

`SHARED_SERIES_DIR` may name a Git checkout containing the pinned shared-series
commit to avoid fetching it again; the checker compares `base.env`,
`series.txt`, and every applied patch body against `FFMPEG_SERIES_COMMIT` before
use. `BUILD` accepts `0` (the default) or `1`. With `BUILD=1`, the checker
builds and installs a private libpelorus, configures Vulkan, all ten Pelorus
filters, and the `pelorus_fgs` bitstream filter, links FFmpeg, then checks every
registration by its exact name. `JOBS` defaults to 4 and cannot exceed 4. When
`pkg-config` cannot resolve `libvmaf`, that optional reader build is disabled
with a named `SKIP:` reason checked by `test/named-skip.sh`. ABI probes use
`CC`, which defaults to `cc`.

Every patch first passes `git apply --check` against the cumulative tree. The
checker is an overlap detector using `git apply --check` (no three-way merge); it
reports textual hunk collisions across patch series and does not replace
`test/build-and-run.sh`, which performs full three-way `git am` replay,
configure, build, link, and runtime smoke testing of the Pelorus stack. A
failure prints the current patch and its conflicting patch or cumulative-tree
predecessor, plus the file and unified-diff hunk headers, before the worktree is
removed. `--self-test`, wired into the Meson fast suite, plants a same-line
collision and a non-overlap context failure, guards the side-data call flow,
verifies that planted failing `CC=false` probes and bad `JOBS` inputs are
rejected, and also proves two non-overlapping controls.

The measurement on 2026-10-15 found neither order supported: both stop at the
`configure` hunk shared by vmafx's `vmaf_pre` registration and Pelorus's first
filter registration. Do not combine these revisions by relying on a three-way
auto-resolution. Re-run the command after either series changes; the full
fingerprints and five commonly touched files are in
[research 0160](../research/0160-series-coexistence.md).

The checker also verifies the reader contract without depending on a numbered
vmafx patch. It retains the final shared-plus-vmafx cumulative tree when the
combined order is blocked and requires that tree's applied `vf_libvmaf.c` to
pass `AV_FRAME_DATA_SEI_UNREGISTERED` to `vmaf_set_perceptual_sidedata`. Two
compiled probes compare
`PelorusSideData`, every header and directory offset, the three section layouts
the reader consumes, the magic, ABI major, UUID length, and reader-visible
section identifiers between Pelorus and vmafx. The vmafx reader's ABI minor may
be older because those raw in-filtergraph blob fields are append-only; it may
not be newer than the producer. This does not validate ABI 1.5's zero-free COBS
carrier: the ABI 1.3 vmafx reader recognizes only the raw Pelorus UUID, so the
encoded carrier path is outside this coexistence check.

## Repository verification entry points

```bash
make verify-native
```

`make verify-native` is the primary product gate. It configures and builds with
Meson/Ninja, runs the fast suite, checks C formatting and clang-tidy, and checks
that `CHANGELOG.md` matches `changelog.d/`. Override `BUILD_DIR` to use another
Meson tree, for example `BUILD_DIR=build-asan make verify-native`.

A test that exits 77 is a skip, and `meson test` exits 0 for it. The fast suite
counts a skip as passed only when [`.config/test-skips.json`](../../.config/test-skips.json)
lists the test with the exact `SKIP:` line it prints and the issue or ADR that
keeps it ([ADR-0188](../adr/0188-named-test-skips.md)).
`scripts/check-test-skips.py <build>/meson-logs/testlog.json` checks this after
`make test` and in every CI fast-suite run. A new skip, or a changed skip message,
fails until the list names it.

`make test` and patch harnesses run tests through `scripts/run-meson-test.py`
([ADR-0246](../adr/0246-meson-testlog-credential-scrub.md)), which strips credential-bearing
keys (tokens, API keys, passwords, and PATs) from the environment before Meson executes,
preventing secrets from leaking into `meson-logs/testlog.txt` and `testlog.json`.
`scripts/check-testlog-credentials.py <build>` scans `meson-logs/testlog*.{txt,json}` for
credential-shaped values (GitHub tokens, `sk-` keys, AWS `AKIA` IDs) and fails if any exist.
The fast-suite test `meson-testlog-credentials` (`scripts/test-meson-testlog-credentials.py`)
verifies name filtering, log detection, and end-to-end environment scrubbing against a stub
Meson runner. On Windows, where the wrapper waits for Meson as a child process, a run still
going after `MESON_RUN_TIMEOUT_S` (30 minutes) is stopped and exits with status 124; Meson
times out each hung test itself. The stop, and an interrupt, end Meson together with every
test process it started (`scripts/process_tree.py`: `taskkill /T /F` on Windows, `SIGKILL` to
the process group on POSIX), so no test keeps running after the wrapper returns. The self-test
checks that a child's exit status passes through, that a child outliving a short deadline is
stopped, and, on POSIX, that a test process started by that child is stopped with it.

`make test` also runs `scripts/check-tester-kit-install.py --run-kit <build>`. It runs
`meson introspect --tests` on the build directory and hands the result to
`tools/tester/install-fast-suite.py`, which installs the fast suite into a temporary directory.
With `--run-kit`, it then builds the relocatable native kit in a temporary prefix via
`tools/tester/build-fast-suite.py` and executes `tests/run-fast-suite.sh`. A fast-suite test
whose command or arguments the tester kit cannot install (such as a fixture path with `..`) or
that fails under the kit runner (such as relying on Meson `should_fail: true` or exiting 77)
fails `make test` locally before push ([ADR-0248](../adr/0248-tester-kit-expected-fail.md)).
The fast-suite test `check-tester-kit-install` (`scripts/test-check-tester-kit-install.py`)
covers installable arguments, planted `..` and outside-tree paths, followed by tiny Meson
project cases verifying positive execution, negative detection of a planted `should_fail` test,
boundary rejection of exit 77 skips, and a kit-only `--werror` build failure whose compiler
message must reach stderr. A runner that outlives its deadline makes the check exit 124 and
prints the runner's partial output, decoded, on stderr; the self-test plants a hanging runner
for that path. On Linux and macOS the self-test fails, rather than skips, when `meson`,
`ninja` or a C compiler is missing, so a lane without them cannot drop the kit cases while
reporting a pass; it skips them on Windows only, where `tests/run-fast-suite.sh` cannot run.
The tiny project declares its options in `meson_options.txt`, the file name every Meson from
the `meson_version` floor on reads.

A `# nosemgrep` or `# nosec` marker in tracked Python must sit on the start line
of the statement or expression it answers and name its rules plus a reason:
`# nosemgrep: <full rule id>[, <id>] - <reason>` and `# nosec <Bxxx> - <reason>`
([ADR-0241](../adr/0241-suppression-marker-binding.md)). The fast-suite test
`suppression-markers` runs `scripts/check-suppression-markers.py --self-test`,
which refuses a misplaced, bare or reasonless marker and then scans the tree;
`suppression-markers-self-test` runs the planted cases alone
(`--self-test-only`) on every platform, Windows included. Each Python file is
read in its declared encoding (PEP 263), and one that does not decode is a
finding.

Every Python `except` clause with a bare `pass` body must carry an explanatory
comment documenting why ignoring the exception is safe. The fast-suite test
`python-empty-except` (`scripts/check-python-empty-except.py`) scans tracked
Python files and fails when an except handler with a bare pass carries no comment;
`python-empty-except-self-test` runs the planted cases.

Every tracked Python script must parse its command line with stdlib `argparse`
rather than by indexing `argv`. The fast-suite test `python-argv`
(`scripts/check-python-argv.py`) scans tracked Python files with the stdlib AST
and refuses:

- any `sys.argv` use other than `sys.argv[0]` or `sys.argv[1:]` passed straight
  to a call, such as `main(sys.argv[1:])`;
- a subscript, an `.index()` or `.pop()` call, or an `in`, `not in`, `==` or `!=`
  test on `argv` inside a function named `main` or `parse_args`, where `argparse`
  owns the command line. A name bound to `argv` or to a `list`, `tuple` or `deque`
  copy of it (`args = list(argv)`), and a parameter of a function in the same
  module that receives `sys.argv[1:]` or such a name, counts as `argv`;
  `argv is None` stays allowed;
- the same `argv` operations anywhere in a module that has a command line (it
  reads `sys.argv` or defines `main` or `parse_args`) but does not import
  `argparse`.

A module without a command line, such as a test helper whose `argv` is a
recorded subprocess call, is out of scope. Two files are excepted in the script
with a reason: the Praetor-managed `.config/agent/hooks/block_evasion.py` and the
FFmpeg command-line stand-in `ffmpeg-patches/test/doc-recipes-stub-ffmpeg.py`.
`python-argv-self-test` runs the planted cases.

No branch or tag may hold a credential (HISS-12,
[ADR-0190](../adr/0190-gitleaks-every-ref.md)). The CI `docs` job installs the
gitleaks release pinned by version and sha256 in `scripts/check-gitleaks.sh`,
runs `scripts/check-gitleaks.sh --self-test`, then scans the full history of
every ref. To run it locally:

```bash
GITLEAKS="$(scripts/check-gitleaks.sh --install /absolute/new-dir)" \
  scripts/check-gitleaks.sh --self-test
GITLEAKS=/absolute/new-dir/gitleaks scripts/check-gitleaks.sh
```

A false positive is exempted by adding its fingerprint
(`commit:file:rule:line`, printed by the failing scan) to `.gitleaksignore`.
`.gitleaks.toml` may only extend the default rules and add rules; an allowlist
there is refused.

Python sources and the workflow files are linted before push
([ADR-0243](../adr/0243-ruff-zizmor-local-gate.md)). `make verify-native` runs
`scripts/check-python-lint.sh --self-test` and then the scan: Ruff 0.17.3 over
the tree with the rule set in `ruff.toml` (Codacy's Ruff patterns plus
`SIM103`, `E721` and `PGH004`), and zizmor 1.32.0 `--offline
--strict-collection` over the workflows, actions and Dependabot files with
`.github/zizmor.yml`, so a file zizmor cannot parse fails the scan instead of
being skipped. Both tools are pinned by hash in
`.config/python-lint/requirements.txt`; the target keeps them in
`.cache/python-lint` (`PYTHON_LINT_ENV`, outside the Meson build directory; a path with spaces works)
through `--ensure`, which reuses that environment while its tool versions match
the lock and rebuilds it after a lock bump or a half-finished install. The
first run in a new checkout, and the first after a bump, needs PyPI. The
pre-commit hook runs `make python-lint` when a Python file, a workflow or the
lint configuration is staged, and the CI `docs` job runs the same commands on
Linux. To run them by hand:

```bash
scripts/check-python-lint.sh --install /absolute/new-dir
PYTHON_LINT_BIN=/absolute/new-dir/bin scripts/check-python-lint.sh --self-test
PYTHON_LINT_BIN=/absolute/new-dir/bin scripts/check-python-lint.sh
```

A Ruff finding the code answers on purpose carries
`# noqa: <code> - <reason>`; a bare `# noqa` fails (`PGH004`). `S101`, `S603`
and `S607` are left to Bandit and its reviewed `# nosec` markers.

The governance targets and the Lefthook jobs run the Praetor engine pinned at
`PRAETOR_REF` in `.github/workflows/standards-gate.yml`, whatever `praetorctl`
or `standardsctl` is first on `PATH`. The engine's own launcher,
`.config/lefthook/engine.sh`, resolves it: it installs the pinned commit with
`go install` into `${XDG_CACHE_HOME:-$HOME/.cache}/praetor/engine/<PRAETOR_REF>/`
on first use (at most `PRAETOR_ENGINE_INSTALL_TIMEOUT` seconds, default 300) and
runs it from there. `make` reaches the same launcher through
`.config/praetor/engine.mk`, which the `Makefile` includes. Both files are
written by `praetorctl adopt`; do not edit them
([ADR-0187](../adr/0187-praetor-engine-7cf9cbb-engine-launcher.md)). The launcher
fails closed, with one line on stderr that names the pin, the cache directory
and the command that fixes it, when the pin is unreadable or declared twice with
different values, or when the cache is empty and Go is missing or the install
fails. It never falls back to `PATH`. Every worktree and every adopted
repository on the machine that pins the same commit shares one cache directory.
The launcher does not compare the cached binary's `version` with the pin; the
hosted `Standards` job does.

To refresh the engine after a pin bump (`PRAETOR_REF` changes), run
`sh .config/lefthook/engine.sh version`: the new pin gets a new directory. To
reinstall a damaged cache, delete that pin's directory and rerun.
`sh .config/lefthook/engine.sh --print-path` prints the resolved binary, and so
does `make praetor-engine-path`. Call the engine through the launcher for
direct commands too (`sh .config/lefthook/engine.sh audit`): a `praetorctl` on
`PATH` may be another commit and then judges the tree by other rules.

`PRAETORCTL=/absolute/path/to/engine` overrides the choice for `make` only; the
hooks always run the pin. The override is not version-checked and prints
nothing. Agent hooks (`.claude/settings.json`) still call `praetorctl` by name from
`PATH`. The PATH coupling is reported upstream as
[cordanaLLM/praetor#906](https://github.com/cordanaLLM/praetor/issues/906).
The documentation targets need Node.js 22.12 or newer; the gate installs its own
locked `markdownlint-cli2` 0.23.2 dependency tree into a temporary directory.

| Target | Composition and purpose |
| --- | --- |
| `make compile-context` | Regenerate cross-tool context and persona projections from their canonical sources |
| `make compile-context-verify` | Fail if a generated projection has drifted |
| `make audit` | Verify the pinned manifest/lock, enforce the <!-- gen:baseline-count -->0<!-- /gen:baseline-count -->-finding HISS baseline ratchet in the hosted Standards mode (`--base`, touched-debt delta; `AUDIT_BASE` overrides the base), and run the engine's policy checks (build warnings, clang-tidy coverage, supply chain, Paperclip pair, hooks, dev container, ruleset). `AUDIT_FLAGS=--offline` skips the forge reads, as the pre-commit hook does |
| `make docs-lint` | Lint public Markdown with the locked Praetor configuration and reject links into private scratch directories |
| `make docs-figures` | Check figure specs and sources; skips with a reason while `docs/figures/` has none |
| `make verify-all` | Run context verification, the audit, `verify-native`, then `docs-lint` and `docs-figures` |
| `make hooks-install` | Install the tracked Lefthook hooks into the shared Git hooks directory; see [Git hooks](#git-hooks-opt-in-per-clone) |

The baseline accepts <!-- gen:baseline-count -->0<!-- /gen:baseline-count --> existing findings in the scanner's supported-file scope
(C, headers, Python, shell, JavaScript/TypeScript, and workflow files);
`scripts/docs/render-counts.py` renders that number from
`.standards-baseline.json`. It is a non-regression ceiling, not a claim of zero debt or whole-tree
source coverage. Fingerprints are keyed by file and line
([Praetor issue 29](https://github.com/cordanaLLM/praetor/issues/29)), so
moving a legacy function can surface it as new.

The local target and the hosted job apply that ceiling differently:

| Where | Command | Fails when |
| --- | --- | --- |
| `standardsctl audit` (plain) | `standardsctl audit` | a finding's fingerprint is not in the baseline; the total exceeds the baseline; any finding, baselined or not, sits in a file with uncommitted changes (Praetor's touched-file rule) |
| `make audit` | `standardsctl audit --base "$(AUDIT_BASE)" --touched-debt-delta-reason "$(AUDIT_DEBT_REASON)"`, base `origin/master` unless `AUDIT_BASE` is set | as hosted step 1 |
| Hosted, step 1 | `standardsctl audit --base <target> --touched-debt-delta-reason <reason>` | the committed baseline records more findings than the baseline on the target (growth guard); a file the branch touches has more findings of some rule than the committed baseline records; a finding in an untouched file is not in the baseline; the total exceeds the baseline |
| Hosted, step 2 | `standardsctl baseline --verify` | a current finding is not recorded at its current line; the total exceeds the baseline |

The target is `origin/<base branch>` for a pull request and the replaced
commit for a push to `master`. Without the debt-delta reason, `--base` would
revoke every baselined finding in a touched file; that zero-debt mode stays
deferred by ADR-0145. Step 1 accepts a touched file whose findings only moved
lines, so step 2 makes the branch re-record the baseline for them. A stale
fingerprint on `master` would otherwise fail the next pull request that does
not touch that file.

Existing findings may shrink. A deliberate increase recorded with
`standardsctl baseline --record --allow-increase --reason=<why>` passes the
growth guard with a `[WARN]` line that prints the reason, so the increase stays
visible in the job log and in the baseline diff. Any change to the baseline
total also changes the managed README block, which the audit compares with the
baseline; refresh it with the adoption command in the ADR. To run the hosted
steps locally:

```bash
CI=true standardsctl audit --base origin/master \
  --touched-debt-delta-reason "local run of the hosted ratchet"
standardsctl baseline --verify
```

Two hosted workflows run on every pull request. `Standards` installs Praetor at
the commit in [ADR-0168](../adr/0168-praetor-engine-492a00f.md), prints
the module version Go recorded for it, verifies generated contexts, and runs
the two ratchet steps above. The live `master` protection, a classic branch
rule, requires `Standards` and the `core`, `ffmpeg-stack`, and `docs` jobs; the
committed ruleset requires all nine pull-request checks once it is applied
([Branch ruleset](#branch-ruleset)). `Praetor Documentation Governance` is Praetor's locked
workflow for the `docs:seo-portal` facet: it runs the same Markdown and figure
checks as `make docs-lint docs-figures`. On a draft pull request its first
step, `Stop on a draft pull request`, fails on purpose and the checks do not
run; they run once the pull request is marked ready for review. `praetorctl
audit` compares that
workflow, `tools/markdownlint/`, `tools/figures/`, the documentation block in
the `Makefile`, and the managed block at the end of `.gitattributes` byte for
byte with the pinned engine's assets. Refresh them only with the adoption
command in the ADR, never by hand.

### CodeQL code scanning

The repository scans C/C++, Python, and GitHub Actions workflows with CodeQL
([ADR-0220](../adr/0220-codeql-code-scanning.md)). The workflow
[`.github/workflows/codeql.yml`](../../.github/workflows/codeql.yml) runs on pull
requests, pushes to `master`, and a weekly cron schedule. For C/C++, it
configures Meson (`meson setup --werror`) before initialization so that compiler
probe checks are excluded from the database, then builds with Ninja after
initialization.

`scripts/check-codeql-workflow.py` verifies the workflow contract (exact action
SHA pinning with `# v` comments, draft PR gates, trigger matrix, and
least-privilege permissions). The fast test suite runs its `--self-test`
battery on all platforms.

Since [ADR-0153](../adr/0153-praetor-full-adoption.md) the manifest declines
no adoption step. A local audit therefore also needs two things CI does not
check:

- **An installed pre-commit hook.** The audit requires Lefthook's
  `pre-commit` hook in the Git hooks directory that every linked worktree
  shares; run `make hooks-install` once ([Git hooks](#git-hooks-opt-in-per-clone)).
  CI installs no hooks, and the audit skips that check when `CI` is set.
- **Live branch protection that matches the ruleset.** With a forge token
  (`GITHUB_TOKEN`, `GH_TOKEN`, or a `gh` login) and an `origin` that names
  `VMAFx/pelorus`, the audit compares the protection GitHub enforces on
  `master` with `.github/rulesets/main.json`. Until the ruleset is applied,
  that comparison fails ([Branch ruleset](#branch-ruleset)). The hosted job
  has no token and reports the check as not made. `AUDIT_FLAGS=--offline`
  (or `--offline`) skips it locally.

Do not create a placeholder hook file, weaken the manifest, or run remote sync
to get past a failing check.

Two audit checks read lists that ordinary code changes can break:

- **Build warnings (HISS-10).** Every `meson setup` in `.github/workflows/`
  passes `--werror`. The audit reads only the command line, not the
  `werror=true` in `meson.build`'s `default_options`, so a hosted lane without
  the flag fails even though Meson would build it with warnings as errors.
  `scripts/check-build-config.py` also pins the Windows job's
  `meson setup --werror build && ninja -C build`.
- **clang-tidy coverage.** `.standards.yaml` declares two clang-tidy lanes:
  `libpelorus` (`.config/clang-tidy/lane-files.txt`), covering every C file the
  Meson build compiles (`libpelorus/src`, `libpelorus/test`, `tools`, and the
  `ffmpeg-patches/test` programs Meson builds against stub headers), and
  `ffmpeg-tree` (`.config/clang-tidy/ffmpeg-tree-files.txt`), covering the 16 C
  units under `ffmpeg-patches/files`, `ffmpeg-patches/test`, and the
  `scripts/encode-options/avoption-probe.c` table generator probe that build
  against a configured FFmpeg tree or installed static prefix
  ([ADR-0224](../adr/0224-ffmpeg-tree-tidy-lane.md),
  [ADR-0233](../adr/0233-probe-ffmpeg-tree-tidy-lane.md)).
  `make tidy` and the `core` job's clang-tidy step read `lane-files.txt`, while
  `ffmpeg-patches/test/build-and-run.sh` runs `ffmpeg-patches/test/tidy-ffmpeg-tree.sh`
  over `ffmpeg-tree-files.txt` with `.config/clang-tidy/ffmpeg-tree.yaml`, so
  the lanes the audit judges are the lanes that run. A tracked C unit that no
  lane reads fails the audit unless the top-level `exceptions` list names it
  with a reason and an expiry at most 90 days ahead. Add a new Meson unit to
  `lane-files.txt` or a new FFmpeg-tree unit to `ffmpeg-tree-files.txt` in the
  same change.

Triage failures by boundary:

- A `verify-native` failure belongs to Pelorus and must be fixed here.
- Context drift means a canonical source changed without regeneration. Edit
  `AGENTS.md` or `.agents/agents/*.md`, then run `make compile-context`; never
  patch a generated projection directly.
- A `docs-lint` diagnostic names a file, line, and markdownlint rule. Fix the
  Markdown; the rule set is locked by the engine.
- Any audit failure is a governance regression in this repository. Fix it,
  or, where the check allows one, declare a dated entry in the `exceptions`
  list of `.standards.yaml` in a reviewed change.

Canonical context sources are `AGENTS.md` and `.agents/agents/*.md`. Generated
files that `compile-context` owns include root `CLAUDE.md`,
`.cursor/rules/hiss-invariants.mdc`, `.github/copilot-instructions.md`,
`.windsurfrules`, `.gemini/GEMINI.md`, `.codex/rules.md`,
and the Markdown persona projections under `.claude/`, `.codex/`, `.github/`,
and `.gemini/`. Direct edits to those projections are overwritten or rejected
by verification. The text register block between the
`<!-- praetor:register:start -->` markers in `AGENTS.md` is rendered from the
manifest by `compile-context`; do not edit it by hand.

The engine installs three text-register skills (`caveman`, `social-text`,
`adhd-format`) under `.agents/skills/`; `compile-context` copies them to
`.claude/skills/`. `compile-context --verify` and the audit also lint the
nested `ffmpeg-patches/AGENTS.md`, `libpelorus/AGENTS.md`, and
`tools/AGENTS.md` in the Caveman register: at most 2.0 articles per 100 prose
words and at most 30 words per sentence. Check one file with
`praetorctl caveman check --kind=context <file>`.

`.paperclip/harness.json` and `.paperclip/rules.md` are a separate generated
pair from `standardsctl paperclip harness`; `compile-context` does not own them.
Regenerating that pair requires an explicit consumer review because the pinned
generator does not yet honor every Pelorus branch, language, and policy choice
(Praetor issue 321). The audit lints the harness's operating-contract and
invariant strings in the internal (Caveman) register. `register.sources` in
`.standards.yaml` records how many strings it read and their digest, so an
edited string also needs new pins. Since the ADR-0168 pin the audit also fails
when `rules.md` is not the engine's rendering of `harness.json`. `adopt` refuses to re-bind that drift, even
with `--force`. Stage the edit, run
`standardsctl caveman check --configured-sources --root=.`, and copy the
`actual` digest (and count, if it changed) that it reports into
`register.sources`.

### Declared policy that no gate runs

The profile and facets in `.standards.yaml` declare more controls than Pelorus
executes. The audit verifies the lock, the baseline, generated surfaces, the
documentation gate, build warnings, clang-tidy coverage, and the release
provenance, signature, and SBOM ([Release](#release)). It does not check the
controls below, and no workflow implements them; they are declared only
([ADR-0145](../adr/0145-praetor-governance-adoption.md)).

| Declared by | Control | Current state |
| --- | --- | --- |
| `native-gpu-systems`, `security:high`, `api:public-contract` | Signed commits, stale-review dismissal, review-thread resolution, 16 required checks | Committed in `.github/rulesets/main.json`, not applied yet; live `master` protection requires linear history and four checks, no signatures. Approving reviews: zero under `review_mode: single_maintainer` ([Branch ruleset](#branch-ruleset)) |
| `native-gpu-systems` | `semgrep`, `cppcheck`, `clippy` | `semgrep` runs in CI (`semgrep` job) and via the fast suite (`scripts/test-semgrep-float-identity.py`, ADR-0239); `cppcheck` not run; Pelorus has no Rust for `clippy` |
| `security:high` | `gitleaks`, `trivy` | Not run; `.gitleaks.toml` only configures a manual `gitleaks` run |
| `api:public-contract` | `buf`, `spectral`, OpenAPI drift, `Migration:` footer check | Not run; Pelorus has no protobuf or OpenAPI surface. The append-only C ABI is guarded by the interop conformance fixture and review |
| `docs:seo-portal` | Schema.org JSON-LD, sitemap, `robots.txt`, Core Web Vitals | Not applicable; Pelorus builds no documentation site. The facet's locked Markdown and figure gate does run |
| `native-gpu-systems` | No per-frame dynamic allocation | HISS-03, by C review only |

The declared linters that do run are `semgrep` (the `semgrep` CI job and
`semgrep-float-identity` test, ADR-0239), `clang-tidy` (the `core` job and
`make verify-native`, over the units in `.config/clang-tidy/lane-files.txt`)
and `markdownlint` (the documentation gate). HISS-04
complexity is split the same way. For C the audit ratchets only the 60-line
function cap. Its cyclomatic, cognitive, and statement measurement covers Go
sources only, so the effective limits of 10, 12, and 40 are not machine-checked
here. The `.clang-tidy` function-size thresholds (75 lines, 120 statements, 20
branches) are advisory and cover the clang-tidy lane only. Those limits are reviewer
checks.

### Agent hooks are registered

The manifest no longer declines `agent-hooks`
([ADR-0145](../adr/0145-praetor-governance-adoption.md)). Adoption registers
the engine's pre-tool interceptor in each agent client's tracked hook file,
beside Pelorus's own hooks:

| File | Client event and matcher | Command | Timeout |
| --- | --- | --- | --- |
| `.claude/settings.json` | `PreToolUse`, `Bash` (the group that also runs `block-unsafe-bash.sh`) | `praetorctl hook claude pre-tool` | 15 s |
| `.codex/hooks.json` | `PreToolUse`, `Bash` (same group as the Codex `block-unsafe-bash.sh`) | `praetorctl hook codex pre-tool` | 15 s |
| `.gemini/settings.json` | `BeforeTool`, `^run_shell_command$` | `praetorctl hook gemini pre-tool` | 15000 ms |

The hook reads the client's JSON payload on stdin, finds the repository from
the payload's `cwd` (or its own working directory), and judges only the shell
command. It denies with exit 2 and a `[BLOCKED BY HISS] ...` line on stderr
when the command skips Git hooks (`--no-verify` and its abbreviations, `-n` on
`git commit` or `git am`), disables Lefthook, sets `core.hooksPath`, writes
into `.git/hooks`, or runs `lefthook uninstall`. Any other command passes with
exit 0 and no output. Outside a checkout that holds `.standards.yaml` it skips
with exit 0 and a reason on stderr. Praetor's `docs/guides/agent-hooks.md` at
the pinned commit lists every rule. The hook judges agent tool calls only;
commands you type in your own terminal never pass through it.

Everyone who runs Claude Code, Codex, or Gemini CLI in this repository needs
an executable named `praetorctl` on `PATH`. The engine pin is `PRAETOR_REF` in
`.github/workflows/standards-gate.yml`, which `.config/lefthook/engine.sh`
also reads (ADR-0187). The pinned `go install` builds `standardsctl`, so copy it under the name the hooks call,
then check it with a sample payload from the repository root:

```bash
ref=$(sed -n 's/^[[:space:]]*PRAETOR_REF:[[:space:]]*//p' .github/workflows/standards-gate.yml)
go install "github.com/cordanaLLM/praetor/cmd/standardsctl@${ref}"
cp "$(go env GOPATH)/bin/standardsctl" "$(go env GOPATH)/bin/praetorctl"   # Windows: standardsctl.exe to praetorctl.exe
printf '%s' '{"tool_name":"Bash","tool_input":{"command":"ls"},"hook_event_name":"PreToolUse"}' \
  | praetorctl hook claude pre-tool; echo "exit=$?"                          # expect exit=0, no output
```

When `praetorctl` is missing, the shell answers 127 (bash) or 1 (`cmd` and
PowerShell). Claude Code and Gemini CLI treat every exit other than 2 as a
non-blocking error: they show it and run the command without the Praetor
policy. Codex documents only exit 0 and exit 2, so its handling of 127 is
unverified. Pelorus's own `block-unsafe-bash` hook runs either way. Install the
engine before starting an agent session rather than relying on that fallback.

On Windows, Claude Code runs hook commands through Git Bash, which the
`.claude/hooks/*.sh` scripts need anyway; Gemini CLI runs them through
PowerShell and appends `exit $LASTEXITCODE` itself. The exit codes above hold
under Git Bash, `cmd /c`, a direct PowerShell call, and Gemini's PowerShell
form ([research digest 0145](../research/0145-praetor-adoption-measurements.md)).
A wrapper that ends with the hook as the last statement of
`powershell -Command` without `exit $LASTEXITCODE` would turn a deny (2) into
1, which the clients do not block on.

Adoption owns the three registrations. It adds a missing row and leaves a file
that already has one byte for byte as it is, so re-running the adoption command
in the ADR is how to restore a row. Keep Pelorus's own hook entries where they
are; the merge preserves them.

### Git hooks (opt-in per clone)

The manifest adopts `git-hooks`
([ADR-0153](../adr/0153-praetor-full-adoption.md)). `lefthook.yml` holds the
jobs the pinned engine renders plus four Pelorus jobs, and
`.config/lefthook/` and `.config/agent/` hold the checkpoint scripts, the
checkpoint policy, and the agent interceptor that come with them. Install the
hooks once per clone, in your own terminal:

```bash
make hooks-install   # runs `lefthook install`
```

| Hook | Jobs |
| --- | --- |
| pre-commit | `reuse-lint` (`reuse lint`; skipped where reuse is not installed, fails on a reuse other than 6.x), `context-check` (`compile-context --verify`), `hiss-audit` (`audit --offline`), `pelorus-audit` (`make audit AUDIT_FLAGS=--offline`) |
| commit-msg | `message-policy` (`forge check-message`: Conventional Commits subject, `Migration:` footer on a breaking change), `dco-signoff` (`Signed-off-by` trailer) |
| post-commit | `state-sync`, `dedupe-cadence` (governance bookkeeping in the git-ignored `.workingdir/`) |
| pre-push | `flavor-audit`, `audit` (reads the forge), `gate` (`gate run --admit-unsupported`), `verify-all` (`make verify-all`) |

`pelorus-audit`, `message-policy`, `dco-signoff` and `verify-all` are the
Pelorus additions. The pre-push
`gate` job needs a clean working tree, and admits a Meson repository without
signing a receipt. The pre-push `audit` and `verify-all` read the forge, so
they fail while live `master` protection differs from the committed ruleset
([Branch ruleset](#branch-ruleset)). Installed hooks need `praetorctl` (or
`standardsctl`), `lefthook` 2.1.14 or newer, `make`, Python 3.10 or newer, and,
for a push, the native and documentation toolchains of `make verify-all`.

#### Commit policy

Every commit needs a Conventional Commits subject, a `Signed-off-by` trailer
(`git commit -s`), and, when breaking (`!` in the subject), a `Migration:`
footer with before/after snippets (HISS-14). The hooks are opt-in, so CI is the
authority: the `Standards` workflow runs `standardsctl forge check-commits`
and a DCO check over every commit of a pull request, from the base branch to
the pull request head.

| Where | Check | Command |
| --- | --- | --- |
| `commit-msg` hook | subject + `Migration:` footer | `praetorctl forge check-message <file>` |
| `commit-msg` hook | DCO trailer | `grep` for `Signed-off-by: Name <email>` in the message |
| `standards-gate.yml`, pull requests | subject + `Migration:` footer | `standardsctl forge check-commits --base origin/<base> --head <pr head>` |
| `standards-gate.yml`, pull requests | DCO trailer | `python3 -I scripts/check-dco.py --base origin/<base> --head <pr head>` |

Check a range locally with `praetorctl forge check-commits --base origin/master`
or `python3 -I scripts/check-dco.py --base origin/master --head HEAD`.

The DCO check (`scripts/check-dco.py`) verifies that every non-merge commit
in the pull request range carries a valid `Signed-off-by` trailer matching
the Developer Certificate of Origin 1.1 format (`Signed-off-by: Full Name <email@example.org>`).
The message is split into lines on LF only, as git does, so a trailer
after a carriage return or another Unicode line separator does not count.
Renovate bot commits are exempt when authored by Renovate in a
Renovate-opened pull request. CI passes the pull request author with
`--pr-author`; to check a Renovate branch locally, add
`--pr-author 'renovate[bot]'`, since without it Renovate's unsigned commits fail.
Before git runs, the script exits 2 for a base or head with a space or any of
`\ : ? * [` or another character outside printable ASCII, a leading `-`, or a
`..`. It does not check the rest of git's ref-name rules: a revision such as
`origin/master/` passes this check, reaches git and fails as an unusable range.
git itself is resolved to an absolute path on `PATH`. Run its self-test battery with
`python3 -I scripts/check-dco.py --self-test`.

`lefthook.yml` sets `no_auto_install: true`. Without it, `lefthook run`
re-installs the hooks whenever the file differs from the one installed, from
whichever worktree commits first. Run `make hooks-install` again after
`lefthook.yml` changes. Because adoption installs hooks only for an unedited
rendering, it does not install them here either.

Linked worktrees share the repository's Git hooks directory, so installing or
removing hooks is repository-wide. Remove them with Lefthook's verified
removal command, typed in your own terminal (the agent hook denies it to
agents):

```bash
lefthook uninstall
```

### Branch ruleset

`.github/rulesets/main.json` is the ruleset `praetor-main-protection` that the
engine renders for `master` and `lts-*`. `.standards.yaml` declares
`overrides.branch_protection.review_mode: single_maintainer` (Praetor's
`docs/guides/review-policy.md`), so the rendering requires:

| Rule | Value |
| --- | --- |
| Pull request | 0 approving reviews, no code-owner review, stale reviews dismissed, review threads resolved |
| Required status checks (strict) | the nine `ci.yml` jobs (the `semgrep` job included; `windows-msvc` reports one check per compiler, so ten contexts), the three `CodeQL` legs, `Documentation Governance`, `REUSE lint`, `Standards & Invariant Verification Gate` |
| History | signed commits, linear history, no force push, no deletion |
| Bypass | repository admin role, pull requests only |

The engine derives the checks from the workflows: a job is required when every
pull request reports it. The `Dev container image` workflow is path-filtered,
so it adds none. The audit re-renders the file and fails on any drift; never
edit it by hand. A change to a job name or a trigger changes the rendering, so
regenerate the file with the adoption command in ADR-0153 in the same change.

The ruleset is committed, not applied. After the change that adds it lands,
the maintainer applies it from an up-to-date `master`:

```bash
praetorctl plan --remote    # preview; reads GITHUB_TOKEN or GH_TOKEN
praetorctl sync --remote    # writes the ruleset, labels and repository metadata
```

`sync --remote` writes rulesets only: the first run creates
`praetor-main-protection` with its bypass actor, a later run keeps the checks
it finds there, and each run prints a review setting it lowers. The classic
`master` rule stays until it is removed by hand. The repository merges by squash only; GitHub signs a squash
commit made in the web interface, which the signed-commit rule needs. Until
the ruleset is applied, an audit with a forge token fails like this:

```text
[FAIL] Live branch protection of master on GitHub does not match the declared policy (protected by branch protection):
  Dismiss stale reviews: declared required, live not enforced
  Signed commits: declared required, live not enforced
  Required status checks: declared 8, live 4 of 8 required; missing: ...
```

### Dev container

`.devcontainer/` holds the bundle the pinned engine renders:
`devcontainer.json`, `Dockerfile.praetor`, and six `praetor-source.*.b64`
parts that build `praetorctl` into the image. The audit compares them with the
rendering byte for byte; regenerate them with
`praetorctl devcontainer --source-root <Praetor checkout at the pin> --force`,
never by hand. The rendered image is the Pelorus toolchain base (below) plus
`praetorctl` and the catalog's `common-utils`, `nix`, and `rust` features, so
its `postCreateCommand` (`make verify-all`) finds Meson, Ninja, clang, glslc,
Vulkan headers, and Node.js.

`.devcontainer/base/Containerfile` is that toolchain base. It starts from the
plain `ubuntu:26.04` image, pinned by digest ([ADR-0179](../adr/0179-dev-image-licence-record.md)),
not from Microsoft's devcontainers base, which ships a source-built GPL `git`.
It adds the `vscode` user with `sudo`, the packages the CI jobs install (git is
Ubuntu's package), Node.js 24, actionlint, and Lefthook, each download checked
against its published SHA-256. The `Dev container image` workflow
(`.github/workflows/devcontainer-image.yml`) handles it:

| Trigger | Job | Result |
| --- | --- | --- |
| Pull request touching `.devcontainer/base/**` or the workflow | `build` | builds the image, pushes nothing; on a draft its first step fails by design |
| Push to `master` touching the same paths, or manual dispatch | `publish` | pushes `ghcr.io/vmafx/pelorus-dev` tagged with the commit SHA and `latest`, reads the digest back, attests SLSA build provenance for it |

Only `publish` holds `packages: write`, with `id-token`, `attestations`, and
`artifact-metadata` for the attestation. Check a published digest with:

```bash
gh attestation verify oci://ghcr.io/vmafx/pelorus-dev@sha256:<digest> --repo VMAFx/pelorus
```

The bundle builds on that image. `.devcontainer/Dockerfile.praetor` and the
`baseImage` field in `devcontainer.json` name
`ghcr.io/vmafx/pelorus-dev@sha256:92599aa475c90ef9609d28979f6ee56d85fd995f52ca0af94c27dd4205040498`,
the image `publish` built from `8e6a41e`, the last commit to change
`.devcontainer/base/Containerfile`. The reference is a digest, never a tag; the
renderer rejects a tag-only `--base-image`, and the audit's `DevContainer
bundle` check fails on any drift from the rendering. The package is public: the
digest resolves without a login.

Refresh the pin whenever `.devcontainer/base/Containerfile` changes:

1. Merge the Containerfile change to `master`; wait for `publish` to push.
2. Read the new digest from the `publish` run summary, or from
   `gh api 'orgs/VMAFx/packages/container/pelorus-dev/versions?per_page=5'`
   (the version tagged with the commit SHA).
3. Verify it: `gh attestation verify oci://ghcr.io/vmafx/pelorus-dev@sha256:<digest> --repo VMAFx/pelorus`.
4. Re-render, never hand-edit:
   `praetorctl devcontainer --force --source-root <Praetor checkout at the pin>
   --base-image ghcr.io/vmafx/pelorus-dev@sha256:<digest>`.
5. Run `praetorctl audit --offline` and confirm `DevContainer bundle` reports in sync.

#### Licence record and notices

The image is public, so it carries the same licence record as the tester image.
`.devcontainer/base/licensing.json` lists every component outside the Ubuntu
packages (Node.js, actionlint, Lefthook, Canonical's `pebble`, the gate itself)
with its SPDX licence, paths, source and licence texts. Ubuntu files are recorded
by package ownership, and each package needs a copyright file and an archive
component of `main` or `universe`. The `licence` stage of the Containerfile runs
`tools/tester/licensing.py` on the finished tree and fails the build on a file
that no package, component or expiring ignore rule owns, an unknown or
non-redistributable licence, or a missing licence text. A tool added under
`/usr/local` without a record entry therefore fails the pull request build.

Inside the image:

| Path | Content |
| --- | --- |
| `/usr/share/licenses/pelorus-dev/THIRD_PARTY_NOTICES.txt` | generated notices: repository and commit, every component with its licence, every Ubuntu package with its version |
| `/usr/local/share/licenses/{node,actionlint,lefthook}/` | licence text of each tool installed outside the package system |
| `/usr/local/share/pelorus-dev/licensing.{py,json}` | the gate and the record; run `python3 -I -B licensing.py check --root / --record licensing.json --commit <40-digit commit>` to repeat the build check |

The image sets no `org.opencontainers.image.licenses` label; the label
`dev.vmafx.pelorus.notices` names the notices file. After a change to what the
image installs, edit `licensing.json` in the same commit and check it without
an image: `python3 -I -B tools/tester/licensing.py record --record .devcontainer/base/licensing.json`.

To try the toolchain base without the registry, build it locally from the
repository root (the licence gate lives in `tools/tester/`):

```bash
docker build --file .devcontainer/base/Containerfile \
  --build-arg PELORUS_COMMIT="$(git rev-parse HEAD)" --tag pelorus-dev .
```

Neither image passes a GPU through: the Vulkan shaders compile, the GPU tests
do not run.

## Dependency updates (Renovate)

`renovate.json` drives two kinds of machine updates, and the build-config
checker (`meson test -C build --suite=fast`, test `build-config-sync`) fails if
either would leave a copied value behind:

- **FFmpeg pin** — one regex manager updates `FFMPEG_TAG` and `FFMPEG_COMMIT`
  in `build-config.env` together
  ([ADR-0144](../adr/0144-ffmpeg-pin-and-ci-runner-policy.md)). The shared
  series pin below it (`FFMPEG_SERIES_*`) is bumped by hand: Renovate cannot
  derive the tarball sha256 or the tagged commit, and
  `scripts/fetch-ffmpeg-series.sh` refuses a series made for another FFmpeg
  release, so an FFmpeg bump needs a series release on the new base first
  ([ADR-0185](../adr/0185-shared-ffmpeg-fix-series.md)).
- **actionlint Go toolchain** — the docs job's `actions/setup-go` step
  (`go-version: '<major>.<minor>.x'`) is bumped by Renovate's built-in
  github-actions handling as dependency `go` (datasource `github-releases`,
  package `actions/go-versions`, `npm` versioning). The checker's own expectation,
  `ACTIONLINT_GO_VERSION` in `scripts/check-build-config.py`, is covered by a
  second regex manager with exactly those templates, so both edits share one
  `renovate/go-<major>.x` branch and PR. The checker verifies that manager's
  templates and that its `matchStrings` entry captures the literal exactly
  once. Removing or breaking that manager fails the fast suite. The checker
  does not evaluate other `renovate.json` settings (`ignorePaths`,
  `enabledManagers`, `packageRules`, `extends` presets); if one of them splits
  or drops the bump, the Renovate PR that edits only one site fails
  `build-config-sync`, and that red PR is the detection point
  ([ADR-0152](../adr/0152-renovate-guard-enforcement-scope.md)). The step name
  carries no version for the same reason
  ([ADR-0151](../adr/0151-renovate-mirrors-checker-toolchain-pins.md)).

The Windows job's `msys2/setup-msys2` action is bumped by Renovate's
github-actions manager like every other action (digest plus `# vX.Y.Z`
comment). The checker holds no copy of that pin. It checks only its shape:
exactly one `uses: msys2/setup-msys2@<40-hex digest> # vX.Y.Z` line, before
the first MSYS2 step. So such a bump changes `ci.yml` alone and stays green
([ADR-0149](../adr/0149-windows-utf8-paths.md)).

The checker decides which managers cover a file the way Renovate does, through
`managerFilePatterns` (see
[research digest 0151](../research/0151-renovate-setup-go-mirroring.md)). An
entry is either a `/regex/` or `/regex/i`, optionally negated with `!`, or a
minimatch glob with `dot` and `nocase`. Globs may use `*`, `?`, whole-segment
`**`, `{a,b}` and a leading `!`. The checker reports any other glob syntax as an
error rather than guess at it: character classes, extglobs, escapes, ranges,
and braces that span `/`. Use a `/regex/` entry for anything more specific.

The docs job's `actions/setup-go` pin and the actionlint release follow the same
rule as setup-msys2: the checker holds no copy of either value and checks only
the shape. The job needs exactly one
`uses: actions/setup-go@<40-hex digest> # vX.Y.Z` line and exactly one
`run: go run github.com/rhysd/actionlint/cmd/actionlint@vX.Y.Z` line, with no
floating tag or branch ref. Renovate's github-actions manager bumps the
setup-go digest. A third regex manager in `renovate.json` covers only
`.github/workflows/ci.yml`, reads the actionlint tag as `currentValue`, and
looks it up as dependency `github.com/rhysd/actionlint` in the `go` datasource.
The checker requires exactly one regex manager for `ci.yml`, with those two
templates and a `matchStrings` entry that matches `ci.yml` once. The manager
for the checker file stays the Go-toolchain one above
([ADR-0170](../adr/0170-renovate-shape-guards.md),
[research digest 0170](../research/0170-renovate-actionlint-manager.md)).

When bumping by hand, change the `go-version` in `.github/workflows/ci.yml` and
`ACTIONLINT_GO_VERSION` together, then run
`python3 scripts/check-build-config.py --self-test` and
`go run github.com/rhysd/actionlint/cmd/actionlint@<the tag in ci.yml>`.

## Release

SemVer tags `v<major>.<minor>.<patch>` and release candidates
`v<major>.<minor>.<patch>-rc.<N>` ([procedure](release.md)). The interop ABI is append-only from
v0.1.0 (`PELORUS_ABI_MINOR` bumps on additions). The shared conformance fixture
must pass in both Pelorus and vmafx before a release that touches the ABI.

The `Release` workflow (`.github/workflows/release.yml`) runs three jobs in
order ([ADR-0169](../adr/0169-release-provenance-slsa3.md)):

1. `ci` calls the whole `CI` workflow (`uses: ./.github/workflows/ci.yml`:
   core, FFmpeg patch-stack regenerate, replay, link and smoke, the lavapipe
   lane, sanitizers, Windows, docs), so a tagged commit with a broken stack never builds a
   release. The call runs with the caller's `github` context, and `ci.yml`
   keys its concurrency group on the workflow name so it cannot cancel a push
   or pull-request run.
2. `build` (`needs: ci`) calls the reusable `.github/workflows/release-build.yml`,
   whose only trigger is `workflow_call`. Its one job configures with
   `meson setup --werror build`, builds with `make build`, runs the fast suite,
   checks the rendered changelog, extracts the release notes from the
   `## [X.Y.Z]` section for the meson version
   (`scripts/release/extract-release-notes.sh`; a missing or empty section
   fails the build), and packages
   `pelorus-ffmpeg-patches-<tag>.tar.gz` (`series.txt`, the README, the
   numbered patches, `files/`, the LGPL-2.1-or-later and EUPL-1.2 texts
   from `LICENSES/`, and a generated `NOTICE`, [licensing](../licensing.md)).
   The release notes extractor unwraps paragraphs and list items, orders
   `### Highlights` first and `### Maintenance` last, and rewrites root-relative
   documentation links and reference definitions into absolute GitHub blob
   URLs pinned to `v<VERSION>` at `<server>/<owner>/<repo>/blob/<tag>/<path>`
   (via `--repo` or `$GITHUB_REPOSITORY`, with `$GITHUB_SERVER_URL` defaulting
   to `https://github.com`), refusing link targets that escape the repository root (`../`).
   The `NOTICE` names
   the shared FFmpeg fix series release the stack needs first (tag, commit,
   tarball sha256); the archive does not contain that series. It then writes an SPDX JSON SBOM of that
   archive, writes `SHA256SUMS` over the archive and the SBOM, attests SLSA v1.0
   build provenance for both with `actions/attest-build-provenance`, signs
   `SHA256SUMS` keyless with cosign into `SHA256SUMS.sigstore.json`, verifies
   that signature against its own workflow identity, and uploads the files as
   one workflow artefact. No step downloads an artefact or restores a cache
   before the attestation, so the attestation covers only what this job built.
   GitHub signs it with the reusable workflow's identity, isolated from the
   caller: SLSA Build Level 3, which `praetorctl audit` measures (HISS-11).
3. `publish` (`needs: build`) runs only on a `v*` tag push. It downloads the
   artefact, re-checks `SHA256SUMS`, and runs `gh release create` with the
   archive, the SBOM, the provenance bundle, `SHA256SUMS`, and
   `SHA256SUMS.sigstore.json`. It is the only job with `contents: write`.

A tag push first checks the tag shape and the rc numbering
(`scripts/release/verify-release.py tag`, [release candidates](release.md)),
then asserts that `${GITHUB_REF_NAME#v}` equals the version meson
reports (`meson introspect --projectinfo build`; meson already fails
configuration if `pelorus.h` disagrees), that `pelorus.h` carries the same
number for number, and fails with an `::error::` line on a mismatch. A
`vX.Y.Z-rc.N` tag additionally runs the candidate legs check and publishes a
prerelease that is never `latest`. A manual `workflow_dispatch` is a **non-publishing rehearsal**: it
runs `ci` and `build`, including the attestation and the signature, skips only
the tag assertion, and never runs `publish`. Review the tag and the
`## [X.Y.Z]` notes before pushing a tag: a manual dispatch is not a
substitute for the tag event and never publishes on its own.

`scripts/check-build-config.py` enforces this shape: the CI call and the
`needs` chain, `workflow_call` as the only trigger of `release-build.yml`, the
tag step, the build-before-attestation step order, no artefact download or
cache in the release build, the SBOM action's upload switches, the signer
identity of the verification, the tag-only `publish` job, its asset list, and
`contents: write` on `publish` alone. Its self-test rejects a mutation of each.

### Verifying a release

Each release carries five files besides the notes. Verify them with the GitHub
CLI, cosign v3 or newer, and `sha256sum`:

```bash
TAG=v0.2.3
gh release download "$TAG" --repo VMAFx/pelorus
# SLSA provenance: signed by the reusable release build, built from the tag
gh attestation verify "pelorus-ffmpeg-patches-$TAG.tar.gz" -R VMAFx/pelorus \
  --signer-workflow VMAFx/pelorus/.github/workflows/release-build.yml \
  --source-ref "refs/tags/$TAG"
# cosign signature over the checksums
cosign verify-blob \
  --certificate-identity "https://github.com/VMAFx/pelorus/.github/workflows/release-build.yml@refs/tags/$TAG" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  --bundle SHA256SUMS.sigstore.json \
  SHA256SUMS
# the archive and the SBOM match the signed checksums
sha256sum -c SHA256SUMS
```

A release candidate carries a sixth file, `CANDIDATE_LEGS.json`, which
`SHA256SUMS` covers like the SBOM.

`gh attestation verify` fetches the attestation from GitHub; pass
`--bundle "pelorus-ffmpeg-patches-$TAG.provenance.sigstore.json"` to verify
against the bundle attached to the release instead. Because a reusable
workflow signs the attestation, `--signer-workflow` names
`release-build.yml`, not `release.yml`. The SBOM,
`pelorus-ffmpeg-patches-<tag>.spdx.json`, has no signature of its own:
`SHA256SUMS` covers it, and the provenance names it as a subject.
