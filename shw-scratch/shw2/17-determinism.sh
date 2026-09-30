#!/usr/bin/env bash
# shw-2 step 17: is each variant deterministic run-to-run (race detector)?
set -uo pipefail
source /c/tmp/pel/shw-scratch/shw2/env.sh
SPEC="${SPEC:-1,linear_images=1,disable_multiplane=1}"; FMT="${FMT:-yuv420p}"
O=$RES/17-determinism/${TAGSUF:-x}; mkdir -p "$O"
for v in ${VARIANTS:-"pelorus_aa_vulkan=planes=1" "pelorus_aa_vulkan=planes=1:fast=1"}; do
    n=${v//[:=]/_}
    for r in 1 2 3 4; do
        "$FFM" -hide_banner -nostdin -loglevel error -y -init_hw_device "vulkan=vk:$SPEC" -filter_hw_device vk \
            -f lavfi -i "testsrc2=size=640x360:rate=12,noise=alls=14:allf=t:all_seed=11" -frames:v 12 \
            -vf "format=$FMT,hwupload,$v,hwdownload,format=$FMT" -f rawvideo "$O/$n-r$r.raw"
    done
    echo "$v: md5 per run: $(for r in 1 2 3 4; do md5sum < "$O/$n-r$r.raw" | cut -c1-10; done | tr '\n' ' ')"
done
