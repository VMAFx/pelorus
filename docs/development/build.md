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

Windows-specific behavior of the suite:

- **`path-utf8`** asserts the Windows half of the UTF-8 path contract. It
  creates a directory + file named with Latin-1, CJK, and an astral emoji
  through `_wfopen`, then reads them back through `pel_x265_csv_parse`'s UTF-8
  path. It checks that ill-formed UTF-8 returns `PEL_ERR_INVALID` and that a
  `\\?\` path past `MAX_PATH` opens. On POSIX the same test asserts the literal
  `fopen` pass-through instead.
- **Shader compile checks** discard their SPIR-V to `NUL`, not `/dev/null`: a
  MinGW `glslangValidator` would otherwise write a real `\dev\null` file.
- **`build-config-sync`** runs `scripts/check-build-config.py` without
  `--self-test` on Windows. The self-test's Git fixtures model the Linux-only
  FFmpeg replay; MSYS2 argument globbing and Python's CRLF text mode break them,
  and the Linux jobs run the full self-test on every PR.

The FFmpeg patch-stack scripts below remain Linux-only.

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
