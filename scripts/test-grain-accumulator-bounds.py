#!/usr/bin/env python3
"""Prove the grain-estimator uint32 reductions are overflow-safe and unbiased.

Checks the fixed-point contract that the host filter, the shipped shader, and
the reference shader share (ADR-0147, ADR-0161):

- the constants agree across all three sources;
- the lag-1 bias makes every biased product non-negative;
- the largest per-slice sum fits uint32 at DCI 8K;
- both shaders round each per-pixel add to the nearest fixed-point unit;
- the fixed-point path reproduces a float reference on one-code-value grain.

The last check simulates the reduction in pure Python (float64 with the
shader's rounding rule) on deterministic white grain with a standard deviation
of one 8-bit code value. Truncating adds, or a lag-1 scale too coarse for the
product of such residuals, push the estimate outside the tolerance.

--self-test also plants defects into in-memory copies of the sources and
requires each to be rejected.
"""

from __future__ import annotations

import math
import pathlib
import random
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

# Precision fixture: deterministic white grain on a flat 8-bit field.
SIM_WIDTH = 192
SIM_HEIGHT = 96
SIM_LEVEL = 115
SIM_SIGMA = 1.0  # 8-bit code values
SIM_SEED = 20261003
SIM_EDGE = 0.06  # the filter's default `edge`
LAG1_TOLERANCE = 0.02
RMS_TOLERANCE = 0.02

C_PATTERNS = {
    "bands": r"#define PEL_GRAIN_BANDS\s+(\d+)",
    "slices": r"#define PEL_GRAIN_SLICES\s+(\d+)",
    "sumsq_gs": r"#define PEL_GRAIN_SUMSQ_GS\s+([0-9.]+)",
    "corr_gs": r"#define PEL_GRAIN_CORR_GS\s+([0-9.]+)",
    "corr_bias": r"#define PEL_GRAIN_CORR_BIAS\s+([0-9.]+)",
    "res_clamp": r"#define PEL_GRAIN_RES_CLAMP\s+([0-9.]+)",
}
SHADER_PATTERNS = {
    "slices": r"const uint SLICES\s*=\s*(\d+)u",
    "sumsq_gs": r"const float SUMSQ_GS\s*=\s*([0-9.]+)",
    "corr_gs": r"const float CORR_GS\s*=\s*([0-9.]+)",
    "corr_bias": r"const float CORR_BIAS\s*=\s*([0-9.]+)",
    "res_clamp": r"const float RES_CLAMP\s*=\s*([0-9.]+)",
    "sumsq_slots": r"uint sumsq\[(\d+)\]",
    "corr_slots": r"uint corr\[(\d+)\]",
}
REFERENCE_PATTERNS = {
    "slices": r"#define PEL_GRAIN_SLICES\s+(\d+)",
    "sumsq_gs": r"const float SUMSQ_GS\s*=\s*([0-9.]+)",
    "corr_gs": r"const float CORR_GS\s*=\s*([0-9.]+)",
    "corr_bias": r"const float CORR_BIAS\s*=\s*([0-9.]+)",
    "res_clamp": r"const float RES_CLAMP\s*=\s*([0-9.]+)",
}
SHARED = ("slices", "sumsq_gs", "corr_gs", "corr_bias", "res_clamp")
ADD_PATTERNS = {
    "sumsq": r"atomicAdd\(sumsq\[bidx\],\s*uint\((.+)\)\);",
    "corr": r"atomicAdd\(corr\[slice\],\s*uint\((.+)\)\);",
}


def parse(text: str, patterns: dict[str, str], label: str) -> dict[str, float]:
    values = {}
    for name, pattern in patterns.items():
        match = re.search(pattern, text)
        if match is None:
            raise ValueError(f"missing {label} {name}")
        values[name] = float(match.group(1))
    return values


def rounds_adds(text: str, label: str) -> list[str]:
    """Each accumulator add must be uint(<value> + 0.5): round to nearest."""
    errors = []
    for name, pattern in ADD_PATTERNS.items():
        match = re.search(pattern, text)
        if match is None:
            errors.append(f"{label}: {name} atomicAdd not found")
        elif not re.search(r"\+\s*0\.5$", match.group(1).strip()):
            errors.append(
                f"{label}: {name} atomicAdd truncates instead of rounding to nearest"
            )
    return errors


