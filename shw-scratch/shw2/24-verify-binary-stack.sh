#!/usr/bin/env bash
# shw-2 step 24: prove the installed FFmpeg was built from exactly the committed
# patch stack: replay HEAD's series in a scratch worktree and compare its tree
# with the build tree's branch (shw/pelorus-regen-d1d2d3).
set -euo pipefail
ROOT=/c/tmp/pel/shw; FF=/c/tmp/pel/ffmpeg; WT=/c/tmp/pel/shw-scratch/ff-am-verify
source "$ROOT/build-config.env"
git -C "$FF" worktree prune; rm -rf "$WT"
git -C "$FF" -c core.hooksPath=/dev/null worktree add -q --detach "$WT" "$FFMPEG_COMMIT"
while IFS= read -r p || [[ -n "$p" ]]; do
    case "$p" in ''|'#'*) continue ;; esac
    git -C "$WT" -c core.hooksPath=/dev/null -c user.name=Pelorus-replay -c user.email=ffmpeg-replay@pelorus.invalid \
        -c commit.gpgSign=false am --3way --no-gpg-sign --no-verify -q "$ROOT/ffmpeg-patches/$p"
done < "$ROOT/ffmpeg-patches/series.txt"
new=$(git -C "$WT" rev-parse HEAD)
git -C "$FF" worktree remove --force "$WT"
echo "clone HEAD $(git -C "$ROOT" rev-parse --short HEAD) replays to tree $(git -C "$FF" rev-parse "$new^{tree}")"
echo "build branch shw/pelorus-regen-d1d2d3 tree        $(git -C "$FF" rev-parse "shw/pelorus-regen-d1d2d3^{tree}")"
echo "build tree checkout HEAD                          $(git -C "$FF" rev-parse --abbrev-ref HEAD) $(git -C "$FF" rev-parse --short HEAD); uncommitted tracked changes: $(git -C "$FF" status --porcelain --untracked-files=no | wc -l)"
if [[ "$(git -C "$FF" rev-parse "$new^{tree}")" == "$(git -C "$FF" rev-parse "shw/pelorus-regen-d1d2d3^{tree}")" ]]; then
    echo "IDENTICAL: installed ffmpeg.exe was built from the committed patch stack"
else
    git -C "$FF" diff --stat "$new" shw/pelorus-regen-d1d2d3
fi
