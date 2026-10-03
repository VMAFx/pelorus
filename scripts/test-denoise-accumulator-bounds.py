#!/usr/bin/env python3
"""Prove the denoise meta=1 residual accumulators are exact and fine at DCI 8K.

The shader (ffmpeg-patches/files/vulkan/pelorus_denoise.comp.glsl) reduces each
workgroup's residuals into shared uint32 partials, then adds one partial per
statistic to a 64-bit (sum_lo/sum_hi) slice sum. This test checks the shader
constants against the C mirror, the overflow bounds of every stage at DCI 8K,
and the fixed-point resolution whose absence was BUG-016: a one-code 10-bit
residual squared must register, and so must a one-code 16-bit |residual|.
"""

from __future__ import annotations

import math
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
C_SOURCE = ROOT / "ffmpeg-patches" / "files" / "vf_pelorus_denoise_vulkan.c"
SHADER = ROOT / "ffmpeg-patches" / "files" / "vulkan" / "pelorus_denoise.comp.glsl"
WIDTH = 8192
HEIGHT = 4320
UINT32_LIMIT = 1 << 32
UINT64_LIMIT = 1 << 64
# The host converts each 64-bit slice sum to double; keep the total exact.
DOUBLE_EXACT_LIMIT = 1 << 53


def match(text: str, pattern: str, label: str) -> str:
    found = re.search(pattern, text)
    if found is None:
        raise ValueError(f"missing {label}")
    return found.group(1)


def parse_sources(c_text: str, shader_text: str) -> dict:
    """Extract every constant both sides must agree on."""
    wg = match(
        c_text,
        r"ff_vk_shader_load\([^;]*\(uint32_t\[\]\)\{\s*(\d+\s*,\s*\d+\s*,\s*\d+)\s*\}",
        "C workgroup size",
    )
    match(
        shader_text,
        r"(uint\(clamp\([^;]*RES_MAX\)\s*\*\s*RES_GS)",
        "shader clamp of each fixed-point add to RES_MAX",
    )
    match(
        shader_text,
        r"(old\s*>\s*0xFFFFFFFFu\s*-\s*v\)\s*atomicAdd\(sum_hi)",
        "shader carry into sum_hi",
    )
    return {
        "slices": int(match(c_text, r"#define PEL_SLICES\s+(\d+)", "C PEL_SLICES")),
        "stats": int(
            match(c_text, r"#define PEL_DENOISE_STATS\s+(\d+)", "C PEL_DENOISE_STATS")
        ),
        "res_gs": float(
            match(c_text, r"#define PEL_DENOISE_RES_GS\s+([0-9.]+)", "C RES_GS")
        ),
        "wg": tuple(int(v) for v in wg.split(",")),
        "sh_slices": int(
            match(shader_text, r"const uint SLICES\s*=\s*(\d+)u", "shader SLICES")
        ),
        "sh_stats": int(
            match(shader_text, r"const uint STATS\s*=\s*(\d+)u", "shader STATS")
        ),
        "sh_gs": float(
            match(shader_text, r"const float RES_GS\s*=\s*([0-9.]+)", "shader RES_GS")
        ),
        "res_max": float(
            match(shader_text, r"const float RES_MAX\s*=\s*([0-9.]+)", "shader RES_MAX")
        ),
        "lo": int(match(shader_text, r"uint sum_lo\[(\d+)\]", "shader sum_lo")),
        "hi": int(match(shader_text, r"uint sum_hi\[(\d+)\]", "shader sum_hi")),
        "cnt_y": int(match(shader_text, r"uint cnt_y\[(\d+)\]", "shader cnt_y")),
        "cnt_c": int(match(shader_text, r"uint cnt_c\[(\d+)\]", "shader cnt_c")),
    }


def check_layout(k: dict) -> list:
    """C/shader agreement and buffer sizing."""
    failures = []
    if (k["sh_slices"], k["sh_stats"], k["sh_gs"]) != (k["slices"], k["stats"], k["res_gs"]):
        failures.append("RES_GS/STATS/SLICES drifted between the C filter and the shader")
    if k["slices"] & (k["slices"] - 1):
        failures.append(f"SLICES={k['slices']} must be a power of two (slice = wg & (SLICES-1))")
    if k["lo"] != k["stats"] * k["slices"] or k["hi"] != k["stats"] * k["slices"]:
        failures.append("sum_lo/sum_hi sizes do not match STATS * SLICES")
    if k["cnt_y"] != k["slices"] or k["cnt_c"] != k["slices"]:
        failures.append("cnt_y/cnt_c sizes do not match SLICES")
    return failures


def check_bounds(k: dict) -> tuple:
    """Overflow bounds of every stage at DCI 8K; returns (failures, wg, slice)."""
    wg_x, wg_y, wg_z = k["wg"]
    invocations = wg_x * wg_y * wg_z
    workgroups = math.ceil(WIDTH / wg_x) * math.ceil(HEIGHT / wg_y)
    wg_per_slice = math.ceil(workgroups / k["slices"])
    # |r| and r^2 are clamped to RES_MAX (and RES_MAX^2); the +0.5 rounding plus
    # one unit covers a final upward float32 rounding before the uint conversion.
    res_max = k["res_max"]
    max_add = math.ceil(max(res_max, res_max * res_max) * k["res_gs"] + 0.5) + 1
    # Every invocation adds at most once per statistic per dispatch.
    wg_partial = invocations * max_add
    slice_sum = wg_per_slice * wg_partial
    bounds = {
        "workgroup partial (shared uint32)": (wg_partial, UINT32_LIMIT),
        "slice sum (sum_hi:sum_lo)": (slice_sum, UINT64_LIMIT),
        "host total (double-exact)": (slice_sum * k["slices"], DOUBLE_EXACT_LIMIT),
        "slice pixel count (uint32)": (wg_per_slice * invocations, UINT32_LIMIT),
    }
    failures = [
        f"DCI 8K {name} bound overflows: {value} >= {limit}"
        for name, (value, limit) in bounds.items()
        if value >= limit
    ]
    return failures, wg_partial, slice_sum


def check_resolution(res_gs: float) -> tuple:
    """The fixed-point scale must keep small residuals non-zero (BUG-016)."""
    resolution = {
        "one-code 10-bit residual squared": res_gs / 1023.0**2,
        "one-code 16-bit |residual|": res_gs / 65535.0,
    }
    failures = [
        f"{name} is {units:.4g} fixed-point units; it would round away (BUG-016)"
        for name, units in resolution.items()
        if units < 1.0
    ]
    return failures, resolution["one-code 10-bit residual squared"]


def main() -> int:
    try:
        k = parse_sources(C_SOURCE.read_text(), SHADER.read_text())
    except ValueError as error:
        print(f"denoise accumulator contract: {error}", file=sys.stderr)
        return 1

    failures = check_layout(k)
    bound_failures, wg_partial, slice_sum = check_bounds(k)
    res_failures, code_sq_units = check_resolution(k["res_gs"])
    failures += bound_failures + res_failures
    if failures:
        for failure in failures:
            print(failure, file=sys.stderr)
        return 1

    print(
        f"denoise accumulator DCI 8K bounds: wg partial={wg_partial}, "
        f"slice={slice_sum} (<2^{math.ceil(math.log2(slice_sum))}), "
        f"10-bit code^2={code_sq_units:.2f} units"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
