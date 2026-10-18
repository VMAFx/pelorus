#!/usr/bin/env bash
#
# fgs-sidedata-smoke.sh — pelorus_fgs source=sidedata turns the grain
# estimator's PEL_SEC_FILMGRAIN, carried in the encoder's user data SEI, into
# one H.274 film grain characteristics SEI per access unit (issue #82,
# ADR-0206).
#
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
#
# GPU test (the estimator runs on Vulkan). Cases:
# The encoder is hevc_nvenc when it can encode on this host, else libx265
# (GPL builds) after a probe shows it keeps the Pelorus SEI: every libx265
# since 2.1 writes its own UUID in front of the caller's payload (measured
# 2026-10-18 on builds 2.1, 3.0, master b81f650e2 and 4.3), so a libx265 that
# wraps or drops the SEI skips by name.
#   grain        noisy 8-bit source -> estimator -> encoder -udu_sei 1 ->
#                pelorus_fgs=source=sidedata: every access unit carries an
#                active FGC SEI, the Pelorus SEI stays, and the decoder exports
#                film grain parameters for every picture.
#   h274         the estimator with model=h274: the H.274-tagged section
#                gives the same active FGC SEI on every access unit.
#   cancel       noisy pictures, then flat ones (the estimator clears apply):
#                one FGC with the cancel flag follows the last active one.
#   no-section   the encoder without the estimator: no FGC SEI is written. This is
#                also the planted case for the counters of the grain case.
#   10-bit       the estimator's output encoded as Main 10: the filter refuses
#                the stream (8-bit only) and ffmpeg exits non-zero.
#   options      default source=options with no component: the stream passes
#                through byte for byte.
#   nvenc        hevc_nvenc writes the zero-free carrier form (ADR-0183) and
#                every access unit still gets an FGC SEI; covered by the cases
#                above when hevc_nvenc is the encoder, run on its own when
#                libx265 is.
#
# Usage: fgs-sidedata-smoke.sh [--self-test] [--disable RULE]
#   RULE is one of: kept wrapped
#   (--disable switches one rule off; the self-test must then fail).
#
# Env:
#   FFMPEG_BIN     patched ffmpeg binary (required)
#   VULKAN_DEVICE  FFmpeg Vulkan device index for the estimator (optional)
#   OUTPUT_ROOT    evidence directory (default: new directory under TMPDIR)
#   FRAMES         pictures per case (default 8, 4..64)
# Exit: 0 pass, 1 fail, 77 skip (the reason is printed on stdout).
set -euo pipefail

DISABLED=" "
SELF_TEST=0
while (($#)); do
    case "$1" in
        --self-test) SELF_TEST=1 ;;
        --disable)
            shift
            DISABLED+="${1:?--disable needs a rule} "
            ;;
        *)
            echo "usage: $0 [--self-test] [--disable RULE]" >&2
            exit 2
            ;;
    esac
    shift
done

rule_on()
{
    [[ "$DISABLED" != *" $1 "* ]]
}

PEL_UUID="e1d7c4a2-6b93-4f08-9a55-0f3c2db17e64"
PEL_CARRIER_UUID="3f9b37b8-fd9a-4621-920e-9b78b55cf9b5"
PEL_X265_UUID="2ca2de09-b517-47db-bb55-a4fe7fc2fc4e"
PEL_HEX="${PEL_UUID//-/}"
PEL_CARRIER_HEX="${PEL_CARRIER_UUID//-/}"

