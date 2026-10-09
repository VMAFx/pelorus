#!/usr/bin/env bash
# Hardware correctness matrix for ADR-0147.
#
# Required environment:
#   FFMPEG_BIN       patched FFmpeg binary (default: ffmpeg)
# Optional:
#   VULKAN_DEVICE    FFmpeg Vulkan device selector, for example 0 or 1
#   OUTPUT_ROOT      retained evidence directory (default: new /tmp directory)
#   PELORUS_VALIDATE auto (default), 1, or 0

set -euo pipefail

PEL_FFMPEG_BIN="${FFMPEG_BIN:-ffmpeg}"
PEL_VULKAN_DEVICE="${VULKAN_DEVICE:-}"
PEL_OUTPUT_ROOT="${OUTPUT_ROOT:-}"
PEL_VALIDATE="${PELORUS_VALIDATE:-auto}"

if [[ -z "$PEL_OUTPUT_ROOT" ]]; then
    PEL_OUTPUT_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/pelorus-vulkan-matrix.XXXXXX")"
fi
mkdir -p "$PEL_OUTPUT_ROOT"
PEL_OUTPUT_ROOT="$(cd "$PEL_OUTPUT_ROOT" && pwd)"

PEL_DEVICE_SPEC="vulkan=pelorus_vk"
if [[ -n "$PEL_VULKAN_DEVICE" ]]; then
    PEL_DEVICE_SPEC+=":${PEL_VULKAN_DEVICE}"
fi

PEL_COMMON=(
    -hide_banner
    -loglevel warning
    -y
    -init_hw_device "$PEL_DEVICE_SPEC"
    -filter_hw_device pelorus_vk
    -threads 1
)
PEL_VALIDATION_ENV=()
PEL_VALIDATION_ENABLED=0

pel_fail()
{
    echo "FAIL: $*" >&2
    exit 1
}

pel_report_failed_run()
{
    local status=$?
    if ((status != 0)); then
        echo "Evidence: $PEL_OUTPUT_ROOT" >&2
    fi
}
trap pel_report_failed_run EXIT

pel_has_validation_layer()
{
    command -v vulkaninfo >/dev/null 2>&1 &&
        vulkaninfo 2>/dev/null | grep 'VK_LAYER_KHRONOS_validation' >/dev/null
}

case "$PEL_VALIDATE" in
    auto)
        if pel_has_validation_layer; then
            PEL_VALIDATION_ENABLED=1
        fi
        ;;
    1)
        pel_has_validation_layer || pel_fail \
            "PELORUS_VALIDATE=1 but VK_LAYER_KHRONOS_validation is unavailable"
        PEL_VALIDATION_ENABLED=1
        ;;
    0) ;;
    *) pel_fail "PELORUS_VALIDATE must be auto, 1, or 0" ;;
esac

if ((PEL_VALIDATION_ENABLED)); then
    PEL_VALIDATION_ENV=(VK_INSTANCE_LAYERS=VK_LAYER_KHRONOS_validation)
fi

# Upstream-owned diagnostics are listed one per line in the allow-list file,
# shared with the tester stages: "<VUID without prefix> | <reference> | <expiry>".
# An entry without a reference, with a malformed or past expiry, never matches.
PEL_VUID_ALLOWLIST="${PEL_VUID_ALLOWLIST:-$(dirname "${BASH_SOURCE[0]}")/vulkan-vuid-allowlist.txt}"
[[ -r "$PEL_VUID_ALLOWLIST" ]] || {
    echo "FAIL: VUID allow-list is unreadable: $PEL_VUID_ALLOWLIST" >&2
    exit 1
}

