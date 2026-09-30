#!/usr/bin/env bash
# shw-2 step 1: deliberate validation-layer positive control.
#  a) canary exe (loads vulkan-1.dll like ffmpeg.exe) with/without the layer
#  b) ffmpeg.exe itself: VK_LOADER_DEBUG=layer shows the layer inserted, and
#     VK_LAYER_ENABLES=best-practices must produce messages on stdout.
set -uo pipefail
source /c/tmp/pel/shw-scratch/shw2/env.sh
OUT=$RES/01-canary; mkdir -p "$OUT"
gcc -O1 -Wall -o "$SCR/vk-validation-canary.exe" "$SCR/vk-validation-canary.c" -lvulkan-1 || exit 1
ntldd "$SCR/vk-validation-canary.exe" 2>/dev/null | grep -i vulkan | tee "$OUT/canary-ntldd.txt"
for dev in 0 1; do
    for vvl in none msys sdk; do
        envs=()
        if [[ $vvl != none ]]; then envs=("$(pel_vvl_env $vvl)" VK_INSTANCE_LAYERS=VK_LAYER_KHRONOS_validation); fi
        env "${envs[@]}" "$SCR/vk-validation-canary.exe" $dev >"$OUT/canary-vk$dev-$vvl.stdout" 2>"$OUT/canary-vk$dev-$vvl.stderr"
        rc=$?
        echo "== canary vk$dev vvl=$vvl rc=$rc stdout-VUIDs: $(grep -Eo 'VUID-[[:alnum:]_.-]+' "$OUT/canary-vk$dev-$vvl.stdout" | sort -u | tr '\n' ' ') stderr-VUIDs: $(grep -Eo 'VUID-[[:alnum:]_.-]+' "$OUT/canary-vk$dev-$vvl.stderr" | sort -u | tr '\n' ' ')"
        cat "$OUT/canary-vk$dev-$vvl.stderr" | grep -E 'canary|vkCreateBuffer'
    done
done
# b) ffmpeg: loader layer trace + best-practices positive control (stock hflip_vulkan)
for dev in 0 1; do
    for vvl in msys sdk; do
        env "$(pel_vvl_env $vvl)" VK_INSTANCE_LAYERS=VK_LAYER_KHRONOS_validation VK_LOADER_DEBUG=layer \
            "$FFM" -hide_banner -nostdin -loglevel warning -y -init_hw_device vulkan=vk:$dev -filter_hw_device vk \
            -f lavfi -i 'testsrc2=size=64x64:rate=1:duration=1' -frames:v 1 \
            -vf 'format=nv12,hwupload,hflip_vulkan,hwdownload,format=nv12' -f null - \
            >"$OUT/ffmpeg-loader-vk$dev-$vvl.stdout" 2>"$OUT/ffmpeg-loader-vk$dev-$vvl.stderr"
        echo "== ffmpeg loader trace vk$dev vvl=$vvl rc=$?"
        grep -h -iE 'insert(ed)? (instance|device) layer|VkLayer_khronos_validation' "$OUT/ffmpeg-loader-vk$dev-$vvl.stdout" "$OUT/ffmpeg-loader-vk$dev-$vvl.stderr" | sort | uniq -c | head -8
        env "$(pel_vvl_env $vvl)" VK_INSTANCE_LAYERS=VK_LAYER_KHRONOS_validation \
            VK_LAYER_ENABLES=VK_VALIDATION_FEATURE_ENABLE_BEST_PRACTICES_EXT \
            "$FFM" -hide_banner -nostdin -loglevel warning -y -init_hw_device vulkan=vk:$dev -filter_hw_device vk \
            -f lavfi -i 'testsrc2=size=64x64:rate=1:duration=1' -frames:v 1 \
            -vf 'format=nv12,hwupload,hflip_vulkan,hwdownload,format=nv12' -f null - \
            >"$OUT/ffmpeg-bp-vk$dev-$vvl.stdout" 2>"$OUT/ffmpeg-bp-vk$dev-$vvl.stderr"
        echo "== ffmpeg best-practices control vk$dev vvl=$vvl rc=$? stdout-lines=$(wc -l <"$OUT/ffmpeg-bp-vk$dev-$vvl.stdout") ids: $(grep -Eo '(BestPractices|VUID|SYNC|UNASSIGNED)-[[:alnum:]_.-]+' "$OUT/ffmpeg-bp-vk$dev-$vvl.stdout" "$OUT/ffmpeg-bp-vk$dev-$vvl.stderr" | sed 's/^[^:]*://' | sort -u | tr '\n' ' ')"
    done
done
