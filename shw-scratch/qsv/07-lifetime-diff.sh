#!/usr/bin/env bash
# shw-3 step 07: is a per-frame MBQP map still read after surface.Data.Locked
# drops to zero?  Same input, same parameters, two reuse policies, each run
# twice (run-to-run determinism control).  If reuse=sync is deterministic and
# reuse=lock differs from it, the difference is caused by repainting a map the
# runtime had not finished reading -- i.e. the unlock-based lifetime (the
# oneVPL documented rule, and FFmpeg's clear_unused_frames()) is too short.
# Per-frame comparison of the decoded per-MB QP (top/bottom means) localises
# the affected frames and relates them to the early-reuse log.
# Env: IMPL, LP, N (300)
set -uo pipefail
export PATH="/c/tmp/pel/prefix/bin:/ucrt64/bin:$PATH"
SCR=/c/tmp/pel/shw-scratch/qsv
IMPL="${IMPL:-0}"; LP="${LP:-on}"; N="${N:-300}"
OUT=/c/tmp/pel/shw-results/qsv/07-lifetime-diff/impl$IMPL-lp$LP
rm -rf "$OUT"; mkdir -p "$OUT"
IN=$SCR/content/own1080.nv12
P=$SCR/qsv-encode-probe.exe
say() { printf '%s\n' "$*" | tee -a "$OUT/summary.txt"; }
enc() { # tag args...
    local tag=$1; shift
    "$P" impl=$IMPL codec=avc lp=$LP qp=30 w=1920 h=1080 n=$N in="$IN" out="$OUT/$tag.h264" \
        csv="$OUT/$tag.csv" delta=12 pattern=alt "$@" >"$OUT/$tag.log" 2>&1 || say "FAIL $tag"
    "$SCR/qpdump.exe" "$OUT/$tag.h264" >"$OUT/$tag.qp.csv" 2>/dev/null
}
framediff() { # a b -> frames whose per-half QP means differ
    paste -d, <(cut -d, -f1,4,7 "$OUT/$1.qp.csv") <(cut -d, -f4,7 "$OUT/$2.qp.csv") |
        awk -F, 'NR>1 && ($2!=$4 || $3!=$5) {printf "%s ", $1; n++} END {printf "(%d frames)", n}'
}
say "######## impl $IMPL LowPower=$LP, AVC value-mode MBQP, $N frames 1080p"
for cfg in "mbqp 1 1" "mbqp 4 1" "mbqp 8 1" "mbqp 1 4" "mbqp 4 4" "mbqp 8 4" "rect 4 4" "rect 8 4"; do
    set -- $cfg; mode=$1; a=$2; rd=$3
    extra=""; [ "$mode" = mbqp ] && extra="qpmode=value"
    for reuse in lock sync; do
        for rep in 1 2; do
            enc $mode-$reuse-a$a-rd$rd-r$rep mode=$mode reuse=$reuse async=$a refdist=$rd $extra
        done
    done
    b=$mode-%s-a$a-rd$rd-r%s
    # shellcheck disable=SC2059
    L1=$(printf "$b" lock 1); L2=$(printf "$b" lock 2); S1=$(printf "$b" sync 1); S2=$(printf "$b" sync 2)
    det() { cmp -s "$OUT/$1.h264" "$OUT/$2.h264" && echo same || echo differ; }
    er() { grep -oE 'not yet synced\) [0-9]+' "$OUT/$1.log" | grep -oE '[0-9]+$'; }
    say "== $mode async=$a GopRefDist=$rd: sync r1-vs-r2 $(det "$S1" "$S2"); lock r1-vs-r2 $(det "$L1" "$L2"); lock-vs-sync(r1) $(det "$L1" "$S1"); early reuses lock r1/r2 = $(er "$L1")/$(er "$L2")"
    say "   frames whose decoded QP differs lock-r1 vs sync-r1: $(framediff "$L1" "$S1")"
    say "   frames whose decoded QP differs lock-r2 vs sync-r1: $(framediff "$L2" "$S1")"
    say "   frames whose decoded QP differs sync-r2 vs sync-r1: $(framediff "$S2" "$S1")"
    say "   early-reused (at-risk) frames lock r1: $(tail -n +2 "$OUT/$L1.csv.reuse.csv" | cut -d, -f3 | sort -n | tr '\n' ' ' | cut -c1-400)"
done
