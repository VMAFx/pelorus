#!/usr/bin/env bash
# shw-2 step 28: Linux CI parity on the committed HEAD, inside pelorus-ci:26.04
# (docker --cpus=8 --memory=6g). Works on an in-container clone of /w so the
# bind-mounted clone is never written. Mirrors ci.yml:
#   - ffmpeg-stack: fetch+verify pinned FFmpeg, "Patches match deterministic
#     regeneration", "Replay all patches, link FFmpeg, and verify registrations"
#     (JOBS=8 resource cap instead of nproc; RUNNER_TEMP=/tmp)
#   - sanitizers: ASan+UBSan fast suite with clang
# Plus an informational black --check of the two Python guards touched by the
# stream, on HEAD and on origin/master (black is not a CI job).
set -uo pipefail
rm -rf /tmp/head && git clone -q /w /tmp/head && cd /tmp/head || exit 1
echo "HEAD $(git log --oneline -1)"
RUNNER_TEMP=/tmp; GITHUB_RUN_ID=local; GITHUB_RUN_ATTEMPT=1
while IFS= read -r entry; do
    case "$entry" in FFMPEG_REMOTE=*|FFMPEG_TAG=*|FFMPEG_COMMIT=*) export "${entry?}" ;; esac
done < build-config.env
rc_all=0

echo "== ffmpeg-stack: Fetch and verify pinned FFmpeg source"
git init -q ffmpeg
git -C ffmpeg remote add origin "$FFMPEG_REMOTE"
git -C ffmpeg fetch -q --depth=1 origin "refs/tags/${FFMPEG_TAG}:refs/tags/${FFMPEG_TAG}" || exit 1
actual=$(git -C ffmpeg rev-parse --verify "refs/tags/${FFMPEG_TAG}^{commit}")
test "$actual" = "$FFMPEG_COMMIT" || { echo "::error::${FFMPEG_TAG} peels to ${actual}, expected ${FFMPEG_COMMIT}"; exit 1; }
echo "OK ${FFMPEG_TAG} -> ${actual}"

echo "== ffmpeg-stack: Patches match deterministic regeneration"
FFMPEG_REPO="$PWD/ffmpeg" WORKTREE="$RUNNER_TEMP/pelorus-gen-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}" \
    ffmpeg-patches/generate.sh > /tmp/gen.log 2>&1 || { tail -30 /tmp/gen.log; exit 1; }
if ! git diff --quiet -- ffmpeg-patches/; then
    echo "::error::generated FFmpeg patches differ from canonical files"; git diff --stat -- ffmpeg-patches/; exit 1
fi
echo "committed patches are byte-identical to fresh regeneration"

echo "== ffmpeg-stack: Replay all patches, link FFmpeg, and verify registrations"
start=$(date +%s)
FFMPEG_REPO="$PWD/ffmpeg" JOBS=8 ffmpeg-patches/test/build-and-run.sh > /tmp/bar.log 2>&1
rc=$?
echo "build-and-run.sh rc=$rc after $(( $(date +%s) - start ))s"
grep -vE '^(CC|AR|LD|LDXX|STRIP|INSTALL|GLSLC|GEN|HOSTCC|HOSTLD|X86ASM|CXX|SPIRV)[[:space:]]' /tmp/bar.log | tail -60
cp /tmp/bar.log /w-results/28-build-and-run.full.log 2>/dev/null || true
[ $rc -eq 0 ] || rc_all=1
echo "warnings in Pelorus-touched units:"
grep -E 'warning:' /tmp/bar.log | grep -iE 'pelorus|qsvenc|nvenc|libaomenc|libsvtav1|vulkan_encode|bsf/pelorus' | sort -u | head -20 || true

echo "== sanitizers job"
CC=clang meson setup build-san -Db_sanitize=address,undefined -Db_lundef=false \
    -Dc_args=-fno-sanitize-recover=alignment > /tmp/san.log 2>&1 || { tail -20 /tmp/san.log; rc_all=1; }
CC=clang meson test -C build-san --suite=fast --print-errorlogs 2>&1 | grep -E '^(Ok|Fail|Expected|Unexpected|Skipped|Timeout):|FAIL'
[ "${PIPESTATUS[0]}" -eq 0 ] || rc_all=1

echo "== informational: black --check (not a CI job)"
for f in scripts/check-shader-bindings.py scripts/check-vulkan-storage-domain.py; do
    printf 'HEAD   %-42s ' "$f"; black -q --check "$f" && echo clean || echo "would reformat"
    printf 'f03badd %-41s ' "$f"; git show "f03badd:$f" | black -q --check - && echo clean || echo "would reformat"
done
echo "ci-parity rc_all=$rc_all"
exit $rc_all
