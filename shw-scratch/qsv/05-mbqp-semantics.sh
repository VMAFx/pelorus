#!/usr/bin/env bash
# shw-3 step 05: pin down how the Windows runtime interprets an AVC mfxExtMBQP
# (delta vs absolute), and whether HEVC MBQP can be forced; per adapter and
# LowPower mode.  60 frames 1080p, CQP 30, delta 12, alternating top/bottom.
# Exact check: per-MB QP from the decoded H.264 (qpdump.exe).
set -uo pipefail
export PATH="/c/tmp/pel/prefix/bin:/ucrt64/bin:$PATH"
SCR=/c/tmp/pel/shw-scratch/qsv
IMPL="${IMPL:-0}"
N="${N:-60}"
OUT=/c/tmp/pel/shw-results/qsv/05-mbqp-semantics/impl$IMPL
mkdir -p "$OUT"
IN=$SCR/content/own1080.nv12
P=$SCR/qsv-encode-probe.exe
summ() { # qp.csv -> "pattern: top/bot means for frames 0..3; mismatches vs expected"
    awk -F, -v exp_hi="$1" -v exp_lo="$2" 'NR>1{
        f=$1; top=(f%2==0); et=top?exp_hi:exp_lo; eb=top?exp_lo:exp_hi;
        if (NR<=5) s=s sprintf(" f%d[%s top=%.2f bot=%.2f]", f, $2, $4, $7);
        if ($5!=et || $6!=et || $8!=eb || $9!=eb) bad++; n++ }
        END { printf "%s | frames=%d with any MB off the expected top=%s/bot=%s pattern: %d\n", s, n, exp_hi"|"exp_lo, exp_lo"|"exp_hi, bad }' "$3"
}
run() { # tag codec lp exp_roi exp_rest args...
    local tag=$1 codec=$2 lp=$3 ehi=$4 elo=$5; shift 5
    local ext=h264; [ "$codec" = hevc ] && ext=h265
    "$P" impl=$IMPL codec=$codec lp=$lp qp=30 w=1920 h=1080 n=$N in="$IN" out="$OUT/$tag.$ext" \
        csv="$OUT/$tag.csv" "$@" >"$OUT/$tag.log" 2>&1
    local rc=$?
    printf '== %-28s rc=%d bytes=%-10s %s\n' "$tag" $rc "$(stat -c %s "$OUT/$tag.$ext" 2>/dev/null)" \
        "$(grep -E 'GetVideoParam|FATAL|warning' "$OUT/$tag.log" | sed -E 's/.*LowPower (0x[0-9a-f]+).*EnableMBQP (0x[0-9a-f]+).*/LowPower=\1 EnableMBQP=\2/' | tr '\n' ' ')"
    if [ "$codec" = avc ] && [ $rc -eq 0 ]; then
        "$SCR/qpdump.exe" "$OUT/$tag.$ext" -1 alt "$ehi" "$elo" >"$OUT/$tag.qp.csv" 2>"$OUT/$tag.qp.summary"
        echo "   $(summ "$ehi" "$elo" "$OUT/$tag.qp.csv")"
        echo "   $(cat "$OUT/$tag.qp.summary")"
    fi
}
LPS="${LPS:-on}"
for lp in $LPS; do
    echo "######## impl $IMPL LowPower=$lp"
    run avc-$lp-none          avc  $lp 30 30 mode=none
    run avc-$lp-mbqp-delta    avc  $lp 42 30 mode=mbqp qpmode=delta
    run avc-$lp-mbqp-delta-i  avc  $lp 42 30 mode=mbqp qpmode=delta initmbqp=1
    run avc-$lp-mbqp-value    avc  $lp 42 30 mode=mbqp qpmode=value
    run avc-$lp-mbqp-value-i  avc  $lp 42 30 mode=mbqp qpmode=value initmbqp=1
    run avc-$lp-rect          avc  $lp 42 30 mode=rect
    run hevc-$lp-none         hevc $lp -  -  mode=none
    run hevc-$lp-mbqp-delta   hevc $lp -  -  mode=mbqp qpmode=delta
    run hevc-$lp-mbqp-delta-i hevc $lp -  -  mode=mbqp qpmode=delta initmbqp=1 skipquery=1
    run hevc-$lp-mbqp-value-i hevc $lp -  -  mode=mbqp qpmode=value initmbqp=1 skipquery=1
    run hevc-$lp-rect         hevc $lp -  -  mode=rect
    run avc-$lp-stats         avc  $lp -  -  mode=none stats=1
    run hevc-$lp-stats        hevc $lp -  -  mode=none stats=1
    run avc-$lp-units-finfo   avc  $lp -  -  mode=none units=1 finfo=1
done
echo "md5 (identical = the map/ROI had no effect):"
( cd "$OUT" && md5sum *.h264 *.h265 2>/dev/null | sort | awk '{print "   " $0}' )
