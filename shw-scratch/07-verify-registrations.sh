#!/usr/bin/env bash
# shw-1 step 7: post-link checks mirroring ffmpeg-patches/test/build-and-run.sh
# (static libavfilter consumer, filter/BSF registration, encoder options) run
# against the INSTALLED binary C:/tmp/pel/prefix/bin/ffmpeg.exe, plus the
# Intel-relevant extras (av1_qsv help, -version, -buildconf, hwaccels).
# Windows differences: PATH (not LD_LIBRARY_PATH) carries libpelorus-0.dll and
# the MSYS2 runtime DLLs; the consumer binary gets EXESUF.
set -uo pipefail
PREFIX_U=/c/tmp/pel/prefix
HERE=/c/tmp/pel/shw/ffmpeg-patches/test
OUT=/c/tmp/pel/shw-results/shw-1/verify
mkdir -p "$OUT"
export PKG_CONFIG_PATH="$PREFIX_U/lib/pkgconfig:/ucrt64/lib/pkgconfig:/ucrt64/share/pkgconfig"
export PATH="$PREFIX_U/bin:$PATH"
FFM="$PREFIX_U/bin/ffmpeg.exe"
fail=0

"$FFM" -hide_banner -version > "$OUT/ffmpeg-version.txt" 2>&1
"$FFM" -hide_banner -buildconf > "$OUT/ffmpeg-buildconf.txt" 2>&1
"$FFM" -hide_banner -hwaccels > "$OUT/ffmpeg-hwaccels.txt" 2>&1
head -3 "$OUT/ffmpeg-version.txt"

echo "== static libavfilter consumer =="
static_cflags_output="$(pkg-config --cflags libavfilter)"
static_libs_output="$(pkg-config --static --libs libavfilter)"
echo "static libs: $static_libs_output" > "$OUT/static-consumer.txt"
if [[ " $static_libs_output " != *" -lpelorus "* ]]; then
    echo "ERROR: pkg-config --static libavfilter omitted -lpelorus"; fail=1
else
    echo "pkg-config --static libavfilter carries -lpelorus"
fi
read -r -a static_cflags <<< "$static_cflags_output"
read -r -a static_libs <<< "$static_libs_output"
if gcc "${static_cflags[@]}" "$HERE/static-libavfilter-consumer.c" \
       -o "$OUT/static-libavfilter-consumer.exe" "${static_libs[@]}" >> "$OUT/static-consumer.txt" 2>&1 \
   && "$OUT/static-libavfilter-consumer.exe"; then
    echo "static consumer linked and found pelorus_scenecut"
else
    echo "ERROR: static consumer failed (see $OUT/static-consumer.txt)"; fail=1
fi

FILTERS=(pelorus_aa_vulkan pelorus_analyze_vulkan pelorus_borderfix_vulkan
    pelorus_deband_vulkan pelorus_deblock_vulkan pelorus_dehalo_vulkan
    pelorus_denoise_vulkan pelorus_grain_estimate_vulkan pelorus_mc_vulkan
    pelorus_scenecut)
echo "== filters =="
"$FFM" -hide_banner -filters > "$OUT/ffmpeg-filters.txt" 2>&1
for f in "${FILTERS[@]}"; do
    if awk -v name="$f" '$2 == name { found = 1 } END { exit !found }' "$OUT/ffmpeg-filters.txt"; then
        echo "registered filter: $f"
    else
        echo "ERROR: filter is not registered: $f"; fail=1
    fi
done
grep -c pelorus "$OUT/ffmpeg-filters.txt"

echo "== bsfs =="
"$FFM" -hide_banner -bsfs > "$OUT/ffmpeg-bsfs.txt" 2>&1
if awk '$1 == "pelorus_fgs" { found = 1 } END { exit !found }' "$OUT/ffmpeg-bsfs.txt"; then
    echo "registered bitstream filter: pelorus_fgs"
else
    echo "ERROR: pelorus_fgs not registered"; fail=1
fi

verify_encoder_options() {
    local encoder="$1" option log="$OUT/encoder-$1.txt"
    shift
    if ! "$FFM" -hide_banner -h "encoder=$encoder" > "$log" 2>&1; then
        echo "ERROR: could not inspect encoder: $encoder"; fail=1; return
    fi
    for option in "$@"; do
        if grep -Eq "(^|[[:space:]])-${option}([[:space:]]|$)" "$log"; then
            echo "registered encoder option: ${encoder} -${option}"
        else
            echo "ERROR: encoder $encoder is missing option: $option"; fail=1
        fi
    done
}
echo "== encoder options =="
verify_encoder_options h264_qsv pelorus_roi
verify_encoder_options hevc_qsv pelorus_roi
verify_encoder_options libaom-av1 pelorus_roi
verify_encoder_options libsvtav1 pelorus_roi
verify_encoder_options h264_nvenc pelorus_roi pelorus_me_hints
verify_encoder_options hevc_nvenc pelorus_roi pelorus_me_hints
verify_encoder_options av1_nvenc pelorus_roi pelorus_film_grain
verify_encoder_options h264_vulkan pelorus_roi
verify_encoder_options hevc_vulkan pelorus_roi
verify_encoder_options av1_vulkan pelorus_roi
# av1_qsv: the stack carries no QSV AV1 steering (0005 touches h264/hevc only)
"$FFM" -hide_banner -h encoder=av1_qsv > "$OUT/encoder-av1_qsv.txt" 2>&1
echo "av1_qsv pelorus options: $(grep -c -- '-pelorus' "$OUT/encoder-av1_qsv.txt") (expected 0: not in the stack)"
for e in h264_qsv hevc_qsv libaom-av1 libsvtav1 hevc_vulkan av1_vulkan h264_nvenc av1_nvenc; do
    grep -- '-pelorus' "$OUT/encoder-$e.txt" | sed "s/^/  [$e] /"
done
echo "RESULT: fail=$fail"
exit "$fail"
