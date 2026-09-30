#!/usr/bin/env bash
# shw-1 step 8c: which QSV hwupload frame-pool shape works on Windows/D3D11?
# (fixed texture-array pool failed with CreateTexture2D E_INVALIDARG 0x80070057)
set -uo pipefail
export PATH="/c/tmp/pel/prefix/bin:$PATH"
FFM=/c/tmp/pel/prefix/bin/ffmpeg.exe
OUT=/c/tmp/pel/shw-results/shw-1/devices
SRC=(-f lavfi -i "testsrc2=size=1280x720:rate=24:duration=1")
run() { local label="$1"; shift
    if "$FFM" -hide_banner -nostdin "$@" > "$OUT/$label.log" 2>&1; then echo "PASS $label"
    else echo "FAIL $label (rc=$?): $(grep -E 'Could not|Error' "$OUT/$label.log" | head -2 | tr '\n' ' ')"; fi; }
for a in 0 1; do
  dev=(-v verbose -init_hw_device "qsv=qs:hw_any,child_device=$a" -filter_hw_device qs)
  run "qsvup-dynamic-hevc-$a" "${dev[@]}" "${SRC[@]}" -frames:v 10 -vf "format=nv12,hwupload" -c:v hevc_qsv -global_quality 25 -f null -
  run "qsvup-extra64-hevc-$a" "${dev[@]}" "${SRC[@]}" -frames:v 10 -vf "format=nv12,hwupload=extra_hw_frames=64" -c:v hevc_qsv -global_quality 25 -f null -
  run "qsv-sysmem-hevc-$a" -v verbose -init_hw_device "qsv=qs:hw_any,child_device=$a" "${SRC[@]}" -frames:v 10 -pix_fmt nv12 -c:v hevc_qsv -global_quality 25 -f null -
  run "qsv-sysmem-h264-$a" -v verbose -init_hw_device "qsv=qs:hw_any,child_device=$a" "${SRC[@]}" -frames:v 10 -pix_fmt nv12 -c:v h264_qsv -global_quality 25 -f null -
  run "qsv-sysmem-av1-$a" -v verbose -init_hw_device "qsv=qs:hw_any,child_device=$a" "${SRC[@]}" -frames:v 10 -pix_fmt nv12 -c:v av1_qsv -global_quality 25 -f null -
  grep -hE "Using device [0-9a-f]{4}:|implementation version is" "$OUT/qsv-sysmem-hevc-$a.log" | sed 's/^/    /'
done