pel_allowlist_entry()
{
    awk -F'|' -v want="${1#VUID-}" -v today="$(date +%F)" '
        /^[[:space:]]*(#|$)/ { next }
        {
            for (i = 1; i <= 3; i++) gsub(/^[[:space:]]+|[[:space:]]+$/, "", $i)
            if (NF == 3 && $1 == want && $2 != "" && $3 ~ /^[0-9]{4}-[0-9]{2}-[0-9]{2}$/ &&
                $3 >= today) { print $1 " | " $2 " | " $3; found = 1; exit }
        }
        END { exit !found }' "$PEL_VUID_ALLOWLIST"
}

pel_check_validation()
{
    local log
    local vuid
    local vuids
    local entry
    local grep_status

    ((PEL_VALIDATION_ENABLED)) || return 0
    for log in "$@"; do
        # grep exits 1 when the log holds no VUID (clean run); any higher status is an error.
        grep_status=0
        vuids="$(grep -Eo 'VUID-[[:alnum:]_.-]+' "$log" | sort -u)" || grep_status=$?
        if ((grep_status > 1)); then
            echo "ERROR: could not scan validation log: $log" >&2
            return 1
        fi
        while IFS= read -r vuid; do
            [[ -n "$vuid" ]] || continue
            if entry="$(pel_allowlist_entry "$vuid")"; then
                echo "$vuid" >>"$PEL_OUTPUT_ROOT/validation-known-upstream.txt"
                echo "$entry" >>"$PEL_OUTPUT_ROOT/validation-allowlisted.txt"
            else
                echo "Unexpected validation diagnostic in $log: $vuid" >&2
                return 1
            fi
        done <<<"$vuids"
    done
}

pel_run()
{
    local label="$1"
    shift
    local stdout="$PEL_OUTPUT_ROOT/${label}.stdout"
    local stderr="$PEL_OUTPUT_ROOT/${label}.stderr"

    if ! env "${PEL_VALIDATION_ENV[@]}" "$PEL_FFMPEG_BIN" \
        "${PEL_COMMON[@]}" "$@" >"$stdout" 2>"$stderr"; then
        sed -n '1,220p' "$stderr" >&2
        pel_fail "$label"
    fi
    pel_check_validation "$stdout" "$stderr" || \
        pel_fail "$label emitted a new Vulkan VUID"
    echo "PASS: $label"
}

pel_emit_raw()
{
    local label="$1"
    local format="$2"
    local source="$3"
    local filter="$4"
    local frames="$5"
    local chain="format=${format},hwupload"

    if [[ -n "$filter" ]]; then
        chain+=",${filter}"
    fi
    chain+=",hwdownload,format=${format}"

    pel_run "$label" \
        -f lavfi -i "$source" -frames:v "$frames" \
        -vf "$chain" -fps_mode passthrough -f rawvideo \
        "$PEL_OUTPUT_ROOT/${label}.raw"
}

command -v "$PEL_FFMPEG_BIN" >/dev/null 2>&1 || pel_fail \
    "FFMPEG_BIN is not executable: $PEL_FFMPEG_BIN"
command -v python3 >/dev/null 2>&1 || pel_fail "python3 is required"

if ! "$PEL_FFMPEG_BIN" -hide_banner -filters \
    >"$PEL_OUTPUT_ROOT/filters.txt" 2>"$PEL_OUTPUT_ROOT/filters.stderr"; then
    pel_fail "could not enumerate patched filters"
fi
for PEL_FILTER in pelorus_analyze_vulkan pelorus_deblock_vulkan \
    pelorus_denoise_vulkan pelorus_aa_vulkan pelorus_dehalo_vulkan \
    pelorus_mc_vulkan; do
    grep "[[:space:]]${PEL_FILTER}[[:space:]]" "$PEL_OUTPUT_ROOT/filters.txt" \
        >/dev/null || \
        pel_fail "patched filter is missing: $PEL_FILTER"
done

# A missing Vulkan device is a skip, never passing evidence.
if ! env "${PEL_VALIDATION_ENV[@]}" "$PEL_FFMPEG_BIN" \
    "${PEL_COMMON[@]}" -f lavfi -i 'color=size=16x16:rate=1:duration=1' \
    -frames:v 1 -vf 'format=yuv420p,hwupload,hwdownload,format=yuv420p' \
    -f null - >"$PEL_OUTPUT_ROOT/device-probe.stdout" \
    2>"$PEL_OUTPUT_ROOT/device-probe.stderr"; then
    echo "SKIP: no usable Vulkan device; no matrix row executed" >&2
    exit 77
fi
pel_check_validation "$PEL_OUTPUT_ROOT/device-probe.stdout" \
    "$PEL_OUTPUT_ROOT/device-probe.stderr" || \
    pel_fail "device probe emitted a new Vulkan VUID"

{
    echo "FFmpeg: $PEL_FFMPEG_BIN"
    echo "Device: ${PEL_VULKAN_DEVICE:-default}"
    echo "Validation: $PEL_VALIDATION_ENABLED"
    "$PEL_FFMPEG_BIN" -version | head -n 1
} >"$PEL_OUTPUT_ROOT/environment.txt"

# Analyzer values must remain in the same logical sample domain across 8-bit,
# planar 10/12-bit, and shifted semi-planar 10/12-bit layouts.
for PEL_FORMAT in yuv420p yuv420p10le p010le yuv420p12le p012le; do
    PEL_ANALYZE_CHAIN="format=${PEL_FORMAT},hwupload,pelorus_analyze_vulkan,"
    PEL_ANALYZE_CHAIN+="hwdownload,format=${PEL_FORMAT},metadata=mode=print:file=-"
    pel_run "analyze-${PEL_FORMAT}" \
        -f lavfi -i 'testsrc2=size=96x64:rate=1:duration=1' -frames:v 1 \
        -vf "$PEL_ANALYZE_CHAIN" \
        -f null -
done

python3 - "$PEL_OUTPUT_ROOT" <<'PY_ANALYZE'
import math
import pathlib
import re
import sys

root = pathlib.Path(sys.argv[1])
formats = ("yuv420p", "yuv420p10le", "p010le", "yuv420p12le", "p012le")
keys = ("variance", "edge", "complexity")

def read_values(fmt):
    text = (root / f"analyze-{fmt}.stdout").read_text()
    values = {}
    for key in keys:
        found = re.findall(rf"lavfi\.pelorus\.{key}=([0-9.eE+-]+)", text)
        if not found:
            raise SystemExit(f"missing analyzer metadata {key} for {fmt}")
        values[key] = float(found[-1])
    return values

baseline = read_values(formats[0])
for fmt in formats[1:]:
    actual = read_values(fmt)
    for key in keys:
        a, b = baseline[key], actual[key]
        tolerance = max(0.002, abs(a) * 0.05)
        if not (math.isfinite(b) and abs(a - b) <= tolerance):
            raise SystemExit(
                f"analyzer domain mismatch {fmt} {key}: {b} vs {a} "
                f"(tolerance {tolerance})"
            )
print("PASS: analyzer logical-domain equivalence")
PY_ANALYZE

# Issue #219 / ADR-0177: per-cell banding, variance and edge maps. showinfo
# prints each Pelorus blob; analyze-maps-check.py decodes it with the
# pel_blob_map() reader rules and checks the values against the frame scalars.
PEL_MAPS_CHECK="$(dirname "${BASH_SOURCE[0]}")/analyze-maps-check.py"
PEL_MAPS_RAMP="color=gray:size=256x128:rate=1:duration=1,format=yuv420p,"
PEL_MAPS_RAMP+="geq=lum='16+X*64/W':cb=128:cr=128"
PEL_MAPS_NOISE="color=c=0x808080:size=256x128:rate=1:duration=1,format=yuv420p,"
PEL_MAPS_NOISE+="noise=alls=40:allf=u:all_seed=7"

pel_maps_run()
{
    local label="$1"
    local source="$2"
    local format="$3"
    local options="$4"

    pel_run "$label" -loglevel info -f lavfi -i "$source" -frames:v 1 \
        -vf "format=${format},hwupload,pelorus_analyze_vulkan${options},hwdownload,format=${format},showinfo" \
        -f null -
}

pel_maps_check()
{
    python3 -I "$PEL_MAPS_CHECK" "$@" || pel_fail "analyze maps: $*"
}

# Negative case: a run that must fail, with `pattern` on stderr and no new VUID.
pel_run_refused()
{
    local label="$1"
    local pattern="$2"
    shift 2
    local stdout="$PEL_OUTPUT_ROOT/${label}.stdout"
    local stderr="$PEL_OUTPUT_ROOT/${label}.stderr"

    if env "${PEL_VALIDATION_ENV[@]}" "$PEL_FFMPEG_BIN" \
        "${PEL_COMMON[@]}" "$@" >"$stdout" 2>"$stderr"; then
        pel_fail "$label was accepted"
    fi
    if ! grep -F -- "$pattern" "$stderr" >/dev/null; then
        sed -n '1,80p' "$stderr" >&2
        pel_fail "$label failed without: $pattern"
    fi
    pel_check_validation "$stdout" "$stderr" || \
        pel_fail "$label emitted a new Vulkan VUID"
    echo "PASS: $label"
}

pel_maps_run maps-ramp "$PEL_MAPS_RAMP" yuv420p ''
pel_maps_run maps-noise "$PEL_MAPS_NOISE" yuv420p ''
pel_maps_run maps-ramp-p010le "$PEL_MAPS_RAMP" p010le ''
pel_maps_run maps-ramp-cell8 "$PEL_MAPS_RAMP" yuv420p '=cell=8'
pel_maps_run maps-ramp-cell64 "$PEL_MAPS_RAMP" yuv420p '=cell=64'
pel_maps_run maps-off "$PEL_MAPS_RAMP" yuv420p '=maps=0'
# Boundaries: a frame smaller than one cell, partial last cells, the largest grid.
pel_maps_run maps-one-cell \
    "color=gray:size=16x16:rate=1:duration=1,format=yuv420p,geq=lum='16+X*16/W':cb=128:cr=128" \
    yuv420p ''
pel_maps_run maps-partial \
    "color=gray:size=100x70:rate=1:duration=1,format=yuv420p,geq=lum='16+X*32/W':cb=128:cr=128" \
    yuv420p ''
pel_maps_run maps-max-grid 'color=c=gray:size=8192x8192:rate=1:duration=1' yuv420p '=cell=8'
pel_run_refused maps-oversized-grid 'exceeds the grid limit of 1048576 cells' \
    -f lavfi -i 'color=c=gray:size=8200x8192:rate=1:duration=1' -frames:v 1 \
    -vf 'format=yuv420p,hwupload,pelorus_analyze_vulkan=cell=8,hwdownload,format=yuv420p' \
    -f null -

pel_maps_check maps "$PEL_OUTPUT_ROOT/maps-ramp.stderr" --grid 8x4 --band-min 0.8
pel_maps_check maps "$PEL_OUTPUT_ROOT/maps-noise.stderr" --grid 8x4 --band-max 0.1
pel_maps_check contrast "$PEL_OUTPUT_ROOT/maps-ramp.stderr" "$PEL_OUTPUT_ROOT/maps-noise.stderr" --margin 0.5
pel_maps_check maps "$PEL_OUTPUT_ROOT/maps-ramp-p010le.stderr" --grid 8x4 --band-min 0.8
pel_maps_check same "$PEL_OUTPUT_ROOT/maps-ramp.stderr" "$PEL_OUTPUT_ROOT/maps-ramp-p010le.stderr"
pel_maps_check maps "$PEL_OUTPUT_ROOT/maps-ramp-cell8.stderr" --grid 32x16
pel_maps_check maps "$PEL_OUTPUT_ROOT/maps-ramp-cell64.stderr" --grid 4x2 --band-min 0.5
pel_maps_check no-maps "$PEL_OUTPUT_ROOT/maps-off.stderr" --grid 8x4
pel_maps_check maps "$PEL_OUTPUT_ROOT/maps-one-cell.stderr" --grid 1x1 --band-min 0.5
pel_maps_check maps "$PEL_OUTPUT_ROOT/maps-partial.stderr" --grid 4x3 --band-min 0.5
pel_maps_check maps "$PEL_OUTPUT_ROOT/maps-max-grid.stderr" --grid 1024x1024
echo 'PASS: analyzer per-cell maps'

# A representative active transform must produce equivalent normalized luma
# across the same layouts. The static family checker covers every arithmetic
# shader; this runtime row catches representation mistakes on real hardware.
for PEL_FORMAT in yuv420p yuv420p10le p010le yuv420p12le p012le; do
    pel_emit_raw "transform-${PEL_FORMAT}" "$PEL_FORMAT" \
        'testsrc2=size=96x64:rate=1:duration=1' \
        'pelorus_deblock_vulkan=bsize=8:edge=2:thr=0.10:str=0.8:planes=1' 1
done

python3 - "$PEL_OUTPUT_ROOT" <<'PY_TRANSFORM'
import array
import math
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
width, height = 96, 64

def luma(fmt, depth, shift):
    raw = (root / f"transform-{fmt}.raw").read_bytes()
    count = width * height
    if depth == 8:
        values = raw[:count]
    else:
        words = array.array("H")
        words.frombytes(raw[: count * 2])
        if sys.byteorder != "little":
            words.byteswap()
        if shift:
            padding_mask = (1 << shift) - 1
            bad_padding = sum(bool(value & padding_mask) for value in words)
            if bad_padding:
                raise SystemExit(
                    f"transform {fmt}: {bad_padding}/{len(words)} luma samples "
                    "have non-zero low padding bits"
                )
        values = ((v >> shift) for v in words)
    scale = (1 << depth) - 1
    return [v / scale for v in values]

cases = {
    "yuv420p": (8, 0),
    "yuv420p10le": (10, 0),
    "p010le": (10, 6),
    "yuv420p12le": (12, 0),
    "p012le": (12, 4),
}
reference = luma("yuv420p", *cases["yuv420p"])
for fmt, params in tuple(cases.items())[1:]:
    actual = luma(fmt, *params)
    errors = [abs(a - b) for a, b in zip(reference, actual)]
    mae = sum(errors) / len(errors)
    maximum = max(errors)
    if not (math.isfinite(mae) and mae <= 0.010 and maximum <= 0.055):
        raise SystemExit(
            f"transform domain mismatch {fmt}: mae={mae:.6f} max={maximum:.6f}"
        )
print("PASS: transform logical-domain equivalence")
PY_TRANSFORM

# Uniform red makes U and V distinct and spatially constant. An active denoise
# must preserve it byte-for-byte while processing both components of plane 1.
for PEL_FORMAT in nv12 p010le p012le; do
    pel_emit_raw "uv-base-${PEL_FORMAT}" "$PEL_FORMAT" \
        'color=c=red:size=64x48:rate=1:duration=1' '' 1
    pel_emit_raw "uv-denoise-${PEL_FORMAT}" "$PEL_FORMAT" \
        'color=c=red:size=64x48:rate=1:duration=1' \
        'pelorus_denoise_vulkan=planes=3:prev=0:patch=1:strength=1:strengthc=1:protect=0' 1
    cmp -s "$PEL_OUTPUT_ROOT/uv-base-${PEL_FORMAT}.raw" \
        "$PEL_OUTPUT_ROOT/uv-denoise-${PEL_FORMAT}.raw" || \
        pel_fail "${PEL_FORMAT} U/V component preservation"
done

python3 - "$PEL_OUTPUT_ROOT" <<'PY_CHROMA'
import array
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
width, height = 64, 48
for fmt, bytes_per_sample, shift in (("nv12", 1, 0), ("p010le", 2, 6), ("p012le", 2, 4)):
    raw = (root / f"uv-denoise-{fmt}.raw").read_bytes()
    offset = width * height * bytes_per_sample
    chroma = raw[offset:]
    if bytes_per_sample == 1:
        values = list(chroma)
    else:
        words = array.array("H")
        words.frombytes(chroma)
        if sys.byteorder != "little":
            words.byteswap()
        values = [v >> shift for v in words]
    u = values[0::2]
    v = values[1::2]
    if not u or not v or sum(u) == 0 or sum(v) == 0 or sum(u) == sum(v):
        raise SystemExit(f"invalid or collapsed U/V components for {fmt}")
print("PASS: NV12/P010/P012 U/V survival")
PY_CHROMA

# Scalar kernels may own only the first component of a packed view. They must
# load the whole texel and preserve G/B/A instead of synthesizing vec4(value).
pel_emit_raw 'rgba-base' rgba 'color=c=red:size=64x48:rate=1:duration=1' '' 1
PEL_RGBA_FILTERS=(
    'pelorus_aa_vulkan=planes=1:depth=8:darkstr=0.3'
    'pelorus_dehalo_vulkan=planes=1:darkstr=1:brightstr=1'
    'pelorus_deblock_vulkan=planes=1:str=1'
    'pelorus_denoise_vulkan=planes=1:prev=0:strength=1:protect=0'
)
PEL_RGBA_NAMES=(aa dehalo deblock denoise)
for PEL_INDEX in "${!PEL_RGBA_FILTERS[@]}"; do
    pel_emit_raw "rgba-${PEL_RGBA_NAMES[$PEL_INDEX]}" rgba \
        'color=c=red:size=64x48:rate=1:duration=1' \
        "${PEL_RGBA_FILTERS[$PEL_INDEX]}" 1
    cmp -s "$PEL_OUTPUT_ROOT/rgba-base.raw" \
        "$PEL_OUTPUT_ROOT/rgba-${PEL_RGBA_NAMES[$PEL_INDEX]}.raw" || \
        pel_fail "RGBA lane preservation in ${PEL_RGBA_NAMES[$PEL_INDEX]}"
done
echo 'PASS: packed RGBA lane preservation'

# planes=1 must alter luma on an edged image without touching chroma.
pel_emit_raw 'planes-base' yuv420p \
    'testsrc2=size=96x64:rate=1:duration=1' '' 1
pel_emit_raw 'planes-aa' yuv420p \
    'testsrc2=size=96x64:rate=1:duration=1' \
    'pelorus_aa_vulkan=planes=1:depth=12:darkstr=0.5:edge=0.02' 1
python3 - "$PEL_OUTPUT_ROOT" <<'PY_PLANES'
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
width, height = 96, 64
y_size = width * height
baseline = (root / "planes-base.raw").read_bytes()
actual = (root / "planes-aa.raw").read_bytes()
if baseline[:y_size] == actual[:y_size]:
    raise SystemExit("selected luma plane was not transformed")
if baseline[y_size:] != actual[y_size:]:
    raise SystemExit("unselected chroma planes changed")
print("PASS: selected and pass-through plane masks")
PY_PLANES

# Direct and tiled denoise must be bit-identical. Then exercise the delayed
# lookahead cadence and the MC side-data consumer rather than compile-only paths.
PEL_NOISY_SOURCE='testsrc2=size=96x64:rate=4:duration=1,noise=alls=12:allf=t+u:all_seed=123'
PEL_DENOISE_BASE='pelorus_denoise_vulkan=prev=2:patch=2:strength=0.7:protect=1'
pel_emit_raw 'denoise-direct' yuv420p "$PEL_NOISY_SOURCE" "${PEL_DENOISE_BASE}:tile=0" 4
pel_emit_raw 'denoise-tiled' yuv420p "$PEL_NOISY_SOURCE" "${PEL_DENOISE_BASE}:tile=1" 4
cmp -s "$PEL_OUTPUT_ROOT/denoise-direct.raw" \
    "$PEL_OUTPUT_ROOT/denoise-tiled.raw" || \
    pel_fail 'direct/tiled denoise mismatch'

# BUG-027: tile=0 and tile=1 are separately specialized pipelines, so a driver
# may optimize their arithmetic differently. Check bit-identity at 8/10/12-bit,
# on semi-planar layouts and with motion-compensated taps, using the 320x192
# configuration that exposed a 1-code drift on NVIDIA.
PEL_TILE_SOURCE='testsrc2=size=320x192:rate=6:duration=1,noise=alls=14:allf=t+u:all_seed=11'
PEL_TILE_DENOISE='pelorus_denoise_vulkan=prev=2:patch=3:sigma=0.1:strength=0.9'
for PEL_TILE_CASE in yuv420p yuv420p10le p010le yuv420p12le p012le \
    mc-yuv420p mc-p010le; do
    PEL_TILE_FORMAT="${PEL_TILE_CASE#mc-}"
    PEL_TILE_FILTER="$PEL_TILE_DENOISE"
    if [[ "$PEL_TILE_CASE" == mc-* ]]; then
        PEL_TILE_FILTER="pelorus_mc_vulkan=meta=1,${PEL_TILE_DENOISE}:mc=1:lookahead=1"
    fi
    for PEL_TILE in 0 1; do
        pel_emit_raw "denoise-tile${PEL_TILE}-${PEL_TILE_CASE}" "$PEL_TILE_FORMAT" \
            "$PEL_TILE_SOURCE" "${PEL_TILE_FILTER}:tile=${PEL_TILE}" 6
    done
    cmp -s "$PEL_OUTPUT_ROOT/denoise-tile0-${PEL_TILE_CASE}.raw" \
        "$PEL_OUTPUT_ROOT/denoise-tile1-${PEL_TILE_CASE}.raw" || \
        pel_fail "direct/tiled denoise mismatch: ${PEL_TILE_CASE}"
done
echo 'PASS: direct/tiled denoise equivalence'

pel_emit_raw 'denoise-lookahead' yuv420p "$PEL_NOISY_SOURCE" \
    "${PEL_DENOISE_BASE}:lookahead=1" 4
pel_emit_raw 'denoise-mc-fallback' yuv420p "$PEL_NOISY_SOURCE" \
    'pelorus_denoise_vulkan=prev=2:mc=1:strength=0.7' 4
pel_emit_raw 'denoise-mc' yuv420p "$PEL_NOISY_SOURCE" \
    'pelorus_mc_vulkan=meta=1,pelorus_denoise_vulkan=prev=2:mc=1:strength=0.7' 4

python3 - "$PEL_OUTPUT_ROOT" <<'PY_CADENCE'
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
expected = 96 * 64 * 3 // 2 * 4
for name in (
    "denoise-lookahead.raw",
    "denoise-mc-fallback.raw",
    "denoise-mc.raw",
):
    size = (root / name).stat().st_size
    if size != expected:
        raise SystemExit(f"{name}: expected {expected} bytes, got {size}")
if (root / "denoise-mc-fallback.raw").read_bytes() == (
    root / "denoise-mc.raw"
).read_bytes():
    raise SystemExit("MC side data did not change denoise output")
print("PASS: lookahead and MC runtime paths")
PY_CADENCE

# ADR-0163: the dehalo gate must reach the halo ring and leave line-art alone.
# Columns are constant down the frame: a 1-px dark stroke (its symmetric core
# reads a zero Sobel) and a Lanczos-ringed 3-px dark line whose outer overshoot
# lobes sit above the 191 fill. The pre-ADR-0163 gate rewrote both line cores
# (stroke 30 -> 159) and never reached the overshoot. Direct and tiled output
# must also stay bit-identical (ADR-0139).
python3 - "$PEL_OUTPUT_ROOT/dehalo-src.yuv" <<'PY_DEHALO_SRC'
import sys

width, height, fill = 96, 64, 191
row = [fill] * width
row[20] = 30
ringed = [189, 195, 196, 181, 148, 105, 78, 104, 166, 203, 198, 188, 190]
row[44:44 + len(ringed)] = ringed
with open(sys.argv[1], "wb") as out:
    out.write(bytes(row) * height)
    out.write(bytes([128]) * (width * height // 2))
PY_DEHALO_SRC
for PEL_TILE in 0 1; do
    pel_run "dehalo-ring-tile${PEL_TILE}" \
        -f rawvideo -pix_fmt yuv420p -s 96x64 \
        -i "$PEL_OUTPUT_ROOT/dehalo-src.yuv" -frames:v 1 \
        -vf "format=yuv420p,hwupload,pelorus_dehalo_vulkan=tile=${PEL_TILE},hwdownload,format=yuv420p" \
        -f rawvideo "$PEL_OUTPUT_ROOT/dehalo-ring-tile${PEL_TILE}.raw"
done
cmp -s "$PEL_OUTPUT_ROOT/dehalo-ring-tile0.raw" \
    "$PEL_OUTPUT_ROOT/dehalo-ring-tile1.raw" || \
    pel_fail 'direct/tiled dehalo mismatch'
python3 - "$PEL_OUTPUT_ROOT" <<'PY_DEHALO'
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
width, height, fill = 96, 64, 191
src = (root / "dehalo-src.yuv").read_bytes()
out = (root / "dehalo-ring-tile0.raw").read_bytes()
if len(out) != len(src):
    raise SystemExit(f"dehalo output size {len(out)} != {len(src)}")
if out[width * height:] != src[width * height:]:
    raise SystemExit("dehalo changed the unselected chroma planes")
line_art = [20] + list(range(47, 53))
halo = [45, 46, 56]
flat = list(range(0, 16)) + list(range(62, 96))
for y in range(4, height - 4):
    s = src[y * width:(y + 1) * width]
    o = out[y * width:(y + 1) * width]
    for x in line_art:
        if abs(o[x] - s[x]) > 1:
            raise SystemExit(f"dehalo rewrote line-art at ({x},{y}): {s[x]} -> {o[x]}")
    for x in flat:
        if o[x] != s[x]:
            raise SystemExit(f"dehalo changed a flat pixel at ({x},{y}): {s[x]} -> {o[x]}")
    before = sum(max(0, s[x] - fill) for x in halo)
    after = sum(max(0, o[x] - fill) for x in halo)
    if after * 2 > before:
        raise SystemExit(f"dehalo left the ring overshoot at row {y}: {before} -> {after}")
print("PASS: dehalo ring gate removes overshoot and keeps line-art")
PY_DEHALO

if [[ -f "$PEL_OUTPUT_ROOT/validation-known-upstream.txt" ]]; then
    sort -u "$PEL_OUTPUT_ROOT/validation-known-upstream.txt" \
        -o "$PEL_OUTPUT_ROOT/validation-known-upstream.txt"
fi

echo "PASS: ADR-0147 Vulkan format matrix"
echo "Evidence: $PEL_OUTPUT_ROOT"
