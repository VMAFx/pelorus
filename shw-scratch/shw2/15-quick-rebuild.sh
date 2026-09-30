#!/usr/bin/env bash
# shw-2 step 15: incremental rebuild of the build tree as-is (experiments only;
# the tree is resynced to the regenerated stack by 13-sync-build.sh afterwards).
set -euo pipefail
FF=/c/tmp/pel/ffmpeg
LOG=/c/tmp/pel/shw-results/shw-2/15-quick-rebuild-${1:-x}.log
export PKG_CONFIG_PATH="/c/tmp/pel/prefix/lib/pkgconfig:/ucrt64/lib/pkgconfig:/ucrt64/share/pkgconfig"
export PATH="/c/tmp/pel/prefix/bin:$PATH"
start=$(date +%s)
{ make -C "$FF" -j8 ffmpeg.exe && make -C "$FF" -j8 install; } > "$LOG" 2>&1 || { tail -30 "$LOG"; exit 1; }
echo "rebuilt in $(( $(date +%s) - start ))s: $(grep -E '^(CC|GLSLC|SPIRV|LD)' "$LOG" | tr '\n' ' ' | cut -c1-300)"
