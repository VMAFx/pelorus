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

pel_check_validation()
{
    local log="$1"
    local vuid

    ((PEL_VALIDATION_ENABLED)) || return 0
    while IFS= read -r vuid; do
        [[ -n "$vuid" ]] || continue
        case "$vuid" in
            *vkCmdCopyBufferToImage-srcBuffer-00174 | \
                *vkCmdCopyImageToBuffer-dstBuffer-00191 | \
                *VkDescriptorSetLayoutBinding-descriptorType-00282 | \
                *VkImageMemoryBarrier2-srcAccessMask-03909 | \
                *VkImageMemoryBarrier2-srcAccessMask-07454)
                echo "$vuid" >>"$PEL_OUTPUT_ROOT/validation-known-upstream.txt"
                ;;
            *)
                echo "Unexpected validation diagnostic in $log: $vuid" >&2
                return 1
                ;;
        esac
    done < <(grep -Eo 'VUID-[[:alnum:]_.-]+' "$log" | sort -u || true)
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
    pel_check_validation "$stderr" || pel_fail "$label emitted a new Vulkan VUID"
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
pel_check_validation "$PEL_OUTPUT_ROOT/device-probe.stderr" || \
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
echo 'PASS: direct/tiled denoise equivalence'

pel_emit_raw 'denoise-lookahead' yuv420p "$PEL_NOISY_SOURCE" \
    "${PEL_DENOISE_BASE}:lookahead=1" 4
pel_emit_raw 'denoise-mc' yuv420p "$PEL_NOISY_SOURCE" \
    'pelorus_mc_vulkan=meta=1,pelorus_denoise_vulkan=prev=2:mc=1:strength=0.7' 4

python3 - "$PEL_OUTPUT_ROOT" <<'PY_CADENCE'
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
expected = 96 * 64 * 3 // 2 * 4
for name in ("denoise-lookahead.raw", "denoise-mc.raw"):
    size = (root / name).stat().st_size
    if size != expected:
        raise SystemExit(f"{name}: expected {expected} bytes, got {size}")
print("PASS: lookahead and MC runtime paths")
PY_CADENCE

if [[ -f "$PEL_OUTPUT_ROOT/validation-known-upstream.txt" ]]; then
    sort -u "$PEL_OUTPUT_ROOT/validation-known-upstream.txt" \
        -o "$PEL_OUTPUT_ROOT/validation-known-upstream.txt"
fi

echo "PASS: ADR-0147 Vulkan format matrix"
echo "Evidence: $PEL_OUTPUT_ROOT"
