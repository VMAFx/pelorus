#!/usr/bin/env bash
# shw-1 step 8f: the Windows-viable end-to-end shape for later stages:
# Vulkan Pelorus filter on GPU N -> hwdownload -> QSV encoder on the same GPU
# (encoder takes system-memory NV12; FFmpeg n9.0.2 has no Vulkan<->D3D11/QSV
# frame mapping on Windows, so this hop is a copy, not zero-copy).
set -uo pipefail
export PATH="/c/tmp/pel/prefix/bin:$PATH"
FFM=/c/tmp/pel/prefix/bin/ffmpeg.exe
OUT=/c/tmp/pel/shw-results/shw-1/devices
run() { local label="$1"; shift
    if "$FFM" -hide_banner -nostdin "$@" > "$OUT/$label.log" 2>&1; then
        echo "PASS $label $(grep -oE 'frame= *[0-9]+' "$OUT/$label.log" | tail -1)"
        grep -hE "Using device [0-9a-f]{4}:|Device [0-9] selected|Using device qs|implementation version is" "$OUT/$label.log" | sed -E 's/@ [0-9a-f]+//; s/^/    /'
    else echo "FAIL $label (rc=$?)"; grep -E "Error|error" "$OUT/$label.log" | head -3 | sed 's/^/    /'; fi; }
for pair in "0 hevc_qsv" "0 av1_qsv" "1 hevc_qsv"; do
    set -- $pair
    run "pipe-deband-$2-gpu$1" -v verbose \
        -init_hw_device "vulkan=vk:$1" -init_hw_device "qsv=qs:hw_any,child_device=$1" -filter_hw_device vk \
        -f lavfi -i "testsrc2=size=1920x1080:rate=24:duration=1" -frames:v 24 \
        -vf "format=nv12,hwupload,pelorus_deband_vulkan,hwdownload,format=nv12" \
        -c:v "$2" -global_quality 25 -f null -
done
