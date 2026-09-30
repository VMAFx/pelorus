#!/usr/bin/env bash
# shw-2 step 30: ci.yml "sanitizers" job on HEAD in a throwaway pelorus-ci:26.04
# container after installing the clang sanitizer runtime the image lacks
# (step 29: libclang_rt.asan*.a missing). Same commands as ci.yml.
set -uo pipefail
export DEBIAN_FRONTEND=noninteractive
{ apt-get update -qq && apt-get install -y -qq --no-install-recommends libclang-rt-21-dev; } >/tmp/apt.log 2>&1 \
    || { tail -5 /tmp/apt.log; exit 2; }
echo "installed: $(dpkg-query -W -f='${Package} ${Version}' libclang-rt-21-dev)"
rm -rf /tmp/head && git clone -q /w /tmp/head && cd /tmp/head || exit 1
echo "HEAD $(git log --oneline -1)"
export CC=clang
meson setup build-san -Db_sanitize=address,undefined -Db_lundef=false \
    -Dc_args=-fno-sanitize-recover=alignment >/tmp/san.log 2>&1 || { tail -20 /tmp/san.log; exit 1; }
meson test -C build-san --suite=fast --print-errorlogs >/tmp/santest.log 2>&1
rc=$?
grep -E '^ *[0-9]+/[0-9]+ ' /tmp/santest.log | grep -vE ' OK ' || true
grep -E '^(Ok|Fail|Expected|Unexpected|Skipped|Timeout):' /tmp/santest.log
echo "sanitizers job rc=$rc"
exit $rc
