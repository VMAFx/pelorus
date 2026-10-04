#!/usr/bin/env python3
"""Compile and run the NVENC external-ME-hint frame setup shipped in patch 0008.

``nvenc-pelorus-me-hints.patch`` is only compiled inside an FFmpeg build with
ffnvcodec, which the fast suite does not have. This test extracts the Pelorus
blob reader and ``nvenc_setup_me_hints()`` from the canonical hand diff,
compiles them in a small C harness against minimal FFmpeg and NVENC shims, and
checks the per-frame contract (BUG-031):

* A session opened with ``enableExternalMEHints`` needs a populated hint buffer
  on EVERY frame; ``meHintCountsPerBlock`` of zero fails the encode on the
  device (``invalid param (8): SetupCEAHints failed``).  A frame without any
  Pelorus motion data must therefore submit one zero-MV candidate per 16x16
  block, and report the absence once.
* A frame with a motion section still translates its quarter-pel field to
  integer-pel hints (positive control).
* A motion-less frame after a motion frame must not reuse the stale hints.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
PATCH = ROOT / "ffmpeg-patches" / "files" / "nvenc-pelorus-me-hints.patch"

SHIMS = r"""
#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define AV_LOG_WARNING 24
#define AV_LOG_VERBOSE 40
#define AV_FRAME_DATA_SEI_UNREGISTERED 1

typedef struct AVCodecContext { void *priv_data; int width, height; } AVCodecContext;
typedef struct AVFrameSideData { uint8_t *data; size_t size; } AVFrameSideData;
typedef struct AVFrame { AVFrameSideData *sd; } AVFrame;

/* Field subset of nvEncodeAPI.h (names and widths as in the installed SDK). */
typedef struct NVENC_EXTERNAL_ME_HINT {
    int32_t mvx : 12;
    int32_t mvy : 10;
    int32_t refidx : 5;
    int32_t dir : 1;
    int32_t partType : 2;
    int32_t lastofPart : 1;
    int32_t lastOfMB : 1;
} NVENC_EXTERNAL_ME_HINT;
typedef struct { uint32_t numCandsPerBlk16x16 : 4; } NVENC_EXTERNAL_ME_HINT_COUNTS_PER_BLOCKTYPE;
typedef struct NV_ENC_PIC_PARAMS {
    NVENC_EXTERNAL_ME_HINT_COUNTS_PER_BLOCKTYPE meHintCountsPerBlock[2];
    NVENC_EXTERNAL_ME_HINT *meExternalHints;
} NV_ENC_PIC_PARAMS;

typedef struct NvencContext {
    int pelorus_me_hints;
    NVENC_EXTERNAL_ME_HINT *me_hints;
    int me_hints_count, me_hints_w, me_hints_h;
    int me_hints_warned, me_hints_absent_warned;
    struct { struct { unsigned enableAQ, enableLookahead; } rcParams; } encode_config;
} NvencContext;

static int warnings;

