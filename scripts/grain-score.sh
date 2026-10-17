#!/usr/bin/env bash
#
# grain-score.sh — score decoded grain-synthesised video output vs reference
#
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
#
# Usage:
#   grain-score.sh --ffmpeg BIN --reference SRC --stream ENC.hevc [--distorted FILE] --metric psnr|ssim|vmaf --out DIR
#
# Scores the decoded, grain-synthesised video output of a stream against a
# reference, explicitly rejecting the encoder's grain-free reconstruction.
#
# Decodes the stream twice to framemd5:
#   1. Grain-applied decode (default decoder behavior)
#   2. Grain-free reconstruction (-export_side_data film_grain)
#
# If both decodes produce identical frame hashes, the stream carries no film
# grain and the script exits 1.
# If --distorted is provided, its decoded frame hashes must match the
# grain-applied decode; if they match the grain-free reconstruction, the script
# exits 1 refusing the grain-free reconstruction. If they match neither, the
# script exits 1.
# If --distorted is omitted, the stream is decoded to DIR/decoded.y4m with grain
# applied, checked the same way, and scored.

set -euo pipefail

usage() {
    cat <<'EOF'
Usage:
  grain-score.sh --ffmpeg BIN --reference SRC --stream ENC.hevc [--distorted FILE] --metric psnr|ssim|vmaf --out DIR

Options:
  --ffmpeg BIN        Path to ffmpeg executable
  --reference SRC     Reference source: file path or lavfi:<filtergraph>
  --stream ENC.hevc   Encoded video stream carrying film grain metadata
  --distorted FILE    Pre-decoded distorted file (optional; validated against decode)
  --metric METRIC     Quality metric: psnr, ssim, or vmaf
  --out DIR           Output directory for evidence and score.txt
  -h, --help          Show this help message
EOF
}

FFMPEG=""
REFERENCE=""
STREAM=""
DISTORTED=""
METRIC=""
OUT=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --ffmpeg)
            FFMPEG="${2:-}"
            shift 2
            ;;
        --reference)
            REFERENCE="${2:-}"
            shift 2
            ;;
        --stream)
            STREAM="${2:-}"
            shift 2
            ;;
        --distorted)
            DISTORTED="${2:-}"
            shift 2
            ;;
        --metric)
            METRIC="${2:-}"
            shift 2
            ;;
        --out)
            OUT="${2:-}"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "ERROR: unknown option: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

if [[ -z "$FFMPEG" || -z "$REFERENCE" || -z "$STREAM" || -z "$METRIC" || -z "$OUT" ]]; then
    echo "ERROR: missing required arguments" >&2
    usage >&2
    exit 1
fi

if [[ ! -x "$FFMPEG" ]]; then
    echo "ERROR: ffmpeg binary is not executable: $FFMPEG" >&2
    exit 1
fi

if [[ ! -r "$STREAM" ]]; then
    echo "ERROR: stream file is not readable: $STREAM" >&2
    exit 1
fi

if [[ "$REFERENCE" != lavfi:* && ! -r "$REFERENCE" ]]; then
    echo "ERROR: reference file is not readable: $REFERENCE" >&2
    exit 1
fi

if [[ -n "$DISTORTED" && ! -r "$DISTORTED" ]]; then
    echo "ERROR: distorted file is not readable: $DISTORTED" >&2
    exit 1
fi

case "$METRIC" in
    psnr|ssim|vmaf) ;;
    *)
        echo "ERROR: unsupported metric: $METRIC (expected psnr, ssim, or vmaf)" >&2
        exit 1
        ;;
esac

mkdir -p -- "$OUT"
if [[ ! -d "$OUT" ]]; then
    echo "ERROR: output directory is not accessible: $OUT" >&2
    exit 1
fi

# Verify required metric filter in ffmpeg
filter_name=""
filter_graph=""
case "$METRIC" in
    psnr)
        filter_name="psnr"
        filter_graph="[0:v][1:v]psnr=shortest=1"
        ;;
    ssim)
        filter_name="ssim"
        filter_graph="[0:v][1:v]ssim=shortest=1"
        ;;
    vmaf)
        filter_name="libvmaf"
        filter_graph="[0:v][1:v]libvmaf=shortest=1"
        ;;
esac

filters_cmd=("$FFMPEG" -nostdin -hide_banner -filters)
if ! "${filters_cmd[@]}" 2>&1 | awk -v name="$filter_name" '$2 == name { found=1 } END { exit !found }'; then
    echo "ERROR: required filter '$filter_name' is missing from ffmpeg" >&2
    exit 1
fi

# Helper to extract raw frame hashes from framemd5 output
pel_extract_hashes() {
    awk -F',' '!/^#/ { gsub(/[ \t]/, "", $NF); print $NF }' "$1"
}

