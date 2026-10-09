#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Compile and run the QP-map format choice and host raster of patch 0009.

``vulkan-pelorus-qpmap.patch`` only compiles inside an FFmpeg build with Vulkan
video headers. This test extracts the map-entry classifier and the host
rasterizer from the canonical hand diff, compiles them in a small C harness
against minimal Vulkan and FFmpeg shims, and checks the contract of #278 and
ADR-0182:

* RADV (Mesa 26.2.4) advertises its delta map as ``R32_SINT`` in OPTIMAL,
  LINEAR and DRM-modifier tiling, and its encode queue family has
  ``VIDEO_ENCODE`` only. Only the LINEAR entry is fillable there, by the
  host-mapped path; the copy needs a transfer-capable family.
* NVIDIA advertises ``R8_SINT`` in LINEAR tiling with ``TRANSFER_DST`` and its
  encode family has ``TRANSFER``: the staging copy, as before.
* Negative: OPTIMAL-only entries on a video-encode-only family, DRM-modifier
  tiling, an unknown format and a zero texel block are not fillable.
* Boundary: the on-GPU raster takes only the 8-bit formats its shader
  declares; texels are written at the advertised width (1, 2 or 4 bytes) into
  rows ``pitch`` bytes apart, padding untouched; the first rectangle wins on
  overlap; emphasis maps scale to the unorm width.

The extracted code compiles with ``-Wall -Wextra -Werror``. ``--self-test``
plants a defect in the extracted code for each rule and requires the harness
to reject every one, and plants one compiler warning and requires the strict
build to refuse it.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
PATCH = ROOT / "ffmpeg-patches" / "files" / "vulkan-pelorus-qpmap.patch"
ENC = "libavcodec/vulkan_encode.c"
FUNCTIONS = ("pelorus_qpmap_texel_bytes", "pelorus_qpmap_entry_fill",
             "pelorus_qpmap_texel_range", "pelorus_qpmap_store_texel",
             "pelorus_qpmap_raster")

