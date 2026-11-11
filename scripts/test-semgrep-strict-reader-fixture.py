#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Check strict-reader fixture writes against planted cases and tracked Python.

Every import form and both reader filenames exercise the same write matrix.
A reverted checker fixture must produce exactly one finding, and the real tree
must stay clean with the checker included in the scan.
"""

from __future__ import annotations

import argparse
import importlib.util
import pathlib
import sys
import tempfile

# Isolated Python omits sibling modules from sys.path; load the shared harness.
sys.dont_write_bytecode = True
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
CHECKER_REL = "scripts/check-vulkan-buffer-domain.py"
RULE_IDS = (
    "pelorus-strict-reader-fixture-bytes",
    "pelorus-strict-reader-fixture-bytes-module",
)
FIXTURE = '(root / "test.patch").write_bytes(patch.encode())'
TEXT_FIXTURE = '(root / "test.patch").write_text(patch)'
IMPORTS = {
    "plain-source": "import c_source",
    "plain-scan": "import c_scan",
    "alias-source": "import c_source as reader",
    "alias-scan": "import c_scan as reader",
    "from-source": "from c_source import source_texts",
    "from-scan": "from c_scan import mask_c",
    "from-alias": "from c_source import source_texts as read",
    "star": "from c_scan import *",
    "multiline": "from c_source import (\n    PatchError,\n    source_texts,\n)",
    "comma": "import pathlib, c_scan",
    "dotted": "import scripts.c_source as reader",
    "package": "from scripts import c_scan",
    "relative": "from .c_source import source_texts",
    "relative-package": "from . import c_scan",
    "nested": "def load_reader():\n    import c_source",
    "late": "import c_scan",
    "both": "import c_source, c_scan",
    "conditional": "if enabled:\n    import c_scan",
    "spaced-dots": "from scripts . c_source import source_texts",
    "continued-import": "import \\\n    c_scan",
    "continued-from": "from c_source \\\n    import source_texts",
    "package-list": "from scripts import (\n    other,  # comment\n    c_scan as scan,\n)",
}
NO_IMPORTS = {
    "unrelated": "# import c_source\ns = 'import c_scan'",
    "docstring": "'''Example:\nimport c_source\n'''",
    "double-docstring": '"""Example:\nfrom c_scan import mask_c\n"""',
    "alias-only": "import pathlib as c_source",
    "similar-name": "import c_source_extra",
}
# Labels and booleans are explicit expectations, independent of rule syntax.
CASES = (
    ("write-text", 'p.write_text("fixture\\n")', True),
    ("write-bytes", 'p.write_bytes(b"fixture\\n")', False),
    ("read-text", "p.read_text()", False),
    ("default-read", 'open("fixture")', False),
    ("path-default-read", "p.open()", False),
    ("io-default-read", 'io.open("fixture")', False),
    ("literal-variable", 'open("fixture", mode=binary_mode)', True),
    ("io-alias", 'io_open("fixture", "w")', True),
    ("io-alias-binary", 'io_open("fixture", "wb")', False),
    ("fstring-mode", 'open("fixture", f"{mode}")', True),
    ("fstring-binary", 'open("fixture", f"rb")', True),
    ("keyword-file", 'open(file="fixture", mode="w")', True),
    ("binary-path-name", 'io.open("wb", "w")', True),
    ("expression-mode", 'open("fixture", "r" + suffix)', True),
    ("literal-import-text", 'p.write_bytes(b"import c_scan")', False),
)
MODES = (
    ('"w"', True),
    ('"a"', True),
    ('"x"', True),
    ('"r+"', True),
    ('"w+"', True),
    ('"at"', True),
    ('"x+t"', True),
    ('"r"', False),
    ('"rt"', False),
    ('"rb"', False),
    ('"wb"', False),
    ('"ab"', False),
    ('"xb"', False),
    ('"r+b"', False),
    ('"w+b"', False),
    ('"br+"', False),
    ("mode", True),
    ('r"wb"', False),
    ('u"w"', True),
    ('"""wb"""', False),
    ('"""w"""', True),
)
OPEN_FORMS = (
    ('open("fixture", {mode})', "open-positional"),
    ('open("fixture", mode={mode})', "open-keyword"),
    ("p.open({mode})", "path-positional"),
    ("p.open(mode={mode})", "path-keyword"),
    ('io.open("fixture", {mode})', "io-positional"),
    ('io.open("fixture", mode={mode})', "io-keyword"),
)


def fixture_cases() -> list[tuple[str, str, bool]]:
    """Return bounded write/read cases with a label and expected refusal."""
    return list(CASES) + [
        (f"{label}:{mode}", form.format(mode=mode), refused)
        for form, label in OPEN_FORMS
        for mode, refused in MODES
    ]


def make_tree(root: pathlib.Path, relative: str, source: str) -> None:
    """Write an exact-byte fixture and an ignore file retaining test paths."""
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    (root / ".semgrepignore").write_bytes(b"build*/\nsubprojects/\n")
    target.write_bytes(source.encode())


def rule_results(data: dict) -> list[dict]:
    """Select findings from either strict-reader scope rule."""
    return [
        result
        for result in data.get("results", [])
        if result.get("check_id", "").endswith(RULE_IDS)
    ]


def plant_matrix(tree: pathlib.Path) -> dict[tuple[str, int], tuple[str, bool]]:
    """Plant import scopes, filename scopes and a module outside both scopes."""
    scopes = [(f"scripts/{name}.py", text, True) for name, text in IMPORTS.items()]
    scopes += [("scripts/c_source.py", "", True), ("scripts/c_scan.py", "", True)]
    scopes += [(f"scripts/{name}.py", text, False) for name, text in NO_IMPORTS.items()]
    expected = {}
    for relative, imports, in_scope in scopes:
        lines = ["import io", "from io import open as io_open", 'binary_mode = "wb"']
        if relative != "scripts/late.py":
            lines.extend(imports.splitlines())
        for label, source, refused in fixture_cases():
            lines.append(source)
            expected[(relative, len(lines))] = (
                label,
                refused and in_scope,
            )
        if relative == "scripts/late.py":
            lines.extend(imports.splitlines())
        make_tree(tree, relative, "\n".join(lines) + "\n")
    return expected


def step_planted_cases(semgrep_bin: str, tmp: pathlib.Path) -> str | None:
    """Require exact finding locations for every refused and accepted form."""
    tree = tmp / "planted_repo"
    expected = plant_matrix(tree)
    data = run_semgrep(semgrep_bin, tree, RULE_FILE, 1, tmp / "cfg", hermetic=True)
    observed = [
        (pathlib.PurePath(result["path"]).as_posix(), result["start"]["line"])
        for result in rule_results(data)
    ]
    wanted = sorted(key for key, (_, refused) in expected.items() if refused)
    if sorted(observed) != wanted:
        missing = sorted(set(wanted) - set(observed))[:10]
        extra = sorted(set(observed) - set(wanted))[:10]
        return f"step (a): missing={missing}, extra={extra}; hits={len(observed)}/{len(wanted)}"
    print(
        f"cases: {len(expected)}, findings: {len(observed)}; all expected locations matched"
    )
    return None


def step_planted_revert(semgrep_bin: str, tmp: pathlib.Path) -> str | None:
    """Require one finding at the checker fixture reverted to a text write."""
    source = (ROOT / CHECKER_REL).read_text(encoding="utf-8")
    if source.count(FIXTURE) != 1:
        return "step (b): expected exactly one byte-written patch fixture"
    line = source[: source.index(FIXTURE)].count("\n") + 1
    tree = tmp / "revert_repo"
    make_tree(tree, CHECKER_REL, source.replace(FIXTURE, TEXT_FIXTURE, 1))
    data = run_semgrep(semgrep_bin, tree, RULE_FILE, 1, tmp / "cfg", hermetic=True)
    results = rule_results(data)
    if len(results) != 1:
        return f"step (b): expected exactly 1 finding, got {len(results)}"
    result = results[0]
    if result["path"] != CHECKER_REL or result["start"]["line"] != line:
        return f"step (b): finding outside reverted fixture: {result}"
    print(f"reverted fixture: {CHECKER_REL}:{line}; expected=1 observed=1")
    return None


def step_real_tree(semgrep_bin: str, tmp: pathlib.Path) -> str | None:
    """Require a clean tracked-tree scan that includes the checker source."""
    data = run_semgrep(semgrep_bin, ROOT, RULE_FILE, 0, tmp / "cfg", hermetic=False)
    scanned = data.get("paths", {}).get("scanned", [])
    if not any(path.endswith(CHECKER_REL) for path in scanned):
        return f"step (c): the scan did not read {CHECKER_REL}"
    results = rule_results(data)
    if results:
        return f"step (c): real tree has {len(results)} findings: {results}"
    print(f"real tree: expected=0 observed=0; scanned {CHECKER_REL}")
    return None


def main(argv: list[str] | None = None) -> int:
    """Check plants and tracked files, or return 77 if Semgrep is unavailable."""
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    semgrep_bin = resolve_executable("semgrep")
    if not semgrep_bin:
        print("SKIP: semgrep not found")
        return 77
    for required in (RULE_FILE, ROOT / CHECKER_REL):
        if not required.is_file():
            sys.stderr.write(f"missing file: {required}\n")
            return 1
    with tempfile.TemporaryDirectory() as directory:
        tmp = pathlib.Path(directory)
        (tmp / "cfg").mkdir()
        try:
            for step in (step_planted_cases, step_real_tree, step_planted_revert):
                problem = step(semgrep_bin, tmp)
                if problem:
                    sys.stderr.write(problem + "\n")
                    return 1
        except ScanError as err:
            sys.stderr.write(f"{err}\n")
            return 1
    print(
        "semgrep strict reader fixture: planted cases, planted revert and real tree ok"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
