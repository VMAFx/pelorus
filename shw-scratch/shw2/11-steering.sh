#!/usr/bin/env bash
# shw-2 step 11: analyze -> encoder-steering side-data path, scenecut -> IDR,
# grain_estimate -> AV1 encoders, and the pelorus_fgs BSF, on real encoders.
# GPU=0 (B580) or 1 (UHD 770).  Vulkan device in the exact-data-path mode from
# step 6; Vulkan validation (core) on; QSV encodes take system-memory frames.
# Every encode runs twice (determinism) with bitexact muxing.
set -uo pipefail
source /c/tmp/pel/shw-scratch/shw2/env.sh
GPU="${GPU:-0}"
case "$GPU" in
    0) VKSPEC="0,disable_multiplane=1" ;;
    1) VKSPEC="1,linear_images=1,disable_multiplane=1" ;;
esac
OUT=$RES/11-steering/gpu$GPU; rm -rf "$OUT"; mkdir -p "$OUT"
export "$(pel_vvl_env msys)" VK_INSTANCE_LAYERS=VK_LAYER_KHRONOS_validation
W=640; H=360; N=20
# Left half: shallow 0x10->0x30 luma ramp (banding-prone, analyze roi=1 should
# flag it).  Right half: textured testsrc2.
SRC="gradients=s=${W}x${H}:c0=0x101010:c1=0x303030:x0=0:y0=0:x1=${W}:y1=0:nb_colors=2:speed=0.00001:seed=1:rate=10[g];testsrc2=s=$((W/2))x${H}:rate=10[t];[g][t]overlay=x=$((W/2)):y=0:format=yuv420,format=nv12[out0]"
VK=(-init_hw_device "vulkan=vk:$VKSPEC" -filter_hw_device vk)
QS=(-init_hw_device "qsv=qs:hw_any,child_device=$GPU")
BX=(-fflags +bitexact -flags:v +bitexact)
ff() { "$FFM" -hide_banner -nostdin -y "$@"; }
say() { printf '%s\n' "$*" | tee -a "$OUT/summary.txt"; }
diagcount() { grep -hEo '(VUID|SYNC|UNASSIGNED)-[[:alnum:]_.-]+' "$@" 2>/dev/null | sort | uniq -c | awk '{printf "%s x%s; ", $2, $1/2}'; }

# Reference: the source exactly as the encoder receives it (after the Vulkan round trip).
ff -loglevel error "${VK[@]}" -f lavfi -i "$SRC" -frames:v $N \
   -vf "format=nv12,hwupload,pelorus_analyze_vulkan=roi=1,hwdownload,format=nv12" \
   -f rawvideo "$OUT/ref.nv12" >"$OUT/ref.stdout" 2>"$OUT/ref.stderr"
ff -loglevel info "${VK[@]}" -f lavfi -i "$SRC" -frames:v 1 \
   -vf "format=nv12,hwupload,pelorus_analyze_vulkan=roi=1,hwdownload,format=nv12,showinfo" -f null - \
   >"$OUT/roi-dump.stdout" 2>"$OUT/roi-dump.stderr"
nroi=$(grep -cE 'index: [0-9]+, region:' "$OUT/roi-dump.stderr")
nleft=$(grep -oE 'region: \([0-9]+, [0-9]+\) -> \([0-9]+, [0-9]+\)' "$OUT/roi-dump.stderr" | sed -E 's/region: \(([0-9]+), [0-9]+\) -> \(([0-9]+), [0-9]+\)/\1 \2/' | awk -v h=$((W/2)) '$2<=h' | wc -l)
say "ROI rectangles on frame 0: $nroi ($nleft entirely in the left ramp half); qoffsets: $(grep -oE 'qp offset: [-0-9/]+' "$OUT/roi-dump.stderr" | cut -d' ' -f3 | sort | uniq -c | awk '{printf "%sx%s ", $1, $2}')"
say "Vulkan diagnostics during analyze: $(diagcount "$OUT"/ref.stdout "$OUT"/roi-dump.stdout)"

