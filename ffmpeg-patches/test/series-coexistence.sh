#!/usr/bin/env bash
#
# series-coexistence.sh — test the shared FFmpeg fixes, vmafx series and
# Pelorus series cumulatively in both project-stack orders (issue #160).
#
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
#
# Env:
#   FFMPEG_REPO       local FFmpeg git checkout with the configured tag
#   VMAFX_SERIES_DIR  vmafx checkout's ffmpeg-patches directory
#   SHARED_SERIES_DIR verified shared-series directory; when unset, fetch and
#                     verify the pinned release with fetch-ffmpeg-series.sh
#   BUILD             1 configures and builds the preferred successful order
#   JOBS              build jobs, at most 4 (default 4)
#
# Usage: series-coexistence.sh | series-coexistence.sh --self-test
set -euo pipefail
shopt -s inherit_errexit

export GIT_CONFIG_GLOBAL=/dev/null
export GIT_CONFIG_NOSYSTEM=1
unset GIT_COMMITTER_NAME GIT_COMMITTER_EMAIL GIT_COMMITTER_DATE
# Git exports GIT_DIR, GIT_WORK_TREE, GIT_INDEX_FILE and related variables to
# hooks; started from the pre-push `make verify-all`, the fixture `git init`,
# `commit` and `tag` calls would follow them into the invoking repository.
# Every git call here names its repository with -C, so drop Git's own list.
for _git_var in $(git rev-parse --local-env-vars); do
    unset "$_git_var"
done
unset _git_var

HERE="$(cd "$(dirname "$0")" && pwd)"
PATCHDIR="$(cd "$HERE/.." && pwd)"
ROOT="$(cd "$PATCHDIR/.." && pwd)"
# shellcheck source=build-config.env
source "$ROOT/build-config.env"

MAX_PATCHES=512
MAX_FILES=256
MAX_REPORTS=64
RUN_ROOT=""
LAST_WORKTREE=""
BASE_REF="${FFMPEG_TAG}"
declare -a ACTIVE_WORKTREES=()
declare -a APPLIED_PATCHES=()
declare -a APPLIED_LABELS=()
PREFERRED_RC=0
REVERSE_RC=0
PREFERRED_TREE=""
READER_TREE=""

fail()
{
    echo "series-coexistence: $*" >&2
    return 1
}

canonical_dir()
{
    (cd -- "$1" 2>/dev/null && pwd -P)
}

cleanup_worktree()
{
    local worktree="$1"

    if [[ -d "$worktree" ]]; then
        git -C "$FFMPEG_REPO" worktree remove --force "$worktree" >/dev/null 2>&1 ||
            echo "WARNING: could not remove worktree $worktree" >&2
    fi
}

cleanup()
{
    local status=$? worktree
    trap - EXIT INT TERM
    for worktree in "${ACTIVE_WORKTREES[@]}"; do
        cleanup_worktree "$worktree"
    done
    if [[ -n "${FFMPEG_REPO:-}" && -d "$FFMPEG_REPO" ]]; then
        git -C "$FFMPEG_REPO" worktree prune >/dev/null 2>&1 ||
            echo "WARNING: could not prune worktrees in $FFMPEG_REPO" >&2
    fi
    case "$RUN_ROOT" in
        "$ROOT"/build/series-coexistence.*|"$ROOT"/build/series-coexistence-self-test.*)
            rm -rf -- "$RUN_ROOT"
            ;;
    esac
    exit "$status"
}

patch_path()
{
    local dir="$1" relative="$2"

    if [[ -f "$dir/$relative" ]]; then
        printf '%s/%s\n' "$dir" "$relative"
    elif [[ -f "$dir/patches/$relative" ]]; then
        printf '%s/patches/%s\n' "$dir" "$relative"
    else
        fail "$dir/series.txt names missing patch $relative"
    fi
}

patch_files()
{
    awk -v max="$MAX_FILES" '
        /^diff --git a\// {
            file = $4
            sub(/^b\//, "", file)
            if (!seen[file]++ && ++count <= max) print file
        }
        END { if (count > max) exit 2 }
    ' "$1"
}

patch_hunks_for_file()
{
    local patch="$1" wanted="$2"
    awk -v wanted="$wanted" '
        /^diff --git a\// {
            file = $4
            sub(/^b\//, "", file)
            next
        }
        file == wanted && /^@@ / { print }
    ' "$patch"
}

