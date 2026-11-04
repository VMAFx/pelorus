#!/usr/bin/env bash
#
# write-patch-notice.sh - NOTICE of the FFmpeg patch archive (#236, ADR-0178).
# The archive carries the licence texts; this file says which text covers which
# path, which FFmpeg the stack applies to, which shared FFmpeg fix series has to
# be applied first (ADR-0185), and where the Pelorus repository and the exact
# release commit are (EUPL-1.2 Article 5).
#
#   write-patch-notice.sh OUT TAG COMMIT
#   write-patch-notice.sh --self-test
#
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
set -euo pipefail

self_test() {
  local tmp_dir out_default out_derived out_partial
  tmp_dir="$(mktemp -d)"
  trap 'rm -rf "$tmp_dir"' EXIT

  out_default="${tmp_dir}/notice-default.txt"
  GITHUB_SERVER_URL="" GITHUB_REPOSITORY="" bash "$0" "$out_default" "v1.2.3" "0123456789abcdef0123456789abcdef01234567" >/dev/null
  grep -q '^Repository: https://github.com/VMAFx/pelorus (source' "$out_default" || {
    echo "self-test FAIL: default repository line mismatch" >&2
    exit 1
  }

  out_derived="${tmp_dir}/notice-derived.txt"
  GITHUB_SERVER_URL="https://github.fork.example" GITHUB_REPOSITORY="fork-org/pelorus-fork" bash "$0" "$out_derived" "v1.2.3" "0123456789abcdef0123456789abcdef01234567" >/dev/null
  grep -q '^Repository: https://github.fork.example/fork-org/pelorus-fork (source' "$out_derived" || {
    echo "self-test FAIL: env-derived repository line mismatch" >&2
    exit 1
  }

  out_partial="${tmp_dir}/notice-partial.txt"
  GITHUB_SERVER_URL="https://github.fork.example" GITHUB_REPOSITORY="" bash "$0" "$out_partial" "v1.2.3" "0123456789abcdef0123456789abcdef01234567" >/dev/null
  grep -q '^Repository: https://github.com/VMAFx/pelorus (source' "$out_partial" || {
    echo "self-test FAIL: partial env should fallback to default" >&2
    exit 1
  }

  if bash "$0" "${tmp_dir}/notice-bad.txt" "v1.2.3" "badcommit" >/dev/null 2>&1; then
    echo "self-test FAIL: bad commit was accepted" >&2
    exit 1
  fi

  if bash "$0" >/dev/null 2>&1; then
    echo "self-test FAIL: missing arguments accepted" >&2
    exit 1
  fi

  echo "write-patch-notice self-test: ok"
  exit 0
}

if [ "${1:-}" = "--self-test" ]; then
  self_test
fi

out="${1:?usage: write-patch-notice.sh OUT TAG COMMIT | --self-test}"
tag="${2:?usage: write-patch-notice.sh OUT TAG COMMIT | --self-test}"
commit="${3:?usage: write-patch-notice.sh OUT TAG COMMIT | --self-test}"
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
series_repo="$(value FFMPEG_SERIES_REPO)"
series_tag="$(value FFMPEG_SERIES_TAG)"
series_commit="$(value FFMPEG_SERIES_COMMIT)"
series_sha256="$(value FFMPEG_SERIES_SHA256)"

if [ -n "${GITHUB_SERVER_URL:-}" ] && [ -n "${GITHUB_REPOSITORY:-}" ]; then
  repo_url="${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}"
else
  repo_url="https://github.com/VMAFx/pelorus"
fi

cat >"$out" <<NOTICE
Pelorus FFmpeg patch stack: notice

Release:    ${tag}
Commit:     ${commit}
Repository: ${repo_url} (source of every file in this archive
            at the commit above; EUPL-1.2 Article 5)
Applies to: FFmpeg ${ffmpeg_tag} (${ffmpeg_commit}) from ${ffmpeg_remote}
Needs first: the shared FFmpeg fix series ${series_tag} of
            https://github.com/${series_repo} (commit ${series_commit}).
            This archive does not contain it. Download its release, check
            ffmpeg-patches-${series_tag}.tar.gz against sha256
            ${series_sha256}
            and against the signature and provenance its release notes name,
            apply its patches in its series.txt order, then this stack in
            ffmpeg-patches/series.txt order. Without the series the stack does
            not apply.

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