regpsnr() { # enc-file -> per-half luma PSNR vs the reference (10 fps raw pairing)
    local f="$1" x half out=""
    for half in left right; do
        [[ $half == left ]] && x=0 || x=$((W/2))
        out+="$half=$(ff -loglevel info -i "$f" -f rawvideo -pix_fmt nv12 -s ${W}x${H} -framerate 10 -i "$OUT/ref.nv12" \
            -lavfi "[0:v]format=nv12,crop=$((W/2)):$H:$x:0[a];[1:v]crop=$((W/2)):$H:$x:0[b];[a][b]psnr" -f null - 2>&1 |
            grep -oE 'PSNR y:[0-9.inf]+' | tail -1 | cut -d: -f2) "
    done
    echo "$out"
}
enc() { # tag, analyze roi (0/1), encoder args...
    local tag="$1" aroi="$2" det; shift 2
    local chain="format=nv12,hwupload,pelorus_analyze_vulkan=roi=$aroi,hwdownload,format=nv12"
    ff -loglevel error "${VK[@]}" "${QS[@]}" -f lavfi -i "$SRC" -frames:v $N -vf "$chain" \
        "$@" "${BX[@]}" -f matroska "$OUT/$tag-rep.mkv" >/dev/null 2>&1
    if ff -loglevel info "${VK[@]}" "${QS[@]}" -f lavfi -i "$SRC" -frames:v $N -vf "$chain" \
        "$@" "${BX[@]}" -f matroska "$OUT/$tag.mkv" >"$OUT/$tag.stdout" 2>"$OUT/$tag.stderr"; then
        det=$(cmp -s "$OUT/$tag.mkv" "$OUT/$tag-rep.mkv" && echo deterministic || echo NONDETERMINISTIC)
        say "OK   $tag [$det] size=$(stat -c %s "$OUT/$tag.mkv") md5=$(md5sum <"$OUT/$tag.mkv" | cut -c1-12) $(regpsnr "$OUT/$tag.mkv")warn=[$(grep -hiE 'pelorus|fall(ing)? ?back|roi|not been used' "$OUT/$tag.stderr" | grep -viE '^ *(Stream|Input|Output)|encoder +:' | sed -E 's/^\[[^]]*\] //' | sort -u | tr '\n' '|')] vkdiag=[$(diagcount "$OUT/$tag.stdout" "$OUT/$tag.stderr")]"
    else
        say "FAIL $tag rc=$? $(grep -m2 -iE 'error|unrecognized|not found' "$OUT/$tag.stderr" | tr '\n' ' ')"
    fi
}

say "== QSV HEVC CQP (dense MBQP path expected on runtime >= 1.28)"
enc hevc-noroi           0 -c:v hevc_qsv -q:v 30 -g 1000
enc hevc-roi-stock       1 -c:v hevc_qsv -q:v 30 -g 1000 -pelorus_roi 0
enc hevc-roi-pelorus     1 -c:v hevc_qsv -q:v 30 -g 1000 -pelorus_roi 1
say "== QSV HEVC ICQ (-global_quality): documented fallback to stock rectangles"
enc hevc-icq-noroi       0 -c:v hevc_qsv -global_quality 30 -g 1000
enc hevc-icq-roi-stock   1 -c:v hevc_qsv -global_quality 30 -g 1000 -pelorus_roi 0
enc hevc-icq-roi-pelorus 1 -c:v hevc_qsv -global_quality 30 -g 1000 -pelorus_roi 1
say "== QSV H.264 (stock rectangle path by contract)"
enc h264-noroi           0 -c:v h264_qsv -q:v 30 -g 1000
enc h264-roi-stock       1 -c:v h264_qsv -q:v 30 -g 1000 -pelorus_roi 0
enc h264-roi-pelorus     1 -c:v h264_qsv -q:v 30 -g 1000 -pelorus_roi 1
if [[ $GPU == 0 ]]; then
    say "== QSV AV1: no pelorus_roi by contract"
    enc av1qsv-noroi       0 -c:v av1_qsv -q:v 100 -g 1000
    enc av1qsv-roi         1 -c:v av1_qsv -q:v 100 -g 1000
    enc av1qsv-roi-pelorus 1 -c:v av1_qsv -q:v 100 -g 1000 -pelorus_roi 1
    say "== libsvtav1 / libaom-av1 ROI (CPU encoders fed by the Vulkan analyze)"
    enc svt-noroi       0 -c:v libsvtav1 -preset 12 -crf 40 -g 1000
    enc svt-roi-off     1 -c:v libsvtav1 -preset 12 -crf 40 -g 1000 -pelorus_roi 0
    enc svt-roi-pelorus 1 -c:v libsvtav1 -preset 12 -crf 40 -g 1000 -pelorus_roi 1
    enc aom-noroi       0 -c:v libaom-av1 -cpu-used 8 -crf 40 -g 1000 -row-mt 0 -threads 1
    enc aom-roi-off     1 -c:v libaom-av1 -cpu-used 8 -crf 40 -g 1000 -row-mt 0 -threads 1 -pelorus_roi 0
    enc aom-roi-pelorus 1 -c:v libaom-av1 -cpu-used 8 -crf 40 -g 1000 -row-mt 0 -threads 1 -pelorus_roi 1
