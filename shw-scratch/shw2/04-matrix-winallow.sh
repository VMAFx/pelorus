#!/usr/bin/env bash
# shw-2 step 4: the format matrix with ONLY the allowlist extended by the two
# stock-FFmpeg diagnostics proven in steps 1-3 (device probe = bare
# hwupload/hwdownload, no Pelorus filter).  Everything else is the repo script.
set -uo pipefail
source /c/tmp/pel/shw-scratch/shw2/env.sh
VVL="${VVL:-msys}"
export "$(pel_vvl_env "$VVL")"
for dev in ${DEVS:-0 1}; do
    out="$RES/04-matrix-winallow-$VVL/vk$dev"
    rm -rf "$out"; mkdir -p "$out"
    echo "== matrix(winallow) vk$dev vvl=$VVL ($(date -Is))"
    FFMPEG_BIN="$FFM" VULKAN_DEVICE=$dev OUTPUT_ROOT="$out" PELORUS_VALIDATE=1 \
        bash "$SCR/vulkan-format-matrix-winallow.sh" >"$out/run.log" 2>&1
    echo "matrix vk$dev rc=$?"; grep -E '^(PASS|FAIL|SKIP|Unexpected)' "$out/run.log"
    echo "-- every diagnostic id per stream (count):"
    grep -hEo '(VUID|SYNC|UNASSIGNED|BestPractices)-[[:alnum:]_.-]+' "$out"/*.stdout "$out"/*.stderr | sort | uniq -c
done
