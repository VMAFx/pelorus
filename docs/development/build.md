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
| --- | --- | --- |
| `tests` | true | build + register the libpelorus test suite |
| `shaders` | true | compile the standalone reference `.comp` shaders to SPIR-V (needs glslang) |
| `tools` | true | build the libpelorus CLI demonstrators (`pelorus_qp_report`; not installed) |

## FFmpeg filters (the patch stack)

Requires `libpelorus` installed (visible to `pkg-config`) plus a Vulkan loader +
SPIR-V compiler.

```bash
cd ffmpeg-patches
FFMPEG_REPO=/absolute/path/to/ffmpeg ./generate.sh           # regenerate patches
FFMPEG_REPO=/absolute/path/to/ffmpeg ./test/build-and-run.sh # apply + build + smoke
```

Both scripts read the exact tag and peeled commit from root `build-config.env`,
verify that the qualified tag resolves to that commit, and work in an isolated
run-owned Git worktree. The committed `*.patch` files are the artifact; edit
the sources under `files/` and regenerate.

### Native Windows (MSYS2 UCRT64)

`build-and-run.sh` also runs from an MSYS2 UCRT64 shell. The packages it needs
are the `mingw-w64-ucrt-x86_64-` builds of `gcc`, `meson`, `ninja`, `pkgconf`,
`nasm`, `python`, `glslang`, `shaderc` (for `glslc`), `vulkan-headers` and
`vulkan-loader`. Optional encoder consumers are enabled when their packages
are installed: `libvpl` for QSV, `aom`, `svt-av1` and `ffnvcodec-headers`.
Three things differ from Linux, and the script handles each of them:

- FFmpeg's program target is `ffmpeg$(EXESUF)`, which is `ffmpeg.exe`. A
  plain `make ffmpeg` has no rule on Windows.
- libpelorus installs `bin/libpelorus-0.dll`. The script puts the private
  prefix's `bin/` on `PATH`, because `LD_LIBRARY_PATH` does nothing on
  Windows and both `ffmpeg.exe` and the static consumer import the DLL.
- Native tools report MSYS paths in Windows form: `/tmp/x` becomes
  `C:/msys64/tmp/x`. The script therefore compares the pkg-config prefix
  against `cygpath -m` of the private prefix.

To run an installed Windows build later, put the install prefix's `bin/` and
`/ucrt64/bin` on `PATH`. Windows first searches the directory that holds the
`.exe`, so an `ffmpeg.exe` installed next to `libpelorus-0.dll` also finds it.

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

The `Release` workflow has two intentionally different entry points:

- A manual `workflow_dispatch` is a **non-publishing rehearsal**. It runs the
  build/fast-test gate, checks the rendered changelog, extracts release notes,
  and constructs the FFmpeg patch-stack archive in the runner, but it cannot run
  `gh release create` and retains no published release artifact.
- Pushing a `v*` tag runs the same gate and packaging, then publishes the GitHub
  release and attaches `pelorus-ffmpeg-patches-<tag>.tar.gz`. Review the tag and
  rendered `[Unreleased]` notes before pushing: a manual dispatch is not a
  substitute for the tag event and never publishes on its own.
