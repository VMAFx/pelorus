#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Check structural enforcement of vf_pelorus_mc subgroup SAD reduction.

docs/development/bench-results.md section "v0.19" (PR #27, commit 6bedd01) documents the ~5%
throughput speedup (0.765 -> 0.727 s on RTX 4090) achieved by replacing the
shared-memory barrier tree in block_sad with a two-level subgroup reduction
(subgroupAdd + lane-0 partial combine).

Because CI has no GPU runner to gate wall-clock performance, this script
guards the mechanism structurally across both shader sources:
  - ffmpeg-patches/files/vulkan/pelorus_mc.comp.glsl
  - libpelorus/shaders/pelorus_mc.comp

For both shaders, it extracts float block_sad(...) and enforces:
  a) subgroupAdd( is called exactly once;
  b) at most 2 barrier() calls;
  c) no loop step halves a stride (>>= 1, /= 2, >> 1u in for/while header);
  d) GL_KHR_shader_subgroup_arithmetic is enabled.

When glslangValidator and spirv-dis are on PATH, it compiles
libpelorus/shaders/pelorus_mc.comp (-V --target-env vulkan1.2) and requires
OpGroupNonUniformFAdd in the disassembly. When either tool is missing, prints
"SKIP: glslang/spirv-dis not found" and exits 77.

Exit status:
  0: all rules pass;
  1: rule refused (prints file + rule + line);
  2: usage / IO / parse error;
 77: glslangValidator or spirv-dis missing (meson skip).
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess  # nosec B404 - compile/disassemble GLSL to check OpGroupNonUniformFAdd
import sys
import tempfile

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from semgrep_harness import resolve_executable

ROOT = _SCRIPTS_DIR.parent
DEFAULT_SHADERS = [
    ROOT / "ffmpeg-patches" / "files" / "vulkan" / "pelorus_mc.comp.glsl",
    ROOT / "libpelorus" / "shaders" / "pelorus_mc.comp",
]
REF_SHADER = ROOT / "libpelorus" / "shaders" / "pelorus_mc.comp"

# A halving step in a for header: x >>= 1, x /= 2, x = x >> 1, x = x / 2.
HALVING_PATTERN = re.compile(
    r">>=|/=\s*2u?\b|=\s*[A-Za-z_]\w*\s*(?:>>\s*1u?|/\s*2u?)\b"
)
SUBGROUP_ADD_PATTERN = re.compile(r"\bsubgroupAdd\s*\(")
BARRIER_PATTERN = re.compile(r"\bbarrier\s*\(\s*\)")
LOOP_START_PATTERN = re.compile(r"\b(for|while)\s*\(")
EXT_PATTERN = re.compile(
    r"#\s*extension\s+GL_KHR_shader_subgroup_arithmetic\s*:\s*(?:require|enable)\b"
)
MAX_SCAN_CHARS = 100_000


def strip_comments(text: str) -> str:
    """Replace GLSL comments with whitespace, preserving line count and character offsets."""
    chars = list(text)
    length = len(chars)
    i = 0
    in_line_comment = False
    in_block_comment = False
    max_steps = length + 1000
    steps = 0
    while i < length and steps < max_steps:
        steps += 1
        if in_line_comment:
            if chars[i] == "\n":
                in_line_comment = False
            else:
                chars[i] = " "
            i += 1
        elif in_block_comment:
            if i + 1 < length and chars[i] == "*" and chars[i + 1] == "/":
                chars[i] = " "
                chars[i + 1] = " "
                i += 2
                in_block_comment = False
            else:
                if chars[i] != "\n":
                    chars[i] = " "
                i += 1
        else:
            if i + 1 < length and chars[i] == "/" and chars[i + 1] == "/":
                chars[i] = " "
                chars[i + 1] = " "
                i += 2
                in_line_comment = True
            elif i + 1 < length and chars[i] == "/" and chars[i + 1] == "*":
                chars[i] = " "
                chars[i + 1] = " "
                i += 2
                in_block_comment = True
            else:
                i += 1
    return "".join(chars)


