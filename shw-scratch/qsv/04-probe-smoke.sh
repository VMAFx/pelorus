#!/usr/bin/env bash
# shw-3 step 04: smoke the raw oneVPL probe (60 frames, 1080p) on one adapter:
# does the runtime honor per-frame MBQP (AVC; HEVC with and without Query),
# and do the encoder statistics buffers come back?
set -uo pipefail
export PATH="/c/tmp/pel/prefix/bin:/ucrt64/bin:$PATH"
SCR=/c/tmp/pel/shw-scratch/qsv
IMPL="${IMPL:-0}"
N="${N:-60}"
OUT=/c/tmp/pel/shw-results/qsv/04-smoke/impl$IMPL
mkdir -p "$OUT"
IN=$SCR/content/own1080.nv12
P=$SCR/qsv-encode-probe.exe
run() { # tag codec args...
    local tag=$1 codec=$2; shift 2
    local ext=h264; [ "$codec" = hevc ] && ext=h265
    "$P" impl=$IMPL codec=$codec qp=30 w=1920 h=1080 n=$N in="$IN" out="$OUT/$tag.$ext" csv="$OUT/$tag.csv" "$@" \
        >"$OUT/$tag.log" 2>&1
    echo "== $tag rc=$? size=$(stat -c %s "$OUT/$tag.$ext" 2>/dev/null)"
    sed 's/^/   /' "$OUT/$tag.log"
    if [ "$codec" = avc ]; then
        "$SCR/qpdump.exe" "$OUT/$tag.$ext" >"$OUT/$tag.qp.csv" 2>/dev/null
        echo "   qpdump (frame,type,base,top_mean,top_min,top_max,bot_mean,bot_min,bot_max) first 6:"
        sed -n 2,7p "$OUT/$tag.qp.csv" | sed 's/^/     /'
    fi
    echo "   probe csv first 4:"; sed -n 1,5p "$OUT/$tag.csv" | sed 's/^/     /'
}
run avc-none      avc mode=none finfo=1 units=1
run avc-mbqp      avc mode=mbqp pattern=alt delta=12 finfo=1
run avc-rect      avc mode=rect pattern=alt delta=12 finfo=1
run avc-stats     avc mode=mbqp pattern=alt delta=12 stats=1
run hevc-none     hevc mode=none units=1
run hevc-mbqp     hevc mode=mbqp pattern=alt delta=12
run hevc-mbqp-noq hevc mode=mbqp pattern=alt delta=12 skipquery=1
run hevc-rect     hevc mode=rect pattern=alt delta=12
run hevc-stats    hevc mode=none stats=1
