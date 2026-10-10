#!/usr/bin/env bash
#
# build-ffmpeg.sh - configure, build and install the patched FFmpeg of one
# tester image kit, keep the tree as compiled for the -source image, and run
# the FFmpeg licence gate on the result (ADR-0173 decision 5, ADR-0180).
#
# Runs inside the build-<kit> stages of tools/tester/Containerfile, in the
# patched tree /src/ffmpeg that the build stage prepared:
#
#   build-ffmpeg.sh [KIT_FEATURE_FLAG...]
#
# The licence flags come from FFMPEG_LICENCE_FLAGS and are the same for every
# kit; a kit adds feature flags only (--enable-nvenc, --enable-libvpl, ...). A
# kit flag that changes the licence is refused before configure runs, and the
# gate refuses the finished binary on anything but GPL-3.0-or-later without a
# nonfree component.
#
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
set -euo pipefail

: "${FFMPEG_LICENCE_FLAGS:?the build stage sets FFMPEG_LICENCE_FLAGS}"
: "${FFMPEG_FEATURE_FLAGS:?the build stage sets FFMPEG_FEATURE_FLAGS}"
: "${MAKE_JOBS:?the build stage sets MAKE_JOBS}"

for flag in "$@"; do
  case "$flag" in
    --enable-gpl | --enable-version3 | --disable-gpl | --disable-version3 | \
      *nonfree* | *nvcc* | *cuda-sdk* | *npp* | *fdk* | *decklink* | *mpeghdec*)
      echo "build-ffmpeg: ${flag} is a licence decision, not a kit feature; refused" >&2
      exit 1
      ;;
    --enable-* | --disable-*) ;;
    *)
      echo "build-ffmpeg: ${flag} is not a configure feature flag" >&2
      exit 1
      ;;
  esac
done

cd /src/ffmpeg
export PKG_CONFIG_PATH="/opt/pelorus/lib/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"
# The flag variables hold several words each; splitting them is intended.
# shellcheck disable=SC2086
./configure --prefix=/opt/ffmpeg ${FFMPEG_LICENCE_FLAGS} ${FFMPEG_FEATURE_FLAGS} "$@"
make -j"${MAKE_JOBS}"
make install

# The tree as compiled, the configure line, the patch order and the shared
# FFmpeg fix series release (applied first, ADR-0185) go to the -source image.
mkdir -p /opt/source
git archive --format=tar HEAD | tar -x -C /opt/source
export LD_LIBRARY_PATH=/opt/ffmpeg/lib:/opt/pelorus/lib
configuration="$(/opt/ffmpeg/bin/ffmpeg -hide_banner -version | sed -n 's/^configuration: *//p')"
if [ -z "$configuration" ]; then
  echo "build-ffmpeg: ffmpeg -version printed no configuration line" >&2
  exit 1
fi
printf '%s\n' "$configuration" > /opt/source/ffmpeg-configure-line.txt
cp /src/pelorus/ffmpeg-patches/series.txt /opt/source/series.txt
cp /src/ffmpeg-series-release/ffmpeg-patches-*.tar.gz /opt/source/

# Licence gate: the planted-flag self-test runs first, so a broken gate cannot
# pass the binary.
/opt/gate/check-ffmpeg-licence-self-test.sh
/opt/gate/check-ffmpeg-licence.sh /opt/ffmpeg/bin/ffmpeg
