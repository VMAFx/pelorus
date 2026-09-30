#!/usr/bin/env bash
# shw-1 step 11: Linux CI parity for the build-and-run.sh change, run inside
# pelorus-ci:26.04 (docker --cpus=8 --memory=6g). Mirrors ci.yml's
# "ffmpeg-stack" job step by step. Deviations (resource/container only):
#   - the FFmpeg repo lives at /tmp/ffmpeg (container-local) instead of
#     $GITHUB_WORKSPACE/ffmpeg, so nothing is added to the bind-mounted clone;
#   - RUNNER_TEMP=/tmp, GITHUB_RUN_ID=local, GITHUB_RUN_ATTEMPT=1;
#   - JOBS=8 (resource cap) instead of nproc.
# Plus shellcheck of the changed script (the docs job does not run it; this is
# a lint receipt only).
set -euo pipefail
cd /w
RUNNER_TEMP=/tmp; GITHUB_RUN_ID=local; GITHUB_RUN_ATTEMPT=1

echo "== Load build configuration"
while IFS= read -r entry; do
  case "$entry" in
    FFMPEG_REMOTE=*|FFMPEG_TAG=*|FFMPEG_COMMIT=*) export "${entry?}" ;;
  esac
done < build-config.env

echo "== Runner and toolchain receipt"
cat /etc/os-release | head -3
for module in vulkan vpl aom SvtAv1Enc ffnvcodec; do
  printf '%s=' "$module"
  pkg-config --modversion "$module"
done
glslc --version | head -1
glslangValidator --version | head -1
shellcheck --version | sed -n 2p

echo "== shellcheck ffmpeg-patches/test/build-and-run.sh"
shellcheck -x ffmpeg-patches/test/build-and-run.sh && echo "shellcheck clean"

echo "== Fetch and verify pinned FFmpeg source"
git init -q /tmp/ffmpeg
git -C /tmp/ffmpeg remote add origin "$FFMPEG_REMOTE"
git -C /tmp/ffmpeg fetch -q --depth=1 origin \
  "refs/tags/${FFMPEG_TAG}:refs/tags/${FFMPEG_TAG}"
actual=$(git -C /tmp/ffmpeg rev-parse --verify \
  "refs/tags/${FFMPEG_TAG}^{commit}")
test "$actual" = "$FFMPEG_COMMIT" || {
  echo "::error::${FFMPEG_TAG} peels to ${actual}, expected ${FFMPEG_COMMIT}"
  exit 1
}
echo "OK ${FFMPEG_TAG} -> ${actual}"

echo "== Patches match deterministic regeneration"
FFMPEG_REPO="/tmp/ffmpeg" \
  WORKTREE="$RUNNER_TEMP/pelorus-gen-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}" \
  ffmpeg-patches/generate.sh
if ! git diff --quiet -- ffmpeg-patches/; then
  echo "::error::generated FFmpeg patches differ from canonical files"
  git diff --stat -- ffmpeg-patches/
  exit 1
fi
echo "committed patches are byte-identical to fresh regeneration"

echo "== Replay all patches, link FFmpeg, and verify registrations"
FFMPEG_REPO="/tmp/ffmpeg" JOBS=8 \
  ffmpeg-patches/test/build-and-run.sh
echo "== ffmpeg-stack parity: PASS"
