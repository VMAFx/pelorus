#!/usr/bin/env bash
#
# grain-score-smoke.sh — verify grain scoring recipe rejects grain-free
# reconstruction and scores decoded synthesised film grain (issue #82, ADR-0207).
#
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
#
# CPU test (needs HEVC decoder + psnr filter). Cases:
#   c1 stream scores (psnr) exit 0; value differs from psnr of the grain-free decode
#   c2 --distorted = grain-applied decode file -> exit 0
#   c3 PLANTED recon: --distorted = grain-free decode file -> exit 1 AND stderr contains "grain-free reconstruction"
#   c4 stream with FGC stripped -> exit 1 "no film grain"
#   c5 --metric vmaf on binary without libvmaf -> exit 1 naming libvmaf (or skip if binary has libvmaf)
#
# Env:
#   FFMPEG_BIN     patched ffmpeg binary (required)
#   OUTPUT_ROOT    evidence directory (default: new directory under TMPDIR)
# Exit: 0 pass, 1 fail, 77 skip (the reason is printed on stdout).

set -euo pipefail

PEL_FFMPEG="${FFMPEG_BIN:?FFMPEG_BIN must name the patched ffmpeg binary}"
PEL_OUT="${OUTPUT_ROOT:-$(mktemp -d "${TMPDIR:-/tmp}/pelorus-grain-score.XXXXXX")}"
mkdir -p "$PEL_OUT"

if [[ -z "${OUTPUT_ROOT:-}" ]]; then
    trap 'rm -rf -- "$PEL_OUT"' EXIT
fi

pel_skip()
{
    echo "SKIP: $*"
    exit 77
}

pel_fail()
{
    echo "FAIL: $*" >&2
    echo "Evidence: $PEL_OUT" >&2
    exit 1
}

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
FIXTURE="$HERE/fixtures/grain-h274-fgc.hevc"
GRAIN_SCORE_SCRIPT="$ROOT/scripts/grain-score.sh"

[[ -x "$PEL_FFMPEG" ]] || pel_fail "FFMPEG_BIN is not executable: $PEL_FFMPEG"
[[ -r "$FIXTURE" ]] || pel_fail "fixture not found or readable: $FIXTURE"
[[ -x "$GRAIN_SCORE_SCRIPT" ]] || pel_fail "grain-score.sh script not found or executable: $GRAIN_SCORE_SCRIPT"

"$PEL_FFMPEG" -nostdin -hide_banner -decoders >"$PEL_OUT/decoders.txt" 2>&1 || pel_fail "ffmpeg -decoders failed"
"$PEL_FFMPEG" -nostdin -hide_banner -filters >"$PEL_OUT/filters.txt" 2>&1 || pel_fail "ffmpeg -filters failed"

if ! awk '{print $2}' "$PEL_OUT/decoders.txt" | grep -qx "hevc"; then
    pel_skip "no HEVC decoder in $PEL_FFMPEG"
fi

if ! awk '{print $2}' "$PEL_OUT/filters.txt" | grep -qx "psnr"; then
    pel_skip "no psnr filter in $PEL_FFMPEG"
fi

REF="lavfi:testsrc2=size=192x128:rate=25,format=yuv420p"
any_fail=0

# c1: stream scores (psnr) exit 0; value differs from psnr of the grain-free decode
c1_out="$PEL_OUT/c1"
c1_status=0
"$GRAIN_SCORE_SCRIPT" \
    --ffmpeg "$PEL_FFMPEG" \
    --reference "$REF" \
    --stream "$FIXTURE" \
    --metric psnr \
    --out "$c1_out" >"$PEL_OUT/c1.stdout" 2>"$PEL_OUT/c1.stderr" || c1_status=$?

if (( c1_status != 0 )); then
    echo "FAIL: c1 stream scoring failed (exit $c1_status)" >&2
    any_fail=1
else
    c1_score="$(awk -F': *' '/^value:/ { print $2 }' "$c1_out/score.txt")"

    "$PEL_FFMPEG" -nostdin -hide_banner \
        -export_side_data film_grain \
        -i "$FIXTURE" \
        -y "$PEL_OUT/c1-recon.y4m" >/dev/null 2>"$PEL_OUT/c1-recon-decode.log"

    "$PEL_FFMPEG" -nostdin -hide_banner \
        -i "$PEL_OUT/c1-recon.y4m" \
        -f lavfi -i "testsrc2=size=192x128:rate=25,format=yuv420p" \
        -lavfi "[0:v][1:v]psnr=shortest=1" \
        -f null - >/dev/null 2>"$PEL_OUT/c1-recon-score.log"

    recon_score="$(awk -F'average:' '/average:/ { split($2, a, " "); print a[1] }' "$PEL_OUT/c1-recon-score.log" | tail -n 1)"

    if [[ -n "$c1_score" && -n "$recon_score" && "$c1_score" != "$recon_score" ]]; then
        echo "PASS: c1 stream scores (psnr) exit 0 (applied=$c1_score != recon=$recon_score)"
    else
        echo "FAIL: c1 score ($c1_score) did not differ from grain-free recon ($recon_score)" >&2
        any_fail=1
    fi
