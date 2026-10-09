#!/usr/bin/env bash
#
# check-ffmpeg-licence.sh - licence gate for the FFmpeg inside a tester image
# (ADR-0173 decision 5, ADR-0176). The build is GPL-3.0-or-later and never
# nonfree. The gate reads what the binary itself reports and fails on
#
#   - a configure line without --enable-gpl and --enable-version3;
#   - --enable-nonfree, --enable-cuda-nvcc, --enable-cuda-sdk, libfdk-aac,
#     decklink or libmpeghdec in the configure line;
#   - the word "nonfree" anywhere in `-version` or `-L`;
#   - a `-L` text that does not name "either version 3 of the License".
#
# Usage: check-ffmpeg-licence.sh FFMPEG_BINARY
# Exit: 0 accepted, 1 refused (each reason on stderr), 2 usage.
#
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
set -euo pipefail

bin="${1:?usage: check-ffmpeg-licence.sh FFMPEG_BINARY}"
if [ ! -x "$bin" ]; then
  echo "check-ffmpeg-licence: ${bin} is not an executable file" >&2
  exit 2
fi

version_out="$("$bin" -hide_banner -version 2>&1)" || {
  echo "check-ffmpeg-licence: '${bin} -version' failed" >&2
  exit 1
}
licence_out="$("$bin" -hide_banner -L 2>&1)" || {
  echo "check-ffmpeg-licence: '${bin} -L' failed" >&2
  exit 1
}
configuration="$(printf '%s\n' "$version_out" | sed -n 's/^configuration: *//p' | head -n 1)"
if [ -z "$configuration" ]; then
  echo "check-ffmpeg-licence: '${bin} -version' printed no configuration line" >&2
  exit 1
fi

status=0
refuse() {
  echo "check-ffmpeg-licence: $1" >&2
  status=1
}

has_flag() {
  local flag
  for flag in $configuration; do
    [ "$flag" = "$1" ] && return 0
  done
  return 1
}

for required in --enable-gpl --enable-version3; do
  has_flag "$required" || refuse "configure line lacks ${required}"
done
for banned in nonfree cuda-nvcc cuda-sdk libfdk-aac libfdk_aac decklink libmpeghdec; do
  has_flag "--enable-${banned}" && refuse "configure line has --enable-${banned}, which this image never ships"
done
if printf '%s\n%s\n' "$version_out" "$licence_out" | grep -qi 'nonfree'; then
  refuse "'-version' or '-L' names nonfree"
fi
if ! printf '%s\n' "$licence_out" | grep -q 'either version 3 of the License'; then
  refuse "'-L' does not report GPL version 3 or later"
fi

if [ "$status" -eq 0 ]; then
  echo "check-ffmpeg-licence: ${bin}: GPL-3.0-or-later, no nonfree component"
fi
exit "$status"
