#!/usr/bin/env bash
# Stop — remind to run the local gate when tracked C/shader/build sources are
# modified but (likely) untested. Quiet on the happy path; never blocks.
set -euo pipefail

root=$(git rev-parse --show-toplevel 2>/dev/null || exit 0)
cd "$root"

# Any modified tracked sources that the fast gate covers?
changed=$(git status --porcelain 2>/dev/null \
    | grep -E '\.(c|h|comp)$|meson\.build$|meson_options\.txt$' || true)
[ -z "$changed" ] && exit 0

echo "  [gate] source changes pending — before pushing, run the local gate:" >&2
echo "         meson test -C build --suite=fast  &&  clang-format --dry-run --Werror libpelorus/**/*.{c,h}" >&2
echo "         (FFmpeg filters: ffmpeg-patches/test/build-and-run.sh)" >&2
exit 0