def extract_block_sad(stripped_text: str) -> tuple[str, int, int]:
    """Extract (body, func_line, body_start_offset) of float block_sad(...) by brace matching."""
    match = re.search(r"\bfloat\s+block_sad\s*\(", stripped_text)
    if not match:
        raise ValueError("float block_sad( not found in shader source")
    func_line = stripped_text[: match.start()].count("\n") + 1
    brace_start = stripped_text.find("{", match.end())
    if brace_start == -1:
        raise ValueError("opening brace for block_sad not found")

    depth = 1
    i = brace_start + 1
    brace_end = -1
    max_steps = min(len(stripped_text), brace_start + MAX_SCAN_CHARS)
    steps = 0
    while i < len(stripped_text) and steps < max_steps:
        steps += 1
        char = stripped_text[i]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                brace_end = i
                break
        i += 1

    if depth != 0 or brace_end == -1:
        raise ValueError("unmatched braces in block_sad function body")

    body_offset = brace_start + 1
    body = stripped_text[body_offset:brace_end]
    return body, func_line, body_offset


def check_subgroup_add(
    body: str, body_offset: int, full_text: str, func_line: int
) -> tuple[str, int, str] | None:
    """Rule (a): verify that subgroupAdd( is called exactly once."""
    matches = list(SUBGROUP_ADD_PATTERN.finditer(body))
    if len(matches) == 1:
        return None
    if matches:
        line = full_text[: body_offset + matches[0].start()].count("\n") + 1
    else:
        line = func_line
    return (
        "a",
        line,
        f"rule (a) refused: block_sad calls subgroupAdd {len(matches)} times (must be exactly 1)",
    )


def check_barrier_count(
    body: str, body_offset: int, full_text: str
) -> tuple[str, int, str] | None:
    """Rule (b): verify that block_sad contains at most 2 barrier() calls."""
    matches = list(BARRIER_PATTERN.finditer(body))
    if len(matches) <= 2:
        return None
    line = full_text[: body_offset + matches[2].start()].count("\n") + 1
    return (
        "b",
        line,
        f"rule (b) refused: block_sad contains {len(matches)} barrier() calls (at most 2 allowed)",
    )


def check_no_stride_halving_loop(
    body: str, body_offset: int, full_text: str
) -> tuple[str, int, str] | None:
    """Rule (c): verify that no for loop step clause halves a stride.

    Only the third clause of a for header is read: a halving in the init or
    the condition (i = n / 2, i < n >> 1) is no step. A while header holds
    no step clause; a while-based tree still trips rule (b).
    """
    for match in LOOP_START_PATTERN.finditer(body):
        start = match.end()
        depth = 1
        j = start
        steps = 0
        while j < len(body) and steps < MAX_SCAN_CHARS:
            steps += 1
            if body[j] == "(":
                depth += 1
            elif body[j] == ")":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        header = body[start:j]
        clauses = header.split(";")
        if len(clauses) != 3:
            continue
        if HALVING_PATTERN.search(clauses[2]):
            line = full_text[: body_offset + match.start()].count("\n") + 1
            return (
                "c",
                line,
                f"rule (c) refused: loop step halves a stride: {header.strip()}",
            )
    return None


def check_extension_enabled(full_text: str) -> tuple[str, int, str] | None:
    """Rule (d): verify that GL_KHR_shader_subgroup_arithmetic is enabled."""
    if EXT_PATTERN.search(full_text):
        return None
    return "d", 1, "rule (d) refused: GL_KHR_shader_subgroup_arithmetic not enabled"


