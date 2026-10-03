#!/usr/bin/env python3
"""Check pelorus_fgs against the H.274 / SMPTE RDD 5 contract (ADR-0155).

FFmpeg's only H.274 film-grain synthesizer (libavcodec/h274.c) implements the
SMPTE RDD 5 profile: film_grain_model_id 0, log2_scale_factor 2..7, and high
cutoff frequencies 2..14 that it reads without H.274's inference. A default
outside that profile turns the grain round-trip into a silent no-op, and an
option the CBS writer rejects drops every packet while the ffmpeg CLI still
exits 0. The fast gate does not build FFmpeg, so the BSF source is checked
statically here; --self-test plants each defect this check exists to stop and
asserts that it is rejected.

Usage: test-fgs-bsf-contract.py [--self-test] [SOURCE]
"""

from __future__ import annotations

import math
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCE = ROOT / "ffmpeg-patches" / "files" / "h265_pelorus_fgs_bsf.c"

RDD5_LOG2 = range(2, 8)  # SMPTE RDD 5-2006 subclause 1.4
RDD5_CUTOFF = (2, 14)  # SMPTE RDD 5-2006 subclause 1.4
H274_INFERRED_CUTOFF = 8  # H.274 (01/2026): absent model-0 cutoff is 8
MIN_DEFAULT_SIGMA = 1.0  # code values; below this the default grain vanishes

OPTION_RE = re.compile(
    r'\{"(?P<name>\w+)",\s*(?:"(?:[^"\\]|\\.)*"\s*)+,\s*OFFSET\(\w+\),\s*'
    r"AV_OPT_TYPE_\w+,\s*\{\.i64 = (?P<default>[^}]+)\},\s*"
    r"(?P<min>-?\w+),\s*(?P<max>-?\w+),"
)
DEFINE_RE = re.compile(r"^#define (\w+) (-?\d+)\b", re.M)


def parse_options(text: str) -> dict[str, tuple[int | None, int | None, int | None]]:
    defines = {name: int(value) for name, value in DEFINE_RE.findall(text)}

    def resolve(token: str) -> int | None:
        token = token.strip()
        if re.fullmatch(r"-?\d+", token):
            return int(token)
        return defines.get(token)

    return {
        m["name"]: (resolve(m["default"]), resolve(m["min"]), resolve(m["max"]))
        for m in OPTION_RE.finditer(text)
    }


def function_body(text: str, name: str) -> str:
    match = re.search(r"^static \w+ " + name + r"\(.*?^\}", text, re.M | re.S)
    return match.group(0) if match else ""


def default_sigma(options: dict) -> float:
    """H.274 eq. (27)-(31): a unitary 16x16 IDCT of unit-variance coefficients
    band-limited to (cutoff_h + 1) x (cutoff_v + 1) has sigma
    sqrt((h + 1)(v + 1)) / 16, scaled by comp_model_value / 2^log2_scale."""
    scale = options.get("scale_y", (0, 0, 0))[0] or 0
    log2 = options.get("log2_scale", (0, 0, 0))[0] or 0
    cut_h = options.get("cutoff_h", (H274_INFERRED_CUTOFF,))[0] or 0
    cut_v = options.get("cutoff_v", (cut_h,))[0] or 0
    return scale * math.sqrt((cut_h + 1) * (cut_v + 1)) / 16.0 / (1 << log2)


def check_defaults(options: dict) -> list[str]:
    errors = []
    model = options.get("model_id", (None,))[0]
    if model != 0:
        errors.append(
            f"model_id default {model} is not 0: libavcodec/h274.h "
            "ff_h274_film_grain_params_supported() rejects every other model"
        )
    log2 = options.get("log2_scale", (None,))[0]
    if log2 not in RDD5_LOG2:
        errors.append(f"log2_scale default {log2} is outside SMPTE RDD 5 [2,7]")
    sigma = default_sigma(options)
    if sigma < MIN_DEFAULT_SIGMA:
        errors.append(
            f"default grain sigma {sigma:.3f} < {MIN_DEFAULT_SIGMA} code value"
        )
    return errors