SHIMS = r"""
#include <limits.h>
#include <math.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef int VkFormat;
#define VK_FORMAT_R8_UNORM 9
#define VK_FORMAT_R8_SINT 14
#define VK_FORMAT_R16_UNORM 70
#define VK_FORMAT_R16_SINT 75
#define VK_FORMAT_R32_SINT 99
typedef int VkImageTiling;
#define VK_IMAGE_TILING_OPTIMAL 0
#define VK_IMAGE_TILING_LINEAR 1
#define VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT 1000158000
typedef uint32_t VkImageUsageFlags;
#define VK_IMAGE_USAGE_TRANSFER_DST_BIT 0x2u
#define VK_IMAGE_USAGE_STORAGE_BIT 0x8u
#define VK_IMAGE_USAGE_VIDEO_ENCODE_QUANTIZATION_DELTA_MAP_BIT_KHR 0x2000000u
typedef struct VkExtent2D { uint32_t width, height; } VkExtent2D;
typedef struct VkVideoFormatPropertiesKHR {
    VkFormat format;
    VkImageTiling imageTiling;
    VkImageUsageFlags imageUsageFlags;
} VkVideoFormatPropertiesKHR;
typedef struct VkVideoFormatQuantizationMapPropertiesKHR {
    VkExtent2D quantizationMapTexelSize;
} VkVideoFormatQuantizationMapPropertiesKHR;

enum PelorusQpMapFill {
    PELORUS_QPMAP_FILL_NONE = 0,
    PELORUS_QPMAP_FILL_HOST,
    PELORUS_QPMAP_FILL_COPY,
    PELORUS_QPMAP_FILL_GPU,
};
typedef struct PelorusQpRect {
    int32_t x0, y0, x1, y1, delta, _pad0, _pad1, _pad2;
} PelorusQpRect;
typedef struct FFVulkanEncodeContext {
    int qpmap_w, qpmap_h, qpmap_texel_bytes, qpmap_emphasis;
    int qpmap_qp_delta_min, qpmap_qp_delta_max;
    int32_t *qpmap_scratch;
    int qpmap_scratch_size;
} FFVulkanEncodeContext;

static int av_clip(int a, int lo, int hi) { return a < lo ? lo : (a > hi ? hi : a); }
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

static int fill(VkFormat f, VkImageTiling t, VkImageUsageFlags u, unsigned texel,
                int emphasis, int can_copy, int want_gpu)
{
    VkVideoFormatPropertiesKHR p = { f, t, u };
    VkVideoFormatQuantizationMapPropertiesKHR q = { { texel, texel } };
    return pelorus_qpmap_entry_fill(&p, &q, emphasis, can_copy, want_gpu);
}

#define DELTA VK_IMAGE_USAGE_VIDEO_ENCODE_QUANTIZATION_DELTA_MAP_BIT_KHR
#define TDST  VK_IMAGE_USAGE_TRANSFER_DST_BIT
#define STOR  VK_IMAGE_USAGE_STORAGE_BIT

static int32_t texel(const uint8_t *p, int bytes)
{
    if (bytes == 1)
        return (int8_t)p[0];
    if (bytes == 2) {
        int16_t v;
        memcpy(&v, p, sizeof(v));
        return v;
    }
    {
        int32_t v;
        memcpy(&v, p, sizeof(v));
        return v;
    }
}

/* 4x2 map, rows 32 bytes apart (padding 0xAA must survive). */
static void raster_case(const char *name, int bytes, int emphasis,
                        const PelorusQpRect *rects, int nb, const int32_t *want)
{
    FFVulkanEncodeContext ctx;
    int32_t scratch[8];
    uint8_t buf[64];
    char what[96];

    memset(&ctx, 0, sizeof(ctx));
    memset(buf, 0xAA, sizeof(buf));
    ctx.qpmap_w = 4;
    ctx.qpmap_h = 2;
    ctx.qpmap_texel_bytes = bytes;
    ctx.qpmap_emphasis = emphasis;
    ctx.qpmap_qp_delta_min = -51;
    ctx.qpmap_qp_delta_max = 51;
    ctx.qpmap_scratch = scratch;
    ctx.qpmap_scratch_size = 8;
    pelorus_qpmap_raster(&ctx, rects, nb, buf, 32);
    for (int y = 0; y < 2; y++) {
        for (int x = 0; x < 4; x++) {
            int32_t got = texel(buf + y * 32 + x * bytes, bytes);
            if (emphasis)
                got = bytes == 1 ? buf[y * 32 + x] : (int32_t)(uint16_t)got;
            snprintf(what, sizeof(what), "%s texel (%d,%d)", name, x, y);
            expect(what, got, want[y * 4 + x]);
        }
        snprintf(what, sizeof(what), "%s row %d padding", name, y);
        expect(what, buf[y * 32 + 4 * bytes], 0xAA);
    }
}

int main(void)
{
    int lo, hi;

    /* Texel widths: signed delta formats, unorm emphasis formats. */
    expect("R8_SINT bytes", pelorus_qpmap_texel_bytes(VK_FORMAT_R8_SINT, 0), 1);
    expect("R16_SINT bytes", pelorus_qpmap_texel_bytes(VK_FORMAT_R16_SINT, 0), 2);
    expect("R32_SINT bytes", pelorus_qpmap_texel_bytes(VK_FORMAT_R32_SINT, 0), 4);
    expect("R8_UNORM is no delta format", pelorus_qpmap_texel_bytes(VK_FORMAT_R8_UNORM, 0), 0);
    expect("R8_UNORM emphasis bytes", pelorus_qpmap_texel_bytes(VK_FORMAT_R8_UNORM, 1), 1);
    expect("R16_UNORM emphasis bytes", pelorus_qpmap_texel_bytes(VK_FORMAT_R16_UNORM, 1), 2);
    expect("R8_SINT is no emphasis format", pelorus_qpmap_texel_bytes(VK_FORMAT_R8_SINT, 1), 0);

    pelorus_qpmap_texel_range(1, &lo, &hi);
    expect("R8 range lo", lo, INT8_MIN);
    expect("R8 range hi", hi, INT8_MAX);
    pelorus_qpmap_texel_range(4, &lo, &hi);
    expect("R32 range lo", lo, INT32_MIN);
    expect("R32 range hi", hi, INT32_MAX);

    /* RADV, Mesa 26.2.4: R32_SINT x {OPTIMAL, LINEAR, DRM}, encode family
     * VIDEO_ENCODE only (no copy, no compute), queried with DELTA alone. */
    expect("RADV OPTIMAL", fill(VK_FORMAT_R32_SINT, VK_IMAGE_TILING_OPTIMAL, DELTA, 16, 0, 0, 0),
           PELORUS_QPMAP_FILL_NONE);
    expect("RADV LINEAR", fill(VK_FORMAT_R32_SINT, VK_IMAGE_TILING_LINEAR, DELTA, 16, 0, 0, 0),
           PELORUS_QPMAP_FILL_HOST);
    expect("RADV DRM modifier", fill(VK_FORMAT_R32_SINT, VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT,
                                     DELTA, 16, 0, 0, 0), PELORUS_QPMAP_FILL_NONE);
    expect("DRM modifier, copy-capable family", fill(VK_FORMAT_R32_SINT,
                                                     VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT,
                                                     DELTA | TDST, 16, 0, 1, 0),
           PELORUS_QPMAP_FILL_NONE);
    /* Even with TRANSFER_DST advertised, a video-encode-only family cannot copy. */
    expect("RADV OPTIMAL+TRANSFER_DST, no copy", fill(VK_FORMAT_R32_SINT, VK_IMAGE_TILING_OPTIMAL,
                                                      DELTA | TDST, 64, 0, 0, 0),
           PELORUS_QPMAP_FILL_NONE);

    /* NVIDIA 615.x: R8_SINT LINEAR, usages 0x200000f, encode family TRANSFER. */
    expect("NVIDIA copy", fill(VK_FORMAT_R8_SINT, VK_IMAGE_TILING_LINEAR, 0x200000f, 16, 0, 1, 0),
           PELORUS_QPMAP_FILL_COPY);
    /* A wide format is fillable by the copy when the family can copy. */
    expect("R32 copy", fill(VK_FORMAT_R32_SINT, VK_IMAGE_TILING_OPTIMAL, DELTA | TDST, 16, 0, 1, 0),
           PELORUS_QPMAP_FILL_COPY);

    /* On-GPU raster: 8-bit formats only (the shader declares r8i / r8). */
    expect("GPU R8", fill(VK_FORMAT_R8_SINT, VK_IMAGE_TILING_OPTIMAL, DELTA | STOR, 16, 0, 1, 1),
           PELORUS_QPMAP_FILL_GPU);
    expect("GPU emphasis R8_UNORM", fill(VK_FORMAT_R8_UNORM, VK_IMAGE_TILING_OPTIMAL, STOR, 16, 1, 0, 1),
           PELORUS_QPMAP_FILL_GPU);
    expect("GPU refuses R32", fill(VK_FORMAT_R32_SINT, VK_IMAGE_TILING_OPTIMAL, DELTA | STOR, 16, 0, 0, 1),
           PELORUS_QPMAP_FILL_NONE);
    expect("R32 STORAGE LINEAR goes host", fill(VK_FORMAT_R32_SINT, VK_IMAGE_TILING_LINEAR,
                                                DELTA | STOR, 16, 0, 0, 1), PELORUS_QPMAP_FILL_HOST);
    expect("GPU needs the build", fill(VK_FORMAT_R8_SINT, VK_IMAGE_TILING_OPTIMAL, DELTA | STOR, 16, 0, 0, 0),
           PELORUS_QPMAP_FILL_NONE);

    /* Negative: unknown format, zero texel block. */
    expect("unknown format", fill(VK_FORMAT_R16_UNORM, VK_IMAGE_TILING_LINEAR, DELTA | TDST, 16, 0, 1, 0),
           PELORUS_QPMAP_FILL_NONE);
    expect("zero texel block", fill(VK_FORMAT_R8_SINT, VK_IMAGE_TILING_LINEAR, DELTA | TDST, 0, 0, 1, 0),
           PELORUS_QPMAP_FILL_NONE);

    {
        /* First rectangle wins on overlap; texels outside every rect are 0. */
        const PelorusQpRect r[2] = {
            { .x0 = 0, .y0 = 0, .x1 = 2, .y1 = 2, .delta = -15 },
            { .x0 = 1, .y0 = 0, .x1 = 4, .y1 = 1, .delta = 7 },
        };
        const int32_t want[8] = { -15, -15, 7, 7, -15, -15, 0, 0 };
        /* Emphasis: (-dQP + 51) / 102 scaled to the unorm range. */
        const int32_t want8[8]  = { 165, 165, 110, 110, 165, 165, 128, 128 };
        const int32_t want16[8] = { 42405, 42405, 28270, 28270, 42405, 42405, 32768, 32768 };
        raster_case("R8_SINT", 1, 0, r, 2, want);
        raster_case("R16_SINT", 2, 0, r, 2, want);
        raster_case("R32_SINT", 4, 0, r, 2, want);
        raster_case("R8_UNORM", 1, 1, r, 2, want8);
        raster_case("R16_UNORM", 2, 1, r, 2, want16);
    }

    if (failures)
        return 1;
    puts("vulkan qpmap fill: all checks passed");
    return 0;
}
"""

