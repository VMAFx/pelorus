#!/usr/bin/env bash
# shw-2 step 18: the (fixed) repo format matrix on both Intel GPUs, in the
# device mode whose stock round trip is exact, with PELORUS_VALIDATE=1, for
# both validation-layer builds (MSYS2 1.4.357 and LunarG SDK 1.4.341.1).
set -uo pipefail
source /c/tmp/pel/shw-scratch/shw2/env.sh
TAG="${TAG:-final}"
echo "== validation self-test"; bash "$REPO/ffmpeg-patches/test/vulkan-format-matrix-validation-self-test.sh"; echo "self-test rc=$?"
for vvl in ${VVLS:-msys sdk}; do
  export "$(pel_vvl_env "$vvl")"
  for spec in ${SPECS:-"0,disable_multiplane=1" "1,linear_images=1,disable_multiplane=1"}; do
    out="$RES/18-matrix-$TAG-$vvl/vk${spec//[,=]/_}"
    rm -rf "$out"; mkdir -p "$out"
    FFMPEG_BIN="$FFM" VULKAN_DEVICE="$spec" OUTPUT_ROOT="$out" PELORUS_VALIDATE=1 \
        bash "$REPO/ffmpeg-patches/test/vulkan-format-matrix.sh" >"$out/run.log" 2>&1
    rc=$?
    echo "== vvl=$vvl VULKAN_DEVICE=$spec rc=$rc"
    grep -E '^(PASS: [^a-z]|PASS: (analyzer|transform|NV12|packed|selected|direct|lookahead|pass-through|ADR)|FAIL|SKIP|Unexpected)' "$out/run.log"
    echo "   diagnostics (id x count over all rows): $(grep -hEo '(VUID|SYNC|UNASSIGNED)-[[:alnum:]_.-]+' "$out"/*.stdout "$out"/*.stderr | sort | uniq -c | awk '{printf "%s x%d; ", $2, $1/2}')"
  done
done
