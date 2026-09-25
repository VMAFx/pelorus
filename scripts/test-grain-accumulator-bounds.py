#!/usr/bin/env python3
"""Prove the grain-estimator uint32 reductions cannot overflow at DCI 8K."""

from __future__ import annotations

import math
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
C_SOURCE = ROOT / "ffmpeg-patches" / "files" / "vf_pelorus_grain_estimate_vulkan.c"
SHADER = (
    ROOT / "ffmpeg-patches" / "files" / "vulkan" / "pelorus_grain_estimate.comp.glsl"
)
REFERENCE = ROOT / "libpelorus" / "shaders" / "pelorus_grain_estimate.comp"
WIDTH = 8192
HEIGHT = 4320
UINT32_LIMIT = 1 << 32


def match_number(text: str, pattern: str, label: str) -> float:
    match = re.search(pattern, text)
    if match is None:
        raise ValueError(f"missing {label}")
    return float(match.group(1))


def main() -> int:
    c_text = C_SOURCE.read_text()
    shader_text = SHADER.read_text()
    reference_text = REFERENCE.read_text()

    try:
        bands = int(match_number(c_text, r"#define PEL_GRAIN_BANDS\s+(\d+)", "C bands"))
        slices = int(
            match_number(c_text, r"#define PEL_GRAIN_SLICES\s+(\d+)", "C slices")
        )
        sumsq_gs = match_number(
            c_text, r"#define PEL_GRAIN_SUMSQ_GS\s+([0-9.]+)", "C SUMSQ_GS"
        )
        corr_gs = match_number(
            c_text, r"#define PEL_GRAIN_CORR_GS\s+([0-9.]+)", "C CORR_GS"
        )
        corr_bias = match_number(
            c_text, r"#define PEL_GRAIN_CORR_BIAS\s+([0-9.]+)", "C CORR_BIAS"
        )
        residual_clamp = match_number(
            c_text, r"#define PEL_GRAIN_RES_CLAMP\s+([0-9.]+)", "C RES_CLAMP"
        )
        shader_slices = int(
            match_number(
                shader_text, r"const uint SLICES\s*=\s*(\d+)u", "shader slices"
            )
        )
        reference_slices = int(
            match_number(
                reference_text,
                r"#define PEL_GRAIN_SLICES\s+(\d+)",
                "reference slices",
            )
        )
        shader_sumsq_gs = match_number(
            shader_text, r"const float SUMSQ_GS\s*=\s*([0-9.]+)", "shader SUMSQ_GS"
        )
        shader_corr_gs = match_number(
            shader_text, r"const float CORR_GS\s*=\s*([0-9.]+)", "shader CORR_GS"
        )
        shader_corr_bias = match_number(
            shader_text, r"const float CORR_BIAS\s*=\s*([0-9.]+)", "shader CORR_BIAS"
        )
        shader_residual_clamp = match_number(
            shader_text, r"const float RES_CLAMP\s*=\s*([0-9.]+)", "shader RES_CLAMP"
        )
        sumsq_slots = int(
            match_number(shader_text, r"uint sumsq\[(\d+)\]", "shader sumsq slots")
        )
        corr_slots = int(
            match_number(shader_text, r"uint corr\[(\d+)\]", "shader corr slots")
        )
    except ValueError as error:
        print(f"grain accumulator contract: {error}", file=sys.stderr)
        return 1

    expected = (slices, sumsq_gs, corr_gs, corr_bias, residual_clamp)
    actual = (
        shader_slices,
        shader_sumsq_gs,
        shader_corr_gs,
        shader_corr_bias,
        shader_residual_clamp,
    )
    if actual != expected or reference_slices != slices:
        print(
            "grain accumulator constants drifted between host, shipped shader, "
            "and reference shader",
            file=sys.stderr,
        )
        return 1
    if sumsq_slots != bands * slices or corr_slots != slices:
        print(
            "grain accumulator array sizes do not match bands/slices", file=sys.stderr
        )
        return 1

    pixels_per_slice = math.ceil(WIDTH * HEIGHT / slices)
    # Add one fixed-point unit beyond the real-number ceiling so the proof also
    # covers a final upward float32 rounding before GLSL's uint conversion.
    max_sumsq_add = math.ceil(residual_clamp**2 * sumsq_gs) + 1
    max_corr_add = math.ceil((corr_bias + residual_clamp**2) * corr_gs) + 1
    bounds = {
        "sumsq": pixels_per_slice * max_sumsq_add,
        "corr": pixels_per_slice * max_corr_add,
        "count": pixels_per_slice,
    }
    failures = {name: value for name, value in bounds.items() if value >= UINT32_LIMIT}
    if failures:
        for name, value in failures.items():
            print(
                f"DCI 8K {name} bound overflows uint32: {value} >= {UINT32_LIMIT}",
                file=sys.stderr,
            )
        return 1

    print(
        f"grain accumulator DCI 8K bounds: {slices} slices, "
        f"sumsq={bounds['sumsq']}, corr={bounds['corr']}, count={bounds['count']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