fi

say "== scenecut: mc(meta=1) -> hwdownload -> pelorus_scenecut -> hevc_qsv; long-GOP decoded source, hard cut at frame 10"
CUT="testsrc2=s=${W}x${H}:rate=10:d=1,format=yuv420p[a];smptehdbars=s=${W}x${H}:rate=10:d=1,format=yuv420p[b];[a][b]concat=n=2:v=1[out0]"
ff -loglevel error -f lavfi -i "$CUT" -frames:v 20 -c:v libx264 -g 1000 -keyint_min 1000 -sc_threshold 0 -bf 0 -crf 10 "$OUT/cut-src.mkv"
say "source keyframes: $("$FFP" -v error -select_streams v:0 -show_entries frame=key_frame,pict_type -of csv=p=0 "$OUT/cut-src.mkv" | awk -F, '{ if ($1==1) printf "%d(%s) ", NR-1, $2 }')"
for fkf in "" "-force_key_frames source"; do
  for fi in 1 0; do
    tag="scenecut-force_idr$fi${fkf:+-fkfsource}"
    # shellcheck disable=SC2086 # $fkf is an intentionally split option pair
    ff -loglevel info "${VK[@]}" "${QS[@]}" -i "$OUT/cut-src.mkv" \
        -vf "format=nv12,hwupload,pelorus_mc_vulkan=meta=1,hwdownload,format=nv12,pelorus_scenecut=force_idr=$fi,showinfo" \
        -c:v hevc_qsv -q:v 30 -g 1000 $fkf "$OUT/$tag.mkv" >"$OUT/$tag.stdout" 2>"$OUT/$tag.stderr"
    rc=$?
    keys=$("$FFP" -v error -select_streams v:0 -show_entries frame=key_frame,pict_type -of csv=p=0 "$OUT/$tag.mkv" |
           awk -F, '{ if ($1==1) printf "%d(%s) ", NR-1, $2 }')
    types=$(grep -oE 'type:[A-Z?]' "$OUT/$tag.stderr" | cut -d: -f2 | tr -d '\n')
    say "rc=$rc $tag: pict_type leaving pelorus_scenecut=$types ; encoded keyframes at: $keys"
  done
done

if [[ $GPU == 0 ]]; then
    say "== grain_estimate(native=1) -> AV1 encoders: is film grain signalled?"
    GRAIN="testsrc2=s=${W}x${H}:rate=10,noise=alls=25:allf=t"
    for e in "libsvtav1 -preset 12 -crf 40" "libaom-av1 -cpu-used 8 -crf 40"; do
        # shellcheck disable=SC2086
        set -- $e; tag="grain-${1}"
        ff -loglevel info "${VK[@]}" -f lavfi -i "$GRAIN" -frames:v 5 \
            -vf "format=yuv420p,hwupload,pelorus_grain_estimate_vulkan=native=1,hwdownload,format=yuv420p,showinfo" \
            -c:v "$@" "$OUT/$tag.mkv" >"$OUT/$tag.stdout" 2>"$OUT/$tag.stderr"
        rc=$?
        fg_frames=$(grep -c 'Film grain parameters' "$OUT/$tag.stderr")
        ff -loglevel info -i "$OUT/$tag.mkv" -c:v copy -bsf:v trace_headers -f null - >"$OUT/$tag.trace" 2>&1
        say "rc=$rc $tag: frames reaching the encoder with AV_FRAME_DATA_FILM_GRAIN_PARAMS=$fg_frames; bitstream $(grep -oE 'film_grain_params_present +[0-9]+ += +[0-9]+' "$OUT/$tag.trace" | sort -u | sed -E 's/ +[01]* += +/=/' | tr '\n' ' ')"
    done
fi

say "== pelorus_fgs BSF on a hevc_qsv bitstream"
ff -loglevel error "${QS[@]}" -f lavfi -i "testsrc2=s=${W}x${H}:rate=10" -frames:v 10 -pix_fmt nv12 \
    -c:v hevc_qsv -q:v 30 -f hevc "$OUT/fgs-in.hevc"
