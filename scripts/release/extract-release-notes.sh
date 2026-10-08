#!/usr/bin/env bash
#
# extract-release-notes.sh - print the "## [VERSION]" section of a changelog
# (up to the next "## [" heading) for use as the GitHub release body. Fails when
# the section is missing or empty; there is no fallback to [Unreleased].
#
# Usage: extract-release-notes.sh VERSION [CHANGELOG]
#
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
set -euo pipefail

version="${1:?usage: extract-release-notes.sh VERSION [CHANGELOG]}"
changelog="${2:-CHANGELOG.md}"

notes="$(V="$version" awk '
  index($0, "## [" ENVIRON["V"] "]") == 1 { f = 1; next }
  /^## \[/ { f = 0 }
  f
' "$changelog")"

if [ -z "$(printf '%s' "$notes" | tr -d '[:space:]')" ]; then
  echo "::error::${changelog} has no non-empty '## [${version}]' section; the release commit must rotate [Unreleased] into it" >&2
  exit 1
fi
printf '%s\n' "$notes"
