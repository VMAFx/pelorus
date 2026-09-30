#!/usr/bin/env bash
# shw-2 step 5b: render the probe raws to PNG (CPU only) for visual inspection.
set -uo pipefail
source /c/tmp/pel/shw-scratch/shw2/env.sh
D=$RES/05-semiplanar-probe/vk${DEV:-0}
cd "$D"
inputs=()
for n in cpu-yuv420p rt-yuv420p "rt-yuv420p,disable_multiplane=1" cpu-hflip-yuv420p gpu-hflip-yuv420p "gpu-hflip-yuv420p,disable_multiplane=1"; do
    inputs+=(-f rawvideo -pix_fmt yuv420p -s 96x64 -i "$n.raw")
done
"$FFM" -hide_banner -loglevel error -y "${inputs[@]}" -filter_complex \
  "[0][1][2]hstack=3[top];[3][4][5]hstack=3[bot];[top][bot]vstack,scale=iw*3:ih*3:flags=neighbor,format=rgb24" \
  -frames:v 1 "$D/grid-yuv420p.png"
echo "$D/grid-yuv420p.png"
