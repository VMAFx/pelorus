#!/usr/bin/env bash
# shw-1: probe the MSYS2 UCRT64 toolchain used for the native Windows build.
set -u
echo "MSYSTEM=$MSYSTEM HOME=$HOME PWD=$PWD"
uname -a
for t in gcc cc meson ninja pkg-config pkgconf nasm glslc glslangValidator glslang python3 python git make bash; do
    printf '%-18s %s\n' "$t" "$(command -v $t || echo MISSING)"
done
gcc --version | head -1
meson --version
ninja --version
nasm -v
glslc --version | head -3
glslangValidator --version | head -2
python3 --version
git --version
make --version | head -1
git config --global --list 2>/dev/null | grep -Ei 'autocrlf|eol|safe' || echo "(no global autocrlf/eol in msys git)"
git config --system --list 2>/dev/null | grep -Ei 'autocrlf|eol' || echo "(no system autocrlf/eol in msys git)"
for m in vulkan vpl aom SvtAv1Enc ffnvcodec x264 x265 dav1d libvmaf; do
    printf '%-10s ' "$m"; pkg-config --modversion "$m" 2>&1
done
echo "PKG_CONFIG_PATH=${PKG_CONFIG_PATH:-}"
nproc
