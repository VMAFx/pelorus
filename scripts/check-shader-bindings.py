#!/usr/bin/env python3
"""Cross-check each filter's GLSL binding declarations against its C descriptor array.

Since the FFmpeg 9 migration (ADR-0143) the shader is a standalone .comp.glsl compiled
to SPIR-V, so the `layout(set=,binding=)` declarations and the
`FFVulkanDescriptorSetBinding` array in the .c are two hand-maintained lists that must
agree. Nothing in the compiler or the build checks that: a swapped pair silently reads
the wrong image, which is corruption rather than a build failure. This is that check.

Exit 0 if every filter agrees, 1 otherwise.
"""
import re, pathlib, sys

FILES = pathlib.Path(__file__).resolve().parent.parent / "ffmpeg-patches" / "files"
FILTERS = ["deband", "analyze", "denoise", "grain_estimate", "mc",
           "dehalo", "aa", "deblock", "borderfix"]

def c_bindings(text):
    """.name entries inside FFVulkanDescriptorSetBinding arrays only."""
    out = []
    for m in re.finditer(r"FFVulkanDescriptorSetBinding\s+\w+\s*\[\s*\]\s*=\s*\{", text):
        i = m.end() - 1
        depth, j = 0, i
        while j < len(text):
            if text[j] == "{": depth += 1
            elif text[j] == "}":
                depth -= 1
                if depth == 0: break
            j += 1
        out += re.findall(r'\.name\s*=\s*"([A-Za-z0-9_]+)"', text[i:j])
    return out

def glsl_bindings(text):
    """Declared identifiers, ordered by (set, binding)."""
    decls = re.findall(
        r"layout\s*\(\s*set\s*=\s*(\d+)\s*,\s*binding\s*=\s*(\d+)[^)]*\)"
        r"\s*[^;{]*?([A-Za-z0-9_]+)\s*(?:\[\s*\]\s*;|\{|;)", text)
    return [d[2] for d in sorted(decls, key=lambda d: (int(d[0]), int(d[1])))]

bad = 0
for n in FILTERS:
    c = FILES / f"vf_pelorus_{n}_vulkan.c"
    g = FILES / "vulkan" / f"pelorus_{n}.comp.glsl"
    if not c.exists() or not g.exists():
        print(f"  MISSING  {n}"); bad += 1; continue
    cn, gn = c_bindings(c.read_text()), glsl_bindings(g.read_text())
    if cn == gn:
        print(f"  ok       {n:<16} {len(cn)} bindings: {', '.join(cn)}")
    else:
        bad += 1
        print(f"  MISMATCH {n}")
        print(f"      C   : {cn}")
        print(f"      GLSL: {gn}")

if bad:
    print(f"\n{bad} filter(s) disagree — a swapped binding silently reads the wrong resource.")
    sys.exit(1)
print(f"\nall {len(FILTERS)} filters: GLSL binding order matches the C descriptor array")