def validate_shader_source(text: str) -> list[tuple[str, int, str]]:
    """Run structural rules (a-d) on GLSL shader source, returning violations."""
    stripped = strip_comments(text)
    body, func_line, body_offset = extract_block_sad(stripped)

    violations: list[tuple[str, int, str]] = []
    err_a = check_subgroup_add(body, body_offset, text, func_line)
    if err_a:
        violations.append(err_a)
    err_b = check_barrier_count(body, body_offset, text)
    if err_b:
        violations.append(err_b)
    err_c = check_no_stride_halving_loop(body, body_offset, text)
    if err_c:
        violations.append(err_c)
    err_d = check_extension_enabled(text)
    if err_d:
        violations.append(err_d)

    return violations


def check_spirv_disassembly(ref_shader_path: Path) -> int:
    """Compile reference shader to SPIR-V and verify OpGroupNonUniformFAdd."""
    glslang = resolve_executable("glslangValidator")
    spirv_dis = resolve_executable("spirv-dis")
    if glslang is None or spirv_dis is None:
        print("SKIP: glslang/spirv-dis not found")
        return 77

    with tempfile.TemporaryDirectory() as tmpdir:
        spv_path = Path(tmpdir) / "pelorus_mc.spv"
        cmd_compile = [
            glslang,
            "-V",
            "--target-env",
            "vulkan1.2",
            str(ref_shader_path),
            "-o",
            str(spv_path),
        ]
        proc_compile = subprocess.run(  # nosec B603 - fixed glslang/spirv-dis argv, no shell, explicit deadline  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - fixed compiler tool invocation, no shell, explicit deadline
            cmd_compile,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc_compile.returncode != 0:
            print(
                f"{ref_shader_path}: compilation failed with code {proc_compile.returncode}:\n"
                f"{proc_compile.stderr or proc_compile.stdout}",
                file=sys.stderr,
            )
            return 1

        cmd_dis = [spirv_dis, str(spv_path)]
        proc_dis = subprocess.run(  # nosec B603 - fixed glslang/spirv-dis argv, no shell, explicit deadline  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - fixed compiler tool invocation, no shell, explicit deadline
            cmd_dis,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc_dis.returncode != 0:
            print(
                f"{spv_path}: spirv-dis failed with code {proc_dis.returncode}:\n"
                f"{proc_dis.stderr or proc_dis.stdout}",
                file=sys.stderr,
            )
            return 1

        if "OpGroupNonUniformFAdd" not in proc_dis.stdout:
            print(
                f"{ref_shader_path}:1: rule (spirv) refused: OpGroupNonUniformFAdd not found "
                "in SPIR-V disassembly",
                file=sys.stderr,
            )
            return 1

    return 0


VALID_BLOCK_SAD = """#version 450
#extension GL_KHR_shader_subgroup_basic : require
#extension GL_KHR_shader_subgroup_arithmetic : require

shared float s_part[128];
shared float s_sad0;

float block_sad(int blk_x, int blk_y, int mvx, int mvy, uint lidx,
                int lx, int ly) {
    float d = 0.0;
    if (lx < bsize && ly < bsize) {
        float c = fetchCur(blk_x + lx, blk_y + ly);
        float r = fetchRef(blk_x + lx + mvx, blk_y + ly + mvy);
        d = abs(c - r);
    }
    float sg = subgroupAdd(d);
    if (subgroupElect()) s_part[gl_SubgroupID] = sg;
    barrier();
    if (lidx == 0u) {
        float t = 0.0;
        for (uint i = 0u; i < gl_NumSubgroups; i++) t += s_part[i];
        s_sad0 = t;
    }
    barrier();
    return s_sad0;
}
"""

# Shape of the pre-6bedd01 barrier tree: one barrier() per halving step.
BARRIER_TREE_BLOCK_SAD = """#version 450
#extension GL_KHR_shader_subgroup_basic : require
#extension GL_KHR_shader_subgroup_arithmetic : require

shared float s_sad[1024];

float block_sad(int blk_x, int blk_y, int mvx, int mvy, uint lidx,
                int lx, int ly) {
    float d = 0.0;
    if (lx < bsize && ly < bsize) {
        float c = fetchCur(blk_x + lx, blk_y + ly);
        float r = fetchRef(blk_x + lx + mvx, blk_y + ly + mvy);
        d = abs(c - r);
    }
    s_sad[lidx] = d;
    barrier();
    /* power-of-two tree reduction over BLOCK_DIM*BLOCK_DIM lanes */
    for (uint stride = (BLOCK_DIM * BLOCK_DIM) / 2u; stride > 0u; stride >>= 1u) {
        if (lidx < stride)
            s_sad[lidx] += s_sad[lidx + stride];
        barrier();
    }
    barrier();
    float sg = subgroupAdd(d);
    return s_sad[0] + sg * 0.0;
}
"""


def self_test_cases() -> list[tuple[str, str, set[str]]]:
    """Return (name, source, rules that must refuse it) planted cases."""
    return [
        ("current block_sad", VALID_BLOCK_SAD, set()),
        ("barrier tree", BARRIER_TREE_BLOCK_SAD, {"b", "c"}),
        (
            "halving outside the step clause",
            VALID_BLOCK_SAD.replace(
                "for (uint i = 0u; i < gl_NumSubgroups; i++)",
                "for (uint i = gl_NumSubgroups / 2u - gl_NumSubgroups / 2u; i < gl_NumSubgroups; i++)",
            ),
            set(),
        ),
        (
            "step halving by assignment",
            BARRIER_TREE_BLOCK_SAD.replace("stride >>= 1u", "stride = stride >> 1u"),
            {"b", "c"},
        ),
        (
            "subgroupAdd removed",
            VALID_BLOCK_SAD.replace("subgroupAdd(d)", "d"),
            {"a"},
        ),
        (
            "three barriers",
            VALID_BLOCK_SAD.replace("return s_sad0;", "barrier();\n    return s_sad0;"),
            {"b"},
        ),
        (
            "missing extension",
            VALID_BLOCK_SAD.replace(
                "#extension GL_KHR_shader_subgroup_arithmetic : require", ""
            ),
            {"d"},
        ),
    ]


def self_test() -> int:
    """Run planted cases: the valid source passes, each defect hits its rules."""
    failures: list[str] = []
    for name, source, expected in self_test_cases():
        rules = {rule for rule, _line, _msg in validate_shader_source(source)}
        if expected and not expected <= rules:
            failures.append(
                f"{name}: must be refused by {sorted(expected)}, got {sorted(rules)}"
            )
        if not expected and rules:
            failures.append(f"{name}: unexpectedly refused by {sorted(rules)}")
    for fail in failures:
        print(f"FAIL: {fail}", file=sys.stderr)
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for checking mc subgroup SAD reduction."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--self-test", action="store_true", help="run self-test planted cases"
    )
    parser.add_argument(
        "--shader",
        action="append",
        type=Path,
        help="path to shader source (repeatable, defaults to both repository shaders)",
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    shader_paths = args.shader if args.shader else DEFAULT_SHADERS

    ref_shader = None
    for p in shader_paths:
        if p.suffix == ".comp" or p.name == "pelorus_mc.comp":
            ref_shader = p
            break
    if ref_shader is None:
        ref_shader = REF_SHADER

    all_violations: list[tuple[Path, str, int, str]] = []

    for path in shader_paths:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as err:
            print(f"check-mc-sad-reduction: {path}: {err}", file=sys.stderr)
            return 2

        try:
            violations = validate_shader_source(text)
        except ValueError as err:
            print(f"check-mc-sad-reduction: {path}: {err}", file=sys.stderr)
            return 2

        for rule, line, msg in violations:
            all_violations.append((path, rule, line, msg))

    if all_violations:
        for path, rule, line, msg in all_violations:
            print(f"{path}:{line}: {msg}")
        return 1

    return check_spirv_disassembly(ref_shader)


if __name__ == "__main__":
    sys.exit(main())