# Proves the strict flags bite: -Wall's -Wunused-variable inside extracted code.
PLANTED_WARNING = "\nstatic int pelorus_planted_warning(void) { int unused_local; return 0; }\n"

MUTATIONS = (
    ("copy ignores the queue family",
     "if (can_copy && (p->imageUsageFlags & VK_IMAGE_USAGE_TRANSFER_DST_BIT))",
     "if ((p->imageUsageFlags & VK_IMAGE_USAGE_TRANSFER_DST_BIT))"),
    ("R8_SINT only (#278)",
     "case VK_FORMAT_R32_SINT: return 4;", "case VK_FORMAT_R32_SINT: return 0;"),
    ("no host-mapped path",
     "        return PELORUS_QPMAP_FILL_HOST;", "        return PELORUS_QPMAP_FILL_NONE;"),
    ("DRM-modifier tiling accepted",
     "p->imageTiling != VK_IMAGE_TILING_LINEAR))", "0))"),
    ("on-GPU raster for any width",
     "p->format == (emphasis ? VK_FORMAT_R8_UNORM : VK_FORMAT_R8_SINT)", "1"),
    ("one byte per texel",
     "if (bytes == 1) {", "if (1) {"),
    ("row pitch ignored",
     "dst + (size_t)y * pitch", "dst + (size_t)y * mapw * bytes"),
    ("last rectangle wins",
     "for (int i = nb_rects - 1; i >= 0; i--)", "for (int i = 0; i < nb_rects; i++)"),
    ("emphasis always 8-bit",
     "bytes == 1 ? UINT8_MAX : UINT16_MAX", "UINT8_MAX"),
    ("8-bit clamp for every width",
     "*lo = bytes == 1 ? INT8_MIN", "*lo = 1 ? INT8_MIN"),
)


