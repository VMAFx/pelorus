#!/usr/bin/env bash
# shw-1 step 10: run the (patched) repo gate ffmpeg-patches/test/build-and-run.sh
# itself, natively under MSYS2 UCRT64, end to end.
# Environment difference (documented): "git" resolves to Git for Windows
# (/c/Program Files/Git/cmd) ahead of MSYS2's /usr/bin/git, because the
# fast-suite build-config-sync self-test fails with MSYS git (argv brace
# expansion when called from native Python) -- that fix is stream s61's lane.
set -euo pipefail
export PATH="/c/Program Files/Git/cmd:$PATH"
echo "git: $(command -v git) ($(git --version))"
cd /c/tmp/pel/shw
start=$(date +%s)
FFMPEG_REPO=/c/tmp/pel/ffmpeg JOBS=8 bash ffmpeg-patches/test/build-and-run.sh
echo "build-and-run.sh finished rc=0 in $(( $(date +%s) - start ))s"
