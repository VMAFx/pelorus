#!/usr/bin/env python3
"""Compile and run the SVT-AV1 ROI event builder shipped in patch 0013.

``svtav1-pelorus-roi.patch`` only compiles inside an FFmpeg build with
libsvtav1, which the fast suite does not have. This test extracts the ROI event
helpers from the canonical hand diff, compiles them in a small C harness
against minimal FFmpeg and SVT-AV1 shims, and checks the sticky-event contract
(BUG-030).

SVT-AV1 keeps the last ROI event pointer and applies it to every later frame
that carries none (verified with libsvtav1 4.2.0: a stream with ROI side data
on its first 33 frames and none afterwards encoded the remaining frames
byte-for-byte like a stream with the ROI on all frames).  The builder must
therefore replace a non-neutral sticky event with a neutral one on the first
frame without usable ROI data, and stay silent otherwise.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
PATCH = ROOT / "ffmpeg-patches" / "files" / "svtav1-pelorus-roi.patch"

SHIMS = r"""
#include <errno.h>
#include <limits.h>
#include <math.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define SVT_AV1_CHECK_VERSION(a, b, c) 1
#define AV_LOG_ERROR 16
#define AV_FRAME_DATA_REGIONS_OF_INTEREST 7
#define AVERROR(e) (-(e))
#define FFABS(a) ((a) >= 0 ? (a) : (-(a)))

typedef struct AVRational { int num, den; } AVRational;
typedef struct AVRegionOfInterest {
    uint32_t self_size;
    int top, bottom, left, right;
    AVRational qoffset;
} AVRegionOfInterest;
typedef struct AVFrameSideData { uint8_t *data; size_t size; } AVFrameSideData;
typedef struct AVFrame { AVFrameSideData *sd; int64_t pts; } AVFrame;
typedef struct AVPixFmtComponentDescriptor { int depth; } AVPixFmtComponentDescriptor;
typedef struct AVPixFmtDescriptor { AVPixFmtComponentDescriptor comp[4]; } AVPixFmtDescriptor;
typedef struct AVCodecContext { void *priv_data; int pix_fmt; } AVCodecContext;

typedef struct SvtAv1RoiMapEvt {
    uint64_t start_picture_number;
    uint8_t *b64_seg_map;
    int16_t seg_qp[8];
    int8_t max_seg_id;
    struct SvtAv1RoiMapEvt *next;
} SvtAv1RoiMapEvt;

struct PelorusSvtRoiSlot {
    SvtAv1RoiMapEvt *evt;
    uint64_t seq;
};

typedef struct SvtContext {
    int pelorus_roi;
    struct PelorusSvtRoiSlot *roi_evts;
    int roi_nevts;
    uint64_t roi_nframes, roi_npackets, roi_nbuilt;
    int roi_peak;
    int roi_sticky;
    unsigned roi_b64_cols, roi_b64_rows;
} SvtContext;

static void av_log(void *avcl, int level, const char *fmt, ...) { (void)avcl; (void)level; (void)fmt; }
static void *av_mallocz(size_t n) { return calloc(1, n); }
static void *av_calloc(size_t n, size_t m) { return calloc(n, m); }
static void *av_realloc_array(void *p, size_t n, size_t m) { return realloc(p, n * m); }
static void av_free(void *p) { free(p); }
static int av_clip(int a, int lo, int hi) { return a < lo ? lo : (a > hi ? hi : a); }
static float av_clipf(float a, float lo, float hi) { return a < lo ? lo : (a > hi ? hi : a); }
static AVFrameSideData *av_frame_get_side_data(const AVFrame *f, int type) { (void)type; return f->sd; }
static const AVPixFmtDescriptor *av_pix_fmt_desc_get(int fmt)
{
    static const AVPixFmtDescriptor d = { { { 8 } } };
    (void)fmt;
    return &d;
}
"""

MAIN = r"""
static int failures;

static void expect(const char *what, long got, long want)
{
    if (got != want) {
        fprintf(stderr, "FAIL %s: got %ld, want %ld\n", what, got, want);
        failures++;
    }
}

static int all_zero(const SvtAv1RoiMapEvt *e, size_t n)
{
    size_t i;

    for (i = 0; i < n; i++)
        if (e->b64_seg_map[i])
            return 0;
    for (i = 0; i < 8; i++)
        if (e->seg_qp[i])
            return 0;
    return 1;
}

static int build(AVCodecContext *avctx, AVFrame *f, AVFrameSideData *sd,
                 AVRegionOfInterest *roi, int qnum, SvtAv1RoiMapEvt **evt)
{
    if (roi) {
        memset(roi, 0, sizeof(*roi));
        roi->self_size = sizeof(*roi);
        roi->right = 64;
        roi->bottom = 64;
        roi->qoffset.num = qnum;
        roi->qoffset.den = 10;
        sd->data = (uint8_t *)roi;
        sd->size = sizeof(*roi);
        f->sd = sd;
    } else {
        f->sd = NULL;
    }
    return svtav1_build_roi_evt(avctx, f, evt);
}