def added_lines(patch: str, path: str) -> list[str]:
    """Post-image added lines of one file section of the hand diff."""
    out: list[str] = []
    current = None
    for line in patch.splitlines():
        match = re.match(r"^diff --git a/(\S+) b/\S+$", line)
        if match:
            current = match.group(1)
            continue
        if current == path and line.startswith("+") and not line.startswith("+++"):
            out.append(line[1:])
    return out


def extract_function(text: str, name: str) -> str:
    """Definition of the static function `name`, from its return type to its closing brace."""
    match = re.search(r"^static [^\n;]*\b" + re.escape(name) + r"\(", text, re.M)
    if not match:
        raise ValueError(f"{name}() not found in {ENC} of the patch")
    open_brace = text.index("{", match.end())
    depth = 0
    for index in range(open_brace, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[match.start():index + 1] + "\n"
    raise ValueError(f"{name}() has no closing brace")


def extract_chunk(patch: str) -> str:
    text = "\n".join(added_lines(patch, ENC))
    return "\n".join(extract_function(text, name) for name in FUNCTIONS)


def build_and_run(cc: list[str], chunk: str, extra: tuple[str, ...] = ()) -> tuple[int, str]:
    with tempfile.TemporaryDirectory(prefix="pelorus-qpmap-fill-") as tmp:
        tmp_path = pathlib.Path(tmp)
        c_file = tmp_path / "harness.c"
        exe = tmp_path / ("harness.exe" if os.name == "nt" else "harness")
        c_file.write_text(SHIMS + chunk + MAIN)
        build = subprocess.run(
            [*cc, "-std=c11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function",
             *extra, str(c_file), "-o", str(exe), "-lm"],
            capture_output=True, text=True, check=False)
        if build.returncode != 0:
            return 2, "harness failed to compile:\n" + build.stdout + build.stderr
        run = subprocess.run([str(exe)], capture_output=True, text=True, check=False)
        return (0 if run.returncode == 0 else 1), run.stdout + run.stderr


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--patch", type=pathlib.Path, default=PATCH)
    parser.add_argument("--self-test", action="store_true",
                        help="also require every planted defect to be rejected")
    parser.add_argument("--cc", nargs="+", default=[os.environ.get("CC", "cc")],
                        help="C compiler command (must be the last option)")
    args = parser.parse_args()

    try:
        chunk = extract_chunk(args.patch.read_text())
    except (OSError, ValueError) as err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 1

    status, output = build_and_run(args.cc, chunk)
    sys.stdout.write(output)
    if status != 0:
        print(f"ERROR: harness {'build' if status == 2 else 'run'} failed", file=sys.stderr)
        return 1
    if not args.self_test:
        return 0

    failures = 0
    status, _ = build_and_run(args.cc, chunk + PLANTED_WARNING)
    if status != 2:
        print("SELF-TEST: a planted compiler warning did not fail the strict build",
              file=sys.stderr)
        failures += 1
    for name, old, new in MUTATIONS:
        if chunk.count(old) != 1:
            print(f"SELF-TEST: mutation '{name}' does not apply (stale)", file=sys.stderr)
            failures += 1
            continue
        # A mutant may leave a parameter unused; that is not what is tested.
        status, output = build_and_run(args.cc, chunk.replace(old, new),
                                       ("-Wno-unused-parameter",))
        if status == 0:
            print(f"SELF-TEST: mutation '{name}' was not detected", file=sys.stderr)
            failures += 1
        elif status == 2:
            print(f"SELF-TEST: mutation '{name}' broke the build instead of a check",
                  file=sys.stderr)
            sys.stderr.write(output)
            failures += 1
    if failures:
        return 1
    print(f"vulkan qpmap fill self-test: {len(MUTATIONS)} mutations rejected, "
          "planted warning refused by -Wall -Wextra -Werror")
    return 0


if __name__ == "__main__":
    sys.exit(main())
