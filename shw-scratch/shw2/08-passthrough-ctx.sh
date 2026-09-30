#!/usr/bin/env bash
# shw-2 step 8: do pass-through Vulkan analyzers (stock blackdetect/scdet and
# Pelorus analyze/grain_estimate/mc) advertise the frames context of the frames
# they forward?  Triggers: linear input frames, Vulkan-hwaccel decoded frames.
set -uo pipefail
source /c/tmp/pel/shw-scratch/shw2/env.sh
OUT=$RES/08-passthrough-ctx${SUFFIX:-}; mkdir -p "$OUT"
DEV="${DEV:-0}"
# CPU-encoded clips for the Vulkan decoder (8-bit 4:2:0).
[[ -f "$OUT/src-hevc.mkv" ]] || "$FFM" -hide_banner -loglevel error -y -f lavfi -i testsrc2=size=320x240:rate=24:duration=1 \
    -pix_fmt yuv420p -c:v libx265 -x265-params log-level=error -g 12 "$OUT/src-hevc.mkv"
[[ -f "$OUT/src-h264.mkv" ]] || "$FFM" -hide_banner -loglevel error -y -f lavfi -i testsrc2=size=320x240:rate=24:duration=1 \
    -pix_fmt yuv420p -c:v libx264 -g 12 "$OUT/src-h264.mkv"
FILTERS=(blackdetect_vulkan scdet_vulkan pelorus_analyze_vulkan pelorus_grain_estimate_vulkan pelorus_mc_vulkan pelorus_deband_vulkan)
run() { # tag, args...
    local tag="$1"; shift
    if "$FFM" -hide_banner -nostdin -v verbose -y "$@" -f null - >"$OUT/$tag.log" 2>&1; then echo "OK   $tag";
    else echo "FAIL $tag: $(grep -m1 -E 'not the in the configured|Error|error' "$OUT/$tag.log")"; fi
    grep -m2 -hE 'Cannot reuse context|Reusing existing frames context' "$OUT/$tag.log" | sed 's/^/        /'
}
for f in "${FILTERS[@]}"; do
    run "linear-vk$DEV-$f" -init_hw_device "vulkan=vk:$DEV,linear_images=1,disable_multiplane=1" -filter_hw_device vk \
        -f lavfi -i testsrc2=size=96x64:rate=4:duration=1 -vf "format=yuv420p,hwupload,$f,hwdownload,format=yuv420p"
    for c in hevc h264; do
        run "vkdec-$c-vk$DEV-$f" -init_hw_device "vulkan=vk:$DEV" -filter_hw_device vk -hwaccel vulkan -hwaccel_device vk \
            -hwaccel_output_format vulkan -i "$OUT/src-$c.mkv" -vf "$f,hwdownload,format=nv12"
    done
done