def simulate(consts: dict[str, float]) -> tuple[float, float, float, float]:
    """Return (rms_ref, lag1_ref, rms_fixed, lag1_fixed) for the fixture."""
    rng = random.Random(SIM_SEED)
    w, h = SIM_WIDTH, SIM_HEIGHT
    img = [
        [min(255, max(0, round(SIM_LEVEL + rng.gauss(0.0, SIM_SIGMA)))) / 255.0 for _ in range(w)]
        for _ in range(h)
    ]

    def stats(x: int, y: int) -> tuple[float, bool]:
        total, lo, hi = 0.0, 1.0, 0.0
        for dy in (-1, 0, 1):
            row = img[min(max(y + dy, 0), h - 1)]
            for dx in (-1, 0, 1):
                v = row[min(max(x + dx, 0), w - 1)]
                total += v
                lo = min(lo, v)
                hi = max(hi, v)
        clamp = consts["res_clamp"]
        resid = min(max(img[y][x] - total / 9.0, -clamp), clamp)
        return resid, (hi - lo) <= SIM_EDGE

    def quantize(value: float) -> int:
        return math.floor(value + 0.5) if consts["rounds"] else math.floor(value)

    sq_ref = prod_ref = 0.0
    sq_fix = corr_fix = 0
    n_sq = n_corr = 0
    for y in range(h):
        cells = [stats(x, y) for x in range(w)]
        for x in range(w):
            resid, flat = cells[x]
            if not flat:
                continue
            sq_ref += resid * resid
            sq_fix += quantize(resid * resid * consts["sumsq_gs"])
            n_sq += 1
            resid_r, flat_r = cells[min(x + 1, w - 1)]
            if flat_r:
                prod = resid * resid_r
                prod_ref += prod
                corr_fix += quantize(max(prod + consts["corr_bias"], 0.0) * consts["corr_gs"])
                n_corr += 1

    var_ref = sq_ref / n_sq
    var_fix = sq_fix / consts["sumsq_gs"] / n_sq
    lag_ref = (prod_ref / n_corr) / var_ref
    mean_prod = (corr_fix / consts["corr_gs"] - n_corr * consts["corr_bias"]) / n_corr
    lag_fix = min(max(mean_prod / var_fix, -1.0), 1.0)  # the host clips to [-1,1]
    return math.sqrt(var_ref), lag_ref, math.sqrt(var_fix), lag_fix


def validate(c_text: str, shader_text: str, reference_text: str) -> list[str]:
    try:
        host = parse(c_text, C_PATTERNS, "C")
        shader = parse(shader_text, SHADER_PATTERNS, "shader")
        reference = parse(reference_text, REFERENCE_PATTERNS, "reference")
    except ValueError as error:
        return [f"grain accumulator contract: {error}"]

    errors = []
    for name in SHARED:
        if not host[name] == shader[name] == reference[name]:
            errors.append(
                f"grain accumulator constant {name} drifted between host "
                f"({host[name]:g}), shipped shader ({shader[name]:g}), and reference "
                f"shader ({reference[name]:g})"
            )
    if shader["sumsq_slots"] != host["bands"] * host["slices"] or (
        shader["corr_slots"] != host["slices"]
    ):
        errors.append("grain accumulator array sizes do not match bands/slices")

    clamp_sq = host["res_clamp"] ** 2
    if host["corr_bias"] < clamp_sq * (1.0 - 1e-9):
        errors.append(
            f"CORR_BIAS {host['corr_bias']:g} is below RES_CLAMP^2 {clamp_sq:g}: "
            "the most negative lag-1 product would wrap the unsigned add"
        )

    errors.extend(rounds_adds(shader_text, "shipped shader"))
    errors.extend(rounds_adds(reference_text, "reference shader"))

    pixels_per_slice = math.ceil(WIDTH * HEIGHT / host["slices"])
    # One fixed-point unit beyond the real-number ceiling covers float32
    # rounding of the product before the round-to-nearest conversion.
    max_sumsq_add = math.ceil(clamp_sq * host["sumsq_gs"]) + 1
    max_corr_add = math.ceil((host["corr_bias"] + clamp_sq) * host["corr_gs"]) + 1
    bounds = {
        "sumsq": pixels_per_slice * max_sumsq_add,
        "corr": pixels_per_slice * max_corr_add,
        "count": pixels_per_slice,
    }
    for name, value in bounds.items():
        if value >= UINT32_LIMIT:
            errors.append(f"DCI 8K {name} bound overflows uint32: {value} >= {UINT32_LIMIT}")
    if errors:
        return errors

    errors, summary = precision_errors(dict(host, rounds=True))
    if not errors:
        print(
            f"grain accumulator DCI 8K bounds: {int(host['slices'])} slices, "
            f"sumsq={bounds['sumsq']}, corr={bounds['corr']}, count={bounds['count']}; "
            f"{summary}"
        )
    return errors


