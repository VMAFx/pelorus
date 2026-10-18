#!/usr/bin/env bash
#
# grain-score-av1-smoke.sh — verify grain scoring recipe rejects grain-free
# reconstruction and scores AV1 synthesised film grain via libdav1d (issue #368, ADR-0212).
#
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
#
# CPU test (needs libdav1d decoder + psnr filter). Cases:
#   a1 stream scores (psnr) exit 0, score.txt has decoder: libdav1d, value != psnr of grain-free decode
#   a2 --distorted = grain-applied decode file -> exit 0
#   a3 PLANTED recon: --distorted = grain-free decode file -> exit 1 AND stderr contains "grain-free reconstruction"
#   a4 --stream grain-av1-nograin.ivf -> exit 1 "no film grain"
#   a5 libdav1d refusal: ffmpeg without libdav1d decoder -> exit 1 naming libdav1d
#
# Env:
#   FFMPEG_BIN     patched ffmpeg binary (required)
#   OUTPUT_ROOT    evidence directory (default: new directory under TMPDIR)
# Exit: 0 pass, 1 fail, 77 skip (the reason is printed on stdout).

set -euo pipefail

PEL_FFMPEG="${FFMPEG_BIN:?FFMPEG_BIN must name the patched ffmpeg binary}"
PEL_OUT="${OUTPUT_ROOT:-$(mktemp -d "${TMPDIR:-/tmp}/pelorus-grain-score-av1.XXXXXX")}"
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
FIXTURE="$HERE/fixtures/grain-av1-fgs.ivf"
NOGRAIN_FIXTURE="$HERE/fixtures/grain-av1-nograin.ivf"
GRAIN_SCORE_SCRIPT="${GRAIN_SCORE_SCRIPT_OVERRIDE:-$ROOT/scripts/grain-score.sh}"

[[ -x "$PEL_FFMPEG" ]] || pel_fail "FFMPEG_BIN is not executable: $PEL_FFMPEG"
[[ -r "$FIXTURE" ]] || pel_fail "fixture not found or readable: $FIXTURE"
[[ -r "$NOGRAIN_FIXTURE" ]] || pel_fail "fixture not found or readable: $NOGRAIN_FIXTURE"
[[ -x "$GRAIN_SCORE_SCRIPT" ]] || pel_fail "grain-score.sh script not found or executable: $GRAIN_SCORE_SCRIPT"

"$PEL_FFMPEG" -nostdin -hide_banner -decoders >"$PEL_OUT/decoders.txt" 2>&1 || pel_fail "ffmpeg -decoders failed"
"$PEL_FFMPEG" -nostdin -hide_banner -filters >"$PEL_OUT/filters.txt" 2>&1 || pel_fail "ffmpeg -filters failed"

if ! awk '{print $2}' "$PEL_OUT/decoders.txt" | grep -qx "libdav1d"; then
    pel_skip "no libdav1d decoder in $PEL_FFMPEG"
fi

if ! awk '{print $2}' "$PEL_OUT/filters.txt" | grep -qx "psnr"; then
    pel_skip "no psnr filter in $PEL_FFMPEG"
fi

REF="lavfi:testsrc2=size=192x128:rate=25,format=yuv420p"
any_fail=0

# a1: stream scores (psnr) exit 0, score.txt has decoder: libdav1d, value != psnr of grain-free decode
a1_out="$PEL_OUT/a1"
a1_status=0
"$GRAIN_SCORE_SCRIPT" \
    --ffmpeg "$PEL_FFMPEG" \
    --reference "$REF" \
    --stream "$FIXTURE" \
    --metric psnr \
    --out "$a1_out" >"$PEL_OUT/a1.stdout" 2>"$PEL_OUT/a1.stderr" || a1_status=$?

if (( a1_status != 0 )); then
    echo "FAIL: a1 stream scoring failed (exit $a1_status)" >&2
    any_fail=1
else
    a1_score="$(awk -F': *' '/^value:/ { print $2 }' "$a1_out/score.txt")"
    a1_decoder="$(awk -F': *' '/^decoder:/ { print $2 }' "$a1_out/score.txt")"

    if [[ "$a1_decoder" != "libdav1d" ]]; then
        echo "FAIL: a1 score.txt decoder is '$a1_decoder', expected 'libdav1d'" >&2
        any_fail=1
    fi

    "$PEL_FFMPEG" -nostdin -hide_banner \
        -export_side_data film_grain \
        -c:v libdav1d \
        -i "$FIXTURE" \
        -y "$PEL_OUT/a1-recon.y4m" >/dev/null 2>"$PEL_OUT/a1-recon-decode.log"

    "$PEL_FFMPEG" -nostdin -hide_banner \
        -i "$PEL_OUT/a1-recon.y4m" \
        -f lavfi -i "testsrc2=size=192x128:rate=25,format=yuv420p" \
        -lavfi "[0:v][1:v]psnr=shortest=1" \
        -f null - >/dev/null 2>"$PEL_OUT/a1-recon-score.log"

    recon_score="$(awk -F'average:' '/average:/ { split($2, a, " "); print a[1] }' "$PEL_OUT/a1-recon-score.log" | tail -n 1)"

    if [[ -n "$a1_score" && -n "$recon_score" && "$a1_score" != "$recon_score" && "$a1_decoder" == "libdav1d" ]]; then
        echo "PASS: a1 stream scores (psnr) exit 0 (applied=$a1_score != recon=$recon_score, decoder=$a1_decoder)"
    else
        echo "FAIL: a1 score ($a1_score) did not differ from grain-free recon ($recon_score)" >&2
        any_fail=1
    fi
