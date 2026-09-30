#!/usr/bin/env bash
# shw-1 step 9b: isolate each MSYS2 incompatibility in build-and-run.sh without
# a full run. Uses the already-built/installed C:/tmp/pel/prefix.
set -uo pipefail
T="$(mktemp -d "${TMPDIR:-/tmp}/pel-probe.XXXXXX")"; T="$(cd "$T" && pwd -P)"
echo "RUN_ROOT-like dir (MSYS form): $T   native: $(cygpath -m "$T")"
# 1. meson --prefix=/tmp/... is argv-converted for native meson -> pkg-config
#    reports the Windows form, so the script's string compare fails.
meson setup "$T/b" /c/tmp/pel/shw --prefix="$T/prefix" --libdir=lib -Dtests=false -Dshaders=false -Dtools=false >/dev/null 2>&1 \
  && meson install -C "$T/b" >/dev/null 2>&1
export PKG_CONFIG_PATH="$T/prefix/lib/pkgconfig"
echo "1. pkg-config prefix: '$(pkg-config --variable=prefix libpelorus)' vs PRIVATE_PREFIX '$T/prefix'"
# 2. restricted PKG_CONFIG_PATH still sees the MSYS2 system modules?
for m in vpl aom SvtAv1Enc ffnvcodec vulkan; do printf '2. %s: ' "$m"; pkg-config --modversion "$m" 2>&1; done
# 3. runtime: ffmpeg.exe imports libpelorus-0.dll; without <prefix>/bin on PATH
objdump -p /c/tmp/pel/prefix/bin/ffmpeg.exe | grep -i "DLL Name" | grep -iE "pelorus|vulkan|vpl|vmaf|x265|SvtAv1|aom" | sed 's/^/3. imports /'
env PATH="/ucrt64/bin:/usr/bin" /c/tmp/pel/prefix/bin/ffmpeg.exe -hide_banner -version >/dev/null 2>&1; echo "3. ffmpeg.exe without prefix/bin on PATH: rc=$? (127/0xC0000135 = DLL not found)"
# 4. make target name
( cd /c/tmp/pel/ffmpeg && make -n ffmpeg >/dev/null 2>&1; echo "4. make -n ffmpeg rc=$?"; make -n ffmpeg.exe >/dev/null 2>&1; echo "4. make -n ffmpeg.exe rc=$?" )
# 5. does a native gcc accept MSYS-form -I/-L from a .pc with prefix=/tmp/...?
mkdir -p "$T/inc"; printf '#define PROBE 1\n' > "$T/inc/probe.h"
printf '#include <probe.h>\nint main(void){return PROBE-1;}\n' > "$T/p.c"
gcc -I"$T/inc" "$T/p.c" -o "$T/p" 2>&1 | head -2; echo "5. gcc -I<msys /tmp path>: rc=${PIPESTATUS[0]}"
rm -rf "$T"