# 1a. Decode stream twice to framemd5: grain-applied and grain-free
decode_applied_cmd=(
    "$FFMPEG" -nostdin -hide_banner
    -i "$STREAM"
    -f framemd5
    -y "$OUT/grain-applied.framemd5"
)
"${decode_applied_cmd[@]}" 2>"$OUT/decode-applied-framemd5.log" || {
    echo "ERROR: failed to decode stream with grain application" >&2
    exit 1
}

decode_free_cmd=(
    "$FFMPEG" -nostdin -hide_banner
    -export_side_data film_grain
    -i "$STREAM"
    -f framemd5
    -y "$OUT/grain-free.framemd5"
)
"${decode_free_cmd[@]}" 2>"$OUT/decode-free-framemd5.log" || {
    echo "ERROR: failed to decode stream without grain application" >&2
    exit 1
}

applied_hashes="$(pel_extract_hashes "$OUT/grain-applied.framemd5")"
free_hashes="$(pel_extract_hashes "$OUT/grain-free.framemd5")"

if [[ -z "$applied_hashes" || -z "$free_hashes" ]]; then
    echo "ERROR: stream decode produced no video frames" >&2
    exit 1
fi

# 1b. Check if stream carries synthesised grain
if [[ "$applied_hashes" == "$free_hashes" ]]; then
    echo "ERROR: stream carries no film grain to synthesise" >&2
    exit 1
fi

# 1c. No --distorted: decode the stream with grain applied and score that.
if [[ -z "$DISTORTED" ]]; then
    DISTORTED="$OUT/decoded.y4m"
    decode_y4m_cmd=(
        "$FFMPEG" -nostdin -hide_banner
        -i "$STREAM"
        -y "$DISTORTED"
    )
    "${decode_y4m_cmd[@]}" 2>"$OUT/decode-distorted-y4m.log" || {
        echo "ERROR: failed to decode stream to $DISTORTED" >&2
        exit 1
    }
fi

# 1d. Whatever gets scored must hash equal to the grain-applied decode.
distorted_target="$DISTORTED"
dist_cmd=(
    "$FFMPEG" -nostdin -hide_banner
    -i "$distorted_target"
    -f framemd5
    -y "$OUT/distorted.framemd5"
)
"${dist_cmd[@]}" 2>"$OUT/decode-distorted-framemd5.log" || {
    echo "ERROR: failed to decode distorted file to framemd5: $distorted_target" >&2
    exit 1
}

dist_hashes="$(pel_extract_hashes "$OUT/distorted.framemd5")"
if [[ -z "$dist_hashes" ]]; then
    echo "ERROR: distorted file produced no video frames: $distorted_target" >&2
    exit 1
fi

if [[ "$dist_hashes" == "$free_hashes" ]]; then
    echo "ERROR: distorted file matches grain-free reconstruction (refused)" >&2
    exit 1
elif [[ "$dist_hashes" != "$applied_hashes" ]]; then
    echo "ERROR: distorted file matches neither decode" >&2
    exit 1
fi

# 1e. Score distorted vs reference
ref_args=()
if [[ "$REFERENCE" == lavfi:* ]]; then
    ref_args=(-f lavfi -i "${REFERENCE#lavfi:}")
else
    ref_args=(-i "$REFERENCE")
fi

score_cmd=(
    "$FFMPEG" -nostdin -hide_banner
    -i "$distorted_target"
    "${ref_args[@]}"
    -lavfi "$filter_graph"
    -f null -
)
"${score_cmd[@]}" >"$OUT/score.stdout" 2>"$OUT/score.stderr" || {
    echo "ERROR: scoring command failed for metric $METRIC" >&2
    if [[ -s "$OUT/score.stderr" ]]; then
        tail -n 20 "$OUT/score.stderr" >&2
    fi
    exit 1
}

# 1f. Parse score value and write score.txt
score_val=""
case "$METRIC" in
    psnr)
        score_val="$(awk -F'average:' '/average:/ { split($2, a, " "); print a[1] }' "$OUT/score.stderr" | tail -n 1)"
        ;;
    ssim)
        score_val="$(awk -F'All:' '/All:/ { split($2, a, " "); print a[1] }' "$OUT/score.stderr" | tail -n 1)"
        ;;
    vmaf)
        score_val="$(awk -F'VMAF score: ' '/VMAF score:/ { split($2, a, " "); print a[1] }' "$OUT/score.stderr" | tail -n 1)"
        ;;
esac

if [[ -z "$score_val" ]]; then
    echo "ERROR: failed to parse $METRIC score value from ffmpeg output" >&2
    if [[ -s "$OUT/score.stderr" ]]; then
        tail -n 20 "$OUT/score.stderr" >&2
    fi
    exit 1
fi

cat >"$OUT/score.txt" <<EOF
metric: $METRIC
value: $score_val
decode_path: grain-applied
EOF

cat "$OUT/score.txt"
