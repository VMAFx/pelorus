#!/usr/bin/env bash
#
# vulkan-lavapipe-filters.sh — run all ten Pelorus filters on the Vulkan device
# the environment selects (the lavapipe lane: VK_DRIVER_FILES = lavapipe ICD)
# with the Khronos validation layer on; any VUID or non-zero exit fails.
#
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
#
# Default mode: nine filters are Vulkan compute filters and run at two sizes.
# pelorus_scenecut is metadata-only on software frames and never touches
# Vulkan, so it is run for registration and exit status and says nothing about
# the device.
#
# --soak (issue #466): each config in SOAK_TABLE (the nine compute filters,
# denoise without its ring, the mc -> denoise side-data chain) runs a short
# (SOAK_WARMUP) and a long (SOAK_FRAMES) 320x180 sequence under validation. The denoise causal ring holds frame refs across
# calls, so a missed unref only shows after many frames. A config fails on:
#   - maxrss growth long - short > SOAK_RSS_MIB (on lavapipe device memory is
#     host memory, so a leaked frame grows RSS like a leaked host buffer);
#   - a stall past SOAK_TIMEOUT (frame or pool leak that blocks the graph);
#   - any VUID (the layer reports leaked Vulkan objects at device destroy);
#   - a frame count, last frame or per-frame side-data count that differs from
#     the config table (Pelorus UUID entries, AV1 film-grain entries).
# Heap leaks smaller than about SOAK_RSS_MIB / (SOAK_FRAMES - SOAK_WARMUP) per
# frame stay below RSS noise and need a LeakSanitizer build instead.
#
# Env:
#   FFMPEG_BIN       patched FFmpeg binary (default: ffmpeg)
#   SOAK_WARMUP      short run frames (default 300)
#   SOAK_FRAMES      long run frames (default 3000)
#   SOAK_RSS_MIB     allowed maxrss growth, MiB (default 16; no leading zero)
#   SOAK_TIMEOUT     deadline per ffmpeg run, seconds (default 600)
#   SOAK_KILL_AFTER  SIGKILL this many seconds after the deadline (default 30)
#   SOAK_CONFIGS     comma list of config names (default: all)
# Usage: vulkan-lavapipe-filters.sh [--self-test|--soak]
#   --self-test runs both modes against a fake ffmpeg that plants each defect.
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

# name|chain|Pelorus UUID entries per frame|AV1 film-grain entries per frame
# Counts measured with showinfo on the patched stack; 0 asserts no entry.
SOAK_TABLE=(
    "denoise_ring|pelorus_denoise_vulkan=prev=4:lookahead=1:meta=1|1|0"
    "mc_denoise|pelorus_mc_vulkan=meta=1,pelorus_denoise_vulkan=mc=1:meta=1|2|0"
    "denoise_prev0|pelorus_denoise_vulkan=prev=0:meta=1|1|0"
    "mc|pelorus_mc_vulkan=meta=1|1|0"
    "deband|pelorus_deband_vulkan=meta=1|1|0"
    "analyze|pelorus_analyze_vulkan|1|0"
    "grain_estimate|pelorus_grain_estimate_vulkan|1|1"
    "dehalo|pelorus_dehalo_vulkan|0|0"
    "aa|pelorus_aa_vulkan|0|0"
    "deblock|pelorus_deblock_vulkan|0|0"
    "borderfix|pelorus_borderfix_vulkan|0|0"
)
# Fixed strings: showinfo prints the SEI type literally as "H.26[45]".
UUID_LINE='User Data Unregistered SEI message: UUID=e1d7c4a2-6b93-4f08-9a55-0f3c2db17e64'
GRAIN_LINE='side data - Film grain parameters: type av1'

# Prints the reason on stderr and exits the script with status 1.
fail()
{
    echo "FAIL: $*" >&2
    exit 1
}

# Prints the distinct VUID names in ffmpeg output $1, space-separated; nothing
# when there are none.
vuids_of()
{
    if grep -Eq 'VUID-' <<<"$1"; then
        grep -Eo 'VUID-[[:alnum:]_.-]+' <<<"$1" | sort -u | tr '\n' ' '
    fi
}

# Line count of a grep pattern ($1 = -E or -F) in $3. grep -c exits 1 on zero
# matches, which is a count; exit 2 is a grep error and fails the caller.
count_lines()
{
    local n rc=0

    n="$(grep "$1" -c -- "$2" <<<"$3")" || rc=$?
    ((rc <= 1)) || { echo "grep $1 '$2' failed (exit $rc)"; return 1; }
    echo "$n"
}

