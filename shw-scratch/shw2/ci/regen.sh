#!/usr/bin/env bash
# shw-2: CI-parity patch regeneration inside pelorus-ci:26.04 (mirrors ci.yml
# ffmpeg-stack "Fetch and verify pinned FFmpeg source" + "Patches match
# deterministic regeneration").  /w = the stream clone, /ff = cached FFmpeg.
set -euo pipefail
cd /w
while IFS= read -r entry; do
    case "$entry" in FFMPEG_REMOTE=*|FFMPEG_TAG=*|FFMPEG_COMMIT=*) export "${entry?}" ;; esac
done < build-config.env
if [[ ! -d /ff/.git ]]; then
    git init -q /ff
    git -C /ff remote add origin "$FFMPEG_REMOTE"
fi
if ! git -C /ff rev-parse -q --verify "refs/tags/${FFMPEG_TAG}^{commit}" >/dev/null; then
    git -C /ff fetch -q --depth=1 origin "refs/tags/${FFMPEG_TAG}:refs/tags/${FFMPEG_TAG}"
fi
# Windows bind mounts surface every file as 0755; CI's Linux checkout is 0644.
# With core.fileMode=false git records new files as 100644, matching CI.
git -C /ff config core.fileMode false
actual=$(git -C /ff rev-parse --verify "refs/tags/${FFMPEG_TAG}^{commit}")
test "$actual" = "$FFMPEG_COMMIT" || { echo "tag peels to $actual, expected $FFMPEG_COMMIT"; exit 1; }
echo "FFmpeg ${FFMPEG_TAG} = $actual"
FFMPEG_REPO=/ff WORKTREE="/tmp/pelorus-gen-$$" ffmpeg-patches/generate.sh
if git diff --quiet -- ffmpeg-patches/; then
    echo "committed patches are byte-identical to fresh regeneration"
else
    echo "generated FFmpeg patches differ from committed:"; git diff --stat -- ffmpeg-patches/
fi
