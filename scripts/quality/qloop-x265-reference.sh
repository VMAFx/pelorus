#!/usr/bin/env bash
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2

set -euo pipefail

# Named skip when x265, ffmpeg or vmaf is not installed on PATH (ADR-0188).
if ! command -v x265 >/dev/null 2>&1 || ! command -v ffmpeg >/dev/null 2>&1 || ! command -v vmaf >/dev/null 2>&1; then
    echo "SKIP: x265, ffmpeg or vmaf not found"
    exit 77
fi

PELORUS_QLOOP="${1:-}"
if [[ -z "$PELORUS_QLOOP" ]]; then
    if [[ -x "${MESON_BUILD_ROOT:-build}/tools/pelorus_qloop" ]]; then
        PELORUS_QLOOP="${MESON_BUILD_ROOT:-build}/tools/pelorus_qloop"
    elif [[ -x "build/tools/pelorus_qloop" ]]; then
        PELORUS_QLOOP="build/tools/pelorus_qloop"
    else
        echo "FAIL: pelorus_qloop binary not found" >&2
        exit 1
    fi
fi

PELORUS_ZONES="${2:-}"
if [[ -z "$PELORUS_ZONES" ]]; then
    if [[ -x "${MESON_BUILD_ROOT:-build}/tools/pelorus_zones" ]]; then
        PELORUS_ZONES="${MESON_BUILD_ROOT:-build}/tools/pelorus_zones"
    elif [[ -x "build/tools/pelorus_zones" ]]; then
        PELORUS_ZONES="build/tools/pelorus_zones"
    else
        echo "FAIL: pelorus_zones binary not found" >&2
        exit 1
    fi
fi

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

# Generate a 2-shot 60-frame raw Y4M clip with contrasting content (320x180, 25 fps).
# Shot 0 (0..29): testsrc; Shot 1 (30..59): testsrc2.
if ! ffmpeg -v error -f lavfi -i testsrc=size=320x180:rate=25 -pix_fmt yuv420p -frames:v 30 \
    -y "$WORK_DIR/ref_shot0.y4m" > "$WORK_DIR/ffmpeg_ref0.log" 2>&1; then
    cat "$WORK_DIR/ffmpeg_ref0.log" >&2
    exit 1
fi

if ! ffmpeg -v error -f lavfi -i testsrc2=size=320x180:rate=25 -pix_fmt yuv420p -frames:v 30 \
    -y "$WORK_DIR/ref_shot1.y4m" > "$WORK_DIR/ffmpeg_ref1.log" 2>&1; then
    cat "$WORK_DIR/ffmpeg_ref1.log" >&2
    exit 1
fi

if ! ffmpeg -v error -i "$WORK_DIR/ref_shot0.y4m" -i "$WORK_DIR/ref_shot1.y4m" \
    -filter_complex "[0:v][1:v]concat=n=2:v=1[v]" -map "[v]" -y "$WORK_DIR/input.y4m" \
    > "$WORK_DIR/ffmpeg_concat.log" 2>&1; then
    cat "$WORK_DIR/ffmpeg_concat.log" >&2
    exit 1
fi

# Loop parameters
TARGET="90.0"
TOLERANCE="1.0"
MIN_CRF="10.0"
MAX_CRF="45.0"
START_CRF="28.0"
RESOLUTION="0.5"
MAX_ITER="8"
DIRECTION="-1"

# Planted negative case: target 101 must end unreachable.
cat /dev/null > "$WORK_DIR/planted_hist.txt"
PLANTED_STATE=""
for ((piter = 0; piter < 12; piter++)); do
    STEP_OUT="$("$PELORUS_QLOOP" --target 101.0 --tolerance "$TOLERANCE" --min "$MIN_CRF" --max "$MAX_CRF" \
        --start "$START_CRF" --resolution "$RESOLUTION" --max-iter "$MAX_ITER" --direction "$DIRECTION" \
        < "$WORK_DIR/planted_hist.txt")"
    STATUS="$(echo "$STEP_OUT" | awk '{print $1}')"
    if [[ "$STATUS" == "probe" ]]; then
        KNOB="$(echo "$STEP_OUT" | awk '{print $2}')"
        if ! x265 --input "$WORK_DIR/ref_shot0.y4m" --crf "$KNOB" --preset ultrafast --aq-mode 0 \
            --frame-threads 1 -o "$WORK_DIR/planted.265" > "$WORK_DIR/planted_x265.log" 2>&1; then
            cat "$WORK_DIR/planted_x265.log" >&2
            exit 1
        fi
        if ! ffmpeg -v error -i "$WORK_DIR/planted.265" -y "$WORK_DIR/planted_dec.y4m" \
            > "$WORK_DIR/planted_dec.log" 2>&1; then
            cat "$WORK_DIR/planted_dec.log" >&2
            exit 1
        fi
        if ! vmaf -r "$WORK_DIR/ref_shot0.y4m" -d "$WORK_DIR/planted_dec.y4m" --quiet --json \
            -o "$WORK_DIR/planted_vmaf.json" > "$WORK_DIR/planted_vmaf.log" 2>&1; then
            cat "$WORK_DIR/planted_vmaf.log" >&2
            exit 1
        fi
        SCORE="$(python3 -I -c '
