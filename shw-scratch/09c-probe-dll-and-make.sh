#!/usr/bin/env bash
# shw-1 step 9c: (3) the in-tree ffmpeg.exe (what build-and-run.sh executes)
# without <prefix>/bin on PATH; (4) make's rule database for 'ffmpeg' vs 'ffmpeg.exe'.
set -uo pipefail
cd /c/tmp/pel/ffmpeg
env PATH="/ucrt64/bin:/usr/bin" ./ffmpeg.exe -hide_banner -version >/dev/null 2>&1
echo "3. in-tree ffmpeg.exe, PATH without prefix/bin: rc=$?"
env PATH="/c/tmp/pel/prefix/bin:/ucrt64/bin:/usr/bin" ./ffmpeg.exe -hide_banner -version >/dev/null 2>&1
echo "3. in-tree ffmpeg.exe, PATH with prefix/bin:    rc=$?"
make -pn -f Makefile .DEFAULT 2>/dev/null | grep -E '^ffmpeg(\.exe|_g\.exe)?:' | sort -u | sed 's/^/4. rule: /'
