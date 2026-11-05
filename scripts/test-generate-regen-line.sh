#!/usr/bin/env bash
#
# test-generate-regen-line.sh — test rebase-notes heading line logic
#
# SPDX-FileCopyrightText: 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
#

set -euo pipefail

# Started from a Git hook, the fixture git calls below would follow GIT_DIR and
# friends into the invoking repository (test-selftest-git-isolation.py).
export GIT_CONFIG_GLOBAL=/dev/null
export GIT_CONFIG_NOSYSTEM=1
unset GIT_AUTHOR_NAME GIT_AUTHOR_EMAIL GIT_COMMITTER_NAME GIT_COMMITTER_EMAIL GIT_COMMITTER_DATE
for _git_var in $(git rev-parse --local-env-vars); do
    unset "$_git_var"
done
unset _git_var

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# shellcheck source=ffmpeg-patches/patch-regen-line.sh
source "$ROOT/ffmpeg-patches/patch-regen-line.sh"

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/pelorus-regen-test.XXXXXX")"
trap 'rm -rf "$TMP_DIR"' EXIT

git -C "$TMP_DIR" init -q
git -C "$TMP_DIR" config user.name "Test Runner"
git -C "$TMP_DIR" config user.email "test@example.com"

mkdir -p "$TMP_DIR/ffmpeg-patches"
echo "patch 1 content" > "$TMP_DIR/ffmpeg-patches/0001-a.patch"
echo "patch 2 content" > "$TMP_DIR/ffmpeg-patches/0002-b.patch"
echo "patch 3 content" > "$TMP_DIR/ffmpeg-patches/0003-c.patch"
git -C "$TMP_DIR" add ffmpeg-patches
git -C "$TMP_DIR" commit -qm "initial patch set"

# Clean tree check -> expects 'no patch regenerates'
out_clean="$(print_patch_regen_line "$TMP_DIR" "ffmpeg-patches/0*.patch")"
if [[ "$out_clean" != "rebase-notes heading: no patch regenerates" ]]; then
    echo "ERROR: expected 'rebase-notes heading: no patch regenerates', got '$out_clean'" >&2
    exit 1
fi

# Modify 0002, add 0004 untracked -> expects 'regenerates 0002, 0004'
echo "patch 2 changed" > "$TMP_DIR/ffmpeg-patches/0002-b.patch"
echo "patch 4 untracked" > "$TMP_DIR/ffmpeg-patches/0004-d.patch"

out_dirty="$(print_patch_regen_line "$TMP_DIR" "ffmpeg-patches/0*.patch")"
if [[ "$out_dirty" != "rebase-notes heading: regenerates 0002, 0004" ]]; then
    echo "ERROR: expected 'rebase-notes heading: regenerates 0002, 0004', got '$out_dirty'" >&2
    exit 1
fi

# A failing git call fails closed instead of reading as a clean tree.
NOT_A_REPO="$TMP_DIR/not-a-repo"
mkdir -p "$NOT_A_REPO"
if out_err="$(GIT_CEILING_DIRECTORIES="$TMP_DIR" print_patch_regen_line "$NOT_A_REPO" "ffmpeg-patches/0*.patch" 2>/dev/null)"; then
    echo "ERROR: git failure outside a repository was accepted, got '$out_err'" >&2
    exit 1
fi

echo "test-generate-regen-line: PASS"