# Prints the file bash runs for $1 (a PATH name or a path) and fails when it is
# not a regular executable file. command -v and type -P return a PATH match
# that is not executable with exit 0, so the file itself is tested.
exec_file()
{
    local path

    path="$(type -P -- "$1")" || return 1
    [[ -f "$path" && -x "$path" ]] || return 1
    echo "$path"
}

# Fails unless $1 is executable and one ffmpeg device probe really inserts the
# validation layer.
probe_layer()
{
    local ffmpeg_bin="$1"
    local out

    exec_file "$ffmpeg_bin" >/dev/null || fail "FFMPEG_BIN is not executable: $ffmpeg_bin"
    # The layer must really be inserted: a lane that silently ran without it
    # would report "clean" for free.
    out="$(VK_LOADER_DEBUG=layer VK_INSTANCE_LAYERS=$LAYER "$ffmpeg_bin" -hide_banner \
        -loglevel warning -init_hw_device vulkan=pelorus_vk \
        -f lavfi -i color=size=16x16:duration=1 -frames:v 1 -f null - 2>&1)" ||
        fail "device probe failed: $(tail -3 <<<"$out")"
    grep -q "Inserted device layer \"$LAYER\"" <<<"$out" ||
        fail "$LAYER was not inserted into the device; validation did not run"
}

