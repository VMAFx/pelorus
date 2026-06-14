#!/usr/bin/env bash
#
# bench.sh — the repeatable Pelorus proof. Pinned corpus, pinned encode/filter
# settings; emits a BD-rate + CAMBI report. See docs/development/benchmarking.md.
#
# Copyright 2026 Lusoris. BSD-2-Clause-Patent.
#
# Requires a built ffmpeg+pelorus (FFMPEG=...), a built vmafx (VMAF=...), a
# Vulkan GPU (DEVICE=vk:0), libpelorus on LD_LIBRARY_PATH, and network for the
# first corpus fetch. Everything else is pinned here.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
FFMPEG="${FFMPEG:-ffmpeg}"
VMAF="${VMAF:-vmaf}"
DEVICE="${DEVICE:-vk:0}"
CORPUS="${CORPUS:-$ROOT/.bench-corpus}"
OUT="${OUT:-$ROOT/bench-out}"

# --- pinned matrix --------------------------------------------------------
CLIPS=("${CLIPS:-bbb synth-banding}")
ENCODERS=("${ENCODERS:-hevc_nvenc av1_nvenc}")
PRESET="${PRESET:-p5}"
CQ="${CQ:-28 34 40 46}"
DEBAND_FILTER="pelorus_deband_vulkan=range=15:thry=0.012:dither=bluenoise:dynamic=1:protect=1"
# -------------------------------------------------------------------------

mkdir -p "$OUT"
# shellcheck disable=SC2086
FFMPEG="$FFMPEG" CORPUS="$CORPUS" "$HERE/fetch-corpus.sh" ${CLIPS[*]}

report="$OUT/REPORT.md"
{
  echo "# Pelorus benchmark — deband proof"
  echo
  echo "- ffmpeg: \`$("$FFMPEG" -version 2>/dev/null | head -1)\`"
  echo "- vmaf: \`$("$VMAF" --version 2>&1 | head -1)\`"
  echo "- GPU device: \`$DEVICE\`; preset \`$PRESET\`; CQ ladder \`$CQ\`"
  echo "- filter: \`$DEBAND_FILTER\`"
  echo
  echo "| clip | encoder | BD-rate (VMAF) | BD-VMAF | CAMBI base→pelorus | banding Δ |"
  echo "|---|---|---:|---:|---|---:|"
} > "$report"

for clip in ${CLIPS[*]}; do
  meta="$CORPUS/$clip.meta"; [ -f "$meta" ] || { echo "missing $meta"; continue; }
  # shellcheck disable=SC1090
  source "$meta"
  for enc in ${ENCODERS[*]}; do
    echo "=== $clip / $enc ==="
    odir="$OUT/${clip}_${enc}"
    # shellcheck disable=SC2086
    python3 "$HERE/run-bench.py" \
      --ffmpeg "$FFMPEG" --vmaf "$VMAF" --src "$CORPUS/$clip.yuv" \
      --width "$width" --height "$height" --pixfmt "$pixfmt" \
      --frames "$frames" --fps "$fps" \
      --encoder "$enc" --preset "$PRESET" --filter "$DEBAND_FILTER" \
      --device "$DEVICE" --cq $CQ --out "$odir"
    python3 - "$odir/result.json" "$clip" "$enc" >> "$report" <<'PY'
import json, sys
r = json.load(open(sys.argv[1])); clip, enc = sys.argv[2], sys.argv[3]
print(f"| {clip} | {enc} | {r['bd_rate_pct']:+.2f}% | {r['bd_vmaf']:+.3f} | "
      f"{r['cambi_baseline_mean']:.4f}→{r['cambi_pelorus_mean']:.4f} | "
      f"{r['cambi_reduction_pct']:+.1f}% |")
PY
  done
done

echo
echo "== report =="; cat "$report"