def precision_errors(consts: dict[str, float]) -> tuple[list[str], str]:
    rms_ref, lag_ref, rms_fix, lag_fix = simulate(consts)
    errors = []
    if abs(rms_fix / rms_ref - 1.0) > RMS_TOLERANCE:
        errors.append(
            f"fixed-point RMS {rms_fix * 255:.4f} vs float {rms_ref * 255:.4f} "
            f"code values: off by more than {RMS_TOLERANCE:.0%}"
        )
    if abs(lag_fix - lag_ref) > LAG1_TOLERANCE:
        errors.append(
            f"fixed-point lag-1 {lag_fix:+.4f} vs float {lag_ref:+.4f}: "
            f"off by more than {LAG1_TOLERANCE}"
        )
    summary = (
        f"1-code grain: RMS {rms_fix * 255:.4f} vs {rms_ref * 255:.4f}, "
        f"lag-1 {lag_fix:+.4f} vs {lag_ref:+.4f}"
    )
    return errors, summary


def _sub(text: str, pattern: str, replacement: str) -> str:
    new, count = re.subn(pattern, replacement, text)
    if count == 0:
        raise ValueError(f"self-test pattern not found: {pattern}")
    return new


def _truncate(text: str) -> str:
    return _sub(text, r"\s*\+\s*0\.5\)\);", "));")


def _set_const(texts: tuple[str, str, str], name: str, value: str) -> tuple[str, str, str]:
    """Set one shared constant in the host, shipped and reference sources."""
    c_text, shader_text, reference_text = texts
    host = rf"(#define PEL_GRAIN_{name}\s+)[0-9.]+"
    glsl = rf"(const float {name}\s*=\s*)[0-9.]+"
    return (
        _sub(c_text, host, rf"\g<1>{value}"),
        _sub(shader_text, glsl, rf"\g<1>{value}"),
        _sub(reference_text, glsl, rf"\g<1>{value}"),
    )


def planted_sources(texts: tuple[str, str, str]) -> dict[str, tuple[tuple[str, str, str], str]]:
    """Defective copies of the sources, each with the error text it must raise."""
    c_text, shader_text, reference_text = texts
    old = _set_const(_set_const(texts, "CORR_GS", "2000.0"), "CORR_BIAS", "1.0")
    old = (old[0], _truncate(old[1]), _truncate(old[2]))
    drifted = _sub(c_text, r"(#define PEL_GRAIN_CORR_GS\s+)[0-9.]+", r"\g<1>100000.0")
    return {
        "pre-ADR-0161 constants with truncation": (old, "truncates"),
        "shipped shader truncates": ((c_text, _truncate(shader_text), reference_text), "truncates"),
        "reference shader truncates": (
            (c_text, shader_text, _truncate(reference_text)),
            "reference shader",
        ),
        "lag-1 scale too coarse": (_set_const(texts, "CORR_GS", "2000.0"), "lag-1"),
        "bias below RES_CLAMP^2": (_set_const(texts, "CORR_BIAS", "0.001"), "below RES_CLAMP^2"),
        "lag-1 scale overflows at DCI 8K": (
            _set_const(texts, "CORR_GS", "400000.0"),
            "corr bound overflows",
        ),
        "host drifts from shaders": ((drifted, shader_text, reference_text), "drifted"),
    }


def self_test(c_text: str, shader_text: str, reference_text: str) -> list[str]:
    """Plant defects and require each to be rejected."""
    failures = []
    cases = planted_sources((c_text, shader_text, reference_text))
    for name, (texts, needle) in cases.items():
        errors = validate(*texts)
        if not any(needle in error for error in errors):
            failures.append(f"self-test: planted defect not rejected: {name} ({errors})")

    # The numeric fixture must catch truncating adds on its own, both with the
    # pre-ADR-0161 scales and with the current ones.
    host = parse(c_text, C_PATTERNS, "C")
    numeric = {
        "pre-ADR-0161 arithmetic": dict(host, corr_gs=2000.0, corr_bias=1.0, rounds=False),
        "current scales with truncation": dict(host, rounds=False),
    }
    for name, consts in numeric.items():
        errors, _ = precision_errors(consts)
        if not errors:
            failures.append(f"self-test: precision fixture accepted {name}")
    if not failures:
        print(
            f"grain accumulator self-test: {len(cases) + len(numeric)} planted "
            "defects rejected"
        )
    return failures


def main() -> int:
    c_text = C_SOURCE.read_text()
    shader_text = SHADER.read_text()
    reference_text = REFERENCE.read_text()
    errors = validate(c_text, shader_text, reference_text)
    if "--self-test" in sys.argv[1:]:
        try:
            errors.extend(self_test(c_text, shader_text, reference_text))
        except ValueError as error:
            errors.append(str(error))
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