# Default mode: each filter for a few frames at two sizes under the validation
# layer; returns 1 if any run exits non-zero or reports a VUID.
run_lane()
{
    local ffmpeg_bin="$1"
    local failed=0
    local filter size frames out rc vuids

    probe_layer "$ffmpeg_bin"
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
            vuids="$(vuids_of "$out")"
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

# Sets TIMEOUT_BIN and the SOAK_* defaults, and fails on a missing timeout or
# a malformed or inconsistent SOAK_* value.
soak_env()
{
    local var

    TIMEOUT_BIN="$(exec_file timeout)" ||
        fail "soak needs coreutils timeout for its per-run deadline; not found as an executable file on PATH"
    SOAK_WARMUP="${SOAK_WARMUP:-300}"
    SOAK_FRAMES="${SOAK_FRAMES:-3000}"
    SOAK_RSS_MIB="${SOAK_RSS_MIB:-16}"
    SOAK_TIMEOUT="${SOAK_TIMEOUT:-600}"
    SOAK_KILL_AFTER="${SOAK_KILL_AFTER:-30}"
    for var in SOAK_WARMUP SOAK_FRAMES SOAK_TIMEOUT SOAK_KILL_AFTER; do
        [[ "${!var}" =~ ^[1-9][0-9]{0,6}$ ]] || fail "$var must be a positive integer: ${!var}"
    done
    # No leading zero: bash arithmetic reads 010 as octal 8 and rejects 08.
    [[ "$SOAK_RSS_MIB" =~ ^(0|[1-9][0-9]{0,5})$ ]] || fail "SOAK_RSS_MIB must be a non-negative integer: $SOAK_RSS_MIB"
    ((SOAK_WARMUP < SOAK_FRAMES)) ||
        fail "SOAK_WARMUP ($SOAK_WARMUP) must be less than SOAK_FRAMES ($SOAK_FRAMES)"
}

# Prints the selected table rows, one per line; an unknown name fails.
soak_rows()
{
    local want row found
    local -a wanted

    if [[ -z "${SOAK_CONFIGS:-}" ]]; then
        printf '%s\n' "${SOAK_TABLE[@]}"
        return 0
    fi
    IFS=',' read -ra wanted <<<"$SOAK_CONFIGS"
    for want in "${wanted[@]}"; do
        found=""
        for row in "${SOAK_TABLE[@]}"; do
            [[ "${row%%|*}" == "$want" ]] && found="$row"
        done
        [[ -n "$found" ]] || fail "unknown soak config in SOAK_CONFIGS: $want"
        printf '%s\n' "$found"
    done
}

# Every showinfo frame in $1 must carry exactly $2 Pelorus UUID and $3
# film-grain side-data entries, so an omission on one frame cannot hide behind
# a duplicate on another. Prints "<uuid total> <grain total>", or the first
# frame that differs, and returns non-zero on a mismatch.
side_data_per_frame()
{
    PEL_UUID_LINE="$UUID_LINE" PEL_GRAIN_LINE="$GRAIN_LINE" awk -v want_u="$2" -v want_g="$3" '
        function check(   msg) {
            if (frame == "") return 0
            if (u != want_u + 0) msg = "Pelorus side data count " u " != " want_u
            else if (g != want_g + 0) msg = "film grain side data count " g " != " want_g
            else return 0
            print "frame n:" frame " " msg
            return 1
        }
        match($0, /n: *[0-9]+ pts:/) {
            if (check()) { failed = 1; exit 1 }
            frame = substr($0, RSTART, RLENGTH)
            gsub(/[^0-9]/, "", frame)
            u = 0
            g = 0
            next
        }
        index($0, ENVIRON["PEL_UUID_LINE"]) || index($0, ENVIRON["PEL_GRAIN_LINE"]) {
            if (frame == "") { print "side data before the first showinfo frame"; failed = 1; exit 1 }
        }
        index($0, ENVIRON["PEL_UUID_LINE"]) { u++; tu++ }
        index($0, ENVIRON["PEL_GRAIN_LINE"]) { g++; tg++ }
        END {
            if (failed) exit 1
            if (check()) exit 1
            print tu + 0, tg + 0
        }' <<<"$1"
}

# One ffmpeg run of N frames; prints "<maxrss KiB> <uuid entries> <grain entries>"
# on success, or the failure reason, and returns non-zero on failure.
soak_run()
{
    local ffmpeg_bin="$1" chain="$2" frames="$3" uuid_per="$4" grain_per="$5"
    local out rc=0 vuids n_frames side rss=""

    # trim bounds the source, so showinfo sees exactly N frames and the graph
    # drains at EOF (-frames:v alone lets one extra frame through the graph).
    # timeout runs ffmpeg in its own process group; -nostdin keeps ffmpeg off
    # the terminal there, where a tty read or setup would stop it (SIGTTIN/SIGTTOU).
    out="$(VK_INSTANCE_LAYERS=$LAYER "$TIMEOUT_BIN" -k "$SOAK_KILL_AFTER" "$SOAK_TIMEOUT" \
        "$ffmpeg_bin" -hide_banner -nostdin -benchmark -loglevel info \
        -init_hw_device vulkan=pelorus_vk -filter_hw_device pelorus_vk \
        -f lavfi -i testsrc2=size=320x180 -frames:v "$frames" \
        -vf "trim=end_frame=$frames,format=yuv420p,hwupload,$chain,hwdownload,format=yuv420p,showinfo" \
        -f null - 2>&1)" || rc=$?
    if ((rc == 124 || rc == 137)); then
        echo "stalled past ${SOAK_TIMEOUT}s (exit $rc), likely a frame or pool leak"
        return 1
    fi
    ((rc == 0)) || { echo "exit status $rc: $(tail -1 <<<"$out")"; return 1; }
    vuids="$(vuids_of "$out")"
    [[ -z "$vuids" ]] || { echo "VUIDs: $vuids"; return 1; }
    n_frames="$(count_lines -E 'n: *[0-9]+ pts:' "$out")" || { echo "$n_frames"; return 1; }
    ((n_frames == frames)) || { echo "frame count $n_frames != $frames"; return 1; }
    grep -Eq "n: *$((frames - 1)) pts:" <<<"$out" || { echo "last frame n:$((frames - 1)) missing"; return 1; }
    side="$(side_data_per_frame "$out" "$uuid_per" "$grain_per")" || { echo "$side"; return 1; }
    [[ "$out" =~ bench:\ maxrss=([0-9]+)KiB ]] && rss="${BASH_REMATCH[1]}"
    [[ -n "$rss" ]] || { echo "no 'bench: maxrss=' line; leak check cannot run"; return 1; }
    echo "$rss $side"
}

# Soaks one table row ($2): a short and a long run, then the maxrss growth
# check. Prints a PASS line, or a FAIL line on stderr and returns 1.
soak_config()
{
    local ffmpeg_bin="$1" row="$2"
    local name chain uuid_per grain_per short long rss_a rss_b growth limit
    local t0 sec

    IFS='|' read -r name chain uuid_per grain_per <<<"$row"
    t0="$SECONDS"
    short="$(soak_run "$ffmpeg_bin" "$chain" "$SOAK_WARMUP" "$uuid_per" "$grain_per")" ||
        { echo "FAIL: soak $name frames=$SOAK_WARMUP: $short" >&2; return 1; }
    long="$(soak_run "$ffmpeg_bin" "$chain" "$SOAK_FRAMES" "$uuid_per" "$grain_per")" ||
        { echo "FAIL: soak $name frames=$SOAK_FRAMES: $long" >&2; return 1; }
    sec=$((SECONDS - t0))
    rss_a="${short%% *}"
    rss_b="${long%% *}"
    growth=$((rss_b - rss_a))
    limit=$((SOAK_RSS_MIB * 1024))
    if ((growth > limit)); then
        echo "FAIL: soak $name maxrss ${rss_a}->${rss_b} KiB grew ${growth} KiB > ${limit} KiB over $((SOAK_FRAMES - SOAK_WARMUP)) frames" >&2
        return 1
    fi
    echo "PASS: soak $name frames=$SOAK_WARMUP/$SOAK_FRAMES maxrss=${rss_a}/${rss_b} KiB growth=${growth} KiB side_data(uuid grain)=${long#* } wall=${sec}s VUIDs: 0"
}

# --soak mode: validates the environment and the layer once, then soaks every
# selected row; returns 1 if any row fails.
run_soak()
{
    local ffmpeg_bin="$1"
    local failed=0 rows row
    local -a selected

    soak_env
    rows="$(soak_rows)" || exit 1
    mapfile -t selected <<<"$rows"
    probe_layer "$ffmpeg_bin"
    for row in "${selected[@]}"; do
        soak_config "$ffmpeg_bin" "$row" || failed=1
    done
    return "$failed"
}

# The fake reads the soak table the script writes next to it, so the
# expectations exist once. Short runs (no showinfo) keep the original plants
# on the last filter at the large size; soak plants fire only in runs of at
# least FAKE_PLANT_FRAMES frames, so they prove the long run is checked too.
write_fake()
{
    local fake="$1"

    cat >"$fake" <<'EOF_FAKE'
#!/usr/bin/env bash
args="$*"
if [[ -n "${VK_LOADER_DEBUG:-}" ]]; then
    [[ "$FAKE_MODE" == nolayer ]] || echo 'Inserted device layer "VK_LAYER_KHRONOS_validation" (x)'
    exit 0
fi
if [[ "$args" != *showinfo* ]]; then
    if [[ "$args" == *pelorus_aa_vulkan* && "$args" == *1280x720* ]]; then
        case "$FAKE_MODE" in
            vuid) echo 'Validation Error: [ VUID-vkCmdDispatch-imageLayout-00344 ] planted' >&2 ;;
            exit1) exit 1 ;;
        esac
    fi
    exit 0
