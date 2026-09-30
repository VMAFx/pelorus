#!/usr/bin/env bash
# shw-2 step 2: the repo's validation self-test, then the UNCHANGED
# ffmpeg-patches/test/vulkan-format-matrix.sh on each Intel GPU with
# PELORUS_VALIDATE=1 (hard requirement, not auto) and the chosen VVL.
set -uo pipefail
source /c/tmp/pel/shw-scratch/shw2/env.sh
VVL="${VVL:-msys}"
export "$(pel_vvl_env "$VVL")"
echo "== validation self-test ($(date -Is))"
bash "$REPO/ffmpeg-patches/test/vulkan-format-matrix-validation-self-test.sh"; echo "self-test rc=$?"
for dev in 0 1; do
    out="$RES/02-matrix-unchanged-$VVL/vk$dev"
    rm -rf "$out"; mkdir -p "$out"
    echo "== matrix vk$dev vvl=$VVL ($(date -Is))"
    FFMPEG_BIN="$FFM" VULKAN_DEVICE=$dev OUTPUT_ROOT="$out" PELORUS_VALIDATE=1 \
        bash "$REPO/ffmpeg-patches/test/vulkan-format-matrix.sh" >"$out/run.log" 2>&1
    echo "matrix vk$dev rc=$?"; tail -5 "$out/run.log"
done
