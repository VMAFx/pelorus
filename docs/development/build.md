<!-- markdownlint-disable MD013 -->
# Building Pelorus

## libpelorus (the core)

Meson + Ninja.

```bash
meson setup build
ninja -C build
meson test -C build --suite=fast      # interop ABI + shader-compile checks
ninja -C build install                # install lib + headers + pkg-config
```

Options (`meson_options.txt`):

| Option | Default | Effect |
|---|---|---|
| `tests` | true | build + register the libpelorus test suite |
| `shaders` | true | compile the standalone reference `.comp` shaders to SPIR-V (needs glslang) |

## FFmpeg filters (the patch stack)

Requires `libpelorus` installed (visible to `pkg-config`) plus a Vulkan loader +
SPIR-V compiler.

```bash
cd ffmpeg-patches
FFMPEG_REPO=/path/to/ffmpeg BASE_TAG=n8.1.1 ./generate.sh   # regenerate patches
./test/build-and-run.sh                                     # apply + build + smoke
```

`generate.sh` works in an **isolated git worktree** at the base tag, so it never
touches your main FFmpeg checkout. The committed `*.patch` files are the
artifact; edit the sources under `files/` and regenerate.

## Local gate (run before pushing)

```bash
meson test -C build --suite=fast
clang-format --dry-run -Werror libpelorus/**/*.{c,h}
clang-tidy -p build libpelorus/src/*.c        # touched files clean
```

## Release

SemVer tags `v<major>.<minor>.<patch>`. The interop ABI is append-only from
v0.1.0 (`PELORUS_ABI_MINOR` bumps on additions). The shared conformance fixture
must pass in both Pelorus and vmafx before a release that touches the ABI.