pel_x265_sei_state()
{
    local log="$1"
    local r_kept=0 r_wrapped=0
    if rule_on kept; then
        r_kept=1
    fi
    if rule_on wrapped; then
        r_wrapped=1
    fi

    awk -v r_kept="$r_kept" \
        -v r_wrapped="$r_wrapped" \
        -v u_pel="UUID=$PEL_UUID" \
        -v u_carrier="UUID=$PEL_CARRIER_UUID" \
        -v u_x265="UUID=$PEL_X265_UUID" \
        -v h_pel="$PEL_HEX" \
        -v h_carrier="$PEL_CARRIER_HEX" '
        BEGIN {
            state = "missing"
            saw_x265 = 0
        }
        {
            if (r_kept && (index($0, u_pel) || index($0, u_carrier))) {
                state = "kept"
                exit
            }
            if (r_wrapped && saw_x265) {
                pos = index($0, "User Data=")
                if (pos > 0) {
                    val = tolower(substr($0, pos + 10))
                    sub(/^[[:space:]]+/, "", val)
                    if (index(val, h_pel) == 1 || index(val, h_carrier) == 1) {
                        state = "wrapped"
                    }
                }
            }
            saw_x265 = (index($0, u_x265) > 0) ? 1 : 0
        }
        END {
            print state
        }' "$log"
}

fixture_kept_raw()
{
    cat <<'EOT'
[Parsed_showinfo_0 @ 0x1] side data - User Data Unregistered SEI message: UUID=e1d7c4a2-6b93-4f08-9a55-0f3c2db17e64
[Parsed_showinfo_0 @ 0x1] User Data=000102030405
EOT
}

fixture_kept_carrier()
{
    cat <<'EOT'
[Parsed_showinfo_0 @ 0x1] side data - User Data Unregistered SEI message: UUID=3f9b37b8-fd9a-4621-920e-9b78b55cf9b5
[Parsed_showinfo_0 @ 0x1] User Data=000102030405
EOT
}

fixture_wrapped_raw()
{
    cat <<'EOT'
[Parsed_showinfo_0 @ 0x1] side data - User Data Unregistered SEI message: UUID=2ca2de09-b517-47db-bb55-a4fe7fc2fc4e
[Parsed_showinfo_0 @ 0x1] User Data=7832363520286275696c642032313729202d20342e33
[Parsed_showinfo_0 @ 0x1] side data - User Data Unregistered SEI message: UUID=2ca2de09-b517-47db-bb55-a4fe7fc2fc4e
[Parsed_showinfo_0 @ 0x1] User Data=e1d7c4a26b934f089a550f3c2db17e6400010203
[Parsed_showinfo_0 @ 0x1] side data - User Data Unregistered SEI message: UUID=2ca2de09-b517-47db-bb55-a4fe7fc2fc4e
[Parsed_showinfo_0 @ 0x1] User Data=e1d7c4a26b934f089a550f3c2db17e6400010203
EOT
}

fixture_wrapped_carrier()
{
    cat <<'EOT'
[Parsed_showinfo_0 @ 0x1] side data - User Data Unregistered SEI message: UUID=2ca2de09-b517-47db-bb55-a4fe7fc2fc4e
[Parsed_showinfo_0 @ 0x1] User Data=7832363520286275696c642032313729202d20342e33
[Parsed_showinfo_0 @ 0x1] side data - User Data Unregistered SEI message: UUID=2ca2de09-b517-47db-bb55-a4fe7fc2fc4e
[Parsed_showinfo_0 @ 0x1] User Data=3f9b37b8fd9a4621920e9b78b55cf9b500010203
[Parsed_showinfo_0 @ 0x1] side data - User Data Unregistered SEI message: UUID=2ca2de09-b517-47db-bb55-a4fe7fc2fc4e
[Parsed_showinfo_0 @ 0x1] User Data=3f9b37b8fd9a4621920e9b78b55cf9b500010203
EOT
}

fixture_x265_info_only()
{
    cat <<'EOT'
[Parsed_showinfo_0 @ 0x1] side data - User Data Unregistered SEI message: UUID=2ca2de09-b517-47db-bb55-a4fe7fc2fc4e
[Parsed_showinfo_0 @ 0x1] User Data=7832363520286275696c642032313729202d20342e33
EOT
}

