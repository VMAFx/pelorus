#!/usr/bin/env bash
#
# build-and-run.sh — build this Pelorus tree, replay its complete patch stack on
# the configured immutable FFmpeg commit, link ffmpeg, and verify registration.
#
# Copyright 2026 Lusoris. BSD-2-Clause-Patent.
#
# Requires: an explicit local FFmpeg checkout, a Vulkan loader + headers, and a
# `glslc` SPIR-V compiler. libpelorus is built, tested, and installed privately
# here; oneVPL is enabled when its pkg-config package is available so the
# cumulative link also covers QSV.
#
# Env:
#   FFMPEG_REPO  path to a FFmpeg git checkout      (required)
#   WORKTREE     new path for the FFmpeg worktree   (optional)
#   JOBS         parallel build jobs                (default nproc)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PATCHDIR="$(cd "$HERE/.." && pwd)"
ROOT="$(cd "$PATCHDIR/.." && pwd)"
# shellcheck source=build-config.env
source "$ROOT/build-config.env"
: "${FFMPEG_REPO:?FFMPEG_REPO must name a local FFmpeg checkout}"
JOBS="${JOBS:-$(nproc)}"

RUN_ROOT=""
PELORUS_BUILD=""
PRIVATE_PREFIX=""
LOG_DIR=""
OWNED_WORKTREE=""
CALLER_WORKTREE=0

canonicalize_existing_dir() {
    (cd -- "$1" 2>/dev/null && pwd -P)
}

canonicalize_new_path() {
    local path="$1"
    local parent
    local name
    local canonical_parent

    parent="$(dirname -- "$path")"
    name="$(basename -- "$path")"
    canonical_parent="$(canonicalize_existing_dir "$parent")" || return 1
    printf '%s/%s\n' "$canonical_parent" "$name"
}

if ! FFMPEG_REPO="$(canonicalize_existing_dir "$FFMPEG_REPO")"; then
    echo "ERROR: FFMPEG_REPO is not an accessible directory" >&2
    exit 1
fi

cleanup() {
    local status=$?
    local cleanup_failed=0
    local owned_dir
    trap - EXIT

    if [[ -n "$OWNED_WORKTREE" ]]; then
        git -C "$OWNED_WORKTREE" am --abort >/dev/null 2>&1 || true
        if ! git -C "$FFMPEG_REPO" worktree remove --force "$OWNED_WORKTREE" \
            >/dev/null 2>&1; then
            echo "WARNING: could not remove owned worktree: $OWNED_WORKTREE" >&2
            cleanup_failed=1
        fi
    fi

    for owned_dir in "$PELORUS_BUILD" "$PRIVATE_PREFIX" "$LOG_DIR"; do
        if [[ -n "$RUN_ROOT" && "$owned_dir" == "$RUN_ROOT/"* ]]; then
            if ! rm -rf -- "$owned_dir"; then
                echo "WARNING: could not remove owned path: $owned_dir" >&2
                cleanup_failed=1
            fi
        fi
    done
    if [[ -n "$RUN_ROOT" ]] && ! rmdir "$RUN_ROOT" 2>/dev/null; then
        echo "WARNING: owned scratch directory is not empty: $RUN_ROOT" >&2
        cleanup_failed=1
    fi

    if (( status == 0 && cleanup_failed )); then
        status=1
    fi
    exit "$status"
}

run_logged() {
    local label="$1"
    local log="$2"
    shift 2

    echo "== $label =="
    if ! "$@" >"$log" 2>&1; then
        echo "ERROR: $label failed; tail of $log:" >&2
        tail -80 "$log" >&2 || true
        return 1
    fi
}

if [[ ${WORKTREE+x} ]]; then
    CALLER_WORKTREE=1
    if [[ -z "$WORKTREE" ]]; then
        echo "ERROR: WORKTREE must not be empty when supplied" >&2
        exit 1
    fi
    if [[ -e "$WORKTREE" || -L "$WORKTREE" ]]; then
        echo "ERROR: refusing existing WORKTREE: $WORKTREE" >&2
        exit 1
    fi
    if ! WORKTREE="$(canonicalize_new_path "$WORKTREE")"; then
        echo "ERROR: WORKTREE parent is not an accessible directory" >&2
        exit 1
    fi
fi

RUN_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/pelorus-ffmpeg-replay.XXXXXX")"
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

if ! CANONICAL_RUN_ROOT="$(canonicalize_existing_dir "$RUN_ROOT")"; then
    echo "ERROR: could not canonicalize scratch directory: $RUN_ROOT" >&2
    exit 1
fi
RUN_ROOT="$CANONICAL_RUN_ROOT"
PELORUS_BUILD="$RUN_ROOT/pelorus-build"
PRIVATE_PREFIX="$RUN_ROOT/prefix"
LOG_DIR="$RUN_ROOT/logs"
mkdir "$LOG_DIR"
if (( ! CALLER_WORKTREE )); then
    WORKTREE="$RUN_ROOT/ffmpeg"
fi

if ! git -C "$FFMPEG_REPO" rev-parse --git-dir >/dev/null 2>&1; then
    echo "ERROR: FFMPEG_REPO is not a git checkout: $FFMPEG_REPO" >&2
    exit 1
fi
if ! TAG_COMMIT="$(git -C "$FFMPEG_REPO" rev-parse --verify \
    "refs/tags/${FFMPEG_TAG}^{commit}" 2>/dev/null)"; then
    echo "ERROR: configured FFmpeg tag is unavailable locally: $FFMPEG_TAG" >&2
    exit 1
