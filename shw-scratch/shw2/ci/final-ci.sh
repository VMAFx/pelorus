#!/usr/bin/env bash
# shw-2 final CI-parity checks on the committed HEAD (container):
#  - ffmpeg-stack "Patches match deterministic regeneration", run twice
#  - libpelorus job: build + fast suite + clang-format + clang-tidy
#  - docs job: changelog --check + ADR index rows
set -uo pipefail
rm -rf /tmp/head && git clone -q /w /tmp/head && cd /tmp/head && git log --oneline -1
while IFS= read -r entry; do
    case "$entry" in FFMPEG_REMOTE=*|FFMPEG_TAG=*|FFMPEG_COMMIT=*) export "${entry?}" ;; esac
done < build-config.env
git -C /ff worktree prune
for run in 1 2; do
    FFMPEG_REPO=/ff WORKTREE="/tmp/gen-$run" ffmpeg-patches/generate.sh >/tmp/gen-$run.log 2>&1 || { tail -20 /tmp/gen-$run.log; exit 1; }
    if git diff --quiet -- ffmpeg-patches/; then echo "regeneration $run: committed patches byte-identical"; else echo "regeneration $run: DIFFERS"; git diff --stat -- ffmpeg-patches/; fi
done
echo "== libpelorus job"
meson setup build >/tmp/m.log 2>&1 && ninja -C build >>/tmp/m.log 2>&1 || { tail -20 /tmp/m.log; exit 1; }
meson test -C build --suite=fast --print-errorlogs 2>&1 | grep -E '^(Ok|Fail|Expected|Unexpected|Skipped|Timeout):'
find libpelorus \( -name '*.c' -o -name '*.h' \) -exec clang-format --dry-run --Werror {} + && echo clang-format-ok
clang-tidy -p build libpelorus/src/*.c >/tmp/tidy.log 2>&1 && echo clang-tidy-ok || { echo clang-tidy-FAIL; tail -20 /tmp/tidy.log; }
echo "== docs job"
bash scripts/release/concat-changelog-fragments.sh --check
missing=0
for adr in docs/adr/[0-9]*.md; do
    n=$(basename "$adr" .md); n=${n%%-*}
    [ "$n" = "0000" ] && continue
    grep -q "\[$n\](" docs/adr/README.md || { echo "ADR $n missing from index"; missing=1; }
done
echo "adr-index missing=$missing"
