#!/usr/bin/env bash
# shw-3 step 02: build the raw oneVPL encode probe and the H.264 per-MB QP dumper.
set -uo pipefail
SCR=/c/tmp/pel/shw-scratch/qsv
OUT=/c/tmp/pel/shw-results/qsv/02-build
mkdir -p "$OUT"
cd "$SCR" || exit 1
{
gcc -O1 -Wall -o qsv-encode-probe.exe qsv-encode-probe.c $(pkg-config --cflags --libs vpl); echo "encode-probe rc=$?"
PKG_CONFIG_PATH=/c/tmp/pel/prefix/lib/pkgconfig gcc -O1 -Wall -o qpdump.exe qpdump.c \
    $(PKG_CONFIG_PATH=/c/tmp/pel/prefix/lib/pkgconfig pkg-config --static --cflags --libs libavformat libavcodec libavutil); echo "qpdump rc=$?"
} 2>&1 | tee "$OUT/build.log"
