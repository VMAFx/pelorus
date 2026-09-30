#!/usr/bin/env bash
# shw-2 step 9: stock-FFmpeg control under core + synchronization validation
# (VK_LAYER_VALIDATE_SYNC=1), so later Pelorus rows can be attributed.
set -uo pipefail
source /c/tmp/pel/shw-scratch/shw2/env.sh
export "$(pel_vvl_env msys)" VK_INSTANCE_LAYERS=VK_LAYER_KHRONOS_validation VK_LAYER_VALIDATE_SYNC=1
OUT=$RES/09-sync-control; mkdir -p "$OUT"
for spec in "0,disable_multiplane=1" "1,linear_images=1,disable_multiplane=1" "0" "1"; do
  for chain in "hwupload,hwdownload" "hwupload,hflip_vulkan,hwdownload" "hwupload,gblur_vulkan,hwdownload" "hwupload,avgblur_vulkan,hwdownload"; do
    tag="vk${spec//[,=]/_}-${chain//,/+}"
    "$FFM" -hide_banner -nostdin -loglevel warning -y -init_hw_device "vulkan=vk:$spec" -filter_hw_device vk \
        -f lavfi -i testsrc2=size=96x64:rate=4:duration=1 -vf "format=yuv420p,$chain,format=yuv420p" -f null - \
        >"$OUT/$tag.stdout" 2>"$OUT/$tag.stderr"
    echo "rc=$? $tag: $(grep -hEo '(VUID|SYNC|UNASSIGNED|BestPractices)-[[:alnum:]_.-]+' "$OUT/$tag.stdout" "$OUT/$tag.stderr" | sort | uniq -c | awk '{printf "%s x%s; ", $2, $1}')"
  done
done