fi

# c2: --distorted = grain-applied decode file -> exit 0
c2_out="$PEL_OUT/c2"
c2_status=0
"$PEL_FFMPEG" -nostdin -hide_banner \
    -i "$FIXTURE" \
    -y "$PEL_OUT/c2-applied.y4m" >/dev/null 2>"$PEL_OUT/c2-decode.log"

"$GRAIN_SCORE_SCRIPT" \
    --ffmpeg "$PEL_FFMPEG" \
    --reference "$REF" \
    --stream "$FIXTURE" \
    --distorted "$PEL_OUT/c2-applied.y4m" \
    --metric psnr \
    --out "$c2_out" >"$PEL_OUT/c2.stdout" 2>"$PEL_OUT/c2.stderr" || c2_status=$?

if (( c2_status == 0 )); then
    echo "PASS: c2 --distorted = grain-applied decode file exit 0"
else
    echo "FAIL: c2 --distorted = grain-applied decode file failed (exit $c2_status)" >&2
    any_fail=1
fi

# c3: PLANTED recon: --distorted = grain-free decode file -> exit 1 AND stderr contains "grain-free reconstruction"
c3_out="$PEL_OUT/c3"
c3_status=0
"$PEL_FFMPEG" -nostdin -hide_banner \
    -export_side_data film_grain \
    -i "$FIXTURE" \
    -y "$PEL_OUT/c3-recon.y4m" >/dev/null 2>"$PEL_OUT/c3-decode.log"

"$GRAIN_SCORE_SCRIPT" \
    --ffmpeg "$PEL_FFMPEG" \
    --reference "$REF" \
    --stream "$FIXTURE" \
    --distorted "$PEL_OUT/c3-recon.y4m" \
    --metric psnr \
    --out "$c3_out" >"$PEL_OUT/c3.stdout" 2>"$PEL_OUT/c3.stderr" || c3_status=$?

if (( c3_status == 1 )) && grep -q "grain-free reconstruction" "$PEL_OUT/c3.stderr"; then
    echo "PASS: c3 PLANTED recon: --distorted = grain-free decode file exit 1 and stderr contains 'grain-free reconstruction'"
else
    echo "FAIL: c3 PLANTED recon: expected exit 1 with 'grain-free reconstruction', got exit $c3_status" >&2
    any_fail=1
fi

# c4: stream with FGC stripped -> exit 1 "no film grain"
c4_out="$PEL_OUT/c4"
c4_status=0
"$PEL_FFMPEG" -nostdin -hide_banner \
    -i "$FIXTURE" \
    -c copy \
    -bsf:v "filter_units=remove_types=39" \
    -y "$PEL_OUT/c4-stripped.hevc" >/dev/null 2>"$PEL_OUT/c4-strip.log"

"$GRAIN_SCORE_SCRIPT" \
    --ffmpeg "$PEL_FFMPEG" \
    --reference "$REF" \
    --stream "$PEL_OUT/c4-stripped.hevc" \
    --metric psnr \
    --out "$c4_out" >"$PEL_OUT/c4.stdout" 2>"$PEL_OUT/c4.stderr" || c4_status=$?

if (( c4_status == 1 )) && grep -qi "no film grain" "$PEL_OUT/c4.stderr"; then
    echo "PASS: c4 stream with FGC stripped exit 1 naming no film grain"
else
    echo "FAIL: c4 stream with FGC stripped: expected exit 1 with 'no film grain', got exit $c4_status" >&2
    any_fail=1
fi

# c5: --metric vmaf on binary without libvmaf -> exit 1 naming libvmaf (only when -filters lacks libvmaf; else skip case by name)
if awk '{print $2}' "$PEL_OUT/filters.txt" | grep -qx "libvmaf"; then
    echo "SKIP: c5 --metric vmaf on binary without libvmaf (binary has libvmaf)"
else
    c5_out="$PEL_OUT/c5"
    c5_status=0
    "$GRAIN_SCORE_SCRIPT" \
        --ffmpeg "$PEL_FFMPEG" \
        --reference "$REF" \
        --stream "$FIXTURE" \
        --metric vmaf \
        --out "$c5_out" >"$PEL_OUT/c5.stdout" 2>"$PEL_OUT/c5.stderr" || c5_status=$?

    if (( c5_status == 1 )) && grep -qi "libvmaf" "$PEL_OUT/c5.stderr"; then
        echo "PASS: c5 --metric vmaf on binary without libvmaf exits 1 naming libvmaf"
    else
        echo "FAIL: c5 --metric vmaf on binary without libvmaf: expected exit 1 with 'libvmaf', got exit $c5_status" >&2
        any_fail=1
    fi
fi

if (( any_fail != 0 )); then
    echo "FAIL: at least one case failed" >&2
    echo "Evidence: $PEL_OUT" >&2
    exit 1
fi

echo "OK: all grain score smoke cases passed"
exit 0
