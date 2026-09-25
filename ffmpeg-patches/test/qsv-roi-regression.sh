#!/usr/bin/env bash
# Compile and run the QSV ROI lifetime/layout regression without QSV hardware.
#
# Env:
#   FFMPEG_REPO  path to a local FFmpeg checkout (required)
#   JOBS         make -j value                    (default nproc)
set -euo pipefail

HERE="$(cd -- "$(dirname -- "$0")" && pwd -P)"
PATCHDIR="$(cd -- "$HERE/.." && pwd -P)"
ROOT="$(cd -- "$PATCHDIR/.." && pwd -P)"
# shellcheck source=build-config.env
source "$ROOT/build-config.env"
: "${FFMPEG_REPO:?FFMPEG_REPO must name a local FFmpeg checkout}"
JOBS="${JOBS:-$(nproc)}"
RUN_ROOT=""
OWNED_WORKTREE=""

canonicalize_existing_dir() {
    (cd -- "$1" 2>/dev/null && pwd -P)
}

if ! FFMPEG_REPO="$(canonicalize_existing_dir "$FFMPEG_REPO")"; then
    echo "ERROR: FFMPEG_REPO is not an accessible directory" >&2
    exit 1
fi

cleanup() {
    local status=$?
    local cleanup_failed=0
    trap - EXIT

    if [[ -n "$OWNED_WORKTREE" ]]; then
        git -C "$OWNED_WORKTREE" -c core.hooksPath=/dev/null \
            am --abort >/dev/null 2>&1 || true
        if ! git -C "$FFMPEG_REPO" -c core.hooksPath=/dev/null \
            worktree remove --force "$OWNED_WORKTREE" >/dev/null 2>&1; then
            echo "WARNING: could not remove owned worktree: $OWNED_WORKTREE" >&2
            cleanup_failed=1
        fi
    fi
    if [[ -n "$RUN_ROOT" ]] && ! rmdir "$RUN_ROOT" 2>/dev/null; then
        echo "WARNING: owned scratch directory is not empty: $RUN_ROOT" >&2
        cleanup_failed=1
    fi

    if (( status == 0 && cleanup_failed )); then
        status=1
    fi
    exit "$status"
}

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

RUN_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/pelorus-qsv-roi.XXXXXX")"
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

if ! CANONICAL_RUN_ROOT="$(canonicalize_existing_dir "$RUN_ROOT")"; then
    echo "ERROR: could not canonicalize scratch directory: $RUN_ROOT" >&2
    exit 1
fi
RUN_ROOT="$CANONICAL_RUN_ROOT"
WORKTREE="$RUN_ROOT/ffmpeg"

# Register first, then record ownership before the fallible checkout. Disable
# caller hooks for every operation that could invoke them.
git -C "$FFMPEG_REPO" -c core.hooksPath=/dev/null \
    worktree add --no-checkout --detach "$WORKTREE" "$FFMPEG_COMMIT"
OWNED_WORKTREE="$WORKTREE"
git -C "$WORKTREE" -c core.hooksPath=/dev/null \
    checkout --force --detach "$FFMPEG_COMMIT"

while IFS= read -r patch || [[ -n "$patch" ]]; do
    case "$patch" in
        ""|\#*) continue ;;
        0005-qsv-pelorus-roi.patch) break ;;
    esac
    git -C "$WORKTREE" \
        -c user.name=Pelorus-Replay \
        -c user.email=replay@pelorus.invalid \
        -c commit.gpgSign=false \
        -c core.hooksPath=/dev/null \
        -c diff.orderFile=/dev/null \
        am --3way "$PATCHDIR/$patch"
done < "$PATCHDIR/series.txt"
git -C "$WORKTREE" -c core.hooksPath=/dev/null \
    apply "$PATCHDIR/files/qsv-pelorus-roi.patch"

install -m 0644 "$HERE/qsv-roi-regression.c" \
    "$WORKTREE/libavcodec/tests/pelorus_qsv_roi.c"
printf '%s\n' "TESTPROGS-\$(CONFIG_QSVENC) += pelorus_qsv_roi" \
    >> "$WORKTREE/libavcodec/Makefile"

configure_qsv() {
    local toolchain="$1"
    local -a compiler_option
    shift

    if [[ -n "$toolchain" ]]; then
        compiler_option=(--toolchain="$toolchain")
    else
        compiler_option=(--cc=clang)
    fi

    (
        cd "$WORKTREE"
        ./configure \
            --disable-everything \
            --disable-doc \
            --disable-programs \
            --enable-encoder=h264_qsv \
            --enable-encoder=hevc_qsv \
            --enable-libvpl \
            --fatal-warnings \
            "${compiler_option[@]}" \
            "$@"
    )
}

echo "== MBQP-present sanitizer build and direct regression =="
configure_qsv clang-asan-ubsan
make -C "$WORKTREE" -j"$JOBS" \
    libavcodec/qsvenc.o \
    libavcodec/qsvenc_h264.o \
    libavcodec/qsvenc_hevc.o \
    libavcodec/tests/pelorus_qsv_roi
ASAN_OPTIONS="detect_leaks=1:halt_on_error=1" \
UBSAN_OPTIONS="halt_on_error=1:print_stacktrace=1" \
    "$WORKTREE/libavcodec/tests/pelorus_qsv_roi"

echo "== MBQP-absent compile gate =="
make -C "$WORKTREE" distclean
configure_qsv "" --extra-cflags=-DQSV_HAVE_MBQP=0
make -C "$WORKTREE" -j"$JOBS" \
    libavcodec/qsvenc.o \
    libavcodec/qsvenc_h264.o \
    libavcodec/qsvenc_hevc.o

echo "QSV ROI regression: PASS"
