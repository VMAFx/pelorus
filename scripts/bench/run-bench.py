#!/usr/bin/env python3
# run-bench.py — prove a Pelorus pre-filter improves a hardware encode.
#
# Copyright 2026 Lusoris. BSD-2-Clause-Patent.
#
# For each CQ point and each variant (baseline = no filter, pelorus = pre-filter
# in the zero-copy Vulkan pipeline), encode the source, decode it, and score it
# vs the source with vmafx (VMAF + CAMBI banding index). Collect RD curves and
# report BD-rate (VMAF) + the CAMBI banding delta. See docs/development/benchmarking.md.
#
# Example:
#   run-bench.py --ffmpeg /tmp/pel-ff-run/ffmpeg --vmaf vmaf \
#     --synth banding --width 1280 --height 720 --frames 48 \
#     --encoder hevc_nvenc --filter "pelorus_deband_vulkan=range=15:dither=bluenoise:dynamic=1" \
#     --cq 28 34 40 46 --out /tmp/bench/deband_hevc

import argparse, json, os, subprocess, sys

def run(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)

def synth_source(args):
    """Generate a clean, banding-prone OR noisy raw-YUV source."""
    src = os.path.join(args.out, f"src_{args.synth}.yuv")
    if args.synth == "banding":
        # smooth dark vertical gradient — the classic deband torture clip
        vf = (f"gradients=size={args.width}x{args.height}:x0=0:y0=0:x1=0:"
              f"y1={args.height}:c0=0x060608:c1=0x2a3038:nb_colors=2,"
              f"format=yuv420p")
    else:  # noise
        vf = (f"color=c=0x404048:size={args.width}x{args.height},"
              f"noise=alls=18:allf=t+u,format=yuv420p")
    run([args.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"{vf.split(',')[0]}",
         "-vf", ",".join(vf.split(",")[1:]) or "null",
         "-frames:v", str(args.frames), "-f", "rawvideo", src])
    return src

def prefilter_once(args, src):
    """Run the Vulkan pre-filter ONCE -> debanded raw YUV. Decouples the (single,
    reliable) GPU compute pass from the encode ladder so the quality proof never
    blocks on a vulkan-in-loop hang. Output pixels are identical to the zero-copy
    pipeline; zero-copy is a throughput property, not a quality one."""
    pf = os.path.join(args.out, "pelorus_src.yuv")
    run([args.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "rawvideo", "-pix_fmt", args.pixfmt, "-s",
         f"{args.width}x{args.height}", "-r", str(args.fps), "-i", src,
         "-init_hw_device", f"vulkan={args.device}",
         "-vf", f"format={args.pixfmt},hwupload,{args.filter},"
                f"hwdownload,format={args.pixfmt}",
         "-f", "rawvideo", pf])
    return pf

def encode(args, src, cq, variant):
    out = os.path.join(args.out, f"{variant}_cq{cq}.mkv")
    inp = args.pelorus_src if (variant == "pelorus" and args.pelorus_src) else src
    cmd = [args.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
           "-f", "rawvideo", "-pix_fmt", args.pixfmt, "-s",
           f"{args.width}x{args.height}", "-r", str(args.fps), "-i", inp]
    if variant == "pelorus" and not args.pelorus_src:
        cmd += ["-init_hw_device", f"vulkan={args.device}",
                "-vf", f"format={args.pixfmt},hwupload,{args.filter},"
                       f"hwdownload,format={args.pixfmt}"]
    cmd += ["-c:v", args.encoder, "-preset", args.preset, "-cq", str(cq),
            "-b:v", "0", out]
    run(cmd)
    return out

def decode(args, mkv):
    dec = mkv.replace(".mkv", ".dec.yuv")
    run([args.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
         "-i", mkv, "-f", "rawvideo", "-pix_fmt", args.pixfmt, dec])
    return dec

