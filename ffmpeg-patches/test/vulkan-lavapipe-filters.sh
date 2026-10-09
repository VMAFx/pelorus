#!/usr/bin/env bash
#
# vulkan-lavapipe-filters.sh — run all ten Pelorus filters on the Vulkan device
# the environment selects (the lavapipe lane: VK_DRIVER_FILES = lavapipe ICD)
# with the Khronos validation layer on; any VUID or non-zero exit fails.
#
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
#
# Nine filters are Vulkan compute filters and run at two sizes. pelorus_scenecut
# is metadata-only on software frames and never touches Vulkan, so it is run for
# registration and exit status and says nothing about the device.
#
# Env:
#   FFMPEG_BIN   patched FFmpeg binary (default: ffmpeg)
# Usage: vulkan-lavapipe-filters.sh [--self-test]
#   --self-test runs this script against a fake ffmpeg that plants each defect.
set -euo pipefail

VULKAN_FILTERS=(
    pelorus_deband_vulkan
    pelorus_analyze_vulkan
    pelorus_denoise_vulkan
    pelorus_grain_estimate_vulkan
    pelorus_mc_vulkan
    pelorus_dehalo_vulkan
    pelorus_aa_vulkan
    pelorus_deblock_vulkan
    pelorus_borderfix_vulkan
)
LAYER=VK_LAYER_KHRONOS_validation

fail()
{
    echo "FAIL: $*" >&2
    exit 1
}

run_lane()
{
    local ffmpeg_bin="$1"
    local failed=0
    local filter size frames out rc vuids

    command -v "$ffmpeg_bin" >/dev/null 2>&1 || fail "FFMPEG_BIN is not executable: $ffmpeg_bin"

    # The layer must really be inserted: a lane that silently ran without it
    # would report "clean" for free.
    out="$(VK_LOADER_DEBUG=layer VK_INSTANCE_LAYERS=$LAYER "$ffmpeg_bin" -hide_banner \
        -loglevel warning -init_hw_device vulkan=pelorus_vk \
        -f lavfi -i color=size=16x16:duration=1 -frames:v 1 -f null - 2>&1)" ||
        fail "device probe failed: $(tail -3 <<<"$out")"
    grep -q "Inserted device layer \"$LAYER\"" <<<"$out" ||
        fail "$LAYER was not inserted into the device; validation did not run"

    for filter in "${VULKAN_FILTERS[@]}"; do
        for size in 320x180:5 1280x720:3; do
            frames="${size#*:}"
            size="${size%:*}"
            rc=0
            out="$(VK_INSTANCE_LAYERS=$LAYER "$ffmpeg_bin" -hide_banner -loglevel warning \
                -init_hw_device vulkan=pelorus_vk -filter_hw_device pelorus_vk \
                -f lavfi -i "testsrc2=size=$size" -frames:v "$frames" \
                -vf "format=yuv420p,hwupload,$filter,hwdownload,format=yuv420p" \
                -f null - 2>&1)" || rc=$?
            vuids=""
            if grep -Eq 'VUID-' <<<"$out"; then
                vuids="$(grep -Eo 'VUID-[[:alnum:]_.-]+' <<<"$out" | sort -u | tr '\n' ' ')"
            fi
            if ((rc != 0)) || [[ -n "$vuids" ]]; then
                echo "FAIL: $filter $size exit=$rc VUIDs: ${vuids:-none}" >&2
                tail -5 <<<"$out" >&2
                failed=1
            else
                echo "PASS: $filter $size exit=0 VUIDs: 0"
            fi
        done
    done

    rc=0
    out="$("$ffmpeg_bin" -hide_banner -loglevel warning -f lavfi -i testsrc2=size=320x180 \
        -frames:v 5 -vf pelorus_scenecut -f null - 2>&1)" || rc=$?
    if ((rc != 0)); then
        echo "FAIL: pelorus_scenecut exit=$rc" >&2
        tail -5 <<<"$out" >&2
        failed=1
    else
        echo "PASS: pelorus_scenecut exit=0 (metadata-only on software frames; no Vulkan, no VUID check)"
    fi
    return "$failed"
}

self_test()
{
    local work failures=0 fake mode

    work="$(mktemp -d "${TMPDIR:-/tmp}/pelorus-lvp-filters.XXXXXX")"
    fake="$work/ffmpeg"
    cat >"$fake" <<'EOF_FAKE'
#!/usr/bin/env bash
args="$*"
if [[ -n "${VK_LOADER_DEBUG:-}" ]]; then
    [[ "$FAKE_MODE" == nolayer ]] || echo 'Inserted device layer "VK_LAYER_KHRONOS_validation" (x)'
    exit 0
fi
if [[ "$args" == *pelorus_aa_vulkan* && "$args" == *1280x720* ]]; then
    case "$FAKE_MODE" in
        vuid) echo 'Validation Error: [ VUID-vkCmdDispatch-imageLayout-00344 ] planted' >&2 ;;
        exit1) exit 1 ;;
    esac
fi
exit 0
EOF_FAKE
    chmod +x "$fake"
    for mode in clean vuid exit1 nolayer; do
        if (FAKE_MODE="$mode" run_lane "$fake") >/dev/null 2>&1; then
            [[ "$mode" == clean ]] || {
                echo "SELF-TEST FAIL: planted defect '$mode' was accepted" >&2
                failures=$((failures + 1))
            }
        else
            [[ "$mode" != clean ]] || {
                echo "SELF-TEST FAIL: clean run was rejected" >&2
                failures=$((failures + 1))
            }
        fi
    done
    rm -rf -- "$work"
    if ((failures)); then
        echo "self-test: FAILED ($failures)" >&2
        return 1
    fi
    echo "self-test: ok"
}

case "${1:-}" in
    --self-test) self_test ;;
    '') run_lane "${FFMPEG_BIN:-ffmpeg}" ;;
    *) fail "usage: $0 [--self-test]" ;;
esac