static void av_log(void *avcl, int level, const char *fmt, ...)
{
    (void)avcl; (void)fmt;
    if (level == AV_LOG_WARNING)
        warnings++;
}
static int av_clip(int a, int amin, int amax)
{
    return a < amin ? amin : (a > amax ? amax : a);
}
static AVFrameSideData *av_frame_get_side_data(const AVFrame *f, int type)
{
    (void)type;
    return f->sd;
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

static void put16(uint8_t *p, unsigned v) { p[0] = v & 255; p[1] = v >> 8; }
static void put32(uint8_t *p, unsigned v) { put16(p, v & 0xffff); put16(p + 2, v >> 16); }

/* Pelorus blob: uuid + 48-byte header + one 16-byte directory entry + a 28-byte
 * motion section + the (dx,dy) quarter-pel grid. Layout per interop.h. */
static size_t build_blob(uint8_t *b, int gc, int gr, const int16_t *mv)
{
    const size_t hdr = 48, dir = 16, sec = 28, nmv = (size_t)gc * gr * 2;
    const size_t img = hdr + dir + sec + nmv * 2;
    uint8_t *im = b + 16;
    size_t i;

    memset(b, 0, 16 + img);
    memcpy(b, pel_uuid, 16);
    memcpy(im, "PELOR1\0\0", 8);
    put16(im + 8, 1);
    put32(im + 12, (unsigned)img);
    put32(im + 16, 1u << 4);
    put16(im + 20, 1);
    put16(im + 22, (unsigned)hdr);
    put16(im + 34, (unsigned)gc);
    put16(im + 36, (unsigned)gr);
    put32(im + hdr + 0, 1u << 4);
    put32(im + hdr + 4, (unsigned)(hdr + dir));
    put32(im + hdr + 8, (unsigned)sec);
    put32(im + hdr + dir + 20, (unsigned)(hdr + dir + sec));
    put32(im + hdr + dir + 24, (unsigned)(nmv * 2));
    for (i = 0; i < nmv; i++)
        put16(im + hdr + dir + sec + 2 * i, (uint16_t)mv[i]);
    return 16 + img;
}

int main(void)
{
    NVENC_EXTERNAL_ME_HINT hints[4]; /* 2x2 blocks of 16x16 */
    NvencContext ctx;
    AVCodecContext avctx;
    AVFrameSideData sd;
    AVFrame frame;
    NV_ENC_PIC_PARAMS pp;
    uint8_t blob[512];
    /* 1x1 producer grid, quarter-pel (+9, -9) -> integer-pel (+2, -2). */
    const int16_t mv[2] = { 9, -9 };
    int i;

    memset(&ctx, 0, sizeof(ctx));
    memset(hints, 0x5a, sizeof(hints));
    ctx.pelorus_me_hints = 1;
    ctx.me_hints = hints;
    ctx.me_hints_w = ctx.me_hints_h = 2;
    ctx.me_hints_count = 4;
    avctx.priv_data = &ctx;
    avctx.width = avctx.height = 32;

    /* 1. No side data at all. */
    frame.sd = NULL;
    memset(&pp, 0, sizeof(pp));
    expect("no-data rc", nvenc_setup_me_hints(&avctx, &frame, &pp), 0);
    expect("no-data L0 candidates", pp.meHintCountsPerBlock[0].numCandsPerBlk16x16, 1);
    expect("no-data L1 candidates", pp.meHintCountsPerBlock[1].numCandsPerBlk16x16, 0);
    expect("no-data buffer set", pp.meExternalHints == hints, 1);
    for (i = 0; i < 4; i++) {
        expect("no-data mvx", hints[i].mvx, 0);
        expect("no-data mvy", hints[i].mvy, 0);
        expect("no-data refidx", hints[i].refidx, 0);
        expect("no-data lastOfMB set", hints[i].lastOfMB != 0, 1); /* signed 1-bit field reads -1 */
    }
    expect("no-data warned once", warnings, 1);

    /* 2. Side data that is not a Pelorus blob. */
    sd.data = (uint8_t *)"not a pelorus blob, just filler bytes for the length check.......";
    sd.size = 64;
    frame.sd = &sd;
    memset(&pp, 0, sizeof(pp));
    nvenc_setup_me_hints(&avctx, &frame, &pp);
    expect("foreign-sei L0 candidates", pp.meHintCountsPerBlock[0].numCandsPerBlk16x16, 1);
    expect("foreign-sei still one warning", warnings, 1);

    /* 3. A real motion blob: positive control, hints carry the field. */
    sd.data = blob;
    sd.size = build_blob(blob, 1, 1, mv);
    memset(&pp, 0, sizeof(pp));
    nvenc_setup_me_hints(&avctx, &frame, &pp);
    expect("motion L0 candidates", pp.meHintCountsPerBlock[0].numCandsPerBlk16x16, 1);
    for (i = 0; i < 4; i++) {
        expect("motion mvx", hints[i].mvx, 2);
        expect("motion mvy", hints[i].mvy, -2);
    }

    /* 4. Motion-less frame after a motion frame: stale vectors must not leak. */
    frame.sd = NULL;
    memset(&pp, 0, sizeof(pp));
    nvenc_setup_me_hints(&avctx, &frame, &pp);
    expect("stale L0 candidates", pp.meHintCountsPerBlock[0].numCandsPerBlk16x16, 1);
    for (i = 0; i < 4; i++) {
        expect("stale mvx", hints[i].mvx, 0);
        expect("stale mvy", hints[i].mvy, 0);
    }

    /* 5. Option off: the frame is left untouched. */
    ctx.pelorus_me_hints = 0;
    memset(&pp, 0, sizeof(pp));
    nvenc_setup_me_hints(&avctx, &frame, &pp);
    expect("off L0 candidates", pp.meHintCountsPerBlock[0].numCandsPerBlk16x16, 0);

    if (failures)
        return 1;
    puts("nvenc me hints: all checks passed");
    return 0;
}
"""


def added_lines(patch: pathlib.Path) -> list[str]:
    """Return the '+' lines of a unified diff without the marker."""
    return [line[1:] for line in patch.read_text().splitlines()
            if line.startswith("+") and not line.startswith("+++")]


def extract_chunk(lines: list[str]) -> str:
    """The Pelorus blob reader through the end of nvenc_setup_me_hints()."""
    start = next((i for i, l in enumerate(lines) if l.startswith("#define PEL_UUID_LEN")), None)
    func = next((i for i, l in enumerate(lines)
                 if l.startswith("static int nvenc_setup_me_hints(")), None)
    if start is None or func is None:
        raise ValueError("blob reader or nvenc_setup_me_hints not found in the patch")
    end = next(i for i in range(func, len(lines)) if lines[i] == "}")
    return "\n".join(lines[start:end + 1]) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--patch", type=pathlib.Path, default=PATCH)
    parser.add_argument("--cc", nargs="+", default=[os.environ.get("CC", "cc")],
                        help="C compiler command (must be the last option)")
    args = parser.parse_args()

    try:
        chunk = extract_chunk(added_lines(args.patch))
    except (OSError, ValueError, StopIteration) as err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix="pelorus-me-hints-") as tmp:
        tmp_path = pathlib.Path(tmp)
        c_file = tmp_path / "harness.c"
        exe = tmp_path / ("harness.exe" if os.name == "nt" else "harness")
        c_file.write_text(SHIMS + chunk + MAIN)
        build = subprocess.run(
            [*args.cc, "-std=c11", "-Wall", "-Wextra", "-Werror",
             "-Wno-unused-function", str(c_file), "-o", str(exe)],
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
