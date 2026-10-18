#!/usr/bin/env bash
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2

set -euo pipefail

# Named skip when x265 or ffmpeg is not installed on PATH (ADR-0188).
if ! command -v x265 >/dev/null 2>&1 || ! command -v ffmpeg >/dev/null 2>&1; then
    echo "SKIP: x265 or ffmpeg not found"
    exit 77
fi

PELORUS_ZONES="${1:-}"
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

# Generate a 2-shot 60-frame raw Y4M clip (320x180, 25 fps).
if ! ffmpeg -v error -f lavfi -i testsrc2=size=320x180:rate=25 -frames:v 60 -y "$WORK_DIR/input.y4m" \
    > "$WORK_DIR/ffmpeg.log" 2>&1; then
    cat "$WORK_DIR/ffmpeg.log" >&2
    exit 1
fi

# Two shots: 0..29 (base CRF 28.0) and 30..59 (CRF 20.0).
cat << 'EOF' > "$WORK_DIR/shots.csv"
0,29,28.00
30,59,20.00
EOF

# Format 1: x265 --zonefile
"$PELORUS_ZONES" --format zonefile < "$WORK_DIR/shots.csv" > "$WORK_DIR/zonefile.txt"

# Format 2: x265 --zones b=
ZONES_PARAM="$("$PELORUS_ZONES" --format zones --base-crf 28.00 < "$WORK_DIR/shots.csv")"

# Note: --aq-mode 0 works around an x265 4.3 issue where --zonefile with default AQ
# reports per-frame QP 'inf' for frames after the reconfigure (Avg QP:inf);
# --no-cutree does not avoid it; cause is not established.

# Encode flat CRF baseline.
if ! x265 --input "$WORK_DIR/input.y4m" --crf 28.0 --aq-mode 0 \
    --csv "$WORK_DIR/flat.csv" --csv-log-level 1 -o "$WORK_DIR/flat.265" \
    > "$WORK_DIR/x265-flat.log" 2>&1; then
    cat "$WORK_DIR/x265-flat.log" >&2
    exit 1
fi

# Encode with --zonefile.
if ! x265 --input "$WORK_DIR/input.y4m" --crf 28.0 --aq-mode 0 \
    --zonefile "$WORK_DIR/zonefile.txt" \
    --csv "$WORK_DIR/zonefile.csv" --csv-log-level 1 -o "$WORK_DIR/zonefile.265" \
    > "$WORK_DIR/x265-zonefile.log" 2>&1; then
    cat "$WORK_DIR/x265-zonefile.log" >&2
    exit 1
fi

# Encode with --zones parameter.
if ! x265 --input "$WORK_DIR/input.y4m" --crf 28.0 --aq-mode 0 \
    --zones "$ZONES_PARAM" \
    --csv "$WORK_DIR/zones.csv" --csv-log-level 1 -o "$WORK_DIR/zones.265" \
    > "$WORK_DIR/x265-zones.log" 2>&1; then
    cat "$WORK_DIR/x265-zones.log" >&2
    exit 1
fi

# Compare mean QPs: shot 1 (0..21) control, shot 2 (38..59) drop; guard band (22..37) excluded.
python3 -I - "$WORK_DIR/flat.csv" "$WORK_DIR/zonefile.csv" "$WORK_DIR/zones.csv" << 'PYEOF'
import csv
import sys

def read_frame_qps(path):
    frames = {}
    base = 0
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        for row in reader:
            if not row or row[0].strip().startswith("Summary") or row[0].strip().startswith("Command"):
                break
            encode_order = int(row[1].strip())
            poc = int(row[3].strip())
            qp = float(row[4].strip())
            if poc == 0 and encode_order > 0:
                base = encode_order
            frame = poc + base
            frames[frame] = qp
    return frames

def mean_qp_range(frames, start, end):
    vals = [frames[i] for i in range(start, end + 1) if i in frames]
    if len(vals) != (end - start + 1):
        missing = [i for i in range(start, end + 1) if i not in frames]
        raise ValueError(f"Missing frames {missing} in range {start}..{end}")
    return sum(vals) / len(vals)

flat = read_frame_qps(sys.argv[1])
zf = read_frame_qps(sys.argv[2])
zp = read_frame_qps(sys.argv[3])

# Shot 1 (frames 0..21), Shot 2 (frames 38..59); guard band 22..37 excluded
flat_s1 = mean_qp_range(flat, 0, 21)
flat_s2 = mean_qp_range(flat, 38, 59)

zf_s1 = mean_qp_range(zf, 0, 21)
zf_s2 = mean_qp_range(zf, 38, 59)

zp_s1 = mean_qp_range(zp, 0, 21)
zp_s2 = mean_qp_range(zp, 38, 59)

zf_s2_drop = flat_s2 - zf_s2
zp_s2_drop = flat_s2 - zp_s2

zf_s1_delta = abs(flat_s1 - zf_s1)
zp_s1_delta = abs(flat_s1 - zp_s1)

print(f"Shot 1 (0..21) mean QP:  flat={flat_s1:.3f}  zonefile={zf_s1:.3f} (|delta|={zf_s1_delta:.3f})  zones={zp_s1:.3f} (|delta|={zp_s1_delta:.3f})")
print(f"Shot 2 (38..59) mean QP: flat={flat_s2:.3f}  zonefile={zf_s2:.3f} (drop={zf_s2_drop:.3f})  zones={zp_s2:.3f} (drop={zp_s2_drop:.3f})")

failed = False
if zf_s2_drop < 1.0:
    print(f"FAIL: zonefile shot 2 drop {zf_s2_drop:.3f} < 1.0", file=sys.stderr)
    failed = True
if zp_s2_drop < 1.0:
    print(f"FAIL: zones shot 2 drop {zp_s2_drop:.3f} < 1.0", file=sys.stderr)
    failed = True
if zf_s1_delta >= 1.0:
    print(f"FAIL: zonefile shot 1 |delta| {zf_s1_delta:.3f} >= 1.0 (control failed)", file=sys.stderr)
    failed = True
if zp_s1_delta >= 1.0:
    print(f"FAIL: zones shot 1 |delta| {zp_s1_delta:.3f} >= 1.0 (control failed)", file=sys.stderr)
    failed = True

if failed:
    sys.exit(1)

print("PASS: x265 honoured both zonefile and zones parameters with QP drop >= 1.0 and shot 1 control |delta| < 1.0")
PYEOF
