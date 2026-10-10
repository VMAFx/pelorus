<!-- markdownlint-disable MD013 -->
# Pelorus FFmpeg patch stack

Native `vf_pelorus_*` Vulkan compute filters, shipped as a stack of patches
against **FFmpeg n9.0.2**, pinned to peeled commit
`946fcce07b6dcd0331c8cc609192aeff5e1924f8` — the same delivery model as the vmafx sibling's
`ffmpeg-patches/`. Interop-producing/consuming filters link **libpelorus** (the
shared interop ABI + filter contracts), while pure pixel transforms do not. A
single filtergraph can carry the Pelorus side-data blob straight through to a
hardware encoder, and a downstream vmafx `vf_libvmaf*` can read it. See
[docs/adr/0104-ffmpeg-patch-stack.md](../docs/adr/0104-ffmpeg-patch-stack.md).

The stack applies **on top of the shared FFmpeg fix series**
([VMAFx/ffmpeg-patches](https://github.com/VMAFx/ffmpeg-patches), release pinned
in root `build-config.env`), not on stock FFmpeg: see
[Shared series first](#shared-series-first).

## Layout

| Path | Role |
| --- | --- |
| `files/` | Canonical filter sources — the source of truth a maintainer edits. |
| `0001-*.patch` … | Generated artifacts (`git format-patch`), the applied form. |
| `series.txt` | Ordered apply list (cumulative stack, 22 patches; number 0021 is retired). |
| `generate.sh` | Regenerate the patches from `files/` on the shared series tip. |
| `test/build-and-run.sh` | Shared series + stack apply, configure, build, smoke-test gate. |

## Shared series first

Generic FFmpeg fixes live in the shared FFmpeg fix series, one release for
every VMAFx project ([ADR-0185](../docs/adr/0185-shared-ffmpeg-fix-series.md)).
Root `build-config.env` pins it by tag, commit and tarball sha256
(`FFMPEG_SERIES_*`). Wherever FFmpeg is patched, the order is:

1. the shared series, in its own `series.txt` order;
2. this stack, in `series.txt` order.

`../scripts/fetch-ffmpeg-series.sh <new-dir>` downloads the pinned release,
verifies it (sha256 pin, signed `SHA256SUMS` with `cosign`, build provenance
with `gh attestation verify`, FFmpeg base) and unpacks it to `<new-dir>/series`.
It needs network access, `cosign` and an authenticated `gh`, and it fails
closed. `generate.sh` and both replay scripts call it themselves. Release
`v0.1.0-rc.1` holds four patches: the Vulkan queue family fix that was Pelorus
patch 0021, GCC 14 and 16 diagnostics, the `hevc_nvenc` user data SEI budget,
and the drop of SEI that NVENC would write truncated. Pelorus patch 0022 builds
on the last two.

## Prerequisites

- A Vulkan SDK / loader + headers, and a **build-time** SPIR-V compiler: `glslc`
  (or `glslang`/`glslangValidator`) on PATH. FFmpeg 9 probes it with
  `check_glslc` and enables the `spirv_compiler` feature. This replaced FFmpeg
  8's `spirv_library` (libshaderc/libglslang linked in to compile GLSL at
  *runtime*), which FFmpeg 9 removed — see ADR-0143.
- **libpelorus installed and visible to pkg-config**
  (`pkg-config --exists libpelorus`). Build it from the repo root:
  `meson setup build && ninja -C build && ninja -C build install`.
- For regeneration and replay: network access, `cosign` and an authenticated
  `gh` (the shared series fetch above).

## Replay and build

```bash
FFMPEG_REPO=/absolute/path/to/ffmpeg ./test/build-and-run.sh
```

The gate verifies the qualified `n9.0.2` tag against root `build-config.env`,
checks out the immutable commit in a run-owned worktree, builds and installs
this Pelorus tree into a private prefix, fetches and verifies the shared
series, applies it and then `series.txt` with
`git am --3way`, links FFmpeg, and smoke-tests filter, BSF, and encoder-option
registration. When their pkg-config modules are present, the gate enables and
compiles the oneVPL, libaom, SVT-AV1, and NVENC consumers as well. Per-patch
`git apply --check` is not a substitute: the stack is cumulative and later
patches' context can depend on earlier ones.

**Building an object is not a gate.** Naming a `.spv.o` on the make command line
builds it through the pattern rule whether or not the Makefile's `OBJS` ever
references it, so a mis-registered shader passes a targeted build and then fails
at link with an undefined `ff_pelorus_<name>_comp_spv_data`. Always finish with
`make ffmpeg` and check the filters register in the linked binary:

```bash
./ffmpeg -hide_banner -filters | grep pelorus     # expect 10
./ffmpeg -hide_banner -bsfs    | grep pelorus     # expect pelorus_fgs
```

## Configure + build

```bash
./configure --enable-vulkan                               # filters auto-enable
make -j libavfilter/vf_pelorus_deband_vulkan.o            # single-TU check
make -j ffmpeg                                             # the REAL gate: links

```

Each compute filter is gated `*_filter_deps="vulkan spirv_compiler"`.
Interop-dependent filters then use a guarded `require_pkg_config` probe for
`libpelorus >= 0.2.0` and a per-filter
`*_filter_extralibs="libpelorus_extralibs"` assignment. That assignment closes
both the FFmpeg binary link and generated `libavfilter.pc` for static external
consumers; libpelorus is never added to global executable extralibs. Pure
transforms carry neither the probe nor the per-filter link assignment.
Force a filter with `--enable-filter=pelorus_deband_vulkan` to make a missing
dependency a configure error, or disable it explicitly. Install libpelorus
where pkg-config can see it (`--prefix=/usr`, or set `PKG_CONFIG_PATH`) before
configuring FFmpeg outside the replay gate.

## Regenerate

```bash
FFMPEG_REPO=/absolute/path/to/ffmpeg ./generate.sh
```

`generate.sh` reads and verifies the tag plus commit in root
`build-config.env`; `BASE_TAG` is not an input. It applies the shared series on
that commit and writes the Pelorus patches as the range above the series tip,
so no Pelorus patch carries a series change. Edit the sources under `files/`,
rerun it twice to prove byte-stability, and commit both sources and regenerated
`*.patch` artifacts in the same change. File numbers are the shipped ones (0021
stays unused); the `[PATCH n/22]` subject of a patch counts its position.

## Use

```bash
# zero-copy: decode -> deband (VRAM) -> hardware AV1 encode
ffmpeg -init_hw_device vulkan -hwaccel vulkan -hwaccel_output_format vulkan \
       -i input.mkv \
       -vf "pelorus_deband_vulkan=range=15:thry=0.012:dither=bluenoise:dynamic=1,hwdownload,format=p010le" \
       -c:v av1_nvenc -cq 28 out.mkv
```

See [docs/metrics/deband.md](../docs/metrics/deband.md) for every option and a
VMAF-measured tuning guide, and [docs/usage/ffmpeg.md](../docs/usage/ffmpeg.md)
for the full zero-copy pipeline.

## License

The patches and the sources under `files/` that they add to FFmpeg are
LGPL-2.1-or-later, the licence of the FFmpeg tree they join; the patches also
carry FFmpeg's own code in their context lines. New `libavfilter/*.c` files
carry FFmpeg's LGPL-2.1 header. The scripts, tests and documents here are
EUPL-1.2, like the rest of Pelorus, except `test/qsv-roi-regression.c`, which
is built inside FFmpeg's tree and is LGPL-2.1-or-later. The filters that link
`libpelorus` (EUPL-1.2) combine both licences in one FFmpeg build; see
[docs/licensing.md](../docs/licensing.md#the-ffmpeg-boundary) and
[ADR-0171](../docs/adr/0171-eupl-relicense.md).
