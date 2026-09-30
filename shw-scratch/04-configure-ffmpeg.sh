#!/usr/bin/env bash
# shw-1 step 4: configure the patched FFmpeg n9.0.2 tree natively (MSYS2 UCRT64).
# Base options = ffmpeg-patches/test/build-and-run.sh configure_ffmpeg()
#   (--prefix --libdir --enable-vulkan [--enable-libvpl --enable-libaom
#    --enable-libsvtav1 when pkg-config finds them] --disable-doc)
# plus the Intel-validation extras requested for stream shw:
#   --enable-gpl --enable-libx264 --enable-libx265 --enable-libdav1d
#   --enable-libvmaf, and --enable-ffnvcodec --enable-nvenc (compile coverage
#   for the NVENC patches; ffnvcodec 13.0.19 headers are present), plus explicit
#   --enable-d3d11va --enable-dxva2 (QSV on Windows sits on D3D11).
# Windows differences vs the script: persistent prefix C:/tmp/pel/prefix shared
# with libpelorus (Windows path form so .pc files are usable by native pkgconf),
# and PATH (not LD_LIBRARY_PATH) carries the libpelorus DLL directory.
set -euo pipefail
FF=/c/tmp/pel/ffmpeg
PREFIX=C:/tmp/pel/prefix
export PKG_CONFIG_PATH="/c/tmp/pel/prefix/lib/pkgconfig:/ucrt64/lib/pkgconfig:/ucrt64/share/pkgconfig"
export PATH="/c/tmp/pel/prefix/bin:$PATH"

[[ "$(pkg-config --variable=prefix libpelorus)" == "$PREFIX" ]] || {
    echo "ERROR: pkg-config did not resolve libpelorus from $PREFIX" >&2; exit 1; }
echo "libpelorus version: $(pkg-config --modversion libpelorus)"

extra=()
for m in vpl aom SvtAv1Enc; do
    pkg-config --exists "$m" || { echo "ERROR: $m missing" >&2; exit 1; }
    echo "found $m $(pkg-config --modversion "$m")"
done
extra+=(--enable-libvpl --enable-libaom --enable-libsvtav1)
extra+=(--enable-gpl --enable-libx264 --enable-libx265 --enable-libdav1d --enable-libvmaf)
extra+=(--enable-ffnvcodec --enable-nvenc --enable-d3d11va --enable-dxva2)

cd "$FF"
echo "+ ./configure --prefix=$PREFIX --libdir=$PREFIX/lib --enable-vulkan ${extra[*]} --disable-doc"
./configure \
    --prefix="$PREFIX" \
    --libdir="$PREFIX/lib" \
    --enable-vulkan "${extra[@]}" --disable-doc
echo "== configure done =="
grep -E '^(CONFIG|HAVE)_(PELORUS|VULKAN|SPIRV_COMPILER|LIBVPL|LIBAOM|LIBSVTAV1|LIBX264|LIBX265|LIBDAV1D|LIBVMAF|NVENC|FFNVCODEC|D3D11VA|DXVA2|QSV|VULKAN_ENCODE|HEVC_QSV_ENCODER|AV1_QSV_ENCODER|H264_QSV_ENCODER|HEVC_VULKAN_ENCODER|AV1_VULKAN_ENCODER|H264_VULKAN_ENCODER|HEVC_NVENC_ENCODER|AV1_NVENC_ENCODER|H264_NVENC_ENCODER|LIBPELORUS)' ffbuild/config.mak | sort
grep -E '^(GLSLC|EXTRALIBS-avfilter|EXTRALIBS)\b' ffbuild/config.mak || true
grep -n 'pelorus' ffbuild/config.mak | head -30