import json, sys
with open(sys.argv[1]) as f:
    d = json.load(f)
print("%.4f" % d["pooled_metrics"]["vmaf"]["mean"])
' "$WORK_DIR/planted_vmaf.json")"
        echo "$KNOB $SCORE" >> "$WORK_DIR/planted_hist.txt"
    elif [[ "$STATUS" == "unreachable" ]]; then
        PLANTED_STATE="unreachable"
        break
    else
        echo "FAIL: planted case target 101 did not return unreachable, got $STATUS" >&2
        exit 1
    fi
done

if [[ "$PLANTED_STATE" != "unreachable" ]]; then
    echo "FAIL: planted case target 101 did not conclude with unreachable" >&2
    exit 1
fi

# Run reference quality loop for each shot.
declare -a SHOT_CRFS
declare -a SHOT_ITERS
declare -a SHOT_LOOP_VMAFS

for s in 0 1; do
    REF_SHOT="$WORK_DIR/ref_shot${s}.y4m"
    HIST_FILE="$WORK_DIR/shot${s}_hist.txt"
    cat /dev/null > "$HIST_FILE"
    DONE_CRF=""
    DONE_VMAF=""
    ITER_COUNT=0

    for ((iter = 0; iter <= MAX_ITER + 1; iter++)); do
        STEP_OUT="$("$PELORUS_QLOOP" --target "$TARGET" --tolerance "$TOLERANCE" --min "$MIN_CRF" \
            --max "$MAX_CRF" --start "$START_CRF" --resolution "$RESOLUTION" --max-iter "$MAX_ITER" \
            --direction "$DIRECTION" < "$HIST_FILE")"
        STATUS="$(echo "$STEP_OUT" | awk '{print $1}')"

        if [[ "$STATUS" == "probe" ]]; then
            KNOB="$(echo "$STEP_OUT" | awk '{print $2}')"
            PROBE_BIN="$WORK_DIR/shot${s}_p${iter}.265"
            PROBE_DEC="$WORK_DIR/shot${s}_p${iter}_dec.y4m"
            PROBE_LOG="$WORK_DIR/x265_shot${s}_p${iter}.log"

            if ! x265 --input "$REF_SHOT" --crf "$KNOB" --preset ultrafast --aq-mode 0 \
                --frame-threads 1 -o "$PROBE_BIN" > "$PROBE_LOG" 2>&1; then
                cat "$PROBE_LOG" >&2
                exit 1
            fi
            if ! ffmpeg -v error -i "$PROBE_BIN" -y "$PROBE_DEC" > "$WORK_DIR/dec_err.log" 2>&1; then
                cat "$WORK_DIR/dec_err.log" >&2
                exit 1
            fi
            VMAF_JSON="$WORK_DIR/vmaf_shot${s}_p${iter}.json"
            if ! vmaf -r "$REF_SHOT" -d "$PROBE_DEC" --quiet --json -o "$VMAF_JSON" \
                > "$WORK_DIR/vmaf_err.log" 2>&1; then
                cat "$WORK_DIR/vmaf_err.log" >&2
                exit 1
            fi
            SCORE="$(python3 -I -c '
import json, sys
with open(sys.argv[1]) as f:
    d = json.load(f)
print("%.4f" % d["pooled_metrics"]["vmaf"]["mean"])
' "$VMAF_JSON")"
            echo "$KNOB $SCORE" >> "$HIST_FILE"
            ITER_COUNT=$((ITER_COUNT + 1))
        elif [[ "$STATUS" == "done" ]]; then
            DONE_CRF="$(echo "$STEP_OUT" | awk '{print $2}')"
            DONE_VMAF="$(python3 -I -c '
import sys
target_k = float(sys.argv[1])
with open(sys.argv[2]) as f:
    for line in f:
        parts = line.strip().split()
        if parts and abs(float(parts[0]) - target_k) < 1e-4:
            print(f"{float(parts[1]):.4f}")
            break
' "$DONE_CRF" "$HIST_FILE")"
            break
        else
            echo "FAIL: unexpected loop state '$STATUS' for shot $s" >&2
            exit 1
        fi
    done

    if [[ -z "$DONE_CRF" ]]; then
        echo "FAIL: shot $s failed to converge to DONE within max_iter" >&2
        exit 1
    fi
    if [[ "$ITER_COUNT" -gt "$MAX_ITER" ]]; then
        echo "FAIL: shot $s exceeded max_iter ($ITER_COUNT > $MAX_ITER)" >&2
        exit 1
    fi

    SHOT_CRFS["$s"]="$DONE_CRF"
    SHOT_ITERS["$s"]="$ITER_COUNT"
    SHOT_LOOP_VMAFS["$s"]="$DONE_VMAF"
done

# Emit zonefile from per-shot DONE CRFs via pelorus_zones
cat << EOF > "$WORK_DIR/shots.csv"
0,29,${SHOT_CRFS[0]}
30,59,${SHOT_CRFS[1]}
EOF

