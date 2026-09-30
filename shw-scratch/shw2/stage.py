#!/usr/bin/env python3
"""shw-2: rebuild the fix commits one at a time from the tested final state.

usage: python stage.py <1|2|3|4>
Stage N applies only that commit's source changes (plus its changelog /
rebase-notes / AGENTS.md text) on top of the current HEAD working tree.
Patches are regenerated afterwards in the CI container (regen.sh).
"""
import pathlib
import shutil
import sys

REPO = pathlib.Path(r"C:/tmp/pel/shw")
FINAL = pathlib.Path(r"C:/tmp/pel/shw-scratch/shw2/final-state")
FRAG = REPO / "changelog.d/fixed/0150-intel-windows-validation.md"
REBASE = REPO / "docs/rebase-notes.md"
AGENTS = REPO / "ffmpeg-patches/AGENTS.md"
MATRIX = REPO / "ffmpeg-patches/test/vulkan-format-matrix.sh"
ADR = "[ADR-0150](docs/adr/0150-intel-arc-b580-uhd770-validation.md)"
NL = chr(10)


def rd(p):
    return p.read_text(encoding="utf-8")


def wr(p, s):
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(s)


def take(rel):
    shutil.copyfile(FINAL / rel, REPO / rel)


def replace_once(p, old, new):
    s = rd(p)
    assert s.count(old) == 1, f"{p}: anchor not unique/absent: {old[:70]!r}"
    wr(p, s.replace(old, new))


def final_block(rel, start, end):
    """Text of the final-state file from the line containing `start` up to (not
    including) the first following line containing `end`."""
    s = rd(FINAL / rel)
    i = s.index(start)
    i = s.rfind("\n", 0, i) + 1
    j = s.index(end, i)
    j = s.rfind("\n", 0, j) + 1
    return s[i:j]


def add_fragment(bullet):
    if not FRAG.exists():
        wr(FRAG, bullet)
    else:
        wr(FRAG, rd(FRAG) + bullet)


REBASE_HEAD = """## Unreleased — ADR-0150 Intel Windows validation fixes

Found on Arc B580 / UHD 770 under Windows (research digest 0150). Each item
names the patches it regenerates.

"""


def add_rebase(item):
    s = rd(REBASE)
    marker = "## Unreleased — FFmpeg base bump n9.0.1 → n9.0.2"
    if REBASE_HEAD not in s:
        assert s.count(marker) == 1
        s = s.replace(marker, REBASE_HEAD + marker)
    start = s.index(REBASE_HEAD) + len(REBASE_HEAD)
    end = s.index(marker, start)
    body = s[start:end].rstrip(NL)
    body = (body + NL if body else "") + item + NL
    wr(REBASE, s[:start] + body + s[end:])


def add_agents(bullet):
    anchor = "\n## Rebase-sensitive invariants\n"
    replace_once(AGENTS, anchor, bullet + anchor)


