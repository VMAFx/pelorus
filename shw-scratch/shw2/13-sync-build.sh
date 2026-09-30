#!/usr/bin/env bash
# shw-2 step 13: rebuild the installed FFmpeg from EXACTLY the regenerated patch
# stack in the clone.  Replays series.txt onto the pinned commit in a scratch
# worktree (git am --3way, ephemeral identity, hooks neutralised - as
# build-and-run.sh apply_stack), then moves the persistent build tree to that
# commit (git only rewrites files that differ, so make stays incremental).
set -euo pipefail
TAG="${1:?usage: 13-sync-build.sh <branch-suffix>}"
ROOT=/c/tmp/pel/shw
PATCHDIR="$ROOT/ffmpeg-patches"
FF=/c/tmp/pel/ffmpeg
WT=/c/tmp/pel/shw-scratch/ff-am-$TAG
LOGS=/c/tmp/pel/shw-results/shw-2/13-build-$TAG; mkdir -p "$LOGS"
source "$ROOT/build-config.env"
BR="shw/pelorus-regen-$TAG"
git -C "$FF" worktree prune
rm -rf "$WT"
git -C "$FF" -c core.hooksPath=/dev/null worktree add --detach "$WT" "$FFMPEG_COMMIT" >/dev/null
n=0
while IFS= read -r patch || [[ -n "$patch" ]]; do
    case "$patch" in ''|'#'*) continue ;; esac
    git -C "$WT" -c core.hooksPath=/dev/null -c user.name=Pelorus-replay \
        -c user.email=ffmpeg-replay@pelorus.invalid -c commit.gpgSign=false \
        am --3way --no-gpg-sign --no-verify -q "$PATCHDIR/$patch"
    n=$((n + 1))
done < "$PATCHDIR/series.txt"
(( n == 18 )) || { echo "expected 18 patches, applied $n" >&2; exit 1; }
NEW=$(git -C "$WT" rev-parse HEAD)
git -C "$FF" branch -f "$BR" "$NEW"
git -C "$FF" worktree remove --force "$WT"
echo "replayed $n patches -> $BR = $NEW"
echo "files changed vs current build tree HEAD $(git -C "$FF" rev-parse --short HEAD):"
git -C "$FF" diff --stat HEAD "$NEW" | tail -20
git -C "$FF" -c core.hooksPath=/dev/null checkout -q --force "$BR"
git -C "$FF" diff --quiet HEAD || { echo "build tree differs from $BR" >&2; exit 1; }
export PKG_CONFIG_PATH="/c/tmp/pel/prefix/lib/pkgconfig:/ucrt64/lib/pkgconfig:/ucrt64/share/pkgconfig"
export PATH="/c/tmp/pel/prefix/bin:$PATH"
EXESUF="$(sed -n 's/^EXESUF=//p' "$FF/ffbuild/config.mak")"
start=$(date +%s)
make -C "$FF" -j8 "ffmpeg${EXESUF}" > "$LOGS/make.log" 2>&1 || { tail -40 "$LOGS/make.log"; exit 1; }
make -C "$FF" -j8 install > "$LOGS/install.log" 2>&1 || { tail -40 "$LOGS/install.log"; exit 1; }
echo "built+installed in $(( $(date +%s) - start ))s; recompiled objects:"
grep -oE '^CC\s+\S+|^GLSLC\s+\S+|^SPIRV\S*\s+\S+|^[A-Z]+\s+libav\S+' "$LOGS/make.log" | sort -u | head -30
echo "warnings in rebuilt units: $(grep -c 'warning:' "$LOGS/make.log")"; grep 'warning:' "$LOGS/make.log" | head -10
/c/tmp/pel/prefix/bin/ffmpeg.exe -hide_banner -version | head -1