"$PELORUS_ZONES" --format zonefile < "$WORK_DIR/shots.csv" > "$WORK_DIR/zonefile.txt"

# Encode whole clip once with --zonefile
if ! x265 --input "$WORK_DIR/input.y4m" --crf 28.0 --aq-mode 0 --preset ultrafast \
    --frame-threads 1 --zonefile "$WORK_DIR/zonefile.txt" -o "$WORK_DIR/full.265" \
    > "$WORK_DIR/x265_full.log" 2>&1; then
    cat "$WORK_DIR/x265_full.log" >&2
    exit 1
fi

if ! ffmpeg -v error -i "$WORK_DIR/full.265" -y "$WORK_DIR/full_dec.y4m" \
    > "$WORK_DIR/ffmpeg_full_dec.log" 2>&1; then
    cat "$WORK_DIR/ffmpeg_full_dec.log" >&2
    exit 1
fi

# Extract decoded frames per shot
if ! ffmpeg -v error -i "$WORK_DIR/full_dec.y4m" -vf "select=between(n\,0\,29),setpts=PTS-STARTPTS" \
    -pix_fmt yuv420p -y "$WORK_DIR/full_dec_s0.y4m" > "$WORK_DIR/ffmpeg_split0.log" 2>&1; then
    cat "$WORK_DIR/ffmpeg_split0.log" >&2
    exit 1
fi

if ! ffmpeg -v error -i "$WORK_DIR/full_dec.y4m" -vf "select=between(n\,30\,59),setpts=PTS-STARTPTS" \
    -pix_fmt yuv420p -y "$WORK_DIR/full_dec_s1.y4m" > "$WORK_DIR/ffmpeg_split1.log" 2>&1; then
    cat "$WORK_DIR/ffmpeg_split1.log" >&2
    exit 1
fi

if ! vmaf -r "$WORK_DIR/ref_shot0.y4m" -d "$WORK_DIR/full_dec_s0.y4m" --quiet --json \
    -o "$WORK_DIR/full_vmaf_s0.json" > "$WORK_DIR/vmaf_full0.log" 2>&1; then
    cat "$WORK_DIR/vmaf_full0.log" >&2
    exit 1
fi

if ! vmaf -r "$WORK_DIR/ref_shot1.y4m" -d "$WORK_DIR/full_dec_s1.y4m" --quiet --json \
    -o "$WORK_DIR/full_vmaf_s1.json" > "$WORK_DIR/vmaf_full1.log" 2>&1; then
    cat "$WORK_DIR/vmaf_full1.log" >&2
    exit 1
fi

FULL_VMAF_0="$(python3 -I -c '
import json, sys
with open(sys.argv[1]) as f:
    d = json.load(f)
print("%.4f" % d["pooled_metrics"]["vmaf"]["mean"])
' "$WORK_DIR/full_vmaf_s0.json")"

FULL_VMAF_1="$(python3 -I -c '
import json, sys
with open(sys.argv[1]) as f:
    d = json.load(f)
print("%.4f" % d["pooled_metrics"]["vmaf"]["mean"])
' "$WORK_DIR/full_vmaf_s1.json")"

# Print summary table
printf "\n"
printf "%-6s  %-10s  %-8s  %-10s  %-16s\n" "shot" "iterations" "CRF" "loop VMAF" "full-encode VMAF"
printf "%-6s  %-10s  %-8s  %-10s  %-16s\n" "0" "${SHOT_ITERS[0]}" "${SHOT_CRFS[0]}" "${SHOT_LOOP_VMAFS[0]}" "$FULL_VMAF_0"
printf "%-6s  %-10s  %-8s  %-10s  %-16s\n" "1" "${SHOT_ITERS[1]}" "${SHOT_CRFS[1]}" "${SHOT_LOOP_VMAFS[1]}" "$FULL_VMAF_1"
printf "\n"

# Verify cross-encode drift: per-shot mean VMAF >= target - 0.5 (89.5)
SLACK_MIN="89.5000"
CHECK_RES="$(python3 -I -c '
import sys
v0, v1, slack = float(sys.argv[1]), float(sys.argv[2]), float(sys.argv[3])
ok0 = 1 if v0 >= slack else 0
ok1 = 1 if v1 >= slack else 0
print(f"{ok0} {ok1}")
' "$FULL_VMAF_0" "$FULL_VMAF_1" "$SLACK_MIN")"

OK0="$(echo "$CHECK_RES" | awk '{print $1}')"
OK1="$(echo "$CHECK_RES" | awk '{print $2}')"

if [[ "$OK0" != "1" ]]; then
    echo "FAIL: shot 0 full-encode mean VMAF $FULL_VMAF_0 < $SLACK_MIN (target - 0.5)" >&2
    exit 1
fi

if [[ "$OK1" != "1" ]]; then
    echo "FAIL: shot 1 full-encode mean VMAF $FULL_VMAF_1 < $SLACK_MIN (target - 0.5)" >&2
    exit 1
fi

echo "PASS: qloop-x265-reference closed loop succeeded on both shots"