def stage1():
    for rel in ("ffmpeg-patches/files/vf_pelorus_analyze_vulkan.c",
                "ffmpeg-patches/files/vf_pelorus_grain_estimate_vulkan.c",
                "ffmpeg-patches/files/vf_pelorus_mc_vulkan.c",
                "ffmpeg-patches/test/vulkan-format-matrix-validation-self-test.sh"):
        take(rel)
    m = "ffmpeg-patches/test/vulkan-format-matrix.sh"
    # pel_run_linear + its common args (inserted after pel_run)
    linear_fn = final_block(m, "# Same device with linear_images=1.", "pel_emit_raw()")
    replace_once(MATRIX, "pel_emit_raw()\n", linear_fn + "pel_emit_raw()\n")
    replace_once(MATRIX, "    pelorus_mc_vulkan; do\n    grep",
                 "    pelorus_mc_vulkan pelorus_grain_estimate_vulkan; do\n    grep")
    row = final_block(m, "# Pass-through analyzers forward their input frames",
                      'if [[ -f "$PEL_OUTPUT_ROOT/validation-known-upstream.txt" ]]; then')
    replace_once(MATRIX, 'if [[ -f "$PEL_OUTPUT_ROOT/validation-known-upstream.txt" ]]; then',
                 row + 'if [[ -f "$PEL_OUTPUT_ROOT/validation-known-upstream.txt" ]]; then')
    add_fragment(
        "- Fixed `pelorus_analyze_vulkan`, `pelorus_grain_estimate_vulkan`, and\n"
        "  `pelorus_mc_vulkan` labelling their forwarded input frames with a fresh output\n"
        "  frames context whenever FFmpeg could not reuse the input one (linear tiling,\n"
        "  missing usage bits). A following `hwdownload` rejected every frame; the output\n"
        "  link now carries the input link's context. The format matrix gained a\n"
        f"  linear-input pass-through row ({ADR}).\n")
    add_rebase(
        "- **Pass-through frames context** (0002 analyze, 0006 grain estimate, 0007 mc):\n"
        "  these filters forward their input `AVFrame`, so their output pad's\n"
        "  `config_props` calls `ff_vk_filter_config_output()` and then replaces the\n"
        "  output link's `hw_frames_ctx` with the input link's. Keep that override\n"
        "  when an upstream rebase changes `ff_vk_filter_init_context()`'s reuse rules;\n"
        "  stock `blackdetect_vulkan`/`scdet_vulkan` still mislabel frames.\n")
    add_agents(
        "- A pass-through filter (one that forwards its input `AVFrame`) must set its\n"
        "  output link's `hw_frames_ctx` to the input link's after\n"
        "  `ff_vk_filter_config_output()`. That helper builds a fresh context whenever\n"
        "  it cannot reuse the input one, and `hwdownload` or an encoder then rejects\n"
        "  the mislabeled frames (ADR-0150).\n")


def stage2():
    for rel in ("ffmpeg-patches/files/vulkan/pelorus_dehalo.comp.glsl",
                "ffmpeg-patches/files/vulkan/pelorus_aa.comp.glsl"):
        take(rel)
    dn = REPO / "ffmpeg-patches/files/vulkan/pelorus_denoise.comp.glsl"
    fin = rd(FINAL / "ffmpeg-patches/files/vulkan/pelorus_denoise.comp.glsl")
    # pel_to_sample: identical text in the final file
    i = fin.index("/* `precise` (SPIR-V NoContraction) keeps this product individually rounded.")
    j = fin.index("float pel_to_storage(float value) {", i)
    replace_once(dn, "float pel_to_sample(float value) {\n    return value * sample_scale;\n}\n",
                 fin[i:j])
    i = fin.index("                /* `precise` pins the patch-SSD accumulation order")
    j = fin.index("                for (int ky = -1; ky <= 1; ky++) {", i)
    replace_once(dn, "                float ssd = 0.0;\n", fin[i:j])
    m = "ffmpeg-patches/test/vulkan-format-matrix.sh"
    row = final_block(m, "# Direct/tiled equivalence must also hold where sample_scale",
                      "# Pass-through analyzers forward their input frames")
    replace_once(MATRIX, "# Pass-through analyzers forward their input frames",
                 row + "# Pass-through analyzers forward their input frames")
    add_fragment(
        "- Fixed `pelorus_denoise_vulkan=tile=1`, `pelorus_dehalo_vulkan=tile=1`, and\n"
        "  `pelorus_aa_vulkan=fast=1` drifting from their direct paths by one code value\n"
        "  at 10/12-bit: the storage-to-sample product (plus denoise's patch SSD and\n"
        "  aa's squared Sobel magnitude) is now `precise`, so a driver can no longer\n"
        "  fuse it into an FMA on one path only. Default-path output can change by one\n"
        "  code value in rare 10/12-bit samples; 8-bit output is unchanged. A 10-bit\n"
        "  direct/tiled denoise row joined the format matrix. An aa `fast=1` residual on\n"
        f"  the UHD 770 (linear single-plane input) remains open ({ADR}).\n")
    add_rebase(
        "- **Contraction contract** (0003 denoise, 0014 dehalo, 0015 aa): values a\n"
        "  shared-memory variant caches, and the denoise patch-SSD accumulation, are\n"
        "  `precise` (SPIR-V NoContraction) in both variants. Without it a driver may\n"
        "  fuse the non-power-of-two `sample_scale` product into an FMA on one path\n"
        "  only, breaking the ADR-0134/0139/0140 bit-identity at 10/12-bit. Preserve the\n"
        "  qualifiers when shader code moves.\n")
    add_agents(
        "- A value that a shared-memory variant (`tile`, `fast`) caches, and any\n"
        "  accumulation fed by it, must be computed with `precise` so both variants\n"
        "  round identically; a driver may otherwise contract the non-power-of-two\n"
        "  `sample_scale` product into an FMA on one path only (ADR-0150).\n")


