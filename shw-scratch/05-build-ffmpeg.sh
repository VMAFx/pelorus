#!/usr/bin/env bash
# shw-1 step 5: link ffmpeg then install, as build-and-run.sh does
# ("make -j ffmpeg" then "make -j install"), capped at -j8 (resource cap).
set -euo pipefail
FF=/c/tmp/pel/ffmpeg
LOGS=/c/tmp/pel/shw-results/shw-1
JOBS="${JOBS:-8}"
export PKG_CONFIG_PATH="/c/tmp/pel/prefix/lib/pkgconfig:/ucrt64/lib/pkgconfig:/ucrt64/share/pkgconfig"
export PATH="/c/tmp/pel/prefix/bin:$PATH"
# Windows difference: build-and-run.sh runs "make ffmpeg", but on mingw the
# program target is ffmpeg$(EXESUF) = ffmpeg.exe ("No rule to make target
# 'ffmpeg'"), so the target name carries EXESUF from ffbuild/config.mak.
EXESUF="$(sed -n 's/^EXESUF=//p' "$FF/ffbuild/config.mak")"
start=$(date +%s)
echo "== link ffmpeg${EXESUF} ($(date -Is)) =="
if ! make -C "$FF" -j"$JOBS" "ffmpeg${EXESUF}" > "$LOGS/05-ffmpeg-build.log" 2>&1; then
    echo "ERROR: make ffmpeg${EXESUF} failed" >&2
    grep -n -E 'error|Error' "$LOGS/05-ffmpeg-build.log" | head -40 >&2 || true
    tail -60 "$LOGS/05-ffmpeg-build.log" >&2 || true
    exit 1
fi
echo "== install ($(date -Is)) =="
if ! make -C "$FF" -j"$JOBS" install > "$LOGS/05-ffmpeg-install.log" 2>&1; then
    echo "ERROR: make install failed" >&2
    tail -60 "$LOGS/05-ffmpeg-install.log" >&2
    exit 1
fi
echo "== done in $(( $(date +%s) - start ))s ($(date -Is)) =="
grep -c -i 'warning:' "$LOGS/05-ffmpeg-build.log" || true
grep -i 'warning:' "$LOGS/05-ffmpeg-build.log" | grep -i pelorus | head -40 || true
ls -la /c/tmp/pel/prefix/bin
