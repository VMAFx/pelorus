#!/usr/bin/env python3
# run-bench.py — prove a Pelorus pre-filter improves a hardware encode.
#
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
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

import argparse, json, math, os, subprocess, sys
from pathlib import Path

def run(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)

def synth_source(args):
    """Generate a clean, banding-prone OR noisy raw-YUV source."""
    src = os.path.join(args.out, f"src_{args.synth}.yuv")
    if args.synth == "banding":
        # smooth dark vertical gradient — the classic deband torture clip
        # Same generator as the synth-banding entry of fetch-corpus.sh (fixed seed),
        # written in the run's own pixfmt so the file matches what later stages read.
        vf = (f"gradients=size={args.width}x{args.height}:x0=0:y0=0:x1=0:"
              f"y1={args.height}:c0=0x060608:c1=0x2a3038:nb_colors=2:"
              f"rate={args.fps}:seed=1,format={args.pixfmt}")
    else:  # noise
        vf = (f"color=c=0x404048:size={args.width}x{args.height},"
              f"noise=alls=18:allf=t+u,format={args.pixfmt}")
    run([args.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"{vf.split(',')[0]}",
         "-vf", ",".join(vf.split(",")[1:]) or "null",
         "-frames:v", str(args.frames), "-f", "rawvideo", src])
    return src

