#!/usr/bin/env bash
# shw-1: build + run the scratch device enumerators (DXGI order, oneVPL impls).
set -euo pipefail
S=/c/tmp/pel/shw-scratch
OUT=/c/tmp/pel/shw-results/shw-1/devices
mkdir -p "$OUT" "$S/bin"
# shellcheck disable=SC2046
gcc -O1 -Wall -o "$S/bin/vpl-enum.exe" "$S/vpl-enum.c" $(pkg-config --cflags --libs vpl)
gcc -O1 -Wall -o "$S/bin/dxgi-enum.exe" "$S/dxgi-enum.c" -ldxgi -luuid
"$S/bin/dxgi-enum.exe" | tee "$OUT/dxgi-adapters.txt"
"$S/bin/vpl-enum.exe" | tee "$OUT/vpl-implementations.txt"
