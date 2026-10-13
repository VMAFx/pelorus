#!/usr/bin/env bash
#
# nvenc-cuda-hop-smoke.sh — NVDEC->Vulkan->NVENC hop succeeds with derived
# Vulkan device (issue #296, ADR-0191).
#
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
#
# GPU test. Before deriving the Vulkan device from the decoder's CUDA device
# (ADR-0191), the Vulkan->CUDA hop created a second CUDA context. Stock
# FFmpeg's hwcontext_vulkan caches the CUDA import of a frame for the first
# context it meets, so cuWaitExternalSemaphoresAsync failed with
# CUDA_ERROR_INVALID_VALUE once a frame met the second context. Measured on
# that setup: pass-through graphs, two writing filters, filters that keep
# reference frames and a single stock gblur_vulkan failed; a single Pelorus
# writing filter passed.
#
# Cases:
#   9 chains with derived device (100 frames):
#     null
#     pelorus_analyze_vulkan
#     pelorus_mc_vulkan=meta=1
#     pelorus_scenecut
#     pelorus_denoise_vulkan=prev=3
#     pelorus_mc_vulkan=meta=1,pelorus_denoise_vulkan=prev=3:mc=1
#     pelorus_borderfix_vulkan=left=4:right=4:planes=0x1,pelorus_borderfix_vulkan=left=2:right=2:planes=0xE
#     pelorus_deband_vulkan,pelorus_deband_vulkan
#     pelorus_grain_estimate_vulkan=strength=2.0,pelorus_denoise_vulkan=strength=0.4
#   Boundary cases: deband-twice at 31, 32, 33 frames
#   Negative (--self-test): null and deband-twice with the separate device setup
#     must fail with CUDA_ERROR_INVALID_VALUE.
#
# Env:
#   FFMPEG_BIN     patched ffmpeg binary (required)
#   OUTPUT_ROOT    evidence directory (default: new directory under TMPDIR)
#   PEL_HOP_SETUP  device setup: "derived" (default) or "separate" (the old failing setup)
#
# Exit: 0 pass, 1 fail, 77 skip (the reason is printed on stdout).
set -euo pipefail

PEL_FFMPEG="${FFMPEG_BIN:?FFMPEG_BIN must name the patched ffmpeg binary}"
PEL_OUT="${OUTPUT_ROOT:-$(mktemp -d "${TMPDIR:-/tmp}/pelorus-nvenc-cuda-hop.XXXXXX")}"
mkdir -p "$PEL_OUT"

PEL_SETUP="${PEL_HOP_SETUP:-derived}"
if [[ "$PEL_SETUP" != "derived" && "$PEL_SETUP" != "separate" ]]; then
    echo "FAIL: unknown PEL_HOP_SETUP '$PEL_SETUP': must be 'derived' or 'separate'" >&2
    exit 1
fi

pel_skip()
{
    echo "SKIP: $*"
    exit 77
}

pel_fail()
{
    echo "FAIL: $*" >&2
    echo "Evidence: $PEL_OUT" >&2
    exit 1
}

pel_first_error()
{
    awk 'tolower($0) ~ /error|failed/ { print; exit }' "$1"
}

pel_first_cuda_error()
{
    awk '/CUDA_ERROR/ { print; exit }' "$1"
}

pel_encoded_frames()
{
    awk '/frames encoded/ { for (i=1; i<=NF; i++) if ($i == "frames" && $(i+1) ~ /^encoded/) { print $(i-1); exit } }' "$1"
}

[[ -x "$PEL_FFMPEG" ]] || pel_fail "FFMPEG_BIN is not executable: $PEL_FFMPEG"

"$PEL_FFMPEG" -hide_banner -encoders >"$PEL_OUT/encoders.txt" 2>&1 ||
    pel_fail "ffmpeg -encoders failed"
"$PEL_FFMPEG" -hide_banner -filters >"$PEL_OUT/filters.txt" 2>&1 ||
    pel_fail "ffmpeg -filters failed"