fixture_unpreceded_pelorus_hex()
{
    cat <<'EOT'
[Parsed_showinfo_0 @ 0x1] side data - User Data Unregistered SEI message: UUID=11111111-2222-3333-4444-555555555555
[Parsed_showinfo_0 @ 0x1] User Data=e1d7c4a26b934f089a550f3c2db17e6400010203
EOT
}

fixture_empty()
{
    :
}

self_test()
{
    local work failures=0

    work="$(mktemp -d "${TMPDIR:-/tmp}/pelorus-fgs-self-test.XXXXXX")"
    trap 'rm -rf -- "$work"' EXIT

    expect_state()
    {
        local name="$1" fn="$2" want="$3" state
        "$fn" >"$work/log"
        state="$(pel_x265_sei_state "$work/log")"
        if [[ "$state" != "$want" ]]; then
            echo "SELF-TEST FAIL: $name: expected $want, got $state" >&2
            failures=$((failures + 1))
        fi
    }

    expect_state "kept raw" fixture_kept_raw "kept"
    expect_state "kept carrier" fixture_kept_carrier "kept"
    expect_state "wrapped raw" fixture_wrapped_raw "wrapped"
    expect_state "wrapped carrier" fixture_wrapped_carrier "wrapped"
    expect_state "x265 info SEI only" fixture_x265_info_only "missing"
    expect_state "unpreceded pelorus hex" fixture_unpreceded_pelorus_hex "missing"
    expect_state "empty log" fixture_empty "missing"

    rm -rf -- "$work"
    trap - EXIT

    if ((failures)); then
        echo "self-test: FAILED ($failures)" >&2
        return 1
    fi
    echo "self-test: ok"
}

if ((SELF_TEST)); then
    self_test || exit 1
    exit 0
fi

PEL_FFMPEG="${FFMPEG_BIN:?FFMPEG_BIN must name the patched ffmpeg binary}"
PEL_FRAMES="${FRAMES:-8}"
PEL_DEVICE="vulkan=pel_vk"
if [[ -n "${VULKAN_DEVICE:-}" ]]; then
    PEL_DEVICE+=":${VULKAN_DEVICE}"
fi
PEL_OUT="${OUTPUT_ROOT:-$(mktemp -d "${TMPDIR:-/tmp}/pelorus-fgs-sidedata.XXXXXX")}"
mkdir -p "$PEL_OUT"

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

# First error line of a log (empty when there is none).
pel_first_error()
{
    awk 'tolower($0) ~ /error|failed/ { print; exit }' "$1"
}

# Lines of a log that contain a fixed string.
pel_count()
{
    awk -v needle="$2" 'index($0, needle) { n++ } END { print n + 0 }' "$1"
}

[[ -x "$PEL_FFMPEG" ]] || pel_fail "FFMPEG_BIN is not executable: $PEL_FFMPEG"
if ! [[ "$PEL_FRAMES" =~ ^[0-9]+$ ]] || ((PEL_FRAMES < 4 || PEL_FRAMES > 64)); then
    pel_fail "FRAMES must be 4..64 (the cancel case needs two halves of two pictures)"
fi

"$PEL_FFMPEG" -hide_banner -encoders >"$PEL_OUT/encoders.txt" 2>&1 ||
    pel_fail "ffmpeg -encoders failed"
"$PEL_FFMPEG" -hide_banner -filters >"$PEL_OUT/filters.txt" 2>&1 ||
    pel_fail "ffmpeg -filters failed"
"$PEL_FFMPEG" -hide_banner -bsfs >"$PEL_OUT/bsfs.txt" 2>&1 || pel_fail "ffmpeg -bsfs failed"
grep -qx 'pelorus_fgs' "$PEL_OUT/bsfs.txt" || pel_fail "pelorus_fgs is not registered"

# The encoder comes first, so a host without one skips for that reason only.
PEL_X265=(-c:v libx265 -x265-params "bframes=0:log-level=error" -udu_sei 1)
PEL_NVENC=(-c:v hevc_nvenc -bf 0 -udu_sei 1)

