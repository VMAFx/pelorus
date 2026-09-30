#!/usr/bin/env bash
# shw-1 step 8: device bring-up on the office box (Arc B580 + UHD 770), using
# the installed C:/tmp/pel/prefix/bin/ffmpeg.exe. Correctness only (no timing),
# so LM Studio stays up. One GPU command at a time; short clips (<=10 frames).
#   - Vulkan: -init_hw_device vulkan=vkN:<idx> for idx 0/1 at -v verbose
#     (FFmpeg logs its GPU listing and the selected device) + a trivial
#     pelorus_deband_vulkan run and a stock hflip_vulkan control on each.
#   - QSV: d3d11va child by DXGI adapter index (child_device=<n>), -v verbose
#     (d3d11va logs the selected adapter) + a 10-frame hevc_qsv / av1_qsv encode.
set -uo pipefail
export PATH="/c/tmp/pel/prefix/bin:$PATH"
FFM=/c/tmp/pel/prefix/bin/ffmpeg.exe
OUT=/c/tmp/pel/shw-results/shw-1/devices
mkdir -p "$OUT"
SRC=(-f lavfi -i "testsrc2=size=1280x720:rate=24:duration=1")

run() { # label, then ffmpeg args
    local label="$1"; shift
    if "$FFM" -hide_banner -nostdin "$@" > "$OUT/$label.log" 2>&1; then
        echo "PASS $label"
    else
        echo "FAIL $label (rc=$?)"; tail -8 "$OUT/$label.log" | sed 's/^/    /'
    fi
}

for idx in 0 1; do
    run "vulkan-init-$idx" -v verbose -init_hw_device "vulkan=vk:$idx" \
        "${SRC[@]}" -frames:v 1 -f null -
    grep -E "GPU listing|^\[.*\] +[0-9]+: |Device [0-9]+ selected|Device [0-9] selected|Using device|selected:" \
        "$OUT/vulkan-init-$idx.log" | sed 's/^/    /' | head -8
    run "vulkan-hflip-$idx" -v verbose -init_hw_device "vulkan=vk:$idx" -filter_hw_device vk \
        "${SRC[@]}" -frames:v 10 \
        -vf "format=nv12,hwupload,hflip_vulkan,hwdownload,format=nv12" -f null -
    run "vulkan-deband-$idx" -v verbose -init_hw_device "vulkan=vk:$idx" -filter_hw_device vk \
        "${SRC[@]}" -frames:v 10 \
        -vf "format=nv12,hwupload,pelorus_deband_vulkan,hwdownload,format=nv12" -f null -
done

for adapter in 0 1; do
    run "qsv-init-$adapter" -v verbose \
        -init_hw_device "qsv=qs:hw_any,child_device=$adapter" \
        "${SRC[@]}" -frames:v 1 -f null -
    grep -iE "Using device|adapter|MFX session|implementation|oneVPL" \
        "$OUT/qsv-init-$adapter.log" | sed 's/^/    /' | head -8
    run "qsv-hevc-$adapter" -v verbose \
        -init_hw_device "qsv=qs:hw_any,child_device=$adapter" -filter_hw_device qs \
        "${SRC[@]}" -frames:v 10 -vf "format=nv12,hwupload=extra_hw_frames=16" \
        -c:v hevc_qsv -global_quality 25 -f null -
    grep -iE "Using device|MFX session|implementation|Initialized" \
        "$OUT/qsv-hevc-$adapter.log" | sed 's/^/    /' | head -6
    run "qsv-av1-$adapter" -v verbose \
        -init_hw_device "qsv=qs:hw_any,child_device=$adapter" -filter_hw_device qs \
        "${SRC[@]}" -frames:v 10 -vf "format=nv12,hwupload=extra_hw_frames=16" \
        -c:v av1_qsv -global_quality 25 -f null -
done