grep -Eq '^ V[^ ]* hevc_nvenc ' "$PEL_OUT/encoders.txt" ||
    pel_skip "hevc_nvenc is not built into $PEL_FFMPEG (no ffnvcodec)"

for filter in pelorus_analyze_vulkan pelorus_mc_vulkan pelorus_scenecut \
    pelorus_denoise_vulkan pelorus_borderfix_vulkan pelorus_deband_vulkan \
    pelorus_grain_estimate_vulkan; do
    if ! grep -Eq " ${filter} " "$PEL_OUT/filters.txt"; then
        pel_skip "$filter is not built into $PEL_FFMPEG"
    fi
done

if ! "$PEL_FFMPEG" -nostdin -hide_banner -loglevel error -f lavfi -i testsrc2=size=256x64 \
    -frames:v 1 -c:v hevc_nvenc -f null - >"$PEL_OUT/probe-nvenc.log" 2>&1 < /dev/null; then
    pel_skip "hevc_nvenc cannot encode on this host: $(head -c 160 "$PEL_OUT/probe-nvenc.log" | head -n 1)"
fi

if ! "$PEL_FFMPEG" -nostdin -hide_banner -loglevel error -init_hw_device cuda=cu:0 \
    -init_hw_device vulkan=vk@cu -filter_hw_device vk -f lavfi -i testsrc2=size=64x64 \
    -frames:v 1 -f null - >"$PEL_OUT/probe-devices.log" 2>&1 < /dev/null; then
    pel_skip "no usable CUDA or Vulkan device: $(head -c 160 "$PEL_OUT/probe-devices.log" | head -n 1)"
fi

PEL_INPUT="$PEL_OUT/input.mkv"
if [[ ! -f "$PEL_INPUT" ]]; then
    if ! "$PEL_FFMPEG" -nostdin -hide_banner -loglevel error -y \
        -f lavfi -i testsrc2=size=1280x720:rate=30 -frames:v 100 \
        -c:v hevc_nvenc "$PEL_INPUT" >"$PEL_OUT/input-gen.log" 2>&1 < /dev/null; then
        pel_fail "input clip generation failed: $(pel_first_error "$PEL_OUT/input-gen.log")"
    fi
fi

self_test()
{
    local -a test_chains=(
        "null|null"
        "deband-twice|pelorus_deband_vulkan,pelorus_deband_vulkan"
    )
    local entry label chain log status cuda_err

    for entry in "${test_chains[@]}"; do
        label="${entry%%|*}"
        chain="${entry#*|}"
        log="$PEL_OUT/self-test-$label.log"
        status=0

        timeout -k 5 120 "$PEL_FFMPEG" -nostdin -hide_banner -v verbose -y \
            -init_hw_device "vulkan=vk:0,disable_multiplane=1" \
            -filter_hw_device vk \
            -hwaccel cuda \
            -hwaccel_output_format cuda \
            -i "$PEL_INPUT" \
            -vf "hwupload,$chain,hwupload=derive_device=cuda" \
            -c:v hevc_nvenc -f null - >"$log" 2>&1 < /dev/null || status=$?

        cuda_err="$(pel_first_cuda_error "$log")"

        if (( status == 0 )) && [[ -z "$cuda_err" ]]; then
            echo "FAIL: self-test $chain unexpectedly passed with separate setup (upstream fix landed? then revisit)" >&2
            echo "Evidence: $PEL_OUT" >&2
            exit 1
        fi

        if (( status != 0 )) && [[ "$cuda_err" == *"CUDA_ERROR_INVALID_VALUE"* ]]; then
            echo "PASS self-test: $chain failed as expected with CUDA_ERROR_INVALID_VALUE"
        else
            echo "FAIL: self-test $chain did not fail with CUDA_ERROR_INVALID_VALUE (exit status $status): ${cuda_err:-$(pel_first_error "$log")}" >&2
            echo "Evidence: $PEL_OUT" >&2
            exit 1
        fi
    done
    echo "OK: self-test passed (2 expected CUDA_ERROR_INVALID_VALUE failures observed)"
    exit 0
}

