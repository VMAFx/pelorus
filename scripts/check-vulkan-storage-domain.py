#!/usr/bin/env python3
"""Enforce the Vulkan storage-domain and component-preservation contract.

FFmpeg exposes integer Vulkan frames through UNORM storage-image views.  For
sub-16-bit formats that is the storage container's domain, not the format's
logical [0, 1] sample domain.  Arithmetic filters must therefore use a
descriptor-derived scale at the shader boundary, and transforms must quantize
in logical code space before writing shifted P010/P012 storage.  Scalar
transforms must also preserve components they do not process and process both U
and V when a selected physical plane is semi-planar.

Luma-only filters must index FFmpeg's per-plane descriptor arrays through a
specialization constant.  A literal ``[0]`` lets glslc contract an unsized
descriptor array to a fixed one-element SPIR-V array even though the C layout
and push-descriptor updates retain every plane.

This intentionally checks the whole arithmetic-filter family.  A local fix in
one filter is not sufficient because all filters share the same representation
boundary.  Borderfix (raw texel copy) and qpmap (integer map output) are named
exemptions.
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
FILES = ROOT / "ffmpeg-patches" / "files"
SHADERS = FILES / "vulkan"

READ_ONLY = ("analyze", "grain_estimate", "mc")
TRANSFORMS = ("deband", "denoise", "aa", "dehalo", "deblock")
ARITHMETIC = READ_ONLY + TRANSFORMS
COMPONENT_FILTERS = ("denoise", "aa", "dehalo", "deblock")
RAW_EXEMPTIONS = ("borderfix", "qpmap")


def strip_comments(text: str) -> str:
    """Remove C/GLSL comments so prose cannot satisfy a source contract."""

    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", "", text)


def image_load_statements(text: str) -> list[str]:
    """Return semicolon-delimited source fragments containing imageLoad()."""

    return [part for part in text.split(";") if "imageLoad(" in part]


def is_raw_copy(statement: str) -> bool:
    """Recognize a direct storage-domain pass-through copy."""

    return "imageStore(" in statement and "imageLoad(" in statement


def is_preserving_read_modify_write(statement: str, full_text: str) -> bool:
    """Recognize a raw texel loaded solely to preserve untouched components."""

    match = re.search(
        r"vec4\s+([A-Za-z_]\w*)\s*=\s*(?:[^;?]+\?\s*)?imageLoad\s*\(",
        statement,
    )
    if not match:
        return False
    name = re.escape(match.group(1))
    assignment = rf"\b{name}\s*(?:\.x|\[\s*comp\s*\])\s*=\s*pel_to_storage\s*\("
    store = rf"imageStore\s*\([^;]*\b{name}\s*\)"
    return bool(re.search(assignment, full_text) and re.search(store, full_text))


def _require(
    errors: list[str], path: pathlib.Path, text: str, pattern: str, reason: str
) -> None:
    if not re.search(pattern, text, flags=re.DOTALL):
        errors.append(f"{path.relative_to(ROOT)}: {reason}")


def _check_shared_header_and_generator(errors: list[str]) -> None:
    header = FILES / "pelorus_vulkan_sample.h"
    if not header.is_file():
        errors.append(
            "ffmpeg-patches/files/pelorus_vulkan_sample.h: missing shared "
            "descriptor-derived sample-scale helper"
        )
    else:
        source = strip_comments(header.read_text())
        _require(
            errors,
            header,
            source,
            r"\bpel_vk_sample_scale\s*\(",
            "must define pel_vk_sample_scale()",
        )
        _require(
            errors,
            header,
            source,
            r"\bpel_vk_sample_code_max\s*\(",
            "must define pel_vk_sample_code_max() for shifted writeback",
        )
        _require(
            errors,
            header,
            source,
            r"av_pix_fmt_desc_get\s*\(",
            "scale must be derived from AVPixFmtDescriptor",
        )

    generator = ROOT / "ffmpeg-patches" / "generate.sh"
    generator_text = strip_comments(generator.read_text())
    _require(
        errors,
        generator,
        generator_text,
        r'cp\s+"\$FILES_DIR/pelorus_vulkan_sample\.h"\s+' r'"\$WORKTREE/libavfilter/"',
        "must install the shared private header into the generated FFmpeg tree",
    )


def _check_shader_image_loads(
    errors: list[str], shader_path: pathlib.Path, shader_text: str, name: str
) -> None:
    for statement in image_load_statements(shader_text):
        if "pel_to_sample(" in statement:
            continue
        if name in TRANSFORMS and (
            is_raw_copy(statement)
            or is_preserving_read_modify_write(statement, shader_text)
        ):
            continue
        errors.append(
            f"{shader_path.relative_to(ROOT)}: imageLoad reaches arithmetic "
            "without pel_to_sample()"
        )


def _check_shader_sample_scale(
    errors: list[str], shader_path: pathlib.Path, shader_text: str
) -> None:
    _require(
        errors,
        shader_path,
        shader_text,
        r"\bfloat\s+sample_scale\s*;",
        "push constants must expose float sample_scale",
    )
    _require(
        errors,
        shader_path,
        shader_text,
        r"\bpel_to_sample\s*\(",
        "must convert storage values before arithmetic",
    )


def _check_one_arithmetic_filter(errors: list[str], name: str) -> None:
    c_path = FILES / f"vf_pelorus_{name}_vulkan.c"
    shader_path = SHADERS / f"pelorus_{name}.comp.glsl"
    if not c_path.is_file() or not shader_path.is_file():
        errors.append(f"{name}: missing canonical C or GLSL source")
        return

    c_text = strip_comments(c_path.read_text())
    shader_text = strip_comments(shader_path.read_text())

    _require(
        errors,
        c_path,
        c_text,
        r'#include\s+"pelorus_vulkan_sample\.h"',
        "must consume the shared sample-scale helper",
    )
    _require(
        errors,
        c_path,
        c_text,
        r"\bpel_vk_sample_scale\s*\(\s*vkctx->input_format\s*\)",
        "must derive sample_scale from the input pixel descriptor",
    )
    _require(
        errors,
        c_path,
        c_text,
        r"\bsample_scale\b",
        "must pass sample_scale to the shader",
    )
    _check_shader_sample_scale(errors, shader_path, shader_text)
    _check_shader_image_loads(errors, shader_path, shader_text, name)


def _check_arithmetic_filters(errors: list[str]) -> None:
    for name in ARITHMETIC:
        _check_one_arithmetic_filter(errors, name)


def _check_scalar_splat(
    errors: list[str], shader_path: pathlib.Path, shader_text: str
) -> None:
    if re.search(
        r"imageStore\s*\([^;]*,\s*vec4\s*\(\s*[A-Za-z_]\w*\s*\)\s*\)",
        shader_text,
        flags=re.DOTALL,
    ):
        errors.append(
            f"{shader_path.relative_to(ROOT)}: scalar-splat imageStore "
            "destroys unrelated components"
        )


def _check_one_transform(errors: list[str], name: str) -> None:
    c_path = FILES / f"vf_pelorus_{name}_vulkan.c"
    shader_path = SHADERS / f"pelorus_{name}.comp.glsl"
    if not c_path.is_file() or not shader_path.is_file():
        return
    c_text = strip_comments(c_path.read_text())
    shader_text = strip_comments(shader_path.read_text())
    _require(
        errors,
        c_path,
        c_text,
        r"\bpel_vk_sample_code_max\s*\(\s*vkctx->output_format\s*\)",
        "must derive writeback code_max from the output pixel descriptor",
    )
    _require(
        errors,
        shader_path,
        shader_text,
        r"constant_id\s*=\s*\d+\)\s*const\s+uint\s+sample_code_max",
        "a specialization constant must expose the logical code maximum",
    )
    _require(
        errors,
        c_path,
        c_text,
        r"SPEC_LIST_ADD\s*\([^;]*sample_code_max",
        "must specialize the shader with the descriptor-derived code maximum",
    )
    _require(
        errors,
        shader_path,
        shader_text,
        r"\bpel_to_storage\s*\(",
        "pixel transforms must convert results back to storage units",
    )
    _require(
        errors,
        shader_path,
        shader_text,
        r"\bfloat\s+code_max\s*=\s*float\s*\(\s*sample_code_max\s*\)"
        r"[^}]*\bround\s*\([^;]*code_max[^;]*\)",
        "writeback must quantize in logical code space before inverse scaling",
    )
    _check_scalar_splat(errors, shader_path, shader_text)


def _check_transforms(errors: list[str]) -> None:
    for name in TRANSFORMS:
        _check_one_transform(errors, name)


def _check_denoise_push_constant_size(errors: list[str]) -> None:
    denoise_c = FILES / "vf_pelorus_denoise_vulkan.c"
    if denoise_c.is_file():
        _require(
            errors,
            denoise_c,
            strip_comments(denoise_c.read_text()),
            r"static_assert\s*\(\s*sizeof\s*\([^;]*opts[^;]*\)\s*<=\s*128",
            "must retain the Vulkan baseline push-constant size guard",
        )


def _check_component_indexing(
    errors: list[str], shader_path: pathlib.Path, shader_text: str
) -> None:
    _require(
        errors,
        shader_path,
        shader_text,
        r"\[\s*comp\s*\]",
        "must address the selected component instead of hard-coding .x",
    )
    _require(
        errors,
        shader_path,
        shader_text,
        r"\[\s*comp\s*\]\s*=\s*pel_to_storage\s*\(",
        "must replace only each computed component in a raw texel",
    )


def _check_one_component_filter(errors: list[str], name: str) -> None:
    c_path = FILES / f"vf_pelorus_{name}_vulkan.c"
    shader_path = SHADERS / f"pelorus_{name}.comp.glsl"
    if not c_path.is_file() or not shader_path.is_file():
        return
    c_text = strip_comments(c_path.read_text())
    shader_text = strip_comments(shader_path.read_text())
    _require(
        errors,
        c_path,
        c_text,
        r"comp\[1\]\.plane\s*==\s*[^;]*comp\[2\]\.plane",
        "must detect semi-planar U/V from the pixel descriptor",
    )
    _require(
        errors,
        c_path,
        c_text,
        r"SPEC_LIST_ADD\s*\([^;]*semi_planar",
        "must specialize the shader for semi-planar physical planes",
    )
    _require(
        errors,
        shader_path,
        shader_text,
        r"constant_id\s*=\s*\d+\)\s*const\s+uint\s+semi_planar",
        "must declare the semi_planar specialization constant",
    )
    _require(
        errors,
        shader_path,
        shader_text,
        r"\bpel_component_count\s*\(",
        "must use a specialization-time per-plane component count",
    )
    _check_component_indexing(errors, shader_path, shader_text)


def _check_component_filters(errors: list[str]) -> None:
    for name in COMPONENT_FILTERS:
        _check_one_component_filter(errors, name)


def _check_mc_readback_scaling(errors: list[str]) -> None:
    mc_c = FILES / "vf_pelorus_mc_vulkan.c"
    if mc_c.is_file():
        mc_text = strip_comments(mc_c.read_text())
        if re.search(r"\b(?:mc_sample_scale|sscale)\b", mc_text):
            errors.append(
                "ffmpeg-patches/files/vf_pelorus_mc_vulkan.c: sample scaling "
                "must happen before shader SAD/candidate selection, not after readback"
            )


def _check_luma_descriptor_arrays(errors: list[str]) -> None:
    for name, constant_id, bindings in (
        ("analyze", 0, ("input_images",)),
        ("mc", 1, ("cur_image", "ref_image")),
    ):
        c_path = FILES / f"vf_pelorus_{name}_vulkan.c"
        shader_path = SHADERS / f"pelorus_{name}.comp.glsl"
        if not c_path.is_file() or not shader_path.is_file():
            continue
        c_text = strip_comments(c_path.read_text())
        shader_text = strip_comments(shader_path.read_text())
        _require(
            errors,
            shader_path,
            shader_text,
            rf"constant_id\s*=\s*{constant_id}\)\s*const\s+uint\s+luma_plane",
            "must preserve luma image bindings as runtime descriptor arrays",
        )
        _require(
            errors,
            c_path,
            c_text,
            rf"SPEC_LIST_ADD\s*\(\s*sl\s*,\s*{constant_id}\s*,\s*32\s*,\s*0u\s*\)",
            "must specialize the luma descriptor-array index to zero",
        )
        for binding in bindings:
            _require(
                errors,
                shader_path,
                shader_text,
                rf"\b{binding}\s*\[\s*luma_plane\s*\]",
                f"must index {binding} through luma_plane",
            )
            if re.search(rf"\b{binding}\s*\[\s*0u?\s*\]", shader_text):
                errors.append(
                    f"{shader_path.relative_to(ROOT)}: literal {binding}[0] "
                    "contracts the per-plane descriptor array"
                )


def _check_raw_exemptions(errors: list[str]) -> None:
    for name in RAW_EXEMPTIONS:
        for path in (
            FILES / f"vf_pelorus_{name}_vulkan.c",
            SHADERS / f"pelorus_{name}.comp.glsl",
        ):
            if not path.is_file():
                continue
            text = strip_comments(path.read_text())
            if re.search(r"pel_vk_sample_scale|pel_to_sample|pel_to_storage", text):
                errors.append(
                    f"{path.relative_to(ROOT)}: raw-copy/map exemption must not "
                    "acquire arithmetic sample-domain conversion"
                )


def check() -> list[str]:
    errors: list[str] = []
    _check_shared_header_and_generator(errors)
    _check_arithmetic_filters(errors)
    _check_transforms(errors)
    _check_denoise_push_constant_size(errors)
    _check_component_filters(errors)
    _check_mc_readback_scaling(errors)
    _check_luma_descriptor_arrays(errors)
    _check_raw_exemptions(errors)
    return errors


def main() -> int:
    errors = check()
    if errors:
        print("Vulkan storage-domain contract violations:")
        for error in errors:
            print(f"  - {error}")
        return 1

    print(
        f"Vulkan storage-domain contract: {len(ARITHMETIC)} arithmetic filters, "
        f"{len(COMPONENT_FILTERS)} scalar component filters, and "
        f"{len(RAW_EXEMPTIONS)} raw exemptions checked"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
