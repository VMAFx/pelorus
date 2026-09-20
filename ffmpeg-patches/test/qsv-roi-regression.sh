#!/usr/bin/env bash
# Compile and run the QSV ROI lifetime/layout regression without QSV hardware.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PATCHDIR="$(dirname "$HERE")"
FFMPEG_REPO="${FFMPEG_REPO:-/home/kilian/dev/ffmpeg-9}"
BASE_TAG="${BASE_TAG:-n9.0.1}"
JOBS="${JOBS:-$(nproc)}"
TMP_ROOT="$(mktemp -d /tmp/pelorus-qsv-roi.XXXXXX)"
WORKTREE="$TMP_ROOT/ffmpeg"

cleanup() {
    git -C "$FFMPEG_REPO" worktree remove --force "$WORKTREE" \
        >/dev/null 2>&1 || true
    rm -rf -- "$TMP_ROOT"
}
trap cleanup EXIT

git -C "$FFMPEG_REPO" worktree add --detach "$WORKTREE" "$BASE_TAG"

while read -r patch; do
    case "$patch" in
        ""|\#*) continue ;;
        0005-qsv-pelorus-roi.patch) break ;;
    esac
    git -C "$WORKTREE" am --3way "$PATCHDIR/$patch"
done < "$PATCHDIR/series.txt"
git -C "$WORKTREE" apply "$PATCHDIR/files/qsv-pelorus-roi.patch"

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
