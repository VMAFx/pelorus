#!/usr/bin/env bash
#
# write-patch-notice.sh - NOTICE of the FFmpeg patch archive (#236, ADR-0178).
# The archive carries the licence texts; this file says which text covers which
# path, which FFmpeg the stack applies to, and where the Pelorus repository and
# the exact release commit are (EUPL-1.2 Article 5).
#
#   write-patch-notice.sh OUT TAG COMMIT
#
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
set -euo pipefail

out="${1:?usage: write-patch-notice.sh OUT TAG COMMIT}"
tag="${2:?usage: write-patch-notice.sh OUT TAG COMMIT}"
commit="${3:?usage: write-patch-notice.sh OUT TAG COMMIT}"
root="$(cd "$(dirname "$0")/../.." && pwd)"

if ! printf '%s' "$commit" | grep -Eqx '[0-9a-f]{40}'; then
  echo "write-patch-notice: COMMIT must be a 40-digit hex commit, got '${commit}'" >&2
  exit 2
fi

value() {
  local found
  found="$(sed -n "s/^$1=//p" "${root}/build-config.env" | head -n 1)"
  if [ -z "$found" ]; then
    echo "write-patch-notice: build-config.env has no $1" >&2
    exit 1
  fi
  printf '%s' "$found"
}
ffmpeg_remote="$(value FFMPEG_REMOTE)"
ffmpeg_tag="$(value FFMPEG_TAG)"
ffmpeg_commit="$(value FFMPEG_COMMIT)"

cat >"$out" <<NOTICE
Pelorus FFmpeg patch stack: notice

Release:    ${tag}
Commit:     ${commit}
Repository: https://github.com/VMAFx/pelorus (source of every file in this archive
            at the commit above; EUPL-1.2 Article 5)
Applies to: FFmpeg ${ffmpeg_tag} (${ffmpeg_commit}) from ${ffmpeg_remote}

Licence of each path (the texts are in LICENSES/):

  ffmpeg-patches/0*.patch                   LGPL-2.1-or-later
      copyright 2000-2026 the FFmpeg developers, 2026 Lusoris
  ffmpeg-patches/files/*.c, *.h, vulkan/**  LGPL-2.1-or-later
      copyright 2026 Lusoris; the vf_pelorus_*_vulkan.c files also name the
      FFmpeg developers
  ffmpeg-patches/series.txt, README.md      EUPL-1.2, copyright 2026 Lusoris

Files that join the FFmpeg tree keep FFmpeg's LGPL-2.1-or-later. An FFmpeg built
from this stack with --enable-gpl --enable-version3 is GPL-3.0-or-later as a
whole; libpelorus, which that build links, stays EUPL-1.2 (docs/licensing.md in
the repository). No file here is nonfree.
NOTICE
echo "write-patch-notice: wrote ${out}"
