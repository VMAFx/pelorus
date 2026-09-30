#!/usr/bin/env bash
# shw-2 step 6: which stock FFmpeg Vulkan device modes give an exact
# hwupload->hwdownload round trip (no Pelorus filter) on each Intel GPU?
set -uo pipefail
source /c/tmp/pel/shw-scratch/shw2/env.sh
OUT=$RES/06-datapath-modes; mkdir -p "$OUT"
SRC='testsrc2=size=96x64:rate=1:duration=1'
for dev in 0 1; do
  for mode in "" ",disable_multiplane=1" ",linear_images=1" ",linear_images=1,disable_multiplane=1"; do
    for fmt in yuv420p nv12 p010le yuv420p10le rgba; do
      tag="vk$dev${mode//[,=]/_}-$fmt"
      "$FFM" -hide_banner -nostdin -loglevel error -y -f lavfi -i "$SRC" -frames:v 1 -vf "format=$fmt" -f rawvideo "$OUT/cpu-$fmt.raw"
      if "$FFM" -hide_banner -nostdin -loglevel error -y -init_hw_device "vulkan=vk:$dev$mode" -filter_hw_device vk \
          -f lavfi -i "$SRC" -frames:v 1 -vf "format=$fmt,hwupload,hwdownload,format=$fmt" -f rawvideo "$OUT/$tag.raw" 2>"$OUT/$tag.err"; then
        r=$(cmp -s "$OUT/$tag.raw" "$OUT/cpu-$fmt.raw" && echo EXACT || echo CORRUPT)
      else
        r="ERROR($(grep -m1 -oE 'VK_ERROR_[A-Z_]+|Cannot allocate memory|Invalid argument' "$OUT/$tag.err"))"
      fi
      printf '%-4s %-40s %-12s %s\n' "vk$dev" "mode='${mode#,}'" "$fmt" "$r"
    done
  done
done