pel_nvenc_ok=0
if grep -Eq '^ V[^ ]* hevc_nvenc ' "$PEL_OUT/encoders.txt" &&
    "$PEL_FFMPEG" -hide_banner -loglevel error -f lavfi -i testsrc2=size=320x192 \
        -frames:v 1 -c:v hevc_nvenc -f null - >"$PEL_OUT/probe-nvenc.log" 2>&1; then
    pel_nvenc_ok=1
fi
if ((pel_nvenc_ok)); then
    PEL_ENC=("${PEL_NVENC[@]}")
elif grep -Eq '^ V[^ ]* libx265 ' "$PEL_OUT/encoders.txt"; then
    PEL_ENC=("${PEL_X265[@]}")
else
    pel_skip "no HEVC encoder: neither libx265 nor a working hevc_nvenc in $PEL_FFMPEG"
fi
echo "encoder: ${PEL_ENC[1]}"

grep -Eq ' pelorus_grain_estimate_vulkan ' "$PEL_OUT/filters.txt" ||
    pel_skip "pelorus_grain_estimate_vulkan is not built into $PEL_FFMPEG (no Vulkan SPIR-V compiler)"
if ! "$PEL_FFMPEG" -hide_banner -loglevel error -init_hw_device "$PEL_DEVICE" \
    -f lavfi -i testsrc2=size=64x64 -frames:v 1 -f null - >"$PEL_OUT/probe-vulkan.log" 2>&1; then
    pel_skip "no usable Vulkan device ($PEL_DEVICE): $(head -c 160 "$PEL_OUT/probe-vulkan.log" | head -n 1)"
fi

PEL_NOISY="testsrc2=size=320x192:rate=25,format=yuv420p,noise=alls=24:allf=t"
PEL_FLAT="color=c=gray:size=320x192:rate=25,format=yuv420p"
PEL_EST="format=nv12,hwupload,pelorus_grain_estimate_vulkan,hwdownload,format=nv12"

# encode NAME FILTERGRAPH BSF ENCODER-ARGS...: one HEVC elementary stream.
# The graph has one output pad; the source is part of it.
pel_encode()
{
    local name="$1" graph="$2" bsf="$3"
    shift 3

    "$PEL_FFMPEG" -hide_banner -loglevel verbose -y -init_hw_device "$PEL_DEVICE" \
        -filter_hw_device pel_vk -filter_complex "$graph" -frames:v "$PEL_FRAMES" \
        "$@" -bsf:v "$bsf" -f hevc "$PEL_OUT/$name.hevc" >"$PEL_OUT/$name-encode.log" 2>&1
}

# trace NAME: the SEI syntax of every access unit, and the decoder's frame
# side data (film grain parameters exported instead of applied).
pel_trace()
{
    local name="$1"

    "$PEL_FFMPEG" -hide_banner -loglevel trace -i "$PEL_OUT/$name.hevc" -c:v copy \
        -bsf:v trace_headers -f null - >"$PEL_OUT/$name-trace.log" 2>&1 ||
        pel_fail "$name: trace_headers failed"
    "$PEL_FFMPEG" -hide_banner -loglevel info -export_side_data film_grain \
        -i "$PEL_OUT/$name.hevc" -vf showinfo -f null - >"$PEL_OUT/$name-tap.log" 2>&1 ||
        pel_fail "$name: decode failed"
}

# trace_headers pads its columns, so the flag value is the line's last field.
pel_flag()
{
    awk -v want="$2" '$0 ~ /film_grain_characteristics_cancel_flag / && $NF == want { n++ }
        END { print n + 0 }' "$PEL_OUT/$1-trace.log"
}
pel_active() { pel_flag "$1" 0; }
pel_cancel() { pel_flag "$1" 1; }
pel_grain_frames() { pel_count "$PEL_OUT/$1-tap.log" "side data - Film grain parameters"; }