def stage3():
    for rel in ("ffmpeg-patches/files/vf_pelorus_denoise_vulkan.c",
                "ffmpeg-patches/files/vulkan/pelorus_denoise.comp.glsl",
                "scripts/check-shader-bindings.py",
                "scripts/check-vulkan-storage-domain.py"):
        take(rel)
    add_fragment(
        "- Fixed `pelorus_denoise_vulkan` exceeding\n"
        "  `maxPerStageDescriptorStorageImages` (16 on the Intel UHD 770) with 21\n"
        "  storage-image descriptors for 3-plane formats\n"
        "  (`VUID-VkPipelineLayoutCreateInfo-descriptorType-03020`). Its six read-only\n"
        "  frames are now sampled images read with `texelFetch()`; the fast gate checks\n"
        f"  a 16-descriptor storage-image budget for every filter ({ADR}).\n")
    add_rebase(
        "- **Descriptor budget** (0003 denoise): the current, four previous, and next\n"
        "  frames are `VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE` bindings (`texture2D` +\n"
        "  `texelFetch`, `GL_EXT_samplerless_texture_functions`); only the output is a\n"
        "  storage image. `scripts/check-shader-bindings.py` fails any filter whose\n"
        "  storage-image bindings × 4 planes exceed 16, and\n"
        "  `scripts/check-vulkan-storage-domain.py` treats `texelFetch()` as a load that\n"
        "  must cross `pel_to_sample()`.\n")
    add_agents(
        "- Read-only frame inputs use `SAMPLED_IMAGE` descriptors (`texture2D` plus\n"
        "  `texelFetch()`); keep storage-image bindings × 4 planes at or below 16, the\n"
        "  Intel UHD 770's `maxPerStageDescriptorStorageImages`.\n"
        "  `scripts/check-shader-bindings.py` enforces the budget (ADR-0150).\n")


def stage4():
    take("ffmpeg-patches/test/vulkan-format-matrix.sh")
    add_fragment(
        "- The Vulkan format matrix now runs on Intel's Windows drivers: it treats\n"
        "  `VUID-VkFormatProperties2-pNext-pNext` and\n"
        "  `VUID-VkHostImageLayoutTransitionInfo-oldLayout-09230` as known-upstream.\n"
        "  Both are raised inside entry points only stock FFmpeg calls, and a bare\n"
        "  `hwupload,hwdownload` emits them. On those drivers, run it with\n"
        "  `VULKAN_DEVICE=\"0,disable_multiplane=1\"` (Arc B580) or\n"
        "  `\"1,linear_images=1,disable_multiplane=1\"` (UHD 770), because the default\n"
        f"  multi-plane host copy corrupts frames in the driver ({ADR}).\n")
    add_rebase(
        "- **On-device gate on Windows Intel**: pass `VULKAN_DEVICE=\"0,disable_multiplane=1\"`\n"
        "  (Arc B580) or `\"1,linear_images=1,disable_multiplane=1\"` (UHD 770). With\n"
        "  default device options the drivers' multi-plane `VK_EXT_host_image_copy`\n"
        "  corrupts every YUV upload/download before any filter runs. The matrix\n"
        "  allowlists the two stock VUIDs the Windows drivers provoke; attribute any\n"
        "  new VUID by entry point before adding it.\n")


if __name__ == "__main__":
    {"1": stage1, "2": stage2, "3": stage3, "4": stage4}[sys.argv[1]]()
    print("stage", sys.argv[1], "applied")