def score(args, ref, dec):
    """Score `dec` against reference `ref` (clean ground truth or encoder input)."""
    j = dec.replace(".dec.yuv", ".vmaf.json")
    cmd = [args.vmaf, "--reference", ref, "--distorted", dec,
           "--width", str(args.width), "--height", str(args.height),
           "--pixel_format", args.pixfmt[3:6] if args.pixfmt.startswith("yuv") else "420",
           "--bitdepth", str(args.bitdepth), "--output", j, "--json"]
    if args.cambi:                      # CAMBI is the banding metric but slow
        cmd += ["--feature", "cambi"]
    # vmaf hangs at 0% CPU AFTER flushing its JSON -> bound it and ignore the kill;
    # the result is already on disk by then.
    try:
        subprocess.run(cmd, timeout=args.vmaf_timeout, capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        pass
    m = json.load(open(j))["pooled_metrics"]
    return m["vmaf"]["mean"], (m["cambi"]["mean"] if args.cambi else float("nan"))

def bitrate_kbps(args, mkv):
    bytes_ = os.path.getsize(mkv)
    dur = args.frames / args.fps
    return bytes_ * 8 / 1000.0 / dur

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ffmpeg", required=True); ap.add_argument("--vmaf", required=True)
    ap.add_argument("--src"); ap.add_argument("--synth", choices=["banding", "noise"])
    ap.add_argument("--width", type=int, required=True); ap.add_argument("--height", type=int, required=True)
    ap.add_argument("--frames", type=int, default=48); ap.add_argument("--fps", type=int, default=25)
    ap.add_argument("--pixfmt", default="yuv420p"); ap.add_argument("--bitdepth", type=int, default=8)
    ap.add_argument("--encoder", required=True); ap.add_argument("--preset", default="p5")
    ap.add_argument("--filter", required=True); ap.add_argument("--device", default="vk:0")
    ap.add_argument("--cq", type=int, nargs="+", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--cambi", action="store_true", help="also measure CAMBI banding (slow)")
    ap.add_argument("--clean-reference", dest="clean_reference", default=None,
                    help="score VMAF against THIS clean YUV instead of the encoder input. Use when "
                         "--src is a noisy/impaired source and the denoise/deband filter restores the "
                         "clean ground truth: scoring against the impaired src wrongly penalizes the "
                         "filter for removing the impairment (the deband 'wash' bug).")
    ap.add_argument("--vmaf-timeout", dest="vmaf_timeout", type=int, default=300,
                    help="seconds before SIGKILLing vmaf (it hangs after writing its JSON)")
    ap.add_argument("--prefilter-once", dest="prefilter_once", action="store_true",
                    help="run the Vulkan filter once -> YUV, then a pure-encoder ladder")
    ap.add_argument("--pelorus-src-file", dest="pelorus_src_file", default=None,
                    help="use an already-prefiltered YUV (skip the Vulkan pass; lets the "
                         "encode ladder run on a plain ffmpeg with no Vulkan/libpelorus dep)")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    src = args.src or synth_source(args)
    # The VMAF reference is decoupled from the encoder input: when --clean-reference is
    # given, BOTH arms are scored against the clean ground truth while the (noisy) src
    # stays the baseline encoder input / the filter's input. Default: ref == src (no change).
    ref = args.clean_reference or src
    if args.pelorus_src_file:
        args.pelorus_src = args.pelorus_src_file
    elif args.prefilter_once:
        args.pelorus_src = prefilter_once(args, src)
    else:
        args.pelorus_src = None
    rows, curves = [], {"baseline": [], "pelorus": []}
    for cq in args.cq:
        for variant in ("baseline", "pelorus"):
            try:
                mkv = encode(args, src, cq, variant)
                dec = decode(args, mkv)
                vmaf, cambi = score(args, ref, dec)
                br = bitrate_kbps(args, mkv)
            except subprocess.CalledProcessError as e:
                print(f"FAIL {variant} cq{cq}: {e.stderr[-400:]}", file=sys.stderr); raise
            rows.append({"cq": cq, "variant": variant, "bitrate_kbps": round(br, 1),
                         "vmaf": round(vmaf, 3), "cambi": round(cambi, 4)})
            curves[variant].append({"bitrate_kbps": br, "score": vmaf})
            print(f"  {variant:8s} cq{cq:<3d} {br:8.1f} kbps  VMAF {vmaf:6.2f}  CAMBI {cambi:.4f}")

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from bd_rate import bd_rate
    bd = bd_rate(curves["baseline"], curves["pelorus"])
    # CAMBI delta at matched cq (lower CAMBI = less banding = better)
    cambi_base = sum(r["cambi"] for r in rows if r["variant"] == "baseline") / len(args.cq)
    cambi_pel = sum(r["cambi"] for r in rows if r["variant"] == "pelorus") / len(args.cq)
    result = {"encoder": args.encoder, "filter": args.filter, "rows": rows,
              "scoring_reference": "clean" if args.clean_reference else "encoder-input",
              "clean_reference_file": args.clean_reference,
              "bd_rate_pct": bd["bd_rate_pct"], "bd_vmaf": bd["bd_quality"],
              "cambi_baseline_mean": round(cambi_base, 4),
              "cambi_pelorus_mean": round(cambi_pel, 4),
              "cambi_reduction_pct": round((cambi_base - cambi_pel) / cambi_base * 100, 1)
              if cambi_base else 0.0}
    json.dump(result, open(os.path.join(args.out, "result.json"), "w"), indent=2)
    print(f"\n== {args.encoder} ==")
    print(f"BD-rate (VMAF): {bd['bd_rate_pct']:+.2f}%   BD-VMAF: {bd['bd_quality']:+.3f}")
    print(f"CAMBI banding: {cambi_base:.4f} -> {cambi_pel:.4f} "
          f"({result['cambi_reduction_pct']:+.1f}% banding)")

if __name__ == "__main__":
    main()
