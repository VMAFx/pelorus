#!/usr/bin/env bash
# shw-1 step 1: fetch the pinned FFmpeg tag exactly like ci.yml's ffmpeg-stack job
# ("Fetch and verify pinned FFmpeg source") into /c/tmp/pel/ffmpeg.
set -euo pipefail
PEL=/c/tmp/pel/shw
while IFS= read -r entry; do
    case "$entry" in
        FFMPEG_REMOTE=*|FFMPEG_TAG=*|FFMPEG_COMMIT=*) export "${entry?}" ;;
    esac
done < "$PEL/build-config.env"
echo "FFMPEG_REMOTE=$FFMPEG_REMOTE FFMPEG_TAG=$FFMPEG_TAG FFMPEG_COMMIT=$FFMPEG_COMMIT"
cd /c/tmp/pel
git init ffmpeg
# keep LF (FFmpeg's configure and Makefiles must not be CRLF-converted)
git -C ffmpeg config core.autocrlf false
git -C ffmpeg remote add origin "$FFMPEG_REMOTE"
git -C ffmpeg fetch --depth=1 origin \
    "refs/tags/${FFMPEG_TAG}:refs/tags/${FFMPEG_TAG}"
actual=$(git -C ffmpeg rev-parse --verify "refs/tags/${FFMPEG_TAG}^{commit}")
test "$actual" = "$FFMPEG_COMMIT" || {
    echo "::error::${FFMPEG_TAG} peels to ${actual}, expected ${FFMPEG_COMMIT}"
    exit 1
}
echo "OK: ${FFMPEG_TAG} peels to ${actual} (== FFMPEG_COMMIT)"
git -C ffmpeg cat-file -t "refs/tags/${FFMPEG_TAG}"
