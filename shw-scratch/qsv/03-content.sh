#!/usr/bin/env bash
# shw-3 step 03: deterministic test content for the ownership/async runs.
#   own1080.nv12 : 1920x1080 NV12, 600 frames @30, testsrc2 (motion + detail in
#                  both halves) with seeded temporal noise so every block carries
#                  texture and a QP change is visible in PSNR.
set -uo pipefail
export PATH="/c/tmp/pel/prefix/bin:/ucrt64/bin:$PATH"
C=/c/tmp/pel/shw-scratch/qsv/content
mkdir -p "$C"
[ -s "$C/own1080.nv12" ] || ffmpeg -hide_banner -nostdin -loglevel error -y \
    -f lavfi -i "testsrc2=s=1920x1080:r=30,noise=alls=6:allf=t+u:all_seed=4242,format=nv12" \
    -frames:v 600 -f rawvideo "$C/own1080.nv12"
ls -la "$C"
sha256sum "$C/own1080.nv12" | tee "$C/own1080.sha256"
