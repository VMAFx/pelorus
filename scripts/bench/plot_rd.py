#!/usr/bin/env python3
# plot_rd.py — render rate-distortion curves from run-bench.py result.json files.
#
# Each --curve is LABEL=path/to/result.json[:variant]; variant defaults to
# "pelorus" (the filtered arm). The shared "baseline" arm is taken from the first
# result that has it. Produces a log-bitrate vs quality PNG (+ an optional second
# panel for a second metric column).
#
#   plot_rd.py --metric vmaf --title "MC->denoise warp (BBB action)" \
#     --out docs/development/images/mc-denoise-warp-rd.png \
#     --baseline-from /tmp/bw2-mc1/result.json \
#     --curve "warp (mc=1)=/tmp/bw2-mc1/result.json" \
#     --curve "same-coord (mc=0)=/tmp/bw2-mc0/result.json"
import argparse
import json
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


def rows_for(path, variant, metric):
    data = json.load(open(path))
    pts = [(r["bitrate_kbps"], r[metric]) for r in data["rows"] if r["variant"] == variant]
    pts.sort()
    return [p[0] for p in pts], [p[1] for p in pts]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--metric", default="vmaf", help="row column to plot on Y (vmaf, cambi, ...)")
    ap.add_argument("--ylabel", default=None)
    ap.add_argument("--title", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--baseline-from", dest="baseline_from", default=None,
                    help="result.json whose 'baseline' arm is the shared no-filter reference")
    ap.add_argument("--curve", action="append", default=[],
                    help="LABEL=result.json[:variant] (variant default 'pelorus')")
    args = ap.parse_args()

    fig, ax = plt.subplots(figsize=(7.2, 4.6), dpi=130)
    ylabel = args.ylabel or (args.metric.upper() + " vs clean")

    if args.baseline_from:
        bx, by = rows_for(args.baseline_from, "baseline", args.metric)
        ax.plot(bx, by, "o--", color="#888888", label="no pre-filter (baseline)", zorder=2)

    palette = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd"]
    for i, spec in enumerate(args.curve):
        label, rhs = spec.split("=", 1)
        path, _, variant = rhs.partition(":")
        variant = variant or "pelorus"
        x, y = rows_for(path, variant, args.metric)
        ax.plot(x, y, "o-", color=palette[i % len(palette)], label=label, linewidth=2, zorder=3)

    ax.set_xscale("log")
    ax.set_xlabel("bitrate (kbps, log scale)")
    ax.set_ylabel(ylabel)
    ax.set_title(args.title)
    ax.grid(True, which="both", ls=":", alpha=0.4)
    ax.legend(loc="best", framealpha=0.9)
    fig.tight_layout()
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    fig.savefig(args.out)
    print("wrote", args.out)


if __name__ == "__main__":
    main()
