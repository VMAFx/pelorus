#!/usr/bin/env bash
# shw-1 step 2 (resumed run): build + fast-test + install libpelorus natively
# (MSYS2 UCRT64), mirroring ffmpeg-patches/test/build-and-run.sh's libpelorus
# stage with a persistent prefix (C:/tmp/pel/prefix) instead of a mktemp one.
# Windows difference: build-and-run.sh aborts when the fast suite fails; the
# build-config-sync self-test is known to fail natively (stream s61's lane), so
# the test result is recorded and the install proceeds.
set -euo pipefail
ROOT=/c/tmp/pel/shw
BUILD=/c/tmp/pel/shw-scratch/build/pelorus-ucrt64
PREFIX=C:/tmp/pel/prefix          # Windows form so meson/pkgconf record it verbatim
LOGS=/c/tmp/pel/shw-results/shw-1
JOBS="${JOBS:-8}"
mkdir -p "$(dirname "$BUILD")" "$LOGS"
rm -rf "$BUILD"

run_logged() {
    local label="$1" log="$2"; shift 2
    echo "== $label =="
    if ! "$@" >"$log" 2>&1; then
        echo "ERROR: $label failed; tail of $log:" >&2
        tail -40 "$log" >&2 || true
        return 1
    fi
}

run_logged "configure libpelorus" "$LOGS/02-pelorus-configure.log" \
    meson setup "$BUILD" "$ROOT" --prefix="$PREFIX" --libdir=lib
run_logged "build libpelorus" "$LOGS/02-pelorus-build.log" \
    meson compile -C "$BUILD" -j "$JOBS"
if run_logged "test libpelorus" "$LOGS/02-pelorus-test.log" \
    meson test -C "$BUILD" --suite=fast --print-errorlogs --num-processes "$JOBS"; then
    echo "fast suite: PASS"
else
    echo "fast suite: FAIL (recorded; see $LOGS/02-pelorus-test.log)"
fi
run_logged "install libpelorus" "$LOGS/02-pelorus-install.log" \
    meson install -C "$BUILD"

export PKG_CONFIG_PATH="/c/tmp/pel/prefix/lib/pkgconfig"
echo "pkg-config prefix: $(pkg-config --variable=prefix libpelorus)"
echo "libpelorus version: $(pkg-config --modversion libpelorus)"
echo "cflags: $(pkg-config --cflags libpelorus)"
echo "libs:   $(pkg-config --libs libpelorus)"
echo "static: $(pkg-config --static --libs libpelorus)"
grep -E '^ *[0-9]+/[0-9]+ |^(Ok|Fail|Expected Fail|Skipped|Timeout):' "$LOGS/02-pelorus-test.log" | tail -40
find /c/tmp/pel/prefix -maxdepth 3 -type f | sort
