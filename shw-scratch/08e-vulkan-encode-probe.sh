#!/usr/bin/env bash
# shw-1 step 8e: runtime evidence for Vulkan Video ENCODE availability
# (hevc_vulkan / av1_vulkan carry -pelorus_roi via patch 0009).
set -uo pipefail
export PATH="/c/tmp/pel/prefix/bin:$PATH"
FFM=/c/tmp/pel/prefix/bin/ffmpeg.exe
OUT=/c/tmp/pel/shw-results/shw-1/devices
for idx in 0 1; do for enc in hevc_vulkan av1_vulkan; do
    log="$OUT/vkenc-$enc-vk$idx.log"
    if "$FFM" -hide_banner -nostdin -v verbose -init_hw_device "vulkan=vk:$idx" -filter_hw_device vk \
        -f lavfi -i "testsrc2=size=1280x720:rate=24:duration=1" -frames:v 5 \
        -vf "format=nv12,hwupload" -c:v "$enc" -f null - > "$log" 2>&1; then
        echo "PASS vk$idx $enc"
    else
        echo "FAIL vk$idx $enc (rc=$?): $(grep -iE 'encode|queue|not supported|Error' "$log" | grep -v 'Lavc\|Lavf' | head -3 | tr '\n' ' ')"
    fi
done; done
