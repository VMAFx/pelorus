#!/usr/bin/env python3
"""Enforce the Vulkan-Video QP-map contract of the hand-maintained patch 0009.

``ffmpeg-patches/files/vulkan-pelorus-qpmap.patch`` makes h264/hevc/av1_vulkan
honor ROI side data through VK_KHR_video_encode_quantization_map.  Hardware
validation on an RTX 4090 (ADR-0166) showed the path never activated and, once
forced on, broke several Vulkan valid-usage rules.  None of these defects is
visible to a compiler, so this check pins each repair in the canonical diff:

* BUG-017: stock FFmpeg enables neither the extension nor its
  ``videoEncodeQuantizationMap`` feature, so the patch must add both to
  ``hwcontext_vulkan.c`` (extension table, feature struct, feature chain,
  feature copy) plus the extension flag and its loader mapping, and the probe
  must check the feature.
* BUG-018: delta-map values must be clamped to the driver-reported per-codec
  range (``minQpDelta``/``maxQpDelta``, ``minQIndexDelta``/``maxQIndexDelta``),
  not only to the libx264 QP span.
* Valid usage: the session parameters are QUANTIZATION_MAP_COMPATIBLE, the map
  image uses the advertised tiling, the map fill is recorded before
  ``vkCmdBeginVideoCodingKHR``, and H.265 enables ``cu_qp_delta``.

``--self-test`` mutates the patch text in memory and requires every mutation to
be rejected, so the check is proven to fail on the defects it guards.
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PATCH = ROOT / "ffmpeg-patches" / "files" / "vulkan-pelorus-qpmap.patch"

HWCTX = "libavutil/hwcontext_vulkan.c"
FUNCS = "libavutil/vulkan_functions.h"
LOADER = "libavutil/vulkan_loader.h"
ENC = "libavcodec/vulkan_encode.c"
H265 = "libavcodec/vulkan_encode_h265.c"

FEATURE_STYPE = "VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VIDEO_ENCODE_QUANTIZATION_MAP_FEATURES_KHR"
CAPS_STYPES = (
    "VK_STRUCTURE_TYPE_VIDEO_ENCODE_H264_QUANTIZATION_MAP_CAPABILITIES_KHR",
    "VK_STRUCTURE_TYPE_VIDEO_ENCODE_H265_QUANTIZATION_MAP_CAPABILITIES_KHR",
    "VK_STRUCTURE_TYPE_VIDEO_ENCODE_AV1_QUANTIZATION_MAP_CAPABILITIES_KHR",
)
CAPS_FIELDS = ("minQpDelta", "maxQpDelta", "minQIndexDelta", "maxQIndexDelta")


def split_files(patch: str) -> dict[str, list[str]]:
    """Map each patched path to its hunk lines (diff markers retained)."""

    files: dict[str, list[str]] = {}
    current = None
    for line in patch.splitlines():
        match = re.match(r"^diff --git a/(\S+) b/\S+$", line)
        if match:
            current = match.group(1)
            files[current] = []
            continue
        if current is not None and line[:1] in ("+", "-", " ", "@"):
            if line.startswith(("+++ ", "--- ")):
                continue
            files[current].append(line)
    return files


def added(lines: list[str]) -> str:
    """Text of the lines a file section adds."""

    return "\n".join(line[1:] for line in lines if line.startswith("+"))


def new_side(lines: list[str]) -> str:
    """Post-image text of a file section (context plus added lines)."""

    return "\n".join(line[1:] for line in lines if line[:1] in ("+", " "))


def hunks_of(lines: list[str], func: str) -> str:
    """Post-image text of the hunks whose @@ header names the function `func`.

    Hunks that only modify an existing upstream function carry its name in the
    @@ header rather than in their context lines.
    """

    out: list[str] = []
    take = False
    for line in lines:
        if line.startswith("@@"):
            take = re.search(r"\b" + re.escape(func) + r"\(", line) is not None
            continue
        if take and line[:1] in ("+", " "):
            out.append(line[1:])
    return "\n".join(out)


def strip_comments(text: str) -> str:
    """Remove C comments so prose cannot satisfy a source contract."""

    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", "", text)


def function_body(text: str, name: str) -> str:
    """Return the brace-balanced body of the C function `name`, or ''."""

    match = re.search(r"\b" + re.escape(name) + r"\([^;{]*\)\s*\{", text)
    if not match:
        return ""
    depth = 0
    for index in range(match.end() - 1, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[match.end():index]
    return ""


def check_libavutil(files: dict[str, list[str]]) -> list[str]:
    """BUG-017: the extension and its feature are enabled by hwcontext_vulkan."""

    errors: list[str] = []
    hw = strip_comments(added(files[HWCTX]))
    if not re.search(r"\{\s*VK_KHR_VIDEO_ENCODE_QUANTIZATION_MAP_EXTENSION_NAME\s*,"
                     r"\s*FF_VK_EXT_VIDEO_ENCODE_QUANTIZATION_MAP\s*\}", hw):
        errors.append(f"{HWCTX}: extension missing from the optional device "
                      "extension table (BUG-017)")
    if not re.search(r"VkPhysicalDeviceVideoEncodeQuantizationMapFeaturesKHR\s+\w+;", hw):
        errors.append(f"{HWCTX}: feature struct missing from VulkanDeviceFeatures (BUG-017)")
    if not re.search(r"FF_VK_STRUCT_EXT\([^;]*FF_VK_EXT_VIDEO_ENCODE_QUANTIZATION_MAP"
                     r"[^;]*" + FEATURE_STYPE, hw):
        errors.append(f"{HWCTX}: feature struct not chained by device_features_init (BUG-017)")
    if not re.search(r"COPY_VAL\(\s*\w+\.videoEncodeQuantizationMap\s*\)", hw):
        errors.append(f"{HWCTX}: videoEncodeQuantizationMap not copied into the "
                      "enabled features (BUG-017)")
    if not re.search(r"#define\s+FF_VK_EXT_VIDEO_ENCODE_QUANTIZATION_MAP\s+\(1ULL\s*<<\s*\d+\)",
                     added(files[FUNCS])):
        errors.append(f"{FUNCS}: FF_VK_EXT_VIDEO_ENCODE_QUANTIZATION_MAP flag not defined")
    if not re.search(r"VK_KHR_VIDEO_ENCODE_QUANTIZATION_MAP_EXTENSION_NAME\s*,"
                     r"\s*FF_VK_EXT_VIDEO_ENCODE_QUANTIZATION_MAP", added(files[LOADER])):
        errors.append(f"{LOADER}: extension not mapped by ff_vk_extensions_to_mask")
    return errors


def check_delta_range(enc: str, probe: str) -> list[str]:
    """BUG-018: delta-map values are clamped to the driver's per-codec range."""

    errors: list[str] = []
    query = function_body(enc, "pelorus_qpmap_query_delta_range")
    if not query:
        errors.append(f"{ENC}: no per-codec delta-range capability query (BUG-018)")
    else:
        if "GetPhysicalDeviceVideoCapabilitiesKHR" not in query:
            errors.append(f"{ENC}: delta-range query does not call "
                          "vkGetPhysicalDeviceVideoCapabilitiesKHR (BUG-018)")
        for token in CAPS_STYPES + CAPS_FIELDS:
            if token not in query:
                errors.append(f"{ENC}: delta-range query ignores {token} (BUG-018)")
    call = re.search(r"pelorus_qpmap_query_delta_range\(\s*ctx\s*,\s*&(\w+)\s*,"
                     r"\s*&(\w+)\s*\)", probe)
    if not call:
        errors.append(f"{ENC}: probe never queries the driver delta range (BUG-018)")
    else:
        lo, hi = call.groups()
        if not re.search(r"qpmap_qp_delta_min\s*=[^;]*\b" + lo + r"\b", probe) or \
                not re.search(r"qpmap_qp_delta_max\s*=[^;]*\b" + hi + r"\b", probe):
            errors.append(f"{ENC}: dQP clamp is not derived from the driver delta "
                          "range (BUG-018)")
    if re.search(r"qpmap_qp_delta_min\s*==\s*0\s*&&\s*ctx->qpmap_qp_delta_max\s*==\s*0",
                 probe):
        errors.append(f"{ENC}: libx264-range-only dQP fallback is back (BUG-018)")
    return errors


