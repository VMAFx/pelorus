#!/usr/bin/env bash
# shw-2 step 7: format matrix on each GPU in the stock-FFmpeg device mode whose
# plain hwupload->hwdownload round trip is exact (step 6):
#   B580 (vk0): disable_multiplane=1            (optimal tiling)
#   UHD 770 (vk1): linear_images=1,disable_multiplane=1
# SCRIPT=unchanged -> repo script as-is; SCRIPT=winallow -> + 2 stock VUIDs.
set -uo pipefail
source /c/tmp/pel/shw-scratch/shw2/env.sh
VVL="${VVL:-msys}"; SCRIPT="${SCRIPT:-winallow}"
export "$(pel_vvl_env "$VVL")"
case "$SCRIPT" in
    unchanged) MATRIX="$REPO/ffmpeg-patches/test/vulkan-format-matrix.sh" ;;
    winallow) MATRIX="$SCR/vulkan-format-matrix-winallow.sh" ;;
esac
for spec in ${SPECS:-"0,disable_multiplane=1" "1,linear_images=1,disable_multiplane=1"}; do
    out="$RES/07-matrix-$SCRIPT-$VVL/vk${spec//[,=]/_}"
    rm -rf "$out"; mkdir -p "$out"
    echo "== matrix($SCRIPT) VULKAN_DEVICE=$spec vvl=$VVL ($(date -Is))"
    FFMPEG_BIN="$FFM" VULKAN_DEVICE="$spec" OUTPUT_ROOT="$out" PELORUS_VALIDATE=1 \
        bash "$MATRIX" >"$out/run.log" 2>&1
    echo "matrix rc=$?"; grep -vE '^\s*$' "$out/run.log" | grep -vE '^PASS: (analyze|transform|uv-|rgba-|planes-|denoise-)' | tail -12
    echo "-- every diagnostic id across all .stdout/.stderr (count):"
    grep -hEo '(VUID|SYNC|UNASSIGNED|BestPractices)-[[:alnum:]_.-]+' "$out"/*.stdout "$out"/*.stderr | sort | uniq -c
    echo "-- validation-known-upstream.txt:"; cat "$out/validation-known-upstream.txt" 2>/dev/null
done
