#!/usr/bin/env bash
# shw-3 step 01: build + run the oneVPL capability probe on both adapters.
# Run from MSYS2 UCRT64.  Output: shw-results/qsv/01-caps/impl{0,1}.txt
set -uo pipefail
SCR=/c/tmp/pel/shw-scratch/qsv
OUT=/c/tmp/pel/shw-results/qsv/01-caps
mkdir -p "$OUT"
cd "$SCR" || exit 1
gcc -O1 -Wall -o qsv-caps-probe.exe qsv-caps-probe.c $(pkg-config --cflags --libs vpl) 2>&1 | tee "$OUT/build.log"
for i in 0 1; do
    ./qsv-caps-probe.exe $i >"$OUT/impl$i.txt" 2>&1
    echo "impl$i rc=$?" | tee -a "$OUT/rc.txt"
done