def check_valid_usage(files: dict[str, list[str]], enc: str) -> list[str]:
    """Valid-usage repairs found by running the path under the validation layer."""

    errors: list[str] = []
    params = strip_comments(hunks_of(files[ENC], "ff_vulkan_encode_create_session_params"))
    if "VK_VIDEO_SESSION_PARAMETERS_CREATE_QUANTIZATION_MAP_COMPATIBLE_BIT_KHR" not in params:
        errors.append(f"{ENC}: session parameters not created QUANTIZATION_MAP_COMPATIBLE "
                      "(VUID-vkCmdEncodeVideoKHR-pNext-10315)")
    image = function_body(enc, "pelorus_qpmap_ensure_image")
    if not re.search(r"\.tiling\s*=\s*ctx->qpmap_tiling", image):
        errors.append(f"{ENC}: map image ignores the advertised tiling "
                      "(VUID-VkImageCreateInfo-pNext-06811)")
    issue = strip_comments(hunks_of(files[ENC], "vulkan_encode_issue"))
    fill = issue.find("pelorus_qpmap_upload(")
    begin = issue.find("CmdBeginVideoCodingKHR(")
    if fill < 0 or begin < 0 or fill > begin:
        errors.append(f"{ENC}: map fill is not recorded before vkCmdBeginVideoCodingKHR "
                      "(VUID-vkCmdCopyBufferToImage-videocoding)")
    h265 = strip_comments(added(files[H265]))
    if not re.search(r"if\s*\(\s*ctx->qpmap_enabled\s*\)\s*unit_opts->cu_qp_delta_enabled_flag"
                     r"\s*=\s*1\s*;", h265):
        errors.append(f"{H265}: cu_qp_delta_enabled_flag not forced on for a QP map")
    return errors


