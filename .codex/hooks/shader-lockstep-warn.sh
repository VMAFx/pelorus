#!/usr/bin/env bash
# PostToolUse(Edit|Write) — guard the FFmpeg 9 shader invariants (root AGENTS.md
# hard rule 4, ADR-0143). Warns to stderr; never blocks.
#
# Before FFmpeg 9 this hook policed a two-copy lockstep between the reference
# .comp and a filter's inline GLSL. That duplication is gone: the shader now
# lives once as ffmpeg-patches/files/vulkan/pelorus_<name>.comp.glsl and is
# compiled to SPIR-V at build time. What remains dangerous is different — a
# .glsl/C binding-order or push-constant mismatch is silent corruption rather
# than a build error, because nothing cross-checks them.
set -euo pipefail

f="$(python3 -c 'import sys,json; print(json.load(sys.stdin).get("tool_input",{}).get("file_path",""))' 2>/dev/null || true)"
[ -z "$f" ] && exit 0
base="$(basename "$f")"

case "$base" in
    pelorus_*.comp.glsl)
        name="${base#pelorus_}"; name="${name%.comp.glsl}"
        echo "  [shader] edited $base — this is the ONLY source for that shader (compiled to SPIR-V at build time)." >&2
        echo "           Verify: layout(set=,binding=) order still matches the FFVulkanDescriptorSetBinding array in" >&2
        echo "           ffmpeg-patches/files/vf_pelorus_${name}_vulkan.c, and the push_constant block still matches that" >&2
        echo "           file's opts struct field-for-field. Neither is compiler-checked. Then regenerate (/ffmpeg-build-patches)." >&2
        ;;
    vf_pelorus_*_vulkan.c)
        name="${base#vf_pelorus_}"; name="${name%_vulkan.c}"
        echo "  [shader] edited $base — if you changed descriptor bindings, push constants or spec constants," >&2
        echo "           mirror it in ffmpeg-patches/files/vulkan/pelorus_${name}.comp.glsl (binding ORDER matters)." >&2
        echo "           Never re-introduce inline GLSL: FFmpeg 9 removed GLSLC/GLSLF/GLSLD and ff_vk_shader_init." >&2
        echo "           Then regenerate the patch (/ffmpeg-build-patches)." >&2
        ;;
    pelorus_*.comp)
        echo "  [shader] edited standalone reference $base — this is a reference compiled by the fast gate, NOT the" >&2
        echo "           shader the filter ships. The shipped one is ffmpeg-patches/files/vulkan/${base}.glsl (ADR-0143)." >&2
        ;;
esac
exit 0
