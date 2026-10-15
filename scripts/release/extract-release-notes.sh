#!/usr/bin/env bash
#
# extract-release-notes.sh - extract, validate, reorder and unwrap the
# "## [VERSION]" section of a changelog for use as the GitHub release body, or
# unwrap an existing release body, rewriting relative documentation links to
# absolute blob URLs at the release tag. Fails when the section or its
# "### Highlights" block (3 to 6 bullets) is missing; there is no fallback
# to [Unreleased].
#
# Usage:
#   extract-release-notes.sh VERSION [CHANGELOG] [--repo OWNER/NAME]
#   extract-release-notes.sh --unwrap FILE --tag TAG [--repo OWNER/NAME]
#   extract-release-notes.sh --self-test
#
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
set -euo pipefail

exec python3 -I "$(dirname "$0")/release_notes.py" "$@"