def check(patch: str) -> list[str]:
    """Return every contract violation in the patch text."""

    errors: list[str] = []
    files = split_files(patch)
    for path in (HWCTX, FUNCS, LOADER, ENC, H265):
        if path not in files:
            errors.append(f"{path}: not patched")
            files[path] = []
    if not files[ENC]:
        return errors
    errors += check_libavutil(files)

    enc = strip_comments(new_side(files[ENC]))
    probe = function_body(enc, "pelorus_qpmap_probe")
    if not probe:
        return errors + [f"{ENC}: pelorus_qpmap_probe not found"]
    if "FF_VK_EXT_VIDEO_ENCODE_QUANTIZATION_MAP" not in probe or FEATURE_STYPE not in probe \
            or "videoEncodeQuantizationMap" not in probe:
        errors.append(f"{ENC}: probe does not require both the extension and its "
                      "feature (BUG-017)")
    errors += check_delta_range(enc, probe)
    errors += check_valid_usage(files, enc)
    return errors


MUTATIONS = (
    ("drop the optional-extension entry",
     lambda t: re.sub(r"\n\+\s*\{ VK_KHR_VIDEO_ENCODE_QUANTIZATION_MAP_EXTENSION_NAME,\s+"
                      r"FF_VK_EXT_VIDEO_ENCODE_QUANTIZATION_MAP \},", "", t)),
    ("drop the feature copy",
     lambda t: re.sub(r"\n\+\s*COPY_VAL\(video_encode_qpmap\.videoEncodeQuantizationMap\);",
                      "", t)),
    ("drop the probe's feature check",
     lambda t: t.replace(FEATURE_STYPE + ");", "0);", 1)),
    ("ignore the AV1 qindex caps",
     lambda t: t.replace("qmap_caps.av1.minQIndexDelta", "0")),
    ("clamp only to the libx264 span",
     lambda t: t.replace("FFMAX(FFMAX(cap_min, -qp_range), INT8_MIN)", "-qp_range")),
    ("drop QUANTIZATION_MAP_COMPATIBLE",
     lambda t: t.replace("VK_VIDEO_SESSION_PARAMETERS_CREATE_QUANTIZATION_MAP_COMPATIBLE_BIT_KHR",
                         "0")),
    ("hard-code OPTIMAL tiling",
     lambda t: t.replace(".tiling        = ctx->qpmap_tiling,",
                         ".tiling        = VK_IMAGE_TILING_OPTIMAL,")),
    ("fill inside the video coding scope",
     lambda t: t.replace("\n+#ifdef VK_KHR_video_encode_quantization_map\n"
                         "+    /* Pelorus: rasterize",
                         "\n+    vk->CmdBeginVideoCodingKHR(cmd_buf, &encode_start);\n"
                         "+#ifdef VK_KHR_video_encode_quantization_map\n"
                         "+    /* Pelorus: rasterize", 1)),
    ("leave H.265 cu_qp_delta off",
     lambda t: t.replace("unit_opts->cu_qp_delta_enabled_flag = 1;", ";")),
)


def self_test(patch: str) -> list[str]:
    """Every mutation must change the text and be rejected by check()."""

    failures: list[str] = []
    for name, mutate in MUTATIONS:
        mutated = mutate(patch)
        if mutated == patch:
            failures.append(f"mutation '{name}' did not apply (stale self-test)")
        elif not check(mutated):
            failures.append(f"mutation '{name}' was not detected")
    return failures


def main(argv: list[str]) -> int:
    patch = PATCH.read_text()
    errors = check(patch)
    if errors:
        print("Vulkan QP-map contract violations:")
        for error in errors:
            print(f"  - {error}")
        return 1
    if "--self-test" in argv:
        failures = self_test(patch)
        if failures:
            print("Vulkan QP-map contract self-test failures:")
            for failure in failures:
                print(f"  - {failure}")
            return 1
        print(f"Vulkan QP-map contract self-test: {len(MUTATIONS)} mutations rejected")
    print("Vulkan QP-map contract: extension/feature enablement, driver dQP range, "
          "and valid-usage repairs present")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