fi

# a2: --distorted = grain-applied libdav1d decode y4m -> exit 0
a2_out="$PEL_OUT/a2"
a2_status=0
"$PEL_FFMPEG" -nostdin -hide_banner \
    -c:v libdav1d \
    -i "$FIXTURE" \
    -y "$PEL_OUT/a2-applied.y4m" >/dev/null 2>"$PEL_OUT/a2-decode.log"

"$GRAIN_SCORE_SCRIPT" \
    --ffmpeg "$PEL_FFMPEG" \
    --reference "$REF" \
    --stream "$FIXTURE" \
    --distorted "$PEL_OUT/a2-applied.y4m" \
    --metric psnr \
    --out "$a2_out" >"$PEL_OUT/a2.stdout" 2>"$PEL_OUT/a2.stderr" || a2_status=$?

if (( a2_status == 0 )); then
    echo "PASS: a2 --distorted = grain-applied decode file exit 0"
else
    echo "FAIL: a2 --distorted = grain-applied decode file failed (exit $a2_status)" >&2
    any_fail=1
fi

# a3: PLANTED recon: --distorted = grain-free libdav1d decode y4m -> exit 1 AND stderr "grain-free reconstruction"
a3_out="$PEL_OUT/a3"
a3_status=0
"$PEL_FFMPEG" -nostdin -hide_banner \
    -export_side_data film_grain \
    -c:v libdav1d \
    -i "$FIXTURE" \
    -y "$PEL_OUT/a3-recon.y4m" >/dev/null 2>"$PEL_OUT/a3-decode.log"

"$GRAIN_SCORE_SCRIPT" \
    --ffmpeg "$PEL_FFMPEG" \
    --reference "$REF" \
    --stream "$FIXTURE" \
    --distorted "$PEL_OUT/a3-recon.y4m" \
    --metric psnr \
    --out "$a3_out" >"$PEL_OUT/a3.stdout" 2>"$PEL_OUT/a3.stderr" || a3_status=$?

if (( a3_status == 1 )) && grep -q "grain-free reconstruction" "$PEL_OUT/a3.stderr"; then
    echo "PASS: a3 PLANTED recon: --distorted = grain-free decode file exit 1 and stderr contains 'grain-free reconstruction'"
else
    echo "FAIL: a3 PLANTED recon: expected exit 1 with 'grain-free reconstruction', got exit $a3_status" >&2
    any_fail=1
fi

# a4: --stream grain-av1-nograin.ivf -> exit 1 "no film grain"
a4_out="$PEL_OUT/a4"
a4_status=0
"$GRAIN_SCORE_SCRIPT" \
    --ffmpeg "$PEL_FFMPEG" \
    --reference "$REF" \
    --stream "$NOGRAIN_FIXTURE" \
    --metric psnr \
    --out "$a4_out" >"$PEL_OUT/a4.stdout" 2>"$PEL_OUT/a4.stderr" || a4_status=$?

if (( a4_status == 1 )) && grep -qi "no film grain" "$PEL_OUT/a4.stderr"; then
    echo "PASS: a4 stream without grain exit 1 naming no film grain"
else
    echo "FAIL: a4 stream without grain: expected exit 1 with 'no film grain', got exit $a4_status" >&2
    any_fail=1
fi

# a5: libdav1d refusal: run grain-score.sh with a stub ffmpeg wrapper script in $PEL_OUT
stub_ffmpeg="$PEL_OUT/ffmpeg-stub.sh"
cat >"$stub_ffmpeg" <<EOF
#!/usr/bin/env bash
for arg in "\$@"; do
    if [[ "\$arg" == "-decoders" ]]; then
        "$PEL_FFMPEG" "\$@" | grep -v "libdav1d"
        exit "\${PIPESTATUS[0]}"
    fi
done
exec "$PEL_FFMPEG" "\$@"
EOF
chmod +x "$stub_ffmpeg"

a5_out="$PEL_OUT/a5"
a5_status=0
"$GRAIN_SCORE_SCRIPT" \
    --ffmpeg "$stub_ffmpeg" \
    --reference "$REF" \
    --stream "$FIXTURE" \
    --metric psnr \
    --out "$a5_out" >"$PEL_OUT/a5.stdout" 2>"$PEL_OUT/a5.stderr" || a5_status=$?

if (( a5_status == 1 )) && grep -qi "libdav1d" "$PEL_OUT/a5.stderr"; then
    echo "PASS: a5 libdav1d refusal: exit 1 naming libdav1d"
else
    echo "FAIL: a5 libdav1d refusal: expected exit 1 with 'libdav1d', got exit $a5_status" >&2
    any_fail=1
fi

if (( any_fail != 0 )); then
    echo "FAIL: at least one case failed" >&2
    echo "Evidence: $PEL_OUT" >&2
    exit 1
fi

echo "OK: all grain score AV1 smoke cases passed"
exit 0
