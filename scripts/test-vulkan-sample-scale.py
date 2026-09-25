#!/usr/bin/env python3
"""Compile and execute the descriptor-to-sample-scale table from ADR-0147."""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import textwrap

ROOT = pathlib.Path(__file__).resolve().parent.parent
HEADER_DIR = ROOT / "ffmpeg-patches" / "files"

PIXDESC_STUB = r"""
#ifndef AVUTIL_PIXDESC_H
#define AVUTIL_PIXDESC_H

#include <stdint.h>

#define AV_PIX_FMT_FLAG_PLANAR (1ULL << 4)
#define AV_PIX_FMT_FLAG_RGB    (1ULL << 5)
#define AV_PIX_FMT_FLAG_PAL    (1ULL << 1)
#define AV_PIX_FMT_FLAG_BITSTREAM (1ULL << 2)
#define AV_PIX_FMT_FLAG_HWACCEL (1ULL << 3)
#define AV_PIX_FMT_FLAG_BAYER  (1ULL << 8)
#define AV_PIX_FMT_FLAG_FLOAT  (1ULL << 9)

enum AVPixelFormat {
    AV_PIX_FMT_NONE = -1,
    AV_PIX_FMT_STUB = 0,
};

typedef struct AVComponentDescriptor {
    int plane;
    int step;
    int offset;
    int shift;
    int depth;
} AVComponentDescriptor;

typedef struct AVPixFmtDescriptor {
    const char *name;
    uint8_t nb_components;
    uint64_t flags;
    AVComponentDescriptor comp[4];
} AVPixFmtDescriptor;

const AVPixFmtDescriptor *av_pix_fmt_desc_get(enum AVPixelFormat pix_fmt);

#endif
"""

TEST_SOURCE = r"""
#include <math.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

#include "pelorus_vulkan_sample.h"

const AVPixFmtDescriptor *av_pix_fmt_desc_get(enum AVPixelFormat pix_fmt)
{
    (void)pix_fmt;
    return NULL;
}

typedef struct ScaleCase {
    const char *name;
    AVPixFmtDescriptor desc;
    float expected;
    uint32_t expected_code_max;
} ScaleCase;

#define DESC(_components, _flags, _step, _shift, _depth)                    \
    {                                                                       \
        .name = "stub", .nb_components = (_components), .flags = (_flags), \
        .comp = {{.plane = 0, .step = (_step), .offset = 0,                \
                  .shift = (_shift), .depth = (_depth)}}                    \
    }

int main(void)
{
    static const ScaleCase cases[] = {
        {"planar-8",  DESC(3, AV_PIX_FMT_FLAG_PLANAR, 1, 0, 8),  1.0f, 255.0f},
        {"planar-10", DESC(3, AV_PIX_FMT_FLAG_PLANAR, 2, 0, 10),
         65535.0f / 1023.0f, 1023.0f},
        {"planar-12", DESC(3, AV_PIX_FMT_FLAG_PLANAR, 2, 0, 12),
         65535.0f / 4095.0f, 4095.0f},
        {"planar-16", DESC(3, AV_PIX_FMT_FLAG_PLANAR, 2, 0, 16), 1.0f, 65535.0f},
        {"p010", DESC(3, AV_PIX_FMT_FLAG_PLANAR, 2, 6, 10),
         65535.0f / 65472.0f, 1023.0f},
        {"p012", DESC(3, AV_PIX_FMT_FLAG_PLANAR, 2, 4, 12),
         65535.0f / 65520.0f, 4095.0f},
        {"p016", DESC(3, AV_PIX_FMT_FLAG_PLANAR, 2, 0, 16), 1.0f, 65535.0f},
        {"gray-10", DESC(1, 0, 2, 0, 10), 65535.0f / 1023.0f, 1023.0f},
        {"packed-rgba", DESC(4, AV_PIX_FMT_FLAG_RGB, 4, 0, 8), 1.0f, 0.0f},
        {"float-planar", DESC(3, AV_PIX_FMT_FLAG_PLANAR | AV_PIX_FMT_FLAG_FLOAT,
                               4, 0, 32), 1.0f, 0.0f},
        {"bad-step", DESC(3, AV_PIX_FMT_FLAG_PLANAR, 3, 0, 10), 1.0f, 0.0f},
        {"bad-depth-zero", DESC(3, AV_PIX_FMT_FLAG_PLANAR, 2, 0, 0), 1.0f, 0.0f},
        {"bad-depth-wide", DESC(3, AV_PIX_FMT_FLAG_PLANAR, 2, 0, 17), 1.0f, 0.0f},
        {"bad-shift", DESC(3, AV_PIX_FMT_FLAG_PLANAR, 2, 7, 10), 1.0f, 0.0f},
    };
    size_t i;

    if (pel_vk_sample_scale_from_desc(NULL) != 1.0f) {
        fputs("NULL descriptor did not fall back to 1.0\n", stderr);
        return 1;
    }
    if (pel_vk_sample_code_max_from_desc(NULL) != 0u) {
        fputs("NULL descriptor did not disable explicit quantization\n", stderr);
        return 1;
    }

    for (i = 0; i < sizeof(cases) / sizeof(cases[0]); i++) {
        const float actual = pel_vk_sample_scale_from_desc(&cases[i].desc);
        const float error = fabsf(actual - cases[i].expected);
        const uint32_t code_max = pel_vk_sample_code_max_from_desc(&cases[i].desc);
        if (error > 0.00001f * fmaxf(1.0f, fabsf(cases[i].expected))) {
            fprintf(stderr, "%s: got %.9g, expected %.9g\n",
                    cases[i].name, actual, cases[i].expected);
            return 1;
        }
        if (code_max != cases[i].expected_code_max) {
            fprintf(stderr, "%s code max: got %u, expected %u\n",
                    cases[i].name, code_max, cases[i].expected_code_max);
            return 1;
        }
    }

    puts("sample-scale descriptor table: 15 cases passed");
    return 0;
}
"""


def main() -> int:
    compiler = shutil.which(os.environ.get("CC", "cc"))
    if compiler is None:
        print("C compiler not found", file=sys.stderr)
        return 1

    if not (HEADER_DIR / "pelorus_vulkan_sample.h").is_file():
        print("pelorus_vulkan_sample.h is missing", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix="pelorus-sample-scale-") as raw_tmp:
        tmp = pathlib.Path(raw_tmp)
        stub_dir = tmp / "libavutil"
        stub_dir.mkdir()
        (stub_dir / "pixdesc.h").write_text(textwrap.dedent(PIXDESC_STUB))
        source = tmp / "test.c"
        binary = tmp / "test-vulkan-sample-scale"
        source.write_text(textwrap.dedent(TEST_SOURCE))
        compile_result = subprocess.run(
            [
                compiler,
                "-std=c11",
                "-Wall",
                "-Wextra",
                "-Werror",
                f"-I{tmp}",
                f"-I{HEADER_DIR}",
                str(source),
                "-lm",
                "-o",
                str(binary),
            ],
            check=False,
        )
        if compile_result.returncode != 0:
            return compile_result.returncode
        return subprocess.run([str(binary)], check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