fi
[[ "$args" == *" -nostdin "* ]] || { echo "fake: soak run without -nostdin" >&2; exit 3; }
U='[Parsed_showinfo_6 @ 0x1]   side data - H.26[45] User Data Unregistered SEI message: UUID=e1d7c4a2-6b93-4f08-9a55-0f3c2db17e64'
frames=0 vf=""
[[ "$args" =~ -frames:v\ ([0-9]+) ]] && frames="${BASH_REMATCH[1]}"
[[ "$args" =~ -vf\ ([^ ]+) ]] && vf="${BASH_REMATCH[1]}"
uuid=0 grain=0
while IFS='|' read -r _ chain u g; do
    [[ "$vf" == *",hwupload,$chain,hwdownload,"* ]] && uuid="$u" grain="$g"
done <"${0%/*}/table"
mode=clean
((frames >= FAKE_PLANT_FRAMES)) && mode="$FAKE_MODE"
case "$mode" in
    hang) exec sleep 30 ;;
    hangterm) trap '' TERM; exec sleep 30 ;;
    exit1) exit 1 ;;
esac
last=$((frames - 1))
[[ "$mode" == framedrop ]] && last=$((frames - 2))
[[ "$mode" == sideearly ]] && echo "$U"
for ((i = 0; i <= last && i < 100000; i++)); do
    echo "[Parsed_showinfo_6 @ 0x1] n: $i pts: $i pts_time:0"
    [[ "$mode" == sidedrop && $i -eq $last ]] && continue
    rep=1
    [[ "$mode" == sidemove ]] && rep=$((i == 0 ? 0 : (i == last ? 2 : 1)))
    for ((k = 0; k < uuid * rep && k < 16; k++)); do
        echo "$U"
    done
    [[ "$mode" == graindrop && $i -eq $last ]] && continue
    for ((k = 0; k < grain * rep && k < 16; k++)); do
        echo '[Parsed_showinfo_6 @ 0x1]   side data - Film grain parameters: type av1; seed=0'
    done
