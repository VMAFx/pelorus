#!/usr/bin/env bash
# shw-1 step 8d: every Pelorus Vulkan filter at defaults, 10 frames of 720p
# NV12, on each Vulkan device (0 = Arc B580, 1 = UHD 770). Pipeline-creation
# smoke only (does it init + run on the Windows Intel drivers), not quality.
set -uo pipefail
export PATH="/c/tmp/pel/prefix/bin:$PATH"
FFM=/c/tmp/pel/prefix/bin/ffmpeg.exe
OUT=/c/tmp/pel/shw-results/shw-1/devices/filters
mkdir -p "$OUT"
SRC=(-f lavfi -i "testsrc2=size=1280x720:rate=24:duration=1")
FILTERS=(pelorus_deband_vulkan pelorus_analyze_vulkan pelorus_denoise_vulkan
    pelorus_grain_estimate_vulkan pelorus_mc_vulkan pelorus_dehalo_vulkan
    pelorus_aa_vulkan pelorus_deblock_vulkan pelorus_borderfix_vulkan)
for idx in 0 1; do
    for f in "${FILTERS[@]}"; do
        log="$OUT/$f-vk$idx.log"
        if "$FFM" -hide_banner -nostdin -v verbose -init_hw_device "vulkan=vk:$idx" -filter_hw_device vk \
            "${SRC[@]}" -frames:v 10 \
            -vf "format=nv12,hwupload,$f,hwdownload,format=nv12" -f null - > "$log" 2>&1; then
            echo "PASS vk$idx $f $(grep -oE 'frame= *[0-9]+' "$log" | tail -1)"
        else
            echo "FAIL vk$idx $f (rc=$?)"; grep -E "Error|error|fail" "$log" | head -4 | sed 's/^/    /'
        fi
    done
done
log="$OUT/pelorus_scenecut-cpu.log"
if "$FFM" -hide_banner -nostdin -v verbose "${SRC[@]}" -frames:v 10 -vf pelorus_scenecut -f null - > "$log" 2>&1; then
    echo "PASS cpu pelorus_scenecut"; else echo "FAIL cpu pelorus_scenecut"; tail -4 "$log"; fi
