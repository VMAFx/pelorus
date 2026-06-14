<!-- markdownlint-disable MD013 -->
# Pelorus FFmpeg patch stack

Native `vf_pelorus_*` Vulkan compute filters, shipped as a stack of patches
against **FFmpeg n8.1.1** — the same delivery model as the vmafx sibling's
`ffmpeg-patches/`. The filters link **libpelorus** (the shared interop ABI +
filter contracts) so a single filtergraph can carry the Pelorus side-data blob
straight through to a hardware encoder, and a downstream vmafx `vf_libvmaf*` can
read it. See [docs/adr/0104-ffmpeg-patch-stack.md](../docs/adr/0104-ffmpeg-patch-stack.md).

## Layout

| Path | Role |
|---|---|
| `files/` | Canonical filter sources — the source of truth a maintainer edits. |
| `0001-*.patch` … | Generated artifacts (`git format-patch`), the applied form. |
| `series.txt` | Ordered apply list (cumulative stack). |
| `generate.sh` | Regenerate the patches from `files/` against a base tag. |
| `test/build-and-run.sh` | Apply + configure + build + smoke-test gate. |

## Prerequisites

- A Vulkan SDK / loader + headers and a SPIR-V compiler (libshaderc or
  libglslang) — FFmpeg's `spirv_library`.
- **libpelorus installed and visible to pkg-config**
  (`pkg-config --exists libpelorus`). Build it from the repo root:
  `meson setup build && ninja -C build && ninja -C build install`.

## Apply

```bash
cd /path/to/ffmpeg            # a pristine n8.1.1 checkout
git reset --hard n8.1.1
for p in /path/to/Pelorus/ffmpeg-patches/0*.patch; do
    git am --3way "$p" || break
done
```

`git am --3way` (not per-patch `git apply --check`) is the correct gate: the
stack is cumulative and later patches' context can depend on earlier ones.

## Configure + build

```bash
./configure --enable-vulkan --enable-libshaderc           # filters auto-enable
make -j libavfilter/vf_pelorus_deband_vulkan.o            # single-TU check
make -j                                                    # full build
```

Each filter is gated `*_filter_deps="vulkan spirv_library libpelorus"`, and a
soft `check_pkg_config libpelorus ...` probe sets the `libpelorus` config item
(the idiomatic FFmpeg pattern, cf. `libvmaf_cuda`). So the filters auto-enable
iff Vulkan, a SPIR-V compiler, **and** libpelorus are all present, and quietly
disable otherwise. Force the issue with `--enable-filter=pelorus_deband_vulkan`
(errors if a dep is missing) or `--disable-filter=pelorus_deband_vulkan`.
Install libpelorus where pkg-config can see it (`--prefix=/usr`, or set
`PKG_CONFIG_PATH`) before configuring FFmpeg.

## Regenerate

```bash
FFMPEG_REPO=/path/to/ffmpeg BASE_TAG=n8.1.1 ./generate.sh
```

Edit the sources under `files/`, rerun `generate.sh`, and commit both the
sources and the regenerated `*.patch` in the same change.

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

BSD-2-Clause-Patent for the patches authored here; the linked FFmpeg binary's
distribution is governed by FFmpeg's LGPL/GPL. New `libavfilter/*.c` files
carry FFmpeg's LGPL-2.1 header (they become part of FFmpeg when applied).