def check_model_values(options: dict, text: str) -> list[str]:
    errors = []
    for name in ("cutoff_h", "cutoff_v"):
        default, low, high = options.get(name, (None, None, None))
        if low is None or high is None or default is None:
            errors.append(f"option {name} is missing or unresolvable")
        elif (
            low < RDD5_CUTOFF[0] or high > RDD5_CUTOFF[1] or not low <= default <= high
        ):
            errors.append(
                f"option {name} range {low}..{high} leaves SMPTE RDD 5 [2,14]"
            )
    fill = function_body(text, "pel_fgs_fill_component")
    for needle in (
        "num_model_values_minus1[c] = 2",
        "comp_model_value[c][0][1] = (int16_t)ctx->cutoff_h",
        "comp_model_value[c][0][2] = (int16_t)ctx->cutoff_v",
        "ctx->intensity_low_c",
        "ctx->intensity_high_c",
    ):
        if needle not in fill:
            errors.append(f"pel_fgs_fill_component does not emit `{needle}`")
    for name in ("intensity_low_c", "intensity_high_c"):
        if name not in options:
            errors.append(f"option {name} (per-component chroma interval) is missing")
    return errors


def check_validation(text: str) -> list[str]:
    errors = []
    required = {
        "pel_fgs_init": (
            "ctx->intensity_low > ctx->intensity_high",
            "ctx->intensity_low_c > ctx->intensity_high_c",
            "AVERROR(EINVAL)",
            "pel_fgs_check_scales(",
        ),
        "pel_fgs_update_fragment": ("pel_fgs_check_stream(bsf)",),
        "pel_fgs_model_value_max": (
            "(1 << bit_depth) - 1",
            "(1 << (bit_depth - 1)) - 1",
        ),
    }
    for function, needles in required.items():
        body = function_body(text, function)
        for needle in needles:
            if needle not in body:
                errors.append(f"{function} lacks `{needle}`")
    return errors


def check(text: str) -> list[str]:
    options = parse_options(text)
    if not options:
        return ["no AVOption entries parsed"]
    return (
        check_defaults(options)
        + check_model_values(options, text)
        + check_validation(text)
    )


MUTATIONS = {
    "model_id default 1": ("{.i64 = PEL_FGS_MODEL_FREQ}", "{.i64 = PEL_FGS_MODEL_AR}"),
    "log2_scale default 8": ("{.i64 = PEL_FGS_RDD5_LOG2_MIN}", "{.i64 = 8}"),
    "scale_y default 1": ("{.i64 = 16}", "{.i64 = 1}"),
    "no explicit cutoffs": (
        "num_model_values_minus1[c] = 2",
        "num_model_values_minus1[c] = 0",
    ),
    "chroma reuses luma interval": (
        "(luma ? ctx->intensity_low : ctx->intensity_low_c)",
        "ctx->intensity_low",
    ),
    "inverted interval accepted": ("ctx->intensity_low > ctx->intensity_high", "0"),
    "no per-AU range check": ("err = pel_fgs_check_stream(bsf);", "err = 0;"),
    "model 1 range unchecked": ("(1 << (bit_depth - 1)) - 1", "(1 << bit_depth) - 1"),
}


def self_test(text: str) -> int:
    failed = 0
    for label, (old, new) in MUTATIONS.items():
        if text.count(old) != 1:
            print(f"self-test: mutation anchor not unique: {label}", file=sys.stderr)
            failed += 1
        elif not check(text.replace(old, new)):
            print(f"self-test: planted defect NOT rejected: {label}", file=sys.stderr)
            failed += 1
    print(
        f"fgs-bsf contract self-test: {len(MUTATIONS) - failed}/{len(MUTATIONS)} rejected"
    )
    return 1 if failed else 0


def main(argv: list[str]) -> int:
    args = [arg for arg in argv if arg != "--self-test"]
    path = pathlib.Path(args[0]) if args else SOURCE
    text = path.read_text(encoding="utf-8")
    errors = check(text)
    for error in errors:
        print(f"fgs-bsf contract: {error}", file=sys.stderr)
    if errors:
        return 1
    print(
        f"fgs-bsf contract: {path.name} OK (default sigma {default_sigma(parse_options(text)):.2f})"
    )
    return self_test(text) if "--self-test" in argv else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
