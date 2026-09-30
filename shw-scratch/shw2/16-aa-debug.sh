#!/usr/bin/env bash
# shw-2 step 16: where do aa fast=1 and fast=0 differ on the UHD 770?
set -uo pipefail
source /c/tmp/pel/shw-scratch/shw2/env.sh
O=$RES/14-tile-stress/aa-debug-${TAGSUF:-x}; mkdir -p "$O"
SPEC="${SPEC:-1,linear_images=1,disable_multiplane=1}"
FMT="${FMT:-yuv420p}"
for v in "planes=15" "planes=15:fast=1" "planes=1" "planes=1:fast=1" "planes=4" "planes=4:fast=1"; do
    n=${v//[:=]/_}
    "$FFM" -hide_banner -nostdin -loglevel error -y -init_hw_device "vulkan=vk:$SPEC" -filter_hw_device vk \
        -f lavfi -i "testsrc2=size=640x360:rate=12,noise=alls=14:allf=t:all_seed=11" -frames:v 12 \
        -vf "format=$FMT,hwupload,pelorus_aa_vulkan=$v,hwdownload,format=$FMT" -f rawvideo "$O/$n.raw"
done
cd "$O"
for p in planes_15 planes_1 planes_4; do
    echo "== $p vs ${p}_fast_1"
    python3 /c/tmp/pel/shw-scratch/shw2/rawcmp.py "$FMT" 640 360 "$p.raw" "${p}_fast_1.raw" 12 | grep -v 'differing=0/'
done
