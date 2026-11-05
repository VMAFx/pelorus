#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Planted-defect recall for the float-identity Semgrep rule (ADR-0239).

Ported from VMAFx/vmafx test_semgrep_float_identity.py
(sha 25ee5f3a25aebc97ee453fc3a70f676c68da2a81).

Checks that `pelorus-test-float-identity` in `.semgrep.yml`:
(a) reports computed float/double equality comparisons in test sources, while
    allowing literals, macros, k-constants, NaN idioms, integer casts, and calls;
(b) catches a planted regression in libpelorus/test/interop_test.c when
    float_bits_equal() is reverted to ==;
(c) produces zero findings across the real repository tree;
(d) keeps its macro and k-constant exemption sound: no test source declares a
    non-const float or double with such a name.
"""

from __future__ import annotations

import importlib.util
import pathlib
import re
import sys
import tempfile

# python3 -I drops the script directory from sys.path: load the sibling by path.
_SPEC = importlib.util.spec_from_file_location(
    "semgrep_harness", pathlib.Path(__file__).with_name("semgrep_harness.py")
)
semgrep_harness = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(semgrep_harness)
ScanError = semgrep_harness.ScanError
run_semgrep = semgrep_harness.run_semgrep
resolve_executable = semgrep_harness.resolve_executable

ROOT = pathlib.Path(__file__).resolve().parent.parent
RULE_FILE = ROOT / ".semgrep.yml"
INTEROP_TEST = ROOT / "libpelorus" / "test" / "interop_test.c"

PLANTED_C = """\
#include <stdbool.h>

#define LIMIT 2.0
static const double kLimit = 3.0;
static double computed(void);
static unsigned bits(double value);
struct opaque;
extern struct opaque *o;