done
[[ "$mode" == vuid ]] && echo 'Validation Error: [ VUID-vkDestroyDevice-device-05137 ] planted' >&2
rss=100000
[[ "$mode" == rssgrow ]] && rss=$((100000 + 20000))
[[ "$mode" == nobench ]] || echo "bench: maxrss=${rss}KiB"
exit 0
EOF_FAKE
    chmod +x "$fake"
}

# Default mode against the fake: clean passes, each planted mode fails, and a
# FFMPEG_BIN that is on PATH but not executable fails before any run.
self_test_lane()
{
    local fake="$1" mode out rc=0 failures=0

    mkdir -p "${fake%/*}/noxff"
    printf '#!/bin/sh\n' >"${fake%/*}/noxff/ffmpeg"
    chmod 0644 "${fake%/*}/noxff/ffmpeg"
    out="$(PATH="${fake%/*}/noxff" run_lane ffmpeg 2>&1)" || rc=$?
    if ((rc == 0)) || ! grep -Fq -- "FFMPEG_BIN is not executable: ffmpeg" <<<"$out"; then
        echo "SELF-TEST FAIL: non-executable FFMPEG_BIN on PATH exited $rc: $out" >&2
        failures=$((failures + 1))
    fi
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
    return "$failures"
}

# Each line: SOAK_WARMUP SOAK_FRAMES SOAK_RSS_MIB SOAK_CONFIGS expected reason
# ("-" = must pass). The reason is checked, not only the exit status: a bad
# value that slips past validation can still fail later for another cause.
# Boundaries: WARMUP = FRAMES - 1 and a zero growth budget still pass clean.
self_test_soak_env()
{
    local fake="$1" row w f r c reason out rc failures=0
    local -a rows=(
        "3 4 0 denoise_ring -"
        "4 4 16 denoise_ring SOAK_WARMUP (4) must be less than SOAK_FRAMES (4)"
        "2 x 16 denoise_ring SOAK_FRAMES must be a positive integer"
        "0 4 16 denoise_ring SOAK_WARMUP must be a positive integer"
        "2 4 -1 denoise_ring SOAK_RSS_MIB must be a non-negative integer"
        "2 4 08 denoise_ring SOAK_RSS_MIB must be a non-negative integer"
        "2 4 010 denoise_ring SOAK_RSS_MIB must be a non-negative integer"
        "2 4 16 nosuch unknown soak config in SOAK_CONFIGS: nosuch"
        "2 4 16 denoise_ring,nosuch unknown soak config in SOAK_CONFIGS: nosuch"
    )

    for row in "${rows[@]}"; do
        read -r w f r c reason <<<"$row"
        rc=0
        out="$(FAKE_MODE=clean FAKE_PLANT_FRAMES=4 SOAK_WARMUP="$w" SOAK_FRAMES="$f" SOAK_RSS_MIB="$r" \
            SOAK_TIMEOUT=1 SOAK_KILL_AFTER=1 SOAK_CONFIGS="$c" run_soak "$fake" 2>&1)" || rc=$?
        if [[ "$reason" == - ]] && ((rc == 0)); then
            continue
        elif [[ "$reason" != - ]] && ((rc != 0)) && grep -Fq -- "$reason" <<<"$out"; then
            continue
        fi
        echo "SELF-TEST FAIL: soak env '$row' exited $rc: $out" >&2
        failures=$((failures + 1))
    done
    # timeout absent from PATH, then present but not executable.
    mkdir -p "${fake%/*}/none" "${fake%/*}/nox"
    printf '#!/bin/sh\n' >"${fake%/*}/nox/timeout"
    chmod 0644 "${fake%/*}/nox/timeout"
    for row in none nox; do
        rc=0
        out="$(PATH="${fake%/*}/$row" run_soak "$fake" 2>&1)" || rc=$?
        ((rc != 0)) && grep -Fq -- "not found as an executable file on PATH" <<<"$out" && continue
        echo "SELF-TEST FAIL: soak ran with timeout PATH case '$row' (exit $rc): $out" >&2
        failures=$((failures + 1))
    done
    return "$failures"
}

# Clean runs every config (empty name list); each plant runs on one config
# and must fail with its own reason, so a check failing for the wrong cause
# is caught. Each line: FAKE_MODE|SOAK_CONFIGS|expected reason.
self_test_soak()
{
    local fake="$1" plant mode reason out configs failures=0
    local -a plants=(
        "clean||"
        "rssgrow|denoise_ring|grew"
        "sidedrop|denoise_ring|frame n:3 Pelorus side data count 0 != 1"
        "graindrop|grain_estimate|frame n:3 film grain side data count 0 != 1"
        "sidemove|grain_estimate|frame n:0 Pelorus side data count 0 != 1"
        "sideearly|denoise_ring|side data before the first showinfo frame"
        "framedrop|denoise_ring|frame count"
        "vuid|denoise_ring|VUID-vkDestroyDevice-device-05137"
        "exit1|denoise_ring|exit status 1"
        "hang|denoise_ring|stalled past 1s (exit 124)"
        "hangterm|denoise_ring|stalled past 1s (exit 137)"
        "nolayer|denoise_ring|was not inserted"
        "nobench|denoise_ring|no 'bench: maxrss=' line"
    )

    for plant in "${plants[@]}"; do
        IFS='|' read -r mode configs reason <<<"$plant"
        if out="$(FAKE_MODE="$mode" FAKE_PLANT_FRAMES=4 SOAK_WARMUP=2 SOAK_FRAMES=4 \
            SOAK_RSS_MIB=16 SOAK_TIMEOUT=1 SOAK_KILL_AFTER=1 SOAK_CONFIGS="$configs" \
            run_soak "$fake" 2>&1)"; then
            [[ "$mode" == clean ]] && continue
            echo "SELF-TEST FAIL: planted soak defect '$mode' was accepted" >&2
        elif [[ "$mode" == clean ]]; then
            echo "SELF-TEST FAIL: clean soak run was rejected: $out" >&2
        elif grep -Fq -- "$reason" <<<"$out"; then
            continue
        else
            echo "SELF-TEST FAIL: soak defect '$mode' failed without '$reason': $out" >&2
        fi
        failures=$((failures + 1))
    done
    return "$failures"
}

# count_lines: two matches, zero matches (grep exit 1) and a grep error (exit 2).
self_test_count()
{
    local failures=0 n

    n="$(count_lines -F a $'a\nb\na')" && [[ "$n" == 2 ]] ||
        { echo "FAIL: self-test: count_lines of two matches gave '$n'" >&2; failures=$((failures + 1)); }
    n="$(count_lines -F z a)" && [[ "$n" == 0 ]] ||
        { echo "FAIL: self-test: count_lines of zero matches gave '$n'" >&2; failures=$((failures + 1)); }
    if n="$(count_lines -E '[' a 2>/dev/null)"; then
        echo "FAIL: self-test: count_lines accepted a grep error ('$n')" >&2
        failures=$((failures + 1))
    fi
    return "$failures"
}

# --self-test mode: writes the fake ffmpeg and runs every self-test group;
# returns 1 if any planted defect passes or the clean run fails.
self_test()
{
    local work fake failures=0 n=0

    work="$(mktemp -d "${TMPDIR:-/tmp}/pelorus-lvp-filters.XXXXXX")"
    fake="$work/ffmpeg"
    printf '%s\n' "${SOAK_TABLE[@]}" >"$work/table"
    write_fake "$fake"
    self_test_lane "$fake" || n=$?
    failures=$((failures + n))
    n=0
    self_test_soak "$fake" || n=$?
    failures=$((failures + n))
    n=0
    self_test_soak_env "$fake" || n=$?
    failures=$((failures + n))
    n=0
    self_test_count || n=$?
    failures=$((failures + n))
    rm -rf -- "$work"
    if ((failures)); then
        echo "self-test: FAILED ($failures)" >&2
        return 1
    fi
    echo "self-test: ok"
}

case "${1:-}" in
    --self-test) self_test ;;
    --soak) run_soak "${FFMPEG_BIN:-ffmpeg}" ;;
    '') run_lane "${FFMPEG_BIN:-ffmpeg}" ;;
    *) fail "usage: $0 [--self-test|--soak]" ;;
esac