fi
if [[ "$TAG_COMMIT" != "$FFMPEG_COMMIT" ]]; then
    echo "ERROR: $FFMPEG_TAG peels to $TAG_COMMIT, expected $FFMPEG_COMMIT" >&2
    exit 1
fi

echo "== private libpelorus prefix: $PRIVATE_PREFIX =="
run_logged "configure libpelorus" "$LOG_DIR/pelorus-configure.log" \
    meson setup "$PELORUS_BUILD" "$ROOT" \
    --prefix="$PRIVATE_PREFIX" --libdir=lib
run_logged "build libpelorus" "$LOG_DIR/pelorus-build.log" \
    meson compile -C "$PELORUS_BUILD" -j "$JOBS"
run_logged "test libpelorus" "$LOG_DIR/pelorus-test.log" \
    meson test -C "$PELORUS_BUILD" --suite=fast --print-errorlogs
run_logged "install libpelorus" "$LOG_DIR/pelorus-install.log" \
    meson install -C "$PELORUS_BUILD"

# Do not inherit a developer's libpelorus search paths. The installed prefix is
# first for pkg-config and the only non-system runtime library path.
export PKG_CONFIG_PATH="$PRIVATE_PREFIX/lib/pkgconfig"
export LD_LIBRARY_PATH="$PRIVATE_PREFIX/lib"
if [[ "$(pkg-config --variable=prefix libpelorus)" != "$PRIVATE_PREFIX" ]]; then
    echo "ERROR: pkg-config did not resolve private libpelorus" >&2
    exit 1
fi
echo "libpelorus version: $(pkg-config --modversion libpelorus)"

PATCHES=()
while IFS= read -r patch || [[ -n "$patch" ]]; do
    case "$patch" in
        ''|'#'*) continue ;;
    esac
    if [[ ! -f "$PATCHDIR/$patch" ]]; then
        echo "ERROR: missing patch listed in series.txt: $patch" >&2
        exit 1
    fi
    PATCHES+=("$patch")
done < "$PATCHDIR/series.txt"
if (( ${#PATCHES[@]} != 18 )); then
    echo "ERROR: expected 18 patches, found ${#PATCHES[@]}" >&2
    exit 1
fi

git -C "$FFMPEG_REPO" worktree add --detach "$WORKTREE" "$FFMPEG_COMMIT"
OWNED_WORKTREE="$WORKTREE"

apply_stack() {
    local patch
    for patch in "${PATCHES[@]}"; do
        echo "am: $patch"
        git -C "$WORKTREE" am --3way "$PATCHDIR/$patch"
    done
}

configure_ffmpeg() (
    local configure_extra=()

    cd -- "$WORKTREE"
    if pkg-config --exists vpl; then
        echo "enabling oneVPL $(pkg-config --modversion vpl)"
        configure_extra+=(--enable-libvpl)
    fi
    exec ./configure --enable-vulkan "${configure_extra[@]}" --disable-doc
)

run_logged "apply 18-patch FFmpeg stack" "$LOG_DIR/ffmpeg-apply.log" \
    apply_stack
run_logged "configure FFmpeg" "$LOG_DIR/ffmpeg-configure.log" \
    configure_ffmpeg
run_logged "link ffmpeg" "$LOG_DIR/ffmpeg-build.log" \
    make -C "$WORKTREE" -j"$JOBS" ffmpeg

FILTERS=(
    pelorus_aa_vulkan
    pelorus_analyze_vulkan
    pelorus_borderfix_vulkan
    pelorus_deband_vulkan
    pelorus_deblock_vulkan
    pelorus_dehalo_vulkan
    pelorus_denoise_vulkan
    pelorus_grain_estimate_vulkan
    pelorus_mc_vulkan
    pelorus_scenecut
)

echo "== verify Pelorus registrations =="
if ! "$WORKTREE/ffmpeg" -hide_banner -filters \
    >"$LOG_DIR/ffmpeg-filters.log" 2>&1; then
    tail -80 "$LOG_DIR/ffmpeg-filters.log" >&2 || true
    exit 1
fi
for filter in "${FILTERS[@]}"; do
    if ! awk -v name="$filter" '$2 == name { found = 1 } END { exit !found }' \
        "$LOG_DIR/ffmpeg-filters.log"; then
        echo "ERROR: filter is not registered: $filter" >&2
        tail -80 "$LOG_DIR/ffmpeg-filters.log" >&2 || true
        exit 1
    fi
    echo "registered filter: $filter"
done

if ! "$WORKTREE/ffmpeg" -hide_banner -bsfs \
    >"$LOG_DIR/ffmpeg-bsfs.log" 2>&1; then
    tail -80 "$LOG_DIR/ffmpeg-bsfs.log" >&2 || true
    exit 1
fi
if ! awk '$1 == "pelorus_fgs" { found = 1 } END { exit !found }' \
    "$LOG_DIR/ffmpeg-bsfs.log"; then
    echo "ERROR: bitstream filter is not registered: pelorus_fgs" >&2
    tail -80 "$LOG_DIR/ffmpeg-bsfs.log" >&2 || true
    exit 1
fi
echo "registered bitstream filter: pelorus_fgs"
echo "OK: 18 patches applied and ffmpeg linked against private libpelorus"