# libx265 must keep the estimator's user data SEI, or no case can pass.
if [[ "${PEL_ENC[1]}" == libx265 ]]; then
    pel_encode x265-probe "$PEL_NOISY,$PEL_EST" null "${PEL_ENC[@]}" ||
        pel_fail "x265-probe: encode failed: $(pel_first_error "$PEL_OUT/x265-probe-encode.log")"
    pel_trace x265-probe
    case "$(pel_x265_sei_state "$PEL_OUT/x265-probe-tap.log")" in
        kept) ;;
        wrapped)
            pel_skip "libx265 writes its own user data UUID ($PEL_X265_UUID) in front of the Pelorus SEI (every x265 since 2.1), so pelorus_fgs finds no section, and hevc_nvenc cannot encode here"
            ;;
        *)
            pel_skip "libx265 drops the Pelorus user data SEI (-udu_sei 1) and hevc_nvenc cannot encode here"
            ;;
    esac
fi

# grain: every access unit carries an active FGC, the Pelorus SEI stays.
pel_encode grain "$PEL_NOISY,$PEL_EST" pelorus_fgs=source=sidedata "${PEL_ENC[@]}" ||
    pel_fail "grain: encode failed: $(pel_first_error "$PEL_OUT/grain-encode.log")"
pel_trace grain
[[ "$(pel_active grain)" == "$PEL_FRAMES" ]] ||
    pel_fail "grain: $(pel_active grain) active FGC SEI for $PEL_FRAMES access units"
[[ "$(pel_cancel grain)" == 0 ]] || pel_fail "grain: unexpected cancel FGC SEI"
[[ "$(pel_grain_frames grain)" == "$PEL_FRAMES" ]] ||
    pel_fail "grain: decoder exported grain for $(pel_grain_frames grain) of $PEL_FRAMES pictures"
pel_pelorus_sei=$(($(pel_count "$PEL_OUT/grain-tap.log" "UUID=$PEL_UUID") + \
    $(pel_count "$PEL_OUT/grain-tap.log" "UUID=$PEL_CARRIER_UUID")))
[[ "$pel_pelorus_sei" == "$PEL_FRAMES" ]] ||
    pel_fail "grain: the Pelorus SEI did not stay in every access unit"
echo "PASS grain: $PEL_FRAMES access units, one active FGC SEI each, Pelorus SEI kept"

# h274: the estimator tags its section with the H.274 model; the filter converts
# that tag the same way, so every access unit still carries an active FGC SEI.
pel_encode h274 "$PEL_NOISY,${PEL_EST/pelorus_grain_estimate_vulkan/pelorus_grain_estimate_vulkan=model=h274}" \
    pelorus_fgs=source=sidedata "${PEL_ENC[@]}" ||
    pel_fail "h274: encode failed: $(pel_first_error "$PEL_OUT/h274-encode.log")"
pel_trace h274
[[ "$(pel_active h274)" == "$PEL_FRAMES" ]] ||
    pel_fail "h274: $(pel_active h274) active FGC SEI for $PEL_FRAMES access units"
echo "PASS h274: $PEL_FRAMES access units with an H.274-tagged section, one active FGC SEI each"

# cancel: noisy half, then flat half. The flat pictures carry apply=0.
pel_half=$((PEL_FRAMES / 2))
pel_encode cancel \
    "$PEL_NOISY,trim=end_frame=${pel_half}[a];$PEL_FLAT,trim=end_frame=${pel_half}[b];[a][b]concat=n=2:v=1,$PEL_EST" \
    pelorus_fgs=source=sidedata "${PEL_ENC[@]}" ||
    pel_fail "cancel: encode failed: $(pel_first_error "$PEL_OUT/cancel-encode.log")"
pel_trace cancel
[[ "$(pel_cancel cancel)" == 1 ]] ||
    pel_fail "cancel: $(pel_cancel cancel) cancel FGC SEI (want 1)"
pel_active_n="$(pel_active cancel)"
((pel_active_n >= 1 && pel_active_n < PEL_FRAMES)) ||
    pel_fail "cancel: $pel_active_n active FGC SEI for $PEL_FRAMES access units"
