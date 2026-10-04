#!/usr/bin/env python3
"""Compile and run the NVENC Pelorus value mappings shipped in the hand diffs.

The NVENC encoder patches are only compiled inside an FFmpeg build with
ffnvcodec, which the fast suite does not have. This test extracts the two pure
mapping functions from the canonical hand-maintained diffs, compiles them in a
small C harness against minimal FFmpeg shims, and checks their results:

* pel_fg_from_aom() (nvenc-pelorus-film-grain.patch) must give NVENC the raw
  AV1 film_grain_params() values: cb/cr_mult and cb/cr_luma_mult with their
  +128 bias, cb/cr_offset with its +256 bias (BUG-019).
* pelorus_roi_qp_range() (nvenc-pelorus-roi.patch) must scale ROI qoffsets by
  the H.264/HEVC QP span for those codecs and by the AV1 qindex span (255) for
  AV1 (BUG-020).

The harness uses the installed ffnvcodec nvEncodeAPI.h when pkg-config finds
it, and otherwise a mirror of the NV_ENC_FILM_GRAIN_PARAMS_AV1 layout from
Video Codec SDK 13.1.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
FILES = ROOT / "ffmpeg-patches" / "files"

SHIMS = r"""
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

enum AVCodecID { AV_CODEC_ID_H264 = 27, AV_CODEC_ID_HEVC = 173, AV_CODEC_ID_AV1 = 225 };

static int av_clip(int a, int amin, int amax)
{
    return a < amin ? amin : (a > amax ? amax : a);
}
static uint8_t av_clip_uint8(int a)
{
    return (uint8_t)av_clip(a, 0, 255);
}

/* Field subset of libavutil/film_grain_params.h AVFilmGrainAOMParams (n9.0.2);
 * uv_mult, uv_mult_luma and uv_offset are unbiased there. */
typedef struct AVFilmGrainAOMParams {
    int num_y_points;
    uint8_t y_points[14][2];
    int chroma_scaling_from_luma;
    int num_uv_points[2];
    uint8_t uv_points[2][10][2];
    int scaling_shift;
    int ar_coeff_lag;
    int8_t ar_coeffs_y[24];
    int8_t ar_coeffs_uv[2][25];
    int ar_coeff_shift;
    int grain_scale_shift;
    int uv_mult[2];
    int uv_mult_luma[2];
    int uv_offset[2];
    int overlap_flag;
    int limit_output_range;
} AVFilmGrainAOMParams;
"""

MIRROR = r"""
/* Mirror of NV_ENC_FILM_GRAIN_PARAMS_AV1 (nvEncodeAPI.h, Video Codec SDK 13.1). */
typedef struct _NV_ENC_FILM_GRAIN_PARAMS_AV1 {
    uint32_t applyGrain : 1;
    uint32_t chromaScalingFromLuma : 1;
    uint32_t overlapFlag : 1;
    uint32_t clipToRestrictedRange : 1;
    uint32_t grainScalingMinus8 : 2;
    uint32_t arCoeffLag : 2;
    uint32_t numYPoints : 4;
    uint32_t numCbPoints : 4;
    uint32_t numCrPoints : 4;
    uint32_t arCoeffShiftMinus6 : 2;
    uint32_t grainScaleShift : 2;
    uint32_t reserved1 : 8;
    uint8_t pointYValue[14];
    uint8_t pointYScaling[14];
    uint8_t pointCbValue[10];
    uint8_t pointCbScaling[10];
    uint8_t pointCrValue[10];
    uint8_t pointCrScaling[10];
    uint8_t arCoeffsYPlus128[24];
    uint8_t arCoeffsCbPlus128[25];
    uint8_t arCoeffsCrPlus128[25];
    uint8_t reserved2[2];
    uint8_t cbMult;
    uint8_t cbLumaMult;
    uint16_t cbOffset;
    uint8_t crMult;
    uint8_t crLumaMult;
    uint16_t crOffset;
} NV_ENC_FILM_GRAIN_PARAMS_AV1;
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

static void check_chroma(const char *label, int mult, int luma_mult, int offset,
                         long want_mult, long want_luma, long want_offset)
{
    AVFilmGrainAOMParams a;
    NV_ENC_FILM_GRAIN_PARAMS_AV1 d;
    char what[96];

    memset(&a, 0, sizeof(a));
    a.scaling_shift = 8;
    a.ar_coeff_shift = 6;
    a.uv_mult[0] = mult;
    a.uv_mult_luma[0] = luma_mult;
    a.uv_offset[0] = offset;
    a.uv_mult[1] = mult;
    a.uv_mult_luma[1] = luma_mult;
    a.uv_offset[1] = offset;
    pel_fg_from_aom(&d, &a, 1);

    snprintf(what, sizeof(what), "%s cbMult", label);
    expect(what, d.cbMult, want_mult);
    snprintf(what, sizeof(what), "%s cbLumaMult", label);
    expect(what, d.cbLumaMult, want_luma);
    snprintf(what, sizeof(what), "%s cbOffset", label);
    expect(what, d.cbOffset, want_offset);
    snprintf(what, sizeof(what), "%s crMult", label);
    expect(what, d.crMult, want_mult);
    snprintf(what, sizeof(what), "%s crLumaMult", label);
    expect(what, d.crLumaMult, want_luma);
    snprintf(what, sizeof(what), "%s crOffset", label);
    expect(what, d.crOffset, want_offset);
}