int main(void)
{
    SvtContext svt;
    AVCodecContext avctx;
    AVFrame f;
    AVFrameSideData sd;
    AVRegionOfInterest roi;
    SvtAv1RoiMapEvt *evt = NULL;
    size_t sbs = 4; /* 2x2 superblocks */
    int i;

    memset(&svt, 0, sizeof(svt));
    memset(&f, 0, sizeof(f));
    svt.pelorus_roi = 1;
    svt.roi_b64_cols = svt.roi_b64_rows = 2;
    avctx.priv_data = &svt;
    avctx.pix_fmt = 0;

    /* 1. No ROI yet and none on the frame: nothing to do. */
    expect("idle rc", build(&avctx, &f, &sd, NULL, 0, &evt), 0);
    expect("idle no event", evt == NULL, 1);
    expect("idle not sticky", svt.roi_sticky, 0);

    /* 2. A real ROI becomes the sticky event. */
    f.pts = 1;
    expect("roi rc", build(&avctx, &f, &sd, &roi, -5, &evt), 0);
    expect("roi event built", evt != NULL, 1);
    expect("roi sticky", svt.roi_sticky, 1);
    svt.roi_nframes++;

    /* 3. First frame without ROI data: a neutral event must replace it. */
    f.pts = 2;
    expect("strip rc", build(&avctx, &f, &sd, NULL, 0, &evt), 0);
    expect("strip sends neutral event", evt != NULL, 1);
    if (evt) {
        expect("neutral map is all zero", all_zero(evt, sbs), 1);
        expect("neutral max_seg_id", evt->max_seg_id, 0);
        expect("neutral next unlinked", evt->next == NULL, 1);
    }
    expect("neutral clears sticky", svt.roi_sticky, 0);
    svt.roi_nframes++;

    /* 4. Further frames inherit the neutral event: no new event. */
    f.pts = 3;
    expect("second strip rc", build(&avctx, &f, &sd, NULL, 0, &evt), 0);
    expect("second strip no event", evt == NULL, 1);

    /* 5. ROI returns, then an all-zero-delta ROI must also reset. */
    expect("roi again rc", build(&avctx, &f, &sd, &roi, -5, &evt), 0);
    expect("roi again event", evt != NULL, 1);
    expect("roi again sticky", svt.roi_sticky, 1);
    expect("zero-delta rc", build(&avctx, &f, &sd, &roi, 0, &evt), 0);
    expect("zero-delta sends neutral event", evt != NULL, 1);
    if (evt)
        expect("zero-delta neutral map", all_zero(evt, sbs), 1);
    expect("zero-delta clears sticky", svt.roi_sticky, 0);

    for (i = 0; i < svt.roi_nevts; i++)
        svtav1_free_roi_evt(svt.roi_evts[i].evt);
    free(svt.roi_evts);

    if (failures)
        return 1;
    puts("svtav1 roi sticky: all checks passed");
    return 0;
}
"""


def extract_chunk(patch: pathlib.Path) -> str:
    """The ROI helpers: from the segment-count define to the closing #endif."""
    lines = [line[1:] for line in patch.read_text().splitlines()
             if line.startswith("+") and not line.startswith("+++")]
    start = next((i for i, l in enumerate(lines)
                  if l.startswith("#define PELORUS_SVTAV1_MAX_SEGMENTS")), None)
    if start is None:
        raise ValueError("PELORUS_SVTAV1_MAX_SEGMENTS not found in the patch")
    end = next((i for i in range(start, len(lines))
                if lines[i].startswith("#endif /* SVT_AV1_CHECK_VERSION(1, 6, 0) */")), None)
    if end is None:
        raise ValueError("closing #endif of the ROI helpers not found")
    return "\n".join(lines[start:end]) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--patch", type=pathlib.Path, default=PATCH)
    parser.add_argument("--cc", nargs="+", default=[os.environ.get("CC", "cc")],
                        help="C compiler command (must be the last option)")
    args = parser.parse_args()

    try:
        chunk = extract_chunk(args.patch)
    except (OSError, ValueError) as err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix="pelorus-svt-roi-") as tmp:
        tmp_path = pathlib.Path(tmp)
        c_file = tmp_path / "harness.c"
        exe = tmp_path / ("harness.exe" if os.name == "nt" else "harness")
        c_file.write_text(SHIMS + chunk + MAIN)
        build = subprocess.run(
            [*args.cc, "-std=c11", "-Wall", "-Wextra", "-Werror",
             "-Wno-unused-function", str(c_file), "-o", str(exe), "-lm"],
            capture_output=True, text=True, check=False)
        if build.returncode != 0:
            print("ERROR: harness failed to compile", file=sys.stderr)
            print(build.stdout + build.stderr, file=sys.stderr)
            return 1
        run = subprocess.run([str(exe)], capture_output=True, text=True, check=False)
        sys.stdout.write(run.stdout)
        sys.stderr.write(run.stderr)
        if run.returncode != 0:
            print(f"ERROR: harness exited {run.returncode}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