ff -loglevel error -i "$OUT/fgs-in.hevc" -c:v copy -f hevc "$OUT/fgs-copy.hevc"
ff -loglevel error -i "$OUT/fgs-in.hevc" -c:v copy \
    -bsf:v "pelorus_fgs=model_id=1:blending_mode=0:log2_scale=6:scale_y=40:intensity_low=16:intensity_high=235" \
    -f hevc "$OUT/fgs-out.hevc"
ff -loglevel error -i "$OUT/fgs-out.hevc" -c:v copy -bsf:v "pelorus_fgs=scale_y=99" -f hevc "$OUT/fgs-twice.hevc"
ff -loglevel error -i "$OUT/fgs-in.hevc" -c:v copy -bsf:v "pelorus_fgs=components=0" -f hevc "$OUT/fgs-passthru.hevc"
ff -loglevel error -i "$OUT/fgs-in.hevc" -c:v copy \
    -bsf:v "pelorus_fgs=components=y+cb+cr:scale_c=20:persistence=0:skip_existing=0" -f hevc "$OUT/fgs-chroma.hevc"
ff -loglevel error -i "$OUT/fgs-in.hevc" -c:v copy -bsf:v "pelorus_fgs=model_id=0:scale_y=60:log2_scale=5" \
    -f hevc "$OUT/fgs-model0.hevc"
for t in in copy out twice passthru chroma model0; do
    ff -loglevel info -i "$OUT/fgs-$t.hevc" -c:v copy -bsf:v trace_headers -f null - >"$OUT/fgs-$t.trace" 2>&1
done
cnt() { grep -c 'Film Grain Characteristics' "$OUT/fgs-$1.trace"; }
say "FGC SEI count: in=$(cnt in) out=$(cnt out) twice(skip_existing=1)=$(cnt twice) chroma=$(cnt chroma) model0=$(cnt model0) (10 access units)"
say "components=0 byte-identical to a plain -c copy remux: $(cmp -s "$OUT/fgs-copy.hevc" "$OUT/fgs-passthru.hevc" && echo yes || echo NO); plain remux identical to encoder output: $(cmp -s "$OUT/fgs-copy.hevc" "$OUT/fgs-in.hevc" && echo yes || echo no)"
fields() { grep -A90 'Film Grain Characteristics' "$OUT/fgs-$1.trace" | grep -oE "$2" | head -"$3" | sed -E 's/ +[01]* += +/=/' | tr '\n' ' '; }
say "fields out: $(fields out '(film_grain_model_id|blending_mode_id|log2_scale_factor|comp_model_present_flag\[0\]|intensity_interval_lower_bound\[0\]\[0\]|intensity_interval_upper_bound\[0\]\[0\]|comp_model_value\[0\]\[0\]\[0\]|film_grain_characteristics_persistence_flag) +[01]* += +[0-9]+' 8)"
say "fields chroma: $(fields chroma '(comp_model_present_flag\[[0-2]\]|comp_model_value\[[0-2]\]\[0\]\[0\]|film_grain_characteristics_persistence_flag) +[01]* += +[0-9]+' 7)"
for m in out model0; do
  d_in=$(ff -loglevel error -i "$OUT/fgs-in.hevc" -f framemd5 - | grep -v '^#' | md5sum | cut -c1-12)
  d_out=$(ff -loglevel error -i "$OUT/fgs-$m.hevc" -f framemd5 - 2>"$OUT/fgs-$m-decode.stderr" | grep -v '^#' | md5sum | cut -c1-12)
  d_exp=$(ff -loglevel error -export_side_data film_grain -i "$OUT/fgs-$m.hevc" -f framemd5 - | grep -v '^#' | md5sum | cut -c1-12)
  say "decode fgs-$m: no-SEI=$d_in with-SEI=$d_out with-SEI+export_side_data=$d_exp -> $( [[ $d_out != "$d_in" && $d_exp == "$d_in" ]] && echo 'decoder synthesized H.274 grain from the inserted SEI' || echo 'no synthesis by the FFmpeg decoder') decoder-warnings=[$(ff -loglevel warning -i "$OUT/fgs-$m.hevc" -f null - 2>&1 | sed -E 's/^\[[^]]*\] //' | sort | uniq -c | head -3 | tr '\n' ' ')]"
done
