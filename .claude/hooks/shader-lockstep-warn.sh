#!/usr/bin/env bash
# PostToolUse(Edit|Write) — remind to keep a filter's two shader implementations
# in lockstep (root AGENTS.md hard rule 4). Warns to stderr; never blocks.
set -euo pipefail

f="$(python3 -c 'import sys,json; print(json.load(sys.stdin).get("tool_input",{}).get("file_path",""))' 2>/dev/null || true)"
[ -z "$f" ] && exit 0
base="$(basename "$f")"

case "$base" in
    pelorus_*.comp)
        name="${base#pelorus_}"; name="${name%.comp}"
        echo "  [lockstep] edited reference shader $base — keep ffmpeg-patches/files/vf_pelorus_${name}_vulkan.c inline GLSL in sync (AGENTS.md rule 4), then regenerate the patch (/ffmpeg-build-patches)." >&2
        ;;
    vf_pelorus_*_vulkan.c)
        name="${base#vf_pelorus_}"; name="${name%_vulkan.c}"
        echo "  [lockstep] edited filter $base — keep libpelorus/shaders/pelorus_${name}.comp in sync (AGENTS.md rule 4), then regenerate the patch (/ffmpeg-build-patches)." >&2
        ;;
esac
exit 0
