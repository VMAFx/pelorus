#!/usr/bin/env bash
#
# fetch-corpus.sh — materialize the pinned benchmark corpus as raw YUV.
#
# Copyright 2026 Lusoris. BSD-2-Clause-Patent.
#
# Reads scripts/bench/corpus.lock, downloads each pinned clip (cached + sha256
# verified), and extracts its pinned segment to raw YUV at the pinned
# resolution/format. Output: <corpus-dir>/<name>.yuv (+ a .meta with dims).
# Makes the bench reproducible from a pinned source on any machine.
#
#   FFMPEG=/path/to/ffmpeg CORPUS=.bench-corpus ./fetch-corpus.sh [name...]
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
FFMPEG="${FFMPEG:-ffmpeg}"
CORPUS="${CORPUS:-$ROOT/.bench-corpus}"
LOCK="$HERE/corpus.lock"
mkdir -p "$CORPUS"

want=("$@")
want_clip() { [ ${#want[@]} -eq 0 ] && return 0; for w in "${want[@]}"; do [ "$w" = "$1" ] && return 0; done; return 1; }

while IFS='|' read -r name url sha seek scale pixfmt frames fps; do
    name="$(echo "$name" | xargs)"; case "$name" in ''|\#*) continue;; esac
    want_clip "$name" || continue
    url="$(echo "$url" | xargs)"; sha="$(echo "$sha" | xargs)"; seek="$(echo "$seek" | xargs)"
    scale="$(echo "$scale" | xargs)"; pixfmt="$(echo "$pixfmt" | xargs)"
    frames="$(echo "$frames" | xargs)"; fps="$(echo "$fps" | xargs)"
    w="${scale%x*}"; h="${scale#*x}"
    yuv="$CORPUS/$name.yuv"

    echo "== $name (${scale} ${pixfmt} ${frames}f@${fps}) =="
    if [ "$url" = "lavfi:gradients" ]; then
        # deterministic synthetic dark gradient (banding torture)
        "$FFMPEG" -hide_banner -loglevel error -y \
            -f lavfi -i "gradients=size=${scale}:x0=0:y0=0:x1=0:y1=${h}:c0=0x060608:c1=0x2a3038:nb_colors=2:rate=${fps}" \
            -vf "format=${pixfmt}" -frames:v "$frames" -f rawvideo "$yuv"
    else
        local_dl="$CORPUS/$(basename "$url")"
        if [ ! -f "$local_dl" ] || [ "$(sha256sum "$local_dl" | cut -d' ' -f1)" != "$sha" ]; then
            echo "  downloading $url"
            curl -sL -o "$local_dl" "$url"
        fi
        got="$(sha256sum "$local_dl" | cut -d' ' -f1)"
        [ "$got" = "$sha" ] || { echo "  SHA MISMATCH: got $got want $sha" >&2; exit 1; }
        echo "  sha256 OK; extracting pinned segment $seek -> $yuv"
        # shellcheck disable=SC2086
        "$FFMPEG" -hide_banner -loglevel error -y $seek -i "$local_dl" \
            -vf "scale=${w}:${h}:flags=bicubic,format=${pixfmt}" \
            -frames:v "$frames" -an -f rawvideo "$yuv"
    fi
    printf 'width=%s\nheight=%s\npixfmt=%s\nframes=%s\nfps=%s\n' \
        "$w" "$h" "$pixfmt" "$frames" "$fps" > "$CORPUS/$name.meta"
    echo "  ready: $yuv ($(stat -c%s "$yuv") bytes)"
done < "$LOCK"
