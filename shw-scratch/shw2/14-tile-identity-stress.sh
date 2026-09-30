#!/usr/bin/env bash
# shw-2 step 14: documented bit-identical variants under stress (ADR-0134
# denoise tile, ADR-0139 dehalo tile, ADR-0140 aa fast): 640x360 (partial edge
# workgroups), 12 noisy frames, 8/10/12-bit planar + semi-planar, all planes.
set -uo pipefail
source /c/tmp/pel/shw-scratch/shw2/env.sh
LABEL="${LABEL:?}"; SPEC="${SPEC:?}"
OUT=$RES/14-tile-stress/$LABEL; rm -rf "$OUT"; mkdir -p "$OUT"
SRC='testsrc2=size=640x360:rate=12,noise=alls=14:allf=t:all_seed=11'
pairs=(
  "pelorus_denoise_vulkan|planes=15|planes=15:tile=1"
  "pelorus_denoise_vulkan|planes=15:patch=3:prev=2:sigma=0.1:strength=0.9|planes=15:patch=3:prev=2:sigma=0.1:strength=0.9:tile=1"
  "pelorus_dehalo_vulkan|planes=15:lowsens=0:highsens=4|planes=15:lowsens=0:highsens=4:tile=1"
  "pelorus_dehalo_vulkan|planes=15:blur=8:lowsens=0|planes=15:blur=8:lowsens=0:tile=1"
  "pelorus_aa_vulkan|planes=15|planes=15:fast=1"
  "pelorus_aa_vulkan|planes=15:blur=8:darkstr=0.5:edge=0.02|planes=15:blur=8:darkstr=0.5:edge=0.02:fast=1"
)
bad=0; total=0
for fmt in yuv420p nv12 yuv420p10le p010le yuv420p12le p012le; do
  for p in "${pairs[@]}"; do
    IFS='|' read -r f a b <<<"$p"
    [[ -z "${ONLY:-}" || $f == *"$ONLY"* ]] || continue
    for v in a b; do
      opt=${!v}
      "$FFM" -hide_banner -nostdin -loglevel error -y -init_hw_device "vulkan=vk:$SPEC" -filter_hw_device vk \
        -f lavfi -i "$SRC" -frames:v 12 -vf "format=$fmt,hwupload,$f=$opt,hwdownload,format=$fmt" \
        -f rawvideo "$OUT/$f-$fmt-$v.raw" 2>"$OUT/$f-$fmt-$v.err" || echo "ERROR $f $fmt $opt: $(head -2 "$OUT/$f-$fmt-$v.err")"
    done
    total=$((total + 1))
    if cmp -s "$OUT/$f-$fmt-a.raw" "$OUT/$f-$fmt-b.raw"; then
      echo "IDENTICAL $fmt $f [$a] == [$b]"
    else
      bad=$((bad + 1))
      echo "DIFFER    $fmt $f [$a] vs [$b]: $(cmp -l "$OUT/$f-$fmt-a.raw" "$OUT/$f-$fmt-b.raw" 2>/dev/null | wc -l) bytes differ"
    fi
    rm -f "$OUT/$f-$fmt-a.raw" "$OUT/$f-$fmt-b.raw"
  done
done
echo "SUMMARY $LABEL: $((total - bad))/$total variant pairs bit-identical"
