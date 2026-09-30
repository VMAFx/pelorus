#!/usr/bin/env bash
# shw-1 step 3: replay ffmpeg-patches/series.txt onto the pinned FFmpeg commit,
# exactly as ffmpeg-patches/test/build-and-run.sh's apply_stack() does
# (git am --3way, ephemeral identity, hooks neutralised, no signing).
# Windows/stream difference: the replay happens in the persistent main worktree
# of /c/tmp/pel/ffmpeg on branch shw/pelorus-n9.0.2 (so the build and install
# persist for later stages) instead of a run-owned mktemp worktree.
set -euo pipefail
ROOT=/c/tmp/pel/shw
PATCHDIR="$ROOT/ffmpeg-patches"
FF=/c/tmp/pel/ffmpeg
# shellcheck source=/dev/null
source "$ROOT/build-config.env"

TAG_COMMIT="$(git -C "$FF" rev-parse --verify "refs/tags/${FFMPEG_TAG}^{commit}")"
if [[ "$TAG_COMMIT" != "$FFMPEG_COMMIT" ]]; then
    echo "ERROR: $FFMPEG_TAG peels to $TAG_COMMIT, expected $FFMPEG_COMMIT" >&2
    exit 1
fi
echo "OK: $FFMPEG_TAG peels to $TAG_COMMIT"

PATCHES=()
while IFS= read -r patch || [[ -n "$patch" ]]; do
    case "$patch" in ''|'#'*) continue ;; esac
    [[ -f "$PATCHDIR/$patch" ]] || { echo "ERROR: missing $patch" >&2; exit 1; }
    PATCHES+=("$patch")
done < "$PATCHDIR/series.txt"
(( ${#PATCHES[@]} == 18 )) || { echo "ERROR: expected 18 patches, found ${#PATCHES[@]}" >&2; exit 1; }

git -C "$FF" config core.autocrlf false
git -C "$FF" -c core.hooksPath=/dev/null am --abort >/dev/null 2>&1 || true
git -C "$FF" -c core.hooksPath=/dev/null checkout --force -B shw/pelorus-n9.0.2 "$FFMPEG_COMMIT"
for patch in "${PATCHES[@]}"; do
    echo "am: $patch"
    git -C "$FF" -c core.hooksPath=/dev/null \
        -c user.name=Pelorus-replay \
        -c user.email=ffmpeg-replay@pelorus.invalid \
        -c commit.gpgSign=false \
        am --3way --no-gpg-sign --no-verify "$PATCHDIR/$patch"
done
git -C "$FF" log --oneline "$FFMPEG_COMMIT"..HEAD
echo "applied ${#PATCHES[@]} patches; HEAD=$(git -C "$FF" rev-parse HEAD)"
# CRLF sanity: nothing the stack added may carry CR bytes
if git -C "$FF" diff "$FFMPEG_COMMIT"..HEAD | grep -q $'\r'; then
    echo "WARNING: CR bytes present in the applied diff" >&2
else
    echo "no CR bytes in applied diff"
fi
