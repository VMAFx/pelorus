#!/usr/bin/env bash
# shw-2: CI-parity local gate in pelorus-ci:26.04 on a private copy of the
# working tree (mirrors ci.yml "libpelorus" job commands + shellcheck/black on
# touched scripts).
set -uo pipefail
rm -rf /tmp/repo && mkdir /tmp/repo && cd /w && git ls-files -z --cached --others --exclude-standard | \
    xargs -0 -I{} cp --parents -- {} /tmp/repo/ 2>/dev/null
cd /tmp/repo && git -C /w rev-parse HEAD > .git-head 2>/dev/null
find . -type f \( -name '*.sh' -o -name '*.py' \) -exec chmod 755 {} +
echo "== meson setup + ninja"; (meson setup build >/tmp/meson.log 2>&1 && ninja -C build >>/tmp/meson.log 2>&1) || { tail -30 /tmp/meson.log; exit 1; }
echo "== meson test --suite=fast"; meson test -C build --suite=fast --print-errorlogs 2>&1 | tail -25
echo "== clang-format (libpelorus, as CI)"; find libpelorus \( -name '*.c' -o -name '*.h' \) -exec clang-format --dry-run --Werror {} + && echo clang-format-ok
echo "== shellcheck touched shell"; shellcheck -x ffmpeg-patches/test/vulkan-format-matrix.sh ffmpeg-patches/test/vulkan-format-matrix-validation-self-test.sh && echo shellcheck-ok
echo "== black --check touched python"; black --check scripts/check-vulkan-storage-domain.py 2>&1 | tail -2; black --check scripts/check-shader-bindings.py 2>&1 | tail -2
echo "== glslc compile of every canonical shader (FFmpeg-tree shaders need FFmpeg includes? try standalone)"
for g in ffmpeg-patches/files/vulkan/*.comp.glsl; do glslc -fshader-stage=compute --target-env=vulkan1.3 -o /dev/null "$g" 2>&1 | head -3 && echo "  ok $g"; done
