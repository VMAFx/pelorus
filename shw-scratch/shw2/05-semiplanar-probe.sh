#!/usr/bin/env bash
# shw-2 step 5: isolate the P010/P012 analyzer-domain mismatch seen in step 4.
# No validation layer here (pure data-path probing).  DEV selects the GPU.
set -uo pipefail
source /c/tmp/pel/shw-scratch/shw2/env.sh
DEV="${DEV:-0}"
OUT=$RES/05-semiplanar-probe/vk$DEV; rm -rf "$OUT"; mkdir -p "$OUT"
SRC='testsrc2=size=96x64:rate=1:duration=1'
ff() { "$FFM" -hide_banner -nostdin -loglevel error -y "$@"; }
hw() { local devopts="$1"; shift; ff -init_hw_device "vulkan=vk:$DEV$devopts" -filter_hw_device vk "$@"; }
echo "# A) analyze on 8-bit semi-planar nv12 vs yuv420p (and multiplane on/off)"
for devopts in "" ",disable_multiplane=1"; do
  for fmt in yuv420p nv12 yuv420p10le p010le p012le; do
    v=$(hw "$devopts" -f lavfi -i "$SRC" -frames:v 1 \
        -vf "format=$fmt,hwupload,pelorus_analyze_vulkan,hwdownload,format=$fmt,metadata=mode=print:file=-" -f null - 2>&1 |
        grep -oE 'lavfi.pelorus.(variance|edge)=[0-9.]+' | tr '\n' ' ')
    echo "devopts='$devopts' $fmt: $v"
  done
done
echo "# B) GPU hflip_vulkan vs CPU hflip (a symmetric upload/download error cannot hide here)"
for devopts in "" ",disable_multiplane=1"; do
  for fmt in yuv420p nv12 yuv420p10le p010le p012le; do
    hw "$devopts" -f lavfi -i "$SRC" -frames:v 1 -vf "format=$fmt,hwupload,hflip_vulkan,hwdownload,format=$fmt" \
        -f rawvideo "$OUT/gpu-hflip-$fmt$devopts.raw"
    ff -f lavfi -i "$SRC" -frames:v 1 -vf "format=$fmt,hflip" -f rawvideo "$OUT/cpu-hflip-$fmt.raw"
    hw "$devopts" -f lavfi -i "$SRC" -frames:v 1 -vf "format=$fmt,hwupload,hwdownload,format=$fmt" \
        -f rawvideo "$OUT/rt-$fmt$devopts.raw"
    ff -f lavfi -i "$SRC" -frames:v 1 -vf "format=$fmt" -f rawvideo "$OUT/cpu-$fmt.raw"
    a=$(cmp -s "$OUT/gpu-hflip-$fmt$devopts.raw" "$OUT/cpu-hflip-$fmt.raw" && echo same || echo DIFF)
    b=$(cmp -s "$OUT/rt-$fmt$devopts.raw" "$OUT/cpu-$fmt.raw" && echo same || echo DIFF)
    echo "devopts='$devopts' $fmt: hflip gpu-vs-cpu=$a  upload/download round-trip=$b"
  done
done
echo "# C) verbose frame-context layout for p010le"
"$FFM" -hide_banner -nostdin -v debug -y -init_hw_device "vulkan=vk:$DEV" -filter_hw_device vk -f lavfi -i "$SRC" -frames:v 1 \
    -vf "format=p010le,hwupload,pelorus_analyze_vulkan,hwdownload,format=p010le" -f null - > "$OUT/p010-debug.log" 2>&1
grep -iE 'multiplane|host.*(copy|transfer)|Using .*format|VK_FORMAT|images|planes' "$OUT/p010-debug.log" | head -20