def add_grain(args, src):
    """Overlay deterministic film-grain-like noise on a REAL source.

    This is the grain axis. It exists because neither of the alternatives works:
    `--synth noise` builds its noise on a flat grey plate, which is the degenerate
    case (nothing to preserve, and the grain estimator starves on it because no 3x3
    neighbourhood stays under edge_thr); and no *public* pinned clip actually carries
    grain -- measured, the Chimera BarScene webm is a VP9 distribution copy whose
    encode removed it (grain_sigma 0.0124, vs 0.0047-0.0101 for real Blu-ray scans and
    0.0131 for clean animation), and the ungraded y4m is 29.6 GB.

    Injecting into real content instead gives a monotonic, seeded, sweepable axis --
    verified 0 -> 20 produces grain_sigma 0.0132 -> 0.0214 with grain_flat 0.331 ->
    0.005. `all_seed` is set explicitly so a run is reproducible (ffmpeg's default
    happens to be fixed, but relying on that is not a contract).

    Returns the grained YUV. The caller keeps the ORIGINAL as the VMAF reference, so a
    denoise/deband arm is scored against the clean ground truth rather than against the
    impairment it is supposed to remove -- the `--clean-reference` rationale.
    """
    out = os.path.join(args.out, f"src_grain{args.grain}.yuv")
    run([args.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "rawvideo", "-pix_fmt", args.pixfmt,
         "-s", f"{args.width}x{args.height}", "-r", str(args.fps), "-i", src,
         "-vf", f"noise=alls={args.grain}:allf=t+u:all_seed={args.grain_seed},format={args.pixfmt}",
         "-frames:v", str(args.frames), "-f", "rawvideo", out])
    return out


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
    is_pelorus = variant in ("pelorus", "pelorus-denoise+nvenc-tuned")
    inp = args.pelorus_src if (is_pelorus and args.pelorus_src) else src
    cmd = [args.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
           "-f", "rawvideo", "-pix_fmt", args.pixfmt, "-s",
           f"{args.width}x{args.height}", "-r", str(args.fps), "-i", inp]
    if is_pelorus and not args.pelorus_src:
        cmd += ["-init_hw_device", f"vulkan={args.device}",
                "-vf", f"format={args.pixfmt},hwupload,{args.filter},"
                       f"hwdownload,format={args.pixfmt}"]
    if variant in ("nvenc-tuned", "nvenc-tflevel", "nvenc-uhq", "pelorus-denoise+nvenc-tuned"):
        tune_val = "uhq" if variant == "nvenc-uhq" else "hq"
        cmd += ["-c:v", args.encoder, "-preset", "p7", "-tune", tune_val]
        # Temporal filtering needs at least 4 B-frames (FFmpeg n9.0.2
        # libavcodec/nvenc.c:1497); every arm keeps the same GOP shape.
        cmd += ["-bf", "4"]
        if variant == "nvenc-tflevel":
            cmd += ["-tf_level", "4"]
        cmd += [
            "-spatial-aq", "1",
            "-temporal-aq", "1",
            "-aq-strength", "8",
            "-rc-lookahead", "32",
            "-multipass", "fullres",
            "-b_ref_mode", "middle",
            "-cq", str(cq),
            "-b:v", "0",
            out,
        ]
    else:
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
    cmd += ["--feature", "ssimulacra2"]
    if args.cambi:                      # CAMBI is the banding metric but slow
        cmd += ["--feature", "cambi"]
    try:
        subprocess.run(cmd, timeout=args.vmaf_timeout, capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        # vmaf hangs at 0% CPU AFTER flushing its JSON -> bound it and ignore the kill;
        # the result is already on disk by then.
        pass
    with open(j, "r", encoding="utf-8") as f:
        m = json.load(f)["pooled_metrics"]
    ssim2 = m.get("ssimulacra2", {}).get("mean", float("nan"))
    vmaf_val = m.get("vmaf", {}).get("mean", float("nan"))
    # vmafx names the CAMBI feature with its options (cambi_hrs_1080_...).
    cambi_key = next((k for k in m if k == "cambi" or k.startswith("cambi_")), None)
    cambi_val = m[cambi_key]["mean"] if args.cambi and cambi_key else float("nan")
    return ssim2, vmaf_val, cambi_val

def bitrate_kbps(args, mkv):
    bytes_ = os.path.getsize(mkv)
    dur = args.frames / args.fps
    return bytes_ * 8 / 1000.0 / dur

def parse_args(argv=None):
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
    ap.add_argument("--grain", type=int, default=0, metavar="N",
                    help="impair the source with seeded grain of strength N (ffmpeg noise "
                         "alls=N) and score BOTH arms against the clean original. This is "
                         "the grain axis: it puts grain on REAL content, unlike --synth "
                         "noise which noises a flat grey plate. Implies --clean-reference.")
    ap.add_argument("--grain-seed", dest="grain_seed", type=int, default=12345,
                    help="noise seed, so a grain run is reproducible")
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
    ap.add_argument("--four-arm", dest="four_arm", action="store_true",
                    help="run four mandatory arms (nvenc-tuned, nvenc-tflevel, nvenc-uhq, "
                         "pelorus-denoise+nvenc-tuned) and emit record.json (ADR-0223, #131)")
    ap.add_argument("--device-name", dest="device_name", default=None,
                    help="hardware device name (default: queried via nvidia-smi)")
    ap.add_argument("--clip", default=None, help="clip identifier name for record.json")
    return ap.parse_args(argv)


def prepare_source_and_reference(args):
    """Resolve the source YUV, prefilter setup, and reference YUV for scoring."""
    src = args.src or synth_source(args)
    # The VMAF reference is decoupled from the encoder input: when --clean-reference is
    # given, BOTH arms are scored against the clean ground truth while the (noisy) src
    # stays the baseline encoder input / the filter's input. Default: ref == src (no change).
    # --grain: the ORIGINAL is the clean ground truth and the grained copy is what the
    # encoder (and the filter) sees. Scoring against the grained source would penalise a
    # denoiser for removing the very impairment under test -- the deband 'wash' bug.
    if args.grain > 0:
        clean = src
        src = add_grain(args, clean)
        ref = args.clean_reference or clean
        print(f"  grain axis: alls={args.grain} seed={args.grain_seed}; "
              f"scoring both arms against the clean original")
    else:
        ref = args.clean_reference or src
    if args.pelorus_src_file:
        args.pelorus_src = args.pelorus_src_file
    elif args.prefilter_once:
        args.pelorus_src = prefilter_once(args, src)
    else:
        args.pelorus_src = None
    return src, ref


def run_ladder(args, src, ref):
    """Run encode, decode, and score for every CQ and variant."""
    if args.four_arm:
        variants = (
            "nvenc-tuned",
            "nvenc-tflevel",
            "nvenc-uhq",
            "pelorus-denoise+nvenc-tuned",
        )
        curves = {v: [] for v in variants}
    else:
        variants = ("baseline", "pelorus")
        curves = {"baseline": [], "pelorus": []}

    rows = []
    for cq in args.cq:
        for variant in variants:
            try:
                mkv = encode(args, src, cq, variant)
                dec = decode(args, mkv)
                ssim2, vmaf, cambi = score(args, ref, dec)
                br = bitrate_kbps(args, mkv)
            except subprocess.CalledProcessError as e:
                print(f"FAIL {variant} cq{cq}: {e.stderr[-400:]}", file=sys.stderr); raise
            row = {"cq": cq, "variant": variant, "bitrate_kbps": round(br, 1),
                   "vmaf": round(vmaf, 3), "cambi": round(cambi, 4)}
            if not math.isnan(ssim2):
                row["ssimulacra2"] = round(ssim2, 2)
            rows.append(row)
            curves[variant].append({"bitrate_kbps": br, "score": vmaf})
            ssim_str = f"  SS2 {ssim2:6.2f}" if not math.isnan(ssim2) else ""
            print(f"  {variant:28s} cq{cq:<3d} {br:8.1f} kbps{ssim_str}  VMAF {vmaf:6.2f}  CAMBI {cambi:.4f}")
    return rows, curves


def compute_bd_summary(args, rows, curves):
    """Compute BD-rate, write result.json, and return summary metrics."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from bd_rate import bd_rate
    base_key = "nvenc-tuned" if args.four_arm else "baseline"
    pel_key = "pelorus-denoise+nvenc-tuned" if args.four_arm else "pelorus"
    bd = bd_rate(curves[base_key], curves[pel_key])
    if "error" in bd:
        print(bd["error"], file=sys.stderr)
    # CAMBI delta at matched cq (lower CAMBI = less banding = better)
    cambi_base = sum(r["cambi"] for r in rows if r["variant"] == base_key) / len(args.cq)
    cambi_pel = sum(r["cambi"] for r in rows if r["variant"] == pel_key) / len(args.cq)
    result = {"encoder": args.encoder, "filter": args.filter, "rows": rows,
              "scoring_reference": "clean" if args.clean_reference else "encoder-input",
              "clean_reference_file": args.clean_reference,
              "bd_rate_pct": bd["bd_rate_pct"], "bd_vmaf": bd["bd_quality"],
              "cambi_baseline_mean": round(cambi_base, 4),
              "cambi_pelorus_mean": round(cambi_pel, 4),
              "cambi_reduction_pct": round((cambi_base - cambi_pel) / cambi_base * 100, 1)
              if cambi_base else 0.0}
    if args.four_arm:
        # SSIMULACRA2 decides (ADR-0223): Pelorus arm against each NVENC arm.
        result["bd_rate_ssimulacra2"] = {}
        for arm in ("nvenc-tuned", "nvenc-tflevel", "nvenc-uhq"):
            ss2 = [{"bitrate_kbps": r["bitrate_kbps"], "score": r.get("ssimulacra2", float("nan"))}
                   for r in rows if r["variant"] == arm]
            ss2_pel = [{"bitrate_kbps": r["bitrate_kbps"], "score": r.get("ssimulacra2", float("nan"))}
                       for r in rows if r["variant"] == pel_key]
            result["bd_rate_ssimulacra2"][arm] = bd_rate(ss2, ss2_pel)
        result["reference"] = "noisy-source"
        if args.clip:
            result["clip"] = args.clip
        if args.device_name:
            result["device"] = args.device_name
    if "error" in bd:
        result["bd_error"] = bd["error"]
    with open(os.path.join(args.out, "result.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    return result, bd, cambi_base, cambi_pel


def emit_and_validate_record(args, result):
    """Emit and validate record.json conforming to record.schema.json (ADR-0223)."""
    import importlib.util
    here = Path(os.path.dirname(os.path.abspath(__file__)))
    mr_path = here / "make-record.py"
    spec = importlib.util.spec_from_file_location("make_record_mod", mr_path)
    mr_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mr_mod)

    clip_name = args.clip or (os.path.basename(args.src) if args.src else f"synth_{args.synth}")
    rec = mr_mod.make_record(
        result,
        device=args.device_name,
        clip=clip_name,
        encoder=args.encoder,
        filter_name=args.filter,
    )
    rec_path = os.path.join(args.out, "record.json")
    with open(rec_path, "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=2)

    val_fn = mr_mod.load_validator()
    errs = val_fn(rec)
    if errs:
        print(f"record validation FAIL ({len(errs)} errors):", file=sys.stderr)
        for e in errs:
            print(f"  - {e}", file=sys.stderr)
        return 1

    print(f"record validated: {rec_path} conforms to record.schema.json (ADR-0223)")
    return 0


def print_summary(args, result, bd, cambi_base, cambi_pel):
    """Print benchmark summary report."""
    print(f"\n== {args.encoder} ==")
    print(f"BD-rate (VMAF): {bd['bd_rate_pct']:+.2f}%   BD-VMAF: {bd['bd_quality']:+.3f}")
    for arm, b in result.get("bd_rate_ssimulacra2", {}).items():
        if "error" in b:
            print(f"BD-rate (SSIMULACRA2) vs {arm}: {b['error']}")
        else:
            print(f"BD-rate (SSIMULACRA2) vs {arm}: {b['bd_rate_pct']:+.2f}%")
    print(f"CAMBI banding: {cambi_base:.4f} -> {cambi_pel:.4f} "
          f"({result['cambi_reduction_pct']:+.1f}% CAMBI reduction; negative = more banding)")


def report(args, rows, curves):
    """Compute BD-rate, write result.json, and print summary report."""
    result, bd, cambi_base, cambi_pel = compute_bd_summary(args, rows, curves)
    if args.four_arm:
        status = emit_and_validate_record(args, result)
        if status != 0:
            return status
    print_summary(args, result, bd, cambi_base, cambi_pel)
    return 0


def main():
    args = parse_args()
    if args.four_arm:
        if len(args.cq) < 4:
            sys.stderr.write("error: four-arm benchmark requires at least 4 CQ points (ADR-0223, #131)\n")
            return 1
        if args.clean_reference or args.grain > 0:
            sys.stderr.write("error: four-arm benchmark requires noisy-source reference; clean-reference is prohibited (ADR-0223, #131)\n")
            return 1
    os.makedirs(args.out, exist_ok=True)
    src, ref = prepare_source_and_reference(args)
    rows, curves = run_ladder(args, src, ref)
    return report(args, rows, curves)


if __name__ == "__main__":
    sys.exit(main())