awk '/film_grain_characteristics_cancel_flag / { last = $NF } END { exit last != 1 }' \
    "$PEL_OUT/cancel-trace.log" || pel_fail "cancel: an active FGC SEI follows the cancel"
echo "PASS cancel: $pel_active_n active FGC SEI, then one cancel"

# no-section: no estimator, so no Pelorus SEI and no FGC SEI. The counters of
# the grain case read 0 here, so they do not pass on any stream.
pel_encode no-section "$PEL_NOISY" pelorus_fgs=source=sidedata "${PEL_ENC[@]}" ||
    pel_fail "no-section: encode failed: $(pel_first_error "$PEL_OUT/no-section-encode.log")"
pel_trace no-section
[[ "$(pel_active no-section)" == 0 && "$(pel_cancel no-section)" == 0 ]] ||
    pel_fail "no-section: FGC SEI written without a Pelorus film grain section"
[[ "$(pel_grain_frames no-section)" == 0 ]] || pel_fail "no-section: decoder exported grain"
echo "PASS no-section: no FGC SEI, the grain counters read 0"

# 10-bit: refused, ffmpeg exits non-zero and names the limit.
if pel_encode ten-bit "$PEL_NOISY,$PEL_EST,format=yuv420p10le" pelorus_fgs=source=sidedata \
    "${PEL_ENC[@]}"; then
    pel_fail "10-bit: the filter accepted a Main 10 stream"
fi
grep -q '8-bit only' "$PEL_OUT/ten-bit-encode.log" ||
    pel_fail "10-bit: the log does not name the 8-bit limit: $(pel_first_error "$PEL_OUT/ten-bit-encode.log")"
echo "PASS 10-bit: refused with the 8-bit limit named"

# options: the default mode with no component is a byte-identical pass-through.
"$PEL_FFMPEG" -hide_banner -loglevel error -y -i "$PEL_OUT/grain.hevc" -c:v copy \
    -bsf:v pelorus_fgs=source=options -f hevc "$PEL_OUT/options.hevc" \
    >"$PEL_OUT/options.log" 2>&1 || pel_fail "options: pass-through failed"
"$PEL_FFMPEG" -hide_banner -loglevel error -y -i "$PEL_OUT/grain.hevc" -c:v copy \
    -f hevc "$PEL_OUT/copy.hevc" >"$PEL_OUT/copy.log" 2>&1 || pel_fail "options: copy failed"
cmp -s "$PEL_OUT/options.hevc" "$PEL_OUT/copy.hevc" ||
    pel_fail "options: source=options without components changed the stream"
echo "PASS options: byte-identical pass-through"

# nvenc: the carrier form reaches the filter too (already covered above when
# hevc_nvenc is the encoder).
if [[ "${PEL_ENC[1]}" == hevc_nvenc ]]; then
    echo "PASS nvenc: hevc_nvenc was the encoder of every case above"
elif ((pel_nvenc_ok)); then
    pel_encode nvenc "$PEL_NOISY,$PEL_EST" pelorus_fgs=source=sidedata "${PEL_NVENC[@]}" ||
        pel_fail "nvenc: encode failed: $(pel_first_error "$PEL_OUT/nvenc-encode.log")"
    pel_trace nvenc
    [[ "$(pel_count "$PEL_OUT/nvenc-tap.log" "UUID=$PEL_CARRIER_UUID")" == "$PEL_FRAMES" ]] ||
        pel_fail "nvenc: the stream does not carry the Pelorus carrier on every picture"
    [[ "$(pel_active nvenc)" == "$PEL_FRAMES" ]] ||
        pel_fail "nvenc: $(pel_active nvenc) active FGC SEI for $PEL_FRAMES access units"
    echo "PASS nvenc: carrier form, one active FGC SEI per access unit"
else
    echo "SKIP nvenc: hevc_nvenc cannot encode on this host"
fi

echo "fgs-sidedata smoke: PASS (evidence $PEL_OUT)"
