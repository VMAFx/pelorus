#!/usr/bin/env bash
# shw-1 step 8b: diagnose the QSV hwupload failure ("Error initializing a child
# frames context") and try the alternative device/frame paths.
set -uo pipefail
export PATH="/c/tmp/pel/prefix/bin:$PATH"
FFM=/c/tmp/pel/prefix/bin/ffmpeg.exe
OUT=/c/tmp/pel/shw-results/shw-1/devices
SRC=(-f lavfi -i "testsrc2=size=1280x720:rate=24:duration=1")
run() { local label="$1"; shift
    if "$FFM" -hide_banner -nostdin "$@" > "$OUT/$label.log" 2>&1; then echo "PASS $label"
    else echo "FAIL $label (rc=$?)"; fi; }
# A: debug log of the failing hwupload path (B580)
run qsvdbg-hwupload-0 -v debug -init_hw_device "qsv=qs:hw_any,child_device=0" -filter_hw_device qs \
    "${SRC[@]}" -frames:v 10 -vf "format=nv12,hwupload=extra_hw_frames=16" -c:v hevc_qsv -f null -
grep -iE "d3d11|child|pool|texture|bind|error|0x8" "$OUT/qsvdbg-hwupload-0.log" | head -25 | sed 's/^/    /'
# B: system-memory frames, encoder bound to the qsv device via the CLI device lookup
run qsv-sysmem-hevc-0 -v verbose -init_hw_device "qsv=qs:hw_any,child_device=0" \
    "${SRC[@]}" -frames:v 10 -pix_fmt nv12 -c:v hevc_qsv -global_quality 25 -f null -
grep -iE "Using device|implementation version|error" "$OUT/qsv-sysmem-hevc-0.log" | head -6 | sed 's/^/    /'
# C: explicit d3d11va parent device, qsv derived from it
run qsv-derive-hevc-0 -v verbose -init_hw_device d3d11va=dx:0 -init_hw_device qsv=qs@dx -filter_hw_device qs \
    "${SRC[@]}" -frames:v 10 -vf "format=nv12,hwupload=extra_hw_frames=16" -c:v hevc_qsv -global_quality 25 -f null -
grep -iE "Using device|implementation version|error" "$OUT/qsv-derive-hevc-0.log" | head -6 | sed 's/^/    /'