overlapping_hunks()
{
    local previous="$1" current="$2" wanted="$3"
    awk -v wanted="$wanted" '
        function path(line, fields) {
            split(line, fields, " ")
            sub(/^b\//, "", fields[4])
            return fields[4]
        }
        function range(token, fields, value) {
            sub(/^[-+]/, "", token)
            split(token, fields, ",")
            value = fields[1] + 0
            count = (fields[2] == "" ? 1 : fields[2] + 0)
            return value SUBSEP count
        }
        FNR == NR {
            if (/^diff --git a\//) previous_file = path($0)
            if (previous_file == wanted && /^@@ /) {
                split($0, header, " ")
                split(range(header[3]), item, SUBSEP)
                previous_start[++n] = item[1]
                previous_end[n] = item[1] + (item[2] > 0 ? item[2] - 1 : 0)
                previous_hunk[n] = $0
            }
            next
        }
        /^diff --git a\// { current_file = path($0) }
        current_file == wanted && /^@@ / {
            split($0, header, " ")
            split(range(header[2]), item, SUBSEP)
            current_start = item[1]
            current_end = item[1] + (item[2] > 0 ? item[2] - 1 : 0)
            for (i = 1; i <= n; i++) {
                if (previous_start[i] <= current_end && current_start <= previous_end[i])
                    print previous_hunk[i] "\t" $0
            }
        }
    ' "$previous" "$current"
}

report_overlap()
{
    local current_label="$1" current_patch="$2" previous_label previous_patch
    local file overlap previous_hunk current_hunk reports=0 i

    echo "ERROR: apply failure: $current_label/$(basename "$current_patch")" >&2
    for ((i = 0; i < ${#APPLIED_PATCHES[@]} && i < MAX_PATCHES; i++)); do
        previous_patch="${APPLIED_PATCHES[$i]}"
        previous_label="${APPLIED_LABELS[$i]}"
        while IFS= read -r file; do
            [[ -n "$file" ]] || continue
            overlap="$(overlapping_hunks "$previous_patch" "$current_patch" "$file")"
            [[ -n "$overlap" ]] || continue
            echo "OVERLAP pair: $previous_label/$(basename "$previous_patch") <-> $current_label/$(basename "$current_patch")" >&2
            echo "  file: $file" >&2
            while IFS=$'\t' read -r previous_hunk current_hunk; do
                echo "  $previous_label hunk: $previous_hunk" >&2
                echo "  $current_label hunk: $current_hunk" >&2
            done <<<"$overlap"
            reports=$((reports + 1))
            if (( reports >= MAX_REPORTS )); then
                echo "  report limit reached: $MAX_REPORTS files" >&2
                return 0
            fi
        done < <(patch_files "$current_patch")
    done
    if (( reports == 0 )); then
        i=$((${#APPLIED_PATCHES[@]} - 1))
        if (( i >= 0 )); then
            previous_patch="${APPLIED_PATCHES[$i]}"
            previous_label="${APPLIED_LABELS[$i]}"
            echo "APPLY pair: cumulative tree after $previous_label/$(basename "$previous_patch") <-> $current_label/$(basename "$current_patch")" >&2
        else
            echo "APPLY pair: $BASE_REF <-> $current_label/$(basename "$current_patch")" >&2
        fi
        while IFS= read -r file; do
            [[ -n "$file" ]] || continue
            echo "  file: $file" >&2
            while IFS= read -r hunk; do
                echo "  $current_label hunk: $hunk" >&2
            done < <(patch_hunks_for_file "$current_patch" "$file")
        done < <(patch_files "$current_patch")
    fi
}

apply_series()
{
    local worktree="$1" label="$2" dir="$3" relative patch log count=0

    [[ -r "$dir/series.txt" ]] || { fail "$label series is unreadable: $dir/series.txt"; return 1; }
    while IFS= read -r relative || [[ -n "$relative" ]]; do
        case "$relative" in
            ''|'#'*) continue ;;
        esac
        count=$((count + 1))
        if (( count > MAX_PATCHES )); then
            fail "$label series exceeds $MAX_PATCHES patches"
            return 1
        fi
        if ! patch="$(patch_path "$dir" "$relative")"; then
            return 1
        fi
        log="$RUN_ROOT/$(basename "$worktree").$(basename "$patch").apply.log"
        echo "APPLY: $label/$relative"
        if ! git -C "$worktree" apply --check --whitespace=nowarn "$patch" >"$log" 2>&1; then
            cat "$log" >&2
            report_overlap "$label" "$patch"
            return 1
        fi
        if ! git -C "$worktree" \
            -c diff.orderFile=/dev/null \
            -c diff.noprefix=false \
            apply --whitespace=nowarn "$patch" >"$log" 2>&1; then
            cat "$log" >&2
            report_overlap "$label" "$patch"
            return 1
        fi
        APPLIED_PATCHES+=("$patch")
        APPLIED_LABELS+=("$label")
    done <"$dir/series.txt"
}

run_order()
{
    local name="$1" first_label="$2" first_dir="$3" second_label="$4" second_dir="$5"
    local worktree="$RUN_ROOT/ffmpeg-$name"

    APPLIED_PATCHES=()
    APPLIED_LABELS=()
    echo "ORDER: shared -> $first_label -> $second_label"
    if ! git -C "$FFMPEG_REPO" worktree add --detach "$worktree" "$BASE_REF" >/dev/null; then
        fail "could not create detached worktree for $name"
        return 1
    fi
    ACTIVE_WORKTREES+=("$worktree")
    if ! apply_series "$worktree" shared "$SHARED_SERIES_DIR" ||
       ! apply_series "$worktree" "$first_label" "$first_dir" ||
       ! apply_series "$worktree" "$second_label" "$second_dir"; then
        echo "ORDER RESULT: $name unsupported" >&2
        cleanup_worktree "$worktree"
        return 1
    fi
    LAST_WORKTREE="$worktree"
    echo "ORDER RESULT: $name supported"
}

prepare_reader_tree()
{
    local worktree="$RUN_ROOT/ffmpeg-reader-contract"

    APPLIED_PATCHES=()
    APPLIED_LABELS=()
    echo "READER TREE: shared -> vmafx"
    if ! git -C "$FFMPEG_REPO" worktree add --detach "$worktree" "$BASE_REF" >/dev/null; then
        fail "could not create detached reader-contract worktree"
        return 1
    fi
    ACTIVE_WORKTREES+=("$worktree")
    if ! apply_series "$worktree" shared "$SHARED_SERIES_DIR" ||
       ! apply_series "$worktree" vmafx "$VMAFX_SERIES_DIR"; then
        cleanup_worktree "$worktree"
        return 1
    fi
    READER_TREE="$worktree"
}

write_probe()
{
    cat >"$RUN_ROOT/abi-probe.c" <<'EOF'
#include <stddef.h>
#include <stdio.h>
#include INTEROP_HEADER
#define OFF(type, member) offsetof(type, member)

int main(void)
{
    printf("magic=%s magic_len=%u abi_major=%u abi_minor=%u uuid_len=%u ",
           PELORUS_MAGIC_STR, PELORUS_MAGIC_LEN, PELORUS_ABI_MAJOR,
           PELORUS_ABI_MINOR, PELORUS_SIDEDATA_UUID_LEN);
    printf("header=%zu ho=%zu,%zu,%zu,%zu,%zu,%zu,%zu,%zu,%zu,%zu,%zu,%zu,%zu,%zu,%zu ",
           sizeof(PelorusSideData), OFF(PelorusSideData, magic), OFF(PelorusSideData, abi_major),
           OFF(PelorusSideData, abi_minor), OFF(PelorusSideData, total_size),
           OFF(PelorusSideData, section_mask), OFF(PelorusSideData, section_count),
           OFF(PelorusSideData, header_size), OFF(PelorusSideData, frame_pts),
           OFF(PelorusSideData, plane_layout), OFF(PelorusSideData, bit_depth),
           OFF(PelorusSideData, grid_cols), OFF(PelorusSideData, grid_rows),
           OFF(PelorusSideData, _pad0), OFF(PelorusSideData, producer_id), OFF(PelorusSideData, _pad1));
    printf("dir=%zu do=%zu,%zu,%zu,%zu sections=%u,%u,%u,%u,%u,%u,%u,%u ",
           sizeof(PelorusSectionDir), OFF(PelorusSectionDir, section_id),
           OFF(PelorusSectionDir, offset), OFF(PelorusSectionDir, size),
           OFF(PelorusSectionDir, struct_minor), PEL_SEC_BANDING, PEL_SEC_VARIANCE,
           PEL_SEC_DENOISE, PEL_SEC_FILMGRAIN, PEL_SEC_MOTION, PEL_SEC_QPREPORT,
           PEL_SEC_MOTION_CONF, PEL_SEC_COMPLEXITY);
    printf("reader_sections=%zu,%zu,%zu bo=%zu,%zu,%zu,%zu vo=%zu,%zu,%zu,%zu,%zu,%zu,%zu co=%zu,%zu,%zu,%zu\n",
           sizeof(PelorusBandingSection), sizeof(PelorusVarianceSection),
           sizeof(PelorusComplexitySection), OFF(PelorusBandingSection, global_banding_risk),
           OFF(PelorusBandingSection, flat_area_fraction), OFF(PelorusBandingSection, cell_data_offset),
           OFF(PelorusBandingSection, cell_data_size), OFF(PelorusVarianceSection, global_variance),
           OFF(PelorusVarianceSection, edge_density), OFF(PelorusVarianceSection, texture_energy),
           OFF(PelorusVarianceSection, var_cell_offset), OFF(PelorusVarianceSection, var_cell_size),
           OFF(PelorusVarianceSection, edge_cell_offset), OFF(PelorusVarianceSection, edge_cell_size),
           OFF(PelorusComplexitySection, complexity), OFF(PelorusComplexitySection, texture_energy),
           OFF(PelorusComplexitySection, motion_component), OFF(PelorusComplexitySection, has_scene_cut));
    return 0;
}
EOF
}

uuid_initializer()
{
    awk '
        /const uint8_t pelorus_sidedata_uuid\[/ { found = 1 }
        found { gsub(/[[:space:]]/, ""); printf "%s", $0 }
        found && /};/ { print ""; exit }
    ' "$1"
}

reader_source()
{
    local worktree="$1" source

    [[ -n "$worktree" ]] || { fail "vmafx cumulative reader tree is unavailable"; return 1; }
    source="$worktree/libavfilter/vf_libvmaf.c"
    [[ -r "$source" ]] || { fail "final vmafx tree has no readable libavfilter/vf_libvmaf.c"; return 1; }
    printf '%s\n' "$source"
}

check_reader_abi()
{
    local vmafx_root="$1" vmafx_source="$2" reader producer
    local reader_minor producer_minor reader_norm producer_norm reader_uuid producer_uuid
    local cc_bin="${CC:-cc}"

    write_probe || return 1
    "$cc_bin" -std=c11 -Wall -Wextra -Werror -I"$ROOT/libpelorus/include" \
        '-DINTEROP_HEADER="pelorus/interop.h"' "$RUN_ROOT/abi-probe.c" \
        -o "$RUN_ROOT/producer-probe" || return 1
    "$cc_bin" -std=c11 -Wall -Wextra -Werror -I"$vmafx_root/core/include" \
        '-DINTEROP_HEADER="libvmaf/pelorus/interop.h"' "$RUN_ROOT/abi-probe.c" \
        -o "$RUN_ROOT/reader-probe" || return 1
    producer="$("$RUN_ROOT/producer-probe")" || return 1
    reader="$("$RUN_ROOT/reader-probe")" || return 1
    [[ -n "$producer" && -n "$reader" ]] || {
        fail "ABI probe returned empty output"
        return 1
    }
    producer_minor="$(sed -n 's/.*abi_minor=\([0-9][0-9]*\).*/\1/p' <<<"$producer")"
    reader_minor="$(sed -n 's/.*abi_minor=\([0-9][0-9]*\).*/\1/p' <<<"$reader")"
    [[ -n "$producer_minor" && -n "$reader_minor" ]] || {
        fail "could not extract abi_minor from probe output"
        return 1
    }
    producer_norm="${producer/abi_minor=$producer_minor/abi_minor=X}"
    reader_norm="${reader/abi_minor=$reader_minor/abi_minor=X}"
    if [[ "$producer_norm" != "$reader_norm" ]]; then
        echo "producer ABI: $producer" >&2
        echo "reader ABI:   $reader" >&2
        fail "vmafx PelorusSideData identifiers differ from libpelorus"
        return 1
    fi
    if (( reader_minor > producer_minor )); then
        fail "vmafx reader ABI minor $reader_minor is newer than producer $producer_minor"
        return 1
    fi
    producer_uuid="$(uuid_initializer "$ROOT/libpelorus/src/interop.c")"
    reader_uuid="$(uuid_initializer "$vmafx_source")"
    [[ -n "$producer_uuid" && "$producer_uuid" == "$reader_uuid" ]] || {
        fail "vmafx pelorus_sidedata_uuid differs from libpelorus"
        return 1
    }
    echo "READER ABI: PelorusSideData=$producer_minor producer/$reader_minor reader; shared identifiers and UUID match"
}

check_reader_flow()
{
    local source="$1" flow

    flow="$(awk '
        /sd = av_frame_get_side_data\(dist, AV_FRAME_DATA_SEI_UNREGISTERED\);/ {
            capture = 1
            left = 24
        }
        capture && left-- > 0 { print }
    ' "$source" | tr '\n' ' ')"
    [[ "$flow" == *'vmaf_set_perceptual_sidedata(s->vmaf, sd->data, sd->size,'* ]] || {
        fail "vmafx reader does not pass the distorted frame side-data buffer to vmaf_set_perceptual_sidedata"
        return 1
    }
}

check_reader_contract()
{
    local worktree="$1" vmafx_root vmafx_header vmafx_source reader

    vmafx_root="$(canonical_dir "$VMAFX_SERIES_DIR/..")" || return 1
    vmafx_header="$vmafx_root/core/include/libvmaf/pelorus/interop.h"
    vmafx_source="$vmafx_root/core/src/interop/pelorus_interop.c"
    [[ -r "$vmafx_header" && -r "$vmafx_source" ]] || {
        fail "vmafx vendored Pelorus ABI is unavailable below $vmafx_root/core"
        return 1
    }
    reader="$(reader_source "$worktree")" || return 1
    check_reader_flow "$reader" || return 1
    echo "READER: AV_FRAME_DATA_SEI_UNREGISTERED -> vmaf_set_perceptual_sidedata"
    check_reader_abi "$vmafx_root" "$vmafx_source" || return 1
}

check_memory()
{
    local available swap_free
    available="$(awk '/^MemAvailable:/ { print $2 }' /proc/meminfo)"
    swap_free="$(awk '/^SwapFree:/ { print $2 }' /proc/meminfo)"
    if (( available < 12582912 || swap_free < 15728640 )); then
        fail "build memory floor unmet: MemAvailable=${available}KiB SwapFree=${swap_free}KiB"
        return 1
    fi
}

build_private_pelorus()
{
    local jobs="$1" build_dir="$RUN_ROOT/pelorus-build" prefix="$RUN_ROOT/prefix"

    meson setup "$build_dir" "$ROOT" --prefix="$prefix" --libdir=lib || return 1
    meson compile -C "$build_dir" -j "$jobs" || return 1
    meson test -C "$build_dir" --suite=fast --num-processes "$jobs" --print-errorlogs || return 1
    meson install -C "$build_dir" || return 1
}

build_ffmpeg()
{
    local worktree="$1" build_dir="$RUN_ROOT/ffmpeg-build" prefix="$RUN_ROOT/prefix"
    local skip_log="$RUN_ROOT/libvmaf-skip.log" jobs="${JOBS:-4}" vmaf_enabled=0
    local filter filter_output bsf_output
    local -a vmaf_option=(--disable-libvmaf)
    local -a pelorus_filters=(
        pelorus_deband_vulkan pelorus_analyze_vulkan pelorus_denoise_vulkan
        pelorus_grain_estimate_vulkan pelorus_mc_vulkan pelorus_dehalo_vulkan
        pelorus_aa_vulkan pelorus_deblock_vulkan pelorus_borderfix_vulkan
        pelorus_scenecut
    )
    local -a filter_options=()

    [[ "$jobs" =~ ^[1-4]$ ]] || { fail "JOBS must be an integer from 1 to 4"; return 1; }
    check_memory || return 1
    build_private_pelorus "$jobs" || return 1
    if pkg-config --exists libvmaf; then
        vmaf_option=(--enable-libvmaf)
        vmaf_enabled=1
    else
        echo "SKIP: libvmaf compile: pkg-config package libvmaf unavailable" | tee "$skip_log"
        "$HERE/named-skip.sh" "libvmaf compile" 77 "$skip_log" \
            "pkg-config package libvmaf unavailable" || return 1
    fi
    for filter in "${pelorus_filters[@]}"; do
        filter_options+=("--enable-filter=$filter")
    done
    mkdir "$build_dir" || return 1
    (
        set -e
        export PKG_CONFIG_PATH="$prefix/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
        export LD_LIBRARY_PATH="$prefix/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
        cd "$build_dir" || exit 1
        "$worktree/configure" \
            --prefix="$prefix" \
            --libdir="$prefix/lib" \
            --enable-vulkan --disable-doc \
            --enable-bsf=pelorus_fgs \
            "${filter_options[@]}" "${vmaf_option[@]}" || exit 1
        make -j"$jobs" ffmpeg || exit 1
        filter_output="$(./ffmpeg -hide_banner -filters)" || exit 1
        for filter in "${pelorus_filters[@]}"; do
            if ! awk -v name="$filter" '$2 == name { found = 1 } END { exit !found }' <<<"$filter_output"; then
                fail "built ffmpeg omitted exact filter registration $filter"
                exit 1
            fi
        done
        bsf_output="$(./ffmpeg -hide_banner -bsfs)" || exit 1
        if ! awk -v name=pelorus_fgs '$1 == name { found = 1 } END { exit !found }' <<<"$bsf_output"; then
            fail "built ffmpeg omitted exact bitstream-filter registration pelorus_fgs"
            exit 1
        fi
        if (( vmaf_enabled )); then
            if ! awk '$2 == "libvmaf" { found = 1 } END { exit !found }' <<<"$filter_output"; then
                fail "built ffmpeg omitted exact filter registration libvmaf"
                exit 1
            fi
        fi
    ) || return 1
}

make_fixture_patch()
{
    local repo="$1" base="$2" branch="$3" file="$4" text="$5" output="$6"

    git -C "$repo" switch --detach "$base" >/dev/null 2>&1
    git -C "$repo" switch -c "$branch" >/dev/null 2>&1
    printf '%s\n' "$text" >"$repo/$file"
    git -C "$repo" add "$file"
    git -C "$repo" -c user.name=Fixture -c user.email=fixture@invalid \
        -c commit.gpgSign=false commit -m "$branch" >/dev/null
    git -C "$repo" format-patch -1 --stdout >"$output"
}

self_test_reader_flow()
{
    local good="$RUN_ROOT/reader-good.c" bad="$RUN_ROOT/reader-bad.c"
    local removed="$RUN_ROOT/reader-removed.c"
    local final_tree="$RUN_ROOT/reader-final" final_source

    printf '%s\n' \
        'sd = av_frame_get_side_data(dist, AV_FRAME_DATA_SEI_UNREGISTERED);' \
        'vmaf_set_perceptual_sidedata(s->vmaf, sd->data, sd->size, index);' >"$good"
    printf '%s\n' \
        'sd = av_frame_get_side_data(dist, AV_FRAME_DATA_SEI_UNREGISTERED);' \
        'vmaf_set_perceptual_sidedata(s->vmaf, other->data, other->size, index);' >"$bad"
    printf '%s\n' 'return 0;' >"$removed"
    check_reader_flow "$good" || { fail "reader-flow control rejected"; return 1; }
    if check_reader_flow "$bad" >/dev/null 2>&1; then
        fail "reader-flow planted wrong buffer accepted"
        return 1
    fi
    mkdir -p "$final_tree/libavfilter"
    cp "$good" "$final_tree/libavfilter/vf_libvmaf.c"
    final_source="$(reader_source "$final_tree")" || return 1
    check_reader_flow "$final_source" || { fail "final-tree reader control rejected"; return 1; }
    cp "$removed" "$final_tree/libavfilter/vf_libvmaf.c"
    if check_reader_flow "$(reader_source "$final_tree")" >/dev/null 2>&1; then
        fail "later vmafx reader removal fixture accepted from final cumulative tree"
        return 1
    fi
}

self_test_reader_abi_failing_cc()
{
    local vmafx_mock="$RUN_ROOT/vmafx-mock"
    local tree_mock="$RUN_ROOT/tree-mock"
    local log="$RUN_ROOT/planted-cc.log"
    local rc=0 saved_vmafx_series="$VMAFX_SERIES_DIR"

    mkdir -p "$vmafx_mock/core/include/libvmaf/pelorus" "$vmafx_mock/core/src/interop" "$vmafx_mock/ffmpeg-patches"
    cp "$ROOT/libpelorus/include/pelorus/pelorus.h" "$vmafx_mock/core/include/libvmaf/pelorus/pelorus.h"
    sed 's#"pelorus/pelorus.h"#"libvmaf/pelorus/pelorus.h"#' \
        "$ROOT/libpelorus/include/pelorus/interop.h" >"$vmafx_mock/core/include/libvmaf/pelorus/interop.h"
    cp "$ROOT/libpelorus/src/interop.c" "$vmafx_mock/core/src/interop/pelorus_interop.c"

    mkdir -p "$tree_mock/libavfilter"
    cat >"$tree_mock/libavfilter/vf_libvmaf.c" <<'EOF'
sd = av_frame_get_side_data(dist, AV_FRAME_DATA_SEI_UNREGISTERED);
vmaf_set_perceptual_sidedata(s->vmaf, sd->data, sd->size, index);
EOF

    VMAFX_SERIES_DIR="$vmafx_mock/ffmpeg-patches"

    # Positive control: with working CC, check_reader_contract must pass
    check_reader_contract "$tree_mock" >/dev/null 2>&1 || {
        VMAFX_SERIES_DIR="$saved_vmafx_series"
        fail "reader contract synthetic positive control failed"
        return 1
    }

    # Planted failing CC in subshell must fail
    (
        set -e
        CC=false
        check_reader_contract "$tree_mock"
    ) >"$log" 2>&1 || rc=$?

    if [[ "$rc" == 0 ]]; then
        VMAFX_SERIES_DIR="$saved_vmafx_series"
        fail "planted failing CC=false in subshell accepted by check_reader_contract"
        return 1
    fi

    # Planted failing CC under || must also fail
    rc=0
    CC=false check_reader_contract "$tree_mock" >"$log" 2>&1 || rc=$?

    VMAFX_SERIES_DIR="$saved_vmafx_series"
    if [[ "$rc" == 0 ]]; then
        fail "planted failing CC=false under || accepted by check_reader_contract"
        return 1
    fi
}

self_test_bad_jobs()
{
    local log="$RUN_ROOT/bad-jobs.log" rc=0

    (
        JOBS=5 prepare_run
    ) >"$log" 2>&1 || rc=$?
    [[ "$rc" == 1 ]] || { fail "bad JOBS=5 returned $rc, expected 1"; return 1; }
    grep -Fq "JOBS must be an integer from 1 to 4" "$log" || {
        fail "bad JOBS=5 omitted expected error: $(cat "$log")"
        return 1
    }
    rc=0
    (
        BUILD=2 prepare_run
    ) >"$log" 2>&1 || rc=$?
    [[ "$rc" == 1 ]] || { fail "bad BUILD=2 returned $rc, expected 1"; return 1; }
    grep -Fq "BUILD must be 0 or 1" "$log" || {
        fail "bad BUILD=2 omitted expected error: $(cat "$log")"
        return 1
    }
}

self_test_apply_pair()
{
    local log="$RUN_ROOT/context-failure.log" rc=0

    cat >"$RUN_ROOT/pelorus/pelorus-context.patch" <<'EOF'
diff --git a/configure b/configure
--- a/configure
+++ b/configure
@@ -99,1 +99,1 @@
-absent-context
+pelorus
EOF
    printf 'pelorus-context.patch\n' >"$RUN_ROOT/pelorus/series.txt"
    run_order context-failure vmafx "$VMAFX_SERIES_DIR" pelorus "$RUN_ROOT/pelorus" >"$log" 2>&1 || rc=$?
    [[ "$rc" == 1 ]] || { fail "context failure returned $rc, expected 1"; return 1; }
    grep -Fq 'APPLY pair:' "$log" || { fail "context failure omitted patch pair"; return 1; }
    grep -Fq 'vmafx-change.patch' "$log" || { fail "context failure omitted prior patch"; return 1; }
    grep -Fq 'pelorus-context.patch' "$log" || { fail "context failure omitted current patch"; return 1; }
    grep -Fq 'file: configure' "$log" || { fail "context failure omitted file"; return 1; }
    grep -Fq 'hunk: @@' "$log" || { fail "context failure omitted hunk"; return 1; }
}

self_test_overlap_pair()
{
    local base="$1" order log rc

    make_fixture_patch "$FFMPEG_REPO" "$base" pelorus-overlap configure \
        $'alpha\nbeta\npelorus\ndelta' "$RUN_ROOT/pelorus/pelorus-overlap.patch"
    printf 'pelorus-overlap.patch\n' >"$RUN_ROOT/pelorus/series.txt"
    for order in forward-overlap reverse-overlap; do
        log="$RUN_ROOT/$order.log"
        rc=0
        if [[ "$order" == forward-overlap ]]; then
            run_order "$order" vmafx "$VMAFX_SERIES_DIR" pelorus "$RUN_ROOT/pelorus" >"$log" 2>&1 || rc=$?
        else
            run_order "$order" pelorus "$RUN_ROOT/pelorus" vmafx "$VMAFX_SERIES_DIR" >"$log" 2>&1 || rc=$?
        fi
        [[ "$rc" == 1 ]] || { fail "$order returned $rc, expected 1"; return 1; }
        grep -Fq 'OVERLAP pair:' "$log" || { fail "$order omitted patch pair"; return 1; }
        grep -Fq 'vmafx-change.patch' "$log" || { fail "$order omitted vmafx patch"; return 1; }
        grep -Fq 'pelorus-overlap.patch' "$log" || { fail "$order omitted Pelorus patch"; return 1; }
        grep -Fq 'file: configure' "$log" || { fail "$order omitted touched file"; return 1; }
        grep -Fq 'hunk: @@' "$log" || { fail "$order omitted hunks"; return 1; }
    done
}

self_test()
{
    local fixture base
    local -a controls=(forward-control reverse-control)

    mkdir -p "$ROOT/build"
    RUN_ROOT="$(mktemp -d "$ROOT/build/series-coexistence-self-test.XXXXXX")"
    trap cleanup EXIT INT TERM
    fixture="$RUN_ROOT/repo"
    mkdir "$fixture" "$RUN_ROOT/shared" "$RUN_ROOT/vmafx" "$RUN_ROOT/pelorus"
    git -C "$fixture" init -q
    printf '%s\n' alpha beta gamma delta >"$fixture/configure"
    git -C "$fixture" add configure
    git -C "$fixture" -c user.name=Fixture -c user.email=fixture@invalid \
        -c commit.gpgSign=false commit -m base >/dev/null
    base="$(git -C "$fixture" rev-parse HEAD)"
    git -C "$fixture" tag base
    printf '# empty shared fixture\n' >"$RUN_ROOT/shared/series.txt"
    make_fixture_patch "$fixture" "$base" vmafx-change configure \
        $'alpha\nbeta\nvmafx\ndelta' "$RUN_ROOT/vmafx/vmafx-change.patch"
    printf 'vmafx-change.patch\n' >"$RUN_ROOT/vmafx/series.txt"
    make_fixture_patch "$fixture" "$base" pelorus-control pelorus.txt \
        control "$RUN_ROOT/pelorus/pelorus-control.patch"
    printf 'pelorus-control.patch\n' >"$RUN_ROOT/pelorus/series.txt"

    FFMPEG_REPO="$fixture"
    SHARED_SERIES_DIR="$RUN_ROOT/shared"
    VMAFX_SERIES_DIR="$RUN_ROOT/vmafx"
    BASE_REF=base
    for order in "${controls[@]}"; do
        if [[ "$order" == forward-control ]]; then
            run_order "$order" vmafx "$VMAFX_SERIES_DIR" pelorus "$RUN_ROOT/pelorus" >/dev/null
        else
            run_order "$order" pelorus "$RUN_ROOT/pelorus" vmafx "$VMAFX_SERIES_DIR" >/dev/null
        fi
        cleanup_worktree "$LAST_WORKTREE"
    done

    self_test_overlap_pair "$base"
    self_test_apply_pair
    self_test_reader_flow
    self_test_reader_abi_failing_cc
    self_test_bad_jobs
    echo "self-test: PASS (2 non-overlap controls; planted overlap rejected in both orders; final reader flow guarded; planted CC=false and bad JOBS rejected)"
}

verify_shared_override()
{
    local dir="$1" top prefix=""

    [[ -r "$dir/base.env" ]] || { fail "SHARED_SERIES_DIR has no base.env"; return 1; }
    grep -Fxq "FFMPEG_TAG=$FFMPEG_TAG" "$dir/base.env" || {
        fail "SHARED_SERIES_DIR base.env has another FFmpeg tag"
        return 1
    }
    grep -Fxq "FFMPEG_COMMIT=$FFMPEG_COMMIT" "$dir/base.env" || {
        fail "SHARED_SERIES_DIR base.env has another FFmpeg commit"
        return 1
    }
    top="$(git -C "$dir" rev-parse --show-toplevel 2>/dev/null)" || {
        fail "SHARED_SERIES_DIR override must be a Git checkout of the pinned release"
        return 1
    }
    if [[ "$dir" != "$top" ]]; then
        prefix="${dir#"$top"/}/"
    fi
    git -C "$top" cat-file -e "${FFMPEG_SERIES_COMMIT}^{commit}" 2>/dev/null || {
        fail "SHARED_SERIES_DIR checkout lacks pinned commit $FFMPEG_SERIES_COMMIT"
        return 1
    }
    if ! git -C "$top" diff --quiet "$FFMPEG_SERIES_COMMIT" -- \
        "${prefix}base.env" "${prefix}series.txt" \
        ":(top,glob)${prefix}patches/*.patch"; then
        fail "SHARED_SERIES_DIR patch content differs from pinned $FFMPEG_SERIES_COMMIT"
        return 1
    fi
}

prepare_run()
{
    local fetched="" vmafx_commit
    local jobs="${JOBS:-4}" build="${BUILD:-0}"

    [[ "$jobs" =~ ^[1-4]$ ]] || { fail "JOBS must be an integer from 1 to 4"; return 1; }
    [[ "$build" =~ ^[01]$ ]] || { fail "BUILD must be 0 or 1"; return 1; }
    : "${FFMPEG_REPO:?FFMPEG_REPO must name a local FFmpeg checkout}"
    : "${VMAFX_SERIES_DIR:?VMAFX_SERIES_DIR must name the vmafx ffmpeg-patches directory}"
    FFMPEG_REPO="$(canonical_dir "$FFMPEG_REPO")" || { fail "FFMPEG_REPO is inaccessible"; return 1; }
    VMAFX_SERIES_DIR="$(canonical_dir "$VMAFX_SERIES_DIR")" || { fail "VMAFX_SERIES_DIR is inaccessible"; return 1; }
    PATCHDIR="$(canonical_dir "$PATCHDIR")"
    if ! git -C "$FFMPEG_REPO" rev-parse --git-dir >/dev/null 2>&1; then
        fail "FFMPEG_REPO is not a git checkout: $FFMPEG_REPO"
        return 1
    fi
    if [[ "$(git -C "$FFMPEG_REPO" rev-parse "${FFMPEG_TAG}^{commit}")" != "$FFMPEG_COMMIT" ]]; then
        fail "$FFMPEG_TAG does not peel to configured $FFMPEG_COMMIT"
        return 1
    fi
    vmafx_commit="$(git -C "$VMAFX_SERIES_DIR" rev-parse HEAD 2>/dev/null)" || vmafx_commit="uncommitted"
    echo "INPUT: vmafx commit $vmafx_commit (unpinned, unverified input)"
    mkdir -p "$ROOT/build"
    RUN_ROOT="$(mktemp -d "$ROOT/build/series-coexistence.XXXXXX")"
    trap cleanup EXIT INT TERM
    if [[ -n "${SHARED_SERIES_DIR:-}" ]]; then
        SHARED_SERIES_DIR="$(canonical_dir "$SHARED_SERIES_DIR")" || {
            fail "SHARED_SERIES_DIR is inaccessible"
            return 1
        }
        verify_shared_override "$SHARED_SERIES_DIR" || return 1
    else
        fetched="$RUN_ROOT/shared-release"
        if ! SHARED_SERIES_DIR="$("$ROOT/scripts/fetch-ffmpeg-series.sh" "$fetched")"; then
            fail "could not fetch and verify shared series"
            return 1
        fi
    fi
}

measure_orders()
{
    PREFERRED_RC=0
    REVERSE_RC=0
    PREFERRED_TREE=""
    READER_TREE=""

    if run_order vmafx-then-pelorus vmafx "$VMAFX_SERIES_DIR" pelorus "$PATCHDIR"; then
        PREFERRED_TREE="$LAST_WORKTREE"
    else
        PREFERRED_RC=$?
    fi
    if run_order pelorus-then-vmafx pelorus "$PATCHDIR" vmafx "$VMAFX_SERIES_DIR"; then
        REVERSE_RC=0
    else
        REVERSE_RC=$?
    fi
    echo "SUMMARY: vmafx-then-pelorus rc=$PREFERRED_RC; pelorus-then-vmafx rc=$REVERSE_RC"
}

normal_run()
{
    local reader_rc=0

    prepare_run || return 1
    measure_orders
    if [[ -n "$PREFERRED_TREE" ]]; then
        READER_TREE="$PREFERRED_TREE"
    else
        prepare_reader_tree || return 1
    fi
    set +e
    (
        set -e
        check_reader_contract "$READER_TREE"
    )
    reader_rc=$?
    set -e
    if (( PREFERRED_RC != 0 )); then
        fail "preferred order shared -> vmafx -> Pelorus is unsupported"
        return 1
    fi
    (( reader_rc == 0 )) || return "$reader_rc"
    if [[ "${BUILD:-0}" == 1 ]]; then
        build_ffmpeg "$PREFERRED_TREE" || return 1
    fi
    if (( REVERSE_RC != 0 )); then
        echo "REVERSE: unsupported"
    else
        echo "REVERSE: supported"
    fi
}

main()
{
    if [[ "${1:-}" == --self-test && $# == 1 ]]; then
        self_test
        return
    fi
    if (( $# != 0 )); then
        fail "usage: $0 [--self-test]"
        return 2
    fi
    normal_run
}

main "$@"