bool planted(double x, float p, float q, int i, int k, double u, double v,
             long long ll, short s, unsigned char c)
{
    double y = computed();
    bool hits = x != y;          /* reported: computed double != */
    hits = hits || x == y;       /* reported: computed double == */
    hits = hits || p == q;       /* reported: computed float == */
    hits = hits || p != q;       /* reported: computed float != */
    hits = hits || 1.0 + y == x;  /* reported: literal prefix, computed */
    hits = hits || kLimit + y != x; /* reported: k-constant prefix, computed */
    hits = hits || (double)(y * 2.0) == x; /* reported: cast of a computed value */
    hits = hits || -LIMIT * y == x; /* reported: macro prefix, computed */
    hits = hits || (x + y) == (u + v); /* reported: arithmetic both sides */
    hits = hits || (p * q) != (q - p); /* reported: float arithmetic both sides */
    hits = hits || x == 1.0 + y; /* reported: computed on the right */
    hits = hits || x != kLimit + y; /* reported: computed on the right */
    hits = hits || p == 1.0f + q; /* reported: float, computed on the right */
    hits = hits || p != q * 2.0f; /* reported: float, computed on the right */
    hits = hits || 1.0f + q == p; /* reported: float, computed on the left */
    hits = hits || 2.0f * q != p; /* reported: float, computed on the left */
    hits = hits || computed() == computed(); /* reported: two call results */
    hits = hits || computed() + 1.0 != computed() + 1.0; /* reported: call inside */
    hits = hits || x == o->d; /* reported: one side typed, field type unknown */
    hits = hits || o->d == x; /* reported: one side typed, field type unknown */
    hits = hits || x != o->d; /* reported: one side typed, field type unknown */
    hits = hits || o->d != x; /* reported: one side typed, field type unknown */
    hits = hits || p == o->f; /* reported: one side typed, field type unknown */
    hits = hits || o->f == p; /* reported: one side typed, field type unknown */
    hits = hits || p != o->f; /* reported: one side typed, field type unknown */
    hits = hits || o->f != p; /* reported: one side typed, field type unknown */
    bool quiet = x == 0.0;       /* literal */
    quiet = quiet || 1.5 != y;   /* literal on the left */
    quiet = quiet || x == LIMIT; /* macro */
    quiet = quiet || y == kLimit; /* k-constant */
    quiet = quiet || x != x;     /* NaN idiom */
    quiet = quiet || i == k;     /* integers */
    quiet = quiet || x == -1.5e3; /* signed literal with exponent */
    quiet = quiet || p == .5f;   /* float literal */
    quiet = quiet || y != -kLimit; /* signed k-constant */
    quiet = quiet || i == (int)y; /* integer cast of a double */
    quiet = quiet || x == 0x1p0; /* hexadecimal floating literal */
    quiet = quiet || p != -0x1.8p-3f; /* signed hexadecimal float literal */
    quiet = quiet || (i + (int)x) == k; /* integer arithmetic */
    quiet = quiet || bits(x + y) == bits(u + v); /* calls return integers */
    quiet = quiet || x == 0x10;  /* hexadecimal integer literal */
    quiet = quiet || y != 1U;    /* suffixed integer literal */
    quiet = quiet || x == 10ul;  /* suffixed integer literal */
    quiet = quiet || ll == (long long)y; /* long long cast */
    quiet = quiet || s == (short)x; /* short cast */
    quiet = quiet || c == (unsigned char)x; /* unsigned char cast */
    quiet = quiet || i == (unsigned long long)y; /* unsigned long long cast */
    quiet = quiet || o->d != o->d; /* NaN idiom on a field */
    return hits || quiet;
}
"""

# The hit lines follow the `/* reported` comments, so a planted line added or
# moved keeps its expectation.
EXPECTED_HITS = tuple(
    n for n, line in enumerate(PLANTED_C.splitlines(), 1) if "/* reported" in line
)
# Three scans at this bound stay under the 600 s Meson timeout in meson.build.
# A non-const float named like a macro or k-constant would pass the rule's
# literal exemption while holding a computed value.
FLOAT_TYPE = re.compile(r"\b(?:double|float)\b")
CONSTANT_NAME = re.compile(r"k[A-Z]\w*|[A-Z][A-Z0-9_]*")
# The declared name: pointer stars and spaces, then the identifier, unless a
# `(` follows it (a function, not a variable).
DECLARED_NAME = re.compile(r"[\s*]*(\w+)\b(?!\s*\()")
# Comments and literal contents are blanked before the qualifier check, so
# `double kX = f(); // const` still counts as mutable and a "//" inside a
# string does not hide the code after it. Blanking keeps every offset and
# newline, so a match maps back to its line.
C_LEXEME = re.compile(
    r'"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'|//[^\n]*|/\*.*?(?:\*/|\Z)',
    re.DOTALL,
)
CONST_QUALIFIER = re.compile(r"\b(?:const|constexpr)\b")
TEST_DIRS = (ROOT / "libpelorus" / "test", ROOT / "ffmpeg-patches" / "test")


def make_tree(root: pathlib.Path, name: str, source: str) -> pathlib.Path:
    """Lay out root/libpelorus/test/<name> with an ignore file that keeps test/."""
    test_dir = root / "libpelorus" / "test"
    test_dir.mkdir(parents=True)
    (root / ".semgrepignore").write_text("build*/\nsubprojects/\n", encoding="utf-8")
    (test_dir / name).write_text(source, encoding="utf-8")
    return root


def step_planted_cases(semgrep_bin: str, tmp: pathlib.Path) -> str | None:
    """(a) Every planted case hits or stays quiet as its comment says."""
    tree = make_tree(tmp / "planted_repo", "planted.c", PLANTED_C)
    results = run_semgrep(semgrep_bin, tree, RULE_FILE, 1, tmp / "cfg").get(
        "results", []
    )
    lines = sorted(r["start"]["line"] for r in results)
    if lines != list(EXPECTED_HITS):
        return f"step (a): expected hits on lines {list(EXPECTED_HITS)}, got {lines}"
    return None


def step_planted_revert(semgrep_bin: str, tmp: pathlib.Path) -> str | None:
    """(b) Reverting float_bits_equal() to == yields exactly one finding."""
    needle = "CHECK(float_bits_equal(got_var, var[i]))"
    source = INTEROP_TEST.read_text(encoding="utf-8")
    if needle not in source:
        return f"step (b): needle '{needle}' not in {INTEROP_TEST}"
    planted = source.replace(needle, "CHECK(got_var == var[i])", 1)
    tree = make_tree(tmp / "revert_repo", "interop_test.c", planted)
    results = run_semgrep(semgrep_bin, tree, RULE_FILE, 1, tmp / "cfg").get(
        "results", []
    )
    if len(results) != 1:
        return f"step (b): expected exactly 1 finding, got {len(results)}"
    if not results[0].get("path", "").endswith("libpelorus/test/interop_test.c"):
        return f"step (b): finding outside interop_test.c: {results[0].get('path')}"
    return None


def step_real_tree(semgrep_bin: str, tmp: pathlib.Path) -> str | None:
    """(c) The real tree is clean, and the scan did read the test sources."""
    data = run_semgrep(semgrep_bin, ROOT, RULE_FILE, 0, tmp / "cfg")
    # Semgrep's built-in ignore list skips test/ folders; without the
    # repository .semgrepignore the scan reads nothing and passes empty.
    scanned = data.get("paths", {}).get("scanned", [])
    if not any(p.endswith("libpelorus/test/interop_test.c") for p in scanned):
        return "step (c): the real-tree scan did not read libpelorus/test/interop_test.c; check .semgrepignore"
    if not any(
        p.endswith("ffmpeg-patches/test/pelorus_sidedata_test.c") for p in scanned
    ):
        return (
            "step (c): the real-tree scan did not read "
            "ffmpeg-patches/test/pelorus_sidedata_test.c; check .semgrepignore"
        )
    results = data.get("results", [])
    if results:
        return f"step (c): real tree has {len(results)} findings: {results}"
    return None


def _blank_lexeme(match: re.Match[str]) -> str:
    """A comment becomes spaces; a literal keeps its quotes, its body spaces."""
    lexeme = match.group(0)
    if lexeme[0] in "\"'":
        return lexeme[0] + re.sub(r"[^\n]", " ", lexeme[1:-1]) + lexeme[-1]
    return re.sub(r"[^\n]", " ", lexeme)


def declaration_start(code: str, pos: int) -> int:
    """Offset where the declaration holding `pos` starts.

    Outside parentheses that is the previous statement boundary.
    Inside a parameter list it is the last `(` or `,` at the same depth, so an
    earlier parameter's `const` does not count for a later one.
    """
    stmt = max(code.rfind(sep, 0, pos) for sep in ";{}") + 1
    opens = []
    for offset, char in enumerate(code[stmt:pos], start=stmt):
        if char == "(":
            opens.append(offset + 1)
        elif char == ")" and opens:
            opens.pop()
        elif char == "," and opens:
            opens[-1] = offset + 1
    return opens[-1] if opens else stmt


def declarators(code: str, start: int, in_params: bool) -> list[tuple[int, int]]:
    """Spans of the declarators that follow a type keyword at `start`.

    Commas at bracket depth 0 split declarators. A statement ends at `;` or
    `{`; a parameter ends at its comma or at the `)` that closes its list,
    so one parameter never reads the next.
    """
    spans: list[tuple[int, int]] = []
    depth = 0
    begin = start
    for offset in range(start, len(code)):
        char = code[offset]
        if char in "([":
            depth += 1
        elif char in ")]" and depth:
            depth -= 1
        elif depth == 0 and (
            char in ");{}" or (char == "," and in_params)
        ):
            break
        elif depth == 0 and char == ",":
            spans.append((begin, offset))
            begin = offset + 1
    else:
        offset = len(code)
    spans.append((begin, offset))
    return spans


def constant_name_findings(text: str, name: str) -> list[str]:
    """Declarations of a non-const float or double with a constant-style name.

    The match runs over the whole blanked text, so a declaration split over
    lines counts. Every declarator of a declaration is read
    (`double a, kB = f();` names kB), but only its declared name, not the
    words in its initialiser or its array bound; the qualifier check reads
    the declaration from its start (declaration_start) to its end, and the
    finding names the line where the type keyword sits.
    """
    code = C_LEXEME.sub(_blank_lexeme, text)
    lines = text.splitlines()
    found = []
    for kind in FLOAT_TYPE.finditer(code):
        start = declaration_start(code, kind.start())
        in_params = start > 0 and code[start - 1] in "(,"
        spans = declarators(code, kind.end(), in_params)
        if CONST_QUALIFIER.search(code[start : spans[-1][1]]):
            continue
        for begin, end in spans:
            decl = DECLARED_NAME.match(code, begin, end)
            if decl and CONSTANT_NAME.fullmatch(decl.group(1)):
                n = code.count("\n", 0, kind.start()) + 1
                found.append(f"{name}:{n}: {lines[n - 1].strip()}")
                break
    return found


def step_constant_names(_semgrep_bin: str, _tmp: pathlib.Path) -> str | None:
    """(d) The planted computed k-name is refused; the real test trees have none."""
    planted = (
        "double kScale = computed();\n"
        "float GAIN;\n"
        "double kNoted = computed(); // const\n"
        "float LIMIT; /* const */\n"
        "static const double kOk = 1.0;\n"
        "double const kEast = 1.0;\n"
        "double kTail = 1.0; const int n = 2;\n"
        "/* const */ double kLead = computed();\n"
        'printf("https://example"); double kResult = computed();\n'
        'puts("double kInString = 1.0;");\n'
        "double\n"
        "kMulti = computed();\n"
        "/* const\n"
        "*/ double kBlock = computed();\n"
        "static const double\n"
        "kSplitOk = 1.0;\n"
        "void f(const char *label,\n"
        "       double kParam, double actual);\n"
        "void g(double x, const double kParamOk, int n);\n"
        "void h(int n, double kLast);\n"
        "double ordinary, kComputed = computed();\n"
        "const double a, kB = 1.0;\n"
        "double scaled(UINT32 n);\n"
    )
    got = constant_name_findings(planted, "planted.c")
    want = [f"planted.c:{n}:" for n in (1, 2, 3, 4, 7, 8, 9, 11, 14, 18, 20, 21)]
    if [g.split(" ", 1)[0] for g in got] != want:
        return f"step (d): constant-style name findings {got}, want lines {want}"
    found = []
    for test_dir in TEST_DIRS:
        for path in sorted(test_dir.rglob("*")):
            if path.suffix in (".c", ".h", ".cpp", ".hpp") and path.is_file():
                text = path.read_text(encoding="utf-8", errors="replace")
                found += constant_name_findings(text, str(path.relative_to(ROOT)))
    if found:
        return "step (d): non-const float with a constant-style name:\n" + "\n".join(
            found
        )
    return None


def main() -> int:
    """Run steps (a) to (d); exit 77 (skip) when semgrep is not installed."""
    semgrep_bin = resolve_executable("semgrep")
    if not semgrep_bin:
        print("SKIP: semgrep not found")
        return 77
    for required in (RULE_FILE, INTEROP_TEST):
        if not required.is_file():
            sys.stderr.write(f"missing file: {required}\n")
            return 1
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        (tmp / "cfg").mkdir()
        try:
            for step in (
                step_planted_cases,
                step_planted_revert,
                step_real_tree,
                step_constant_names,
            ):
                problem = step(semgrep_bin, tmp)
                if problem:
                    sys.stderr.write(problem + "\n")
                    return 1
        except ScanError as err:
            sys.stderr.write(f"{err}\n")
            return 1
    print(
        "semgrep float identity: planted cases, planted revert, real tree and constant names ok"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