if [[ "${1:-}" == "--self-test" ]]; then
    self_test
fi

run_case()
{
    local name="$1" chain="$2" frames="$3"
    local log="$PEL_OUT/$name.log"
    local status=0
    local -a dev_args
    if [[ "$PEL_SETUP" == "derived" ]]; then
        dev_args=(
            -init_hw_device cuda=cu:0
            -init_hw_device vulkan=vk@cu
            -filter_hw_device vk
            -hwaccel cuda
            -hwaccel_device cu
            -hwaccel_output_format cuda
        )
    elif [[ "$PEL_SETUP" == "separate" ]]; then
        dev_args=(
            -init_hw_device "vulkan=vk:0,disable_multiplane=1"
            -filter_hw_device vk
            -hwaccel cuda
            -hwaccel_output_format cuda
        )
    else
        pel_fail "unknown PEL_HOP_SETUP '$PEL_SETUP': must be 'derived' or 'separate'"
    fi

    local -a frame_args=()
    if [[ -n "$frames" && "$frames" -gt 0 ]]; then
        frame_args=(-frames:v "$frames")
    fi

    timeout -k 5 120 "$PEL_FFMPEG" -nostdin -hide_banner -v verbose -y \
        "${dev_args[@]}" \
        -i "$PEL_INPUT" \
        "${frame_args[@]}" \
        -vf "hwupload,$chain,hwupload=derive_device=cuda" \
        -c:v hevc_nvenc -f null - >"$log" 2>&1 < /dev/null || status=$?

    local cuda_err
    cuda_err="$(pel_first_cuda_error "$log")"

    if (( status != 0 )) || [[ -n "$cuda_err" ]]; then
        local err_line="${cuda_err:-$(pel_first_error "$log")}"
        pel_fail "$chain: ${err_line:-exit status $status}"
    fi

    local encoded
    encoded="$(pel_encoded_frames "$log")"
    local expected="${frames:-100}"
    if [[ -z "$expected" || "$expected" -le 0 ]]; then
        expected=100
    fi
    if [[ "$encoded" != "$expected" ]]; then
        pel_fail "$chain: $encoded of $expected frames encoded"
    fi

    echo "PASS $chain: $encoded of $expected frames, no CUDA error"
}

# The 9 chains measured for issue #296
declare -a chains=(
    "null|null"
    "pelorus_analyze_vulkan|pelorus_analyze_vulkan"
    "pelorus_mc_vulkan|pelorus_mc_vulkan=meta=1"
    "pelorus_scenecut|pelorus_scenecut"
    "pelorus_denoise_vulkan|pelorus_denoise_vulkan=prev=3"
    "pelorus_mc_denoise|pelorus_mc_vulkan=meta=1,pelorus_denoise_vulkan=prev=3:mc=1"
    "pelorus_borderfix_twice|pelorus_borderfix_vulkan=left=4:right=4:planes=0x1,pelorus_borderfix_vulkan=left=2:right=2:planes=0xE"
    "pelorus_deband_twice|pelorus_deband_vulkan,pelorus_deband_vulkan"
    "pelorus_grain_estimate_denoise|pelorus_grain_estimate_vulkan=strength=2.0,pelorus_denoise_vulkan=strength=0.4"
)

for entry in "${chains[@]}"; do
    c_name="${entry%%|*}"
    c_chain="${entry#*|}"
    run_case "$c_name" "$c_chain" 100
done

# Boundary: deband-twice chain also at 31, 32, 33 frames
run_case "pelorus_deband_twice_31" "pelorus_deband_vulkan,pelorus_deband_vulkan" 31
run_case "pelorus_deband_twice_32" "pelorus_deband_vulkan,pelorus_deband_vulkan" 32
run_case "pelorus_deband_twice_33" "pelorus_deband_vulkan,pelorus_deband_vulkan" 33
