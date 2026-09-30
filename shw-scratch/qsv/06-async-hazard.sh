#!/usr/bin/env bash
# shw-3 step 06: does a per-frame ROI/MBQP buffer land on its own frame under
# async_depth > 1 and B-frames, and what lifetime does the runtime need?
# Raw oneVPL probe, 1080p, 300 frames, CQP 30, +12 on the top half for even
# frames / bottom half for odd frames.
#   reuse=lock : a slot's buffers are repainted once surface.Data.Locked == 0
#                (the documented oneVPL rule = FFmpeg clear_unused_frames())
#   reuse=sync : ... and only after the slot's previous frame was synced
#   shared     : ONE buffer repainted before every submission (pre-ADR-0146)
#   AVC : exact per-MB QP from the decoded stream (qpdump.exe).  MBQP runs use
#         value mode (the runtime applies DeltaQP as absolute QP, step 05) ->
#         absolute check 42/30; rectangles -> relative check (+12 over the
#         modal QP of the other half, absorbs B-pyramid offsets).
#   HEVC: MBQP is refused by the runtime, so rectangles only; half PSNR.
# Env: IMPL=0|1, LP=on|off, N=300, ASYNCS="1 4", RDS="1 4"
set -uo pipefail
export PATH="/c/tmp/pel/prefix/bin:/ucrt64/bin:$PATH"
SCR=/c/tmp/pel/shw-scratch/qsv
IMPL="${IMPL:-0}"; LP="${LP:-on}"; N="${N:-300}"; ASYNCS="${ASYNCS:-1 4}"; RDS="${RDS:-1 4}"
HEVC="${HEVC:-1}"
OUT=/c/tmp/pel/shw-results/qsv/06-async-hazard/impl$IMPL-lp$LP
rm -rf "$OUT"; mkdir -p "$OUT"
IN=$SCR/content/own1080.nv12
P=$SCR/qsv-encode-probe.exe
say() { printf '%s\n' "$*" | tee -a "$OUT/summary.txt"; }
enc() { # tag codec args...
    local tag=$1 codec=$2; shift 2
    local ext=h264; [ "$codec" = hevc ] && ext=h265
    "$P" impl=$IMPL codec=$codec lp=$LP qp=30 w=1920 h=1080 n=$N in="$IN" out="$OUT/$tag.$ext" \
        csv="$OUT/$tag.csv" delta=12 pattern=alt "$@" >"$OUT/$tag.log" 2>&1
    echo $?
}
early() { grep -oE 'early slot reuses \(previous frame not yet synced\) [0-9]+' "$OUT/$1.log" | grep -oE '[0-9]+$'; }
halfpsnr() { # es tag -> <tag>.top.log / <tag>.bot.log (relative names: native ffmpeg + cwd)
    local es=$1 tag=$2
    ( cd "$OUT" && ffmpeg -hide_banner -nostdin -loglevel error -framerate 30 -f hevc -i "$(basename "$es")" \
        -f rawvideo -pix_fmt nv12 -s 1920x1080 -framerate 30 -i "$(cygpath -m "$IN")" -frames:v $N \
        -lavfi "[0:v]format=nv12,split[a0][a1];[1:v]split[b0][b1];[a0]crop=1920:480:0:0[at];[b0]crop=1920:480:0:0[bt];[at][bt]psnr=stats_file=$tag.top.log[o0];[a1]crop=1920:504:0:576[ab];[b1]crop=1920:504:0:576[bb];[ab][bb]psnr=stats_file=$tag.bot.log[o1]" \
        -map '[o0]' -f null - -map '[o1]' -f null - 2>"$tag.psnr.err" )
}
say "######## impl $IMPL LowPower=$LP, $N frames 1080p, CQP 30, delta +12 alternating; async {$ASYNCS}; GopRefDist {$RDS}"
for rd in $RDS; do
    say "== AVC GopRefDist=$rd (exact per-MB QP; 'mismatched' = frame with > 2 MB rows off the expected pattern)"
    for spec in mbqp:lock mbqp:sync shared:lock rect:lock rect:sync sharedrect:lock; do
        mode=${spec%%:*}; reuse=${spec##*:}
        for a in $ASYNCS; do
            tag=avc-$mode-$reuse-a$a-rd$rd
            if [ "$mode" = mbqp ] || [ "$mode" = shared ]; then extra="qpmode=value"; chk="alt 42 30";
            else extra=""; chk="altrel 12 0"; fi
            rc=$(enc $tag avc mode=$mode reuse=$reuse async=$a refdist=$rd $extra)
            if [ "$rc" = 0 ]; then
                # shellcheck disable=SC2086
                "$SCR/qpdump.exe" "$OUT/$tag.h264" -1 $chk >"$OUT/$tag.qp.csv" 2>"$OUT/$tag.qp.err"
                mism=$(awk -F, 'NR>1 && $11>240' "$OUT/$tag.qp.csv" | wc -l)
                say "$(printf '%-26s mismatched=%3s/%s early_reuse=%-4s | %s' $tag "$mism" "$N" "$(early $tag)" \
                    "$(python3 "$SCR/correlate.py" "$OUT/$tag.qp.csv" "$OUT/$tag.csv.reuse.csv")")"
            else
                say "$(printf '%-26s rc=%s %s' $tag "$rc" "$(grep -E 'FATAL' "$OUT/$tag.log")")"
            fi
        done
    done
    [ "$HEVC" = 1 ] || continue
    say "== HEVC GopRefDist=$rd (half-PSNR drop vs the no-ROI encode; the ROI half must drop more)"
    enc hevc-none-rd$rd hevc mode=none refdist=$rd async=4 >/dev/null
    halfpsnr "$OUT/hevc-none-rd$rd.h265" hevc-none-rd$rd
    for spec in rect:lock rect:sync sharedrect:lock; do
        mode=${spec%%:*}; reuse=${spec##*:}
        for a in $ASYNCS; do
            tag=hevc-$mode-$reuse-a$a-rd$rd
            rc=$(enc $tag hevc mode=$mode reuse=$reuse async=$a refdist=$rd)
            if [ "$rc" = 0 ]; then
                halfpsnr "$OUT/$tag.h265" $tag
                python3 "$SCR/halfpsnr.py" "$OUT/hevc-none-rd$rd.top.log" "$OUT/hevc-none-rd$rd.bot.log" \
                    "$OUT/$tag.top.log" "$OUT/$tag.bot.log" alt >"$OUT/$tag.half.csv" 2>&1
                say "$(printf '%-26s bytes=%-8s early_reuse=%-4s %s' $tag "$(stat -c %s "$OUT/$tag.h265")" "$(early $tag)" "$(head -1 "$OUT/$tag.half.csv")")"
            else
                say "$(printf '%-26s rc=%s %s' $tag "$rc" "$(grep -E 'FATAL' "$OUT/$tag.log")")"
            fi
        done
    done
done
