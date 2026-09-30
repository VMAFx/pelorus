#!/usr/bin/env bash
# shw-2 step 33: produce aa planes=15 fast=0/1 outputs for one noisy input on the
# UHD 770 (linear single-plane mode) and on the Arc B580 (its exact mode and the
# same linear mode), then locate every fast/direct difference (33-*.py).
# Correctness run only (no timing). Sequential: one GPU workload at a time.
set -uo pipefail
source /c/tmp/pel/shw-scratch/shw2/env.sh
O=$RES/33-aa-residual; rm -rf "$O"; mkdir -p "$O"
IN="testsrc2=size=640x360:rate=12,noise=alls=14:allf=t:all_seed=11"
run() { # name spec fastflag
    "$FFM" -hide_banner -nostdin -loglevel error -y -init_hw_device "vulkan=vk:$2" -filter_hw_device vk \
        -f lavfi -i "$IN" -frames:v 12 \
        -vf "format=yuv420p,hwupload,pelorus_aa_vulkan=planes=15$3,hwdownload,format=yuv420p" \
        -f rawvideo "$O/$1.raw" || echo "run $1 rc=$?"
}
run uhd-f0     "1,linear_images=1,disable_multiplane=1" ""
run uhd-f1     "1,linear_images=1,disable_multiplane=1" ":fast=1"
run b580-f0    "0,disable_multiplane=1" ""
run b580-f1    "0,disable_multiplane=1" ":fast=1"
run b580lin-f0 "0,linear_images=1,disable_multiplane=1" ""
run b580lin-f1 "0,linear_images=1,disable_multiplane=1" ":fast=1"
python3 "$SCR/33-aa-residual-locate.py" "$O" 640 360 12