int main(void)
{
    AVFilmGrainAOMParams a;
    NV_ENC_FILM_GRAIN_PARAMS_AV1 d;

    /* libaom film-grain test vector 1, as libdav1d exports it (unbiased):
     * coded cb 247/192/18 and cr 229/192/54. */
    memset(&a, 0, sizeof(a));
    a.scaling_shift = 11;
    a.ar_coeff_lag = 2;
    a.ar_coeff_shift = 8;
    a.num_y_points = 1;
    a.y_points[0][0] = 16;
    a.ar_coeffs_y[2] = -58;
    a.uv_mult[0] = 119;
    a.uv_mult_luma[0] = 64;
    a.uv_offset[0] = -238;
    a.uv_mult[1] = 101;
    a.uv_mult_luma[1] = 64;
    a.uv_offset[1] = -202;
    pel_fg_from_aom(&d, &a, 1);
    expect("vector1 grainScalingMinus8", d.grainScalingMinus8, 3);
    expect("vector1 arCoeffShiftMinus6", d.arCoeffShiftMinus6, 2);
    expect("vector1 arCoeffsYPlus128[2]", d.arCoeffsYPlus128[2], 70);
    expect("vector1 cbMult", d.cbMult, 247);
    expect("vector1 cbLumaMult", d.cbLumaMult, 192);
    expect("vector1 cbOffset", d.cbOffset, 18);
    expect("vector1 crMult", d.crMult, 229);
    expect("vector1 crLumaMult", d.crLumaMult, 192);
    expect("vector1 crOffset", d.crOffset, 54);

    check_chroma("neutral", 0, 0, 0, 128, 128, 256);
    check_chroma("minimum", -128, -128, -256, 0, 0, 0);
    check_chroma("maximum", 127, 127, 255, 255, 255, 511);
    check_chroma("clamped", 1000, -1000, 4096, 255, 0, 511);

    expect("roi qp range h264 8-bit", pelorus_roi_qp_range(AV_CODEC_ID_H264, 8), 51);
    expect("roi qp range hevc 8-bit", pelorus_roi_qp_range(AV_CODEC_ID_HEVC, 8), 51);
    expect("roi qp range hevc 10-bit", pelorus_roi_qp_range(AV_CODEC_ID_HEVC, 10), 63);
    expect("roi qp range av1 8-bit", pelorus_roi_qp_range(AV_CODEC_ID_AV1, 8), 255);
    expect("roi qp range av1 10-bit", pelorus_roi_qp_range(AV_CODEC_ID_AV1, 10), 255);

    if (failures)
        return 1;
    puts("nvenc mapping: all checks passed");
    return 0;
}
"""


def added_lines(patch: pathlib.Path) -> list[str]:
    """Return the '+' lines of a unified diff without the marker."""
    out = []
    for line in patch.read_text().splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            out.append(line[1:])
    return out


def extract_function(lines: list[str], signature: str, label: str) -> str:
    """Extract a top-level C function from added diff lines by its signature."""
    pattern = re.compile(r"^static\s+[a-z_ ]+\b" + re.escape(signature) + r"\s*\(")
    for start, line in enumerate(lines):
        if pattern.match(line):
            for end in range(start + 1, len(lines)):
                if lines[end] == "}":
                    return "\n".join(lines[start : end + 1]) + "\n"
            break
    raise ValueError(f"{label}: function {signature} not found")


def nvenc_header_source() -> str:
    """Prefer the installed SDK header; fall back to the 13.1 layout mirror."""
    pkg_config = shutil.which("pkg-config")
    if pkg_config:
        probe = subprocess.run(
            [pkg_config, "--variable=includedir", "ffnvcodec"],
            capture_output=True,
            text=True,
            check=False,
        )
        include_dir = probe.stdout.strip()
        header = pathlib.Path(include_dir) / "ffnvcodec" / "nvEncodeAPI.h"
        if probe.returncode == 0 and header.is_file():
            print(f"using installed SDK header {header}")
            return f'#include "{header.as_posix()}"\n'
    print("ffnvcodec not found; using the SDK 13.1 struct mirror")
    return MIRROR


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--film-grain-patch",
        type=pathlib.Path,
        default=FILES / "nvenc-pelorus-film-grain.patch",
    )
    parser.add_argument(
        "--roi-patch", type=pathlib.Path, default=FILES / "nvenc-pelorus-roi.patch"
    )
    parser.add_argument(
        "--cc",
        nargs="+",
        default=[os.environ.get("CC", "cc")],
        help="C compiler command (must be the last option)",
    )
    args = parser.parse_args()

    try:
        fg = extract_function(
            added_lines(args.film_grain_patch), "pel_fg_from_aom", "film grain"
        )
        roi = extract_function(
            added_lines(args.roi_patch), "pelorus_roi_qp_range", "ROI"
        )
    except (OSError, ValueError) as err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 1

    source = SHIMS + nvenc_header_source() + fg + roi + MAIN
    with tempfile.TemporaryDirectory(prefix="pelorus-nvenc-map-") as tmp:
        tmp_path = pathlib.Path(tmp)
        c_file = tmp_path / "harness.c"
        exe = tmp_path / ("harness.exe" if os.name == "nt" else "harness")
        c_file.write_text(source)
        build = subprocess.run(
            [*args.cc, "-std=c11", "-Wall", "-Wextra", "-Werror",
             "-Wno-unused-function", str(c_file), "-o", str(exe)],
            capture_output=True,
            text=True,
            check=False,
        )
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
