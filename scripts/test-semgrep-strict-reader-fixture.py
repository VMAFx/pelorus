#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Check strict-reader fixture writes against planted cases and tracked Python.

An AST importer inventory checks the explicit rule paths against tracked Python.
A reverted checker fixture must produce exactly one finding, and the real tree
must stay clean with the checker included in the scan.
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import os
import pathlib
import subprocess  # nosec B404 - fixed git argv, no shell, explicit deadline
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
scrubbed_env = semgrep_harness.scrubbed_env
resolve_executable = semgrep_harness.resolve_executable

ROOT = pathlib.Path(__file__).resolve().parent.parent
CHECKER_REL = "scripts/check-vulkan-buffer-domain.py"
RULE_ID = "pelorus-strict-reader-fixture-bytes"
READER_PATHS = {"scripts/c_source.py", "scripts/c_scan.py"}
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
    "dynamic": 'importlib.import_module("c_source")',
    "dynamic-bare": 'import_module("scripts.c_scan")',
    "dynamic-builtin": '__import__("c_source")',
    "dynamic-keyword": 'importlib.import_module(name="c_scan")',
    "dynamic-builtin-keyword": '__import__(name="c_source")',
    "dynamic-qualified": 'loader.__import__("scripts.c_scan")',
    "dynamic-relative": 'loader.import_module(".c_scan")',
    "class": "class Reader:\n    import c_scan",
    "try": "try:\n    import c_scan\nexcept ImportError:\n    pass",
    "dotted-component": "import tools.c_scan.reader",
    "from-dotted-component": "from tools.c_source.reader import source_texts",
}
NO_IMPORTS = {
    "unrelated": "# import c_source\ns = 'import c_scan'",
    "docstring": "'''Example:\nimport c_source\n'''",
    "double-docstring": '"""Example:\nfrom c_scan import mask_c\n"""',
    "alias-only": "import pathlib as c_source",
    "similar-name": "import c_source_extra",
    "from-alias-only": "from pathlib import Path as c_source",
    "dynamic-similar": 'importlib.import_module("c_scan_extra")',
    "dynamic-no-argument": "import_module()",
    "variable-module": "importlib.import_module(name)",
    "dynamic-keyword-variable": "import_module(name=module)",
    "dynamic-other-keyword": 'import_module(package="c_scan")',
}
# Labels and booleans are explicit expectations, independent of rule syntax.
CASES = (
    ("write-text", 'p.write_text("fixture\\n")', True),
    ("write-bytes", 'p.write_bytes(b"fixture\\n")', False),
    ("read-text", "p.read_text()", False),
    ("text-wrapper", "io.TextIOWrapper(raw)", True),
    ("bare-text-wrapper", "TextIOWrapper(raw)", True),
    ("aliased-text-wrapper", "wrapper(raw)", True),
    ("qualified-text-wrapper", "other.TextIOWrapper(raw)", True),
    ("fdopen-default", "os.fdopen(fd)", False),
    ("bare-fdopen-default", "fdopen(fd)", False),
    ("named-default", "tempfile.NamedTemporaryFile()", False),
    ("temporary-default", "tempfile.TemporaryFile()", False),
    ("spooled-default", "tempfile.SpooledTemporaryFile()", False),
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
    ("os.fdopen(fd, {mode})", "fdopen-positional"),
    ("os.fdopen(fd, mode={mode})", "fdopen-keyword"),
    ("fdopen(fd, {mode})", "bare-fdopen-positional"),
    ("fdopen(fd, mode={mode})", "bare-fdopen-keyword"),
    ("other.fdopen(fd, {mode})", "qualified-fdopen-positional"),
    ("other.fdopen(fd, mode={mode})", "qualified-fdopen"),
)

TEMPFILE_FORMS = tuple(
    (f"{prefix}{name}({argument})", f"{prefix}{name}:{argument}")
    for name in ("NamedTemporaryFile", "TemporaryFile", "SpooledTemporaryFile")
    for prefix in ("tempfile.", "", "other.")
    for argument in ("{mode}", "mode={mode}")
)


def fixture_cases() -> list[tuple[str, str, bool]]:
    """Return bounded write/read cases with a label and expected refusal."""
    defaults = [
        (f"{prefix}{name}:default", f"{prefix}{name}()", False)
        for name in ("NamedTemporaryFile", "TemporaryFile", "SpooledTemporaryFile")
        for prefix in ("", "other.")
    ]
    return (
        list(CASES)
        + defaults
        + [
            (f"{label}:{mode}", form.format(mode=mode), refused)
            for form, label in (*OPEN_FORMS, *TEMPFILE_FORMS)
            for mode, refused in MODES
        ]
    )


def make_tree(root: pathlib.Path, relative: str, source: str) -> None:
    """Write an exact-byte fixture and an ignore file retaining test paths."""
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    (root / ".semgrepignore").write_bytes(b"build*/\nsubprojects/\n")
    target.write_bytes(source.encode())


def rule_results(data: dict) -> list[dict]:
    """Select findings from the strict-reader rule."""
    return [
        result
        for result in data.get("results", [])
        if result.get("check_id", "").endswith(RULE_ID)
    ]


def reader_name(name: str) -> bool:
    """Recognize exact reader components in an absolute or relative module name."""
    return bool({"c_source", "c_scan"}.intersection(name.split(".")))


def import_argument(node: ast.Call) -> ast.AST | None:
    """Select the first positional module name, otherwise its name keyword."""
    if node.args:
        return node.args[0]
    return next((kw.value for kw in node.keywords if kw.arg == "name"), None)


def imports_reader(node: ast.AST) -> bool:
    """Recognize import statements and literal calls to Python import loaders."""
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        names = [alias.name for alias in node.names]
        if isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
        return any(reader_name(name) for name in names)
    if isinstance(node, ast.Call):
        name = getattr(node.func, "attr", getattr(node.func, "id", ""))
        argument = import_argument(node)
        if name in {"import_module", "__import__"} and isinstance(
            argument, ast.Constant
        ):
            return isinstance(argument.value, str) and reader_name(argument.value)
    return False


def find_importers(sources: dict[str, bytes]) -> set[str]:
    """Parse every supplied source and fail with its path on invalid Python."""
    importers = set()
    for path, source in sources.items():
        try:
            tree = ast.parse(source, filename=path)
        except SyntaxError as err:
            raise ScanError(f"importer parse failed: {path}: {err}") from err
        if any(imports_reader(node) for node in ast.walk(tree)):
            importers.add(path)
    return importers


def step_importer_cases() -> str | None:
    """Check import forms, exclusions and a parse failure without Semgrep."""
    sources = {name: source.encode() for name, source in (IMPORTS | NO_IMPORTS).items()}
    observed = find_importers(sources)
    wanted = set(IMPORTS)
    if observed != wanted:
        return (
            f"importer cases: missing={sorted(wanted - observed)}, "
            f"extra={sorted(observed - wanted)}"
        )
    invalid = "scripts/unparsable.py"
    try:
        find_importers({invalid: b"def broken(:\n"})
    except ScanError as err:
        if invalid not in str(err):
            return f"importer parse failure omitted path: {err}"
    else:
        return f"importer parse failure accepted: {invalid}"
    print(
        f"importer cases: positive={len(IMPORTS)} negative={len(NO_IMPORTS)} parse failures=1"
    )
    return None


def plant_matrix(tree: pathlib.Path) -> dict[tuple[str, int], tuple[str, bool]]:
    """Plant identical writes in listed paths and an unlisted reader importer."""
    scopes = sorted(READER_PATHS | {CHECKER_REL, "scripts/other.py"})
    expected = {}
    for relative in scopes:
        lines = []
        if relative == CHECKER_REL:
            lines = ['x = "abc"  # filler'] * 2048
        lines += [
            "import io, os, tempfile",
            "from io import open as io_open, TextIOWrapper, TextIOWrapper as wrapper",
            "from os import fdopen",
            "from tempfile import NamedTemporaryFile, TemporaryFile, SpooledTemporaryFile",
            'binary_mode = "wb"',
        ]
        if relative != CHECKER_REL:
            lines.append("import c_scan")
        for label, source, refused in fixture_cases():
            lines.append(source)
            expected[(relative, len(lines))] = (
                label,
                refused and relative != "scripts/other.py",
            )
        if relative == CHECKER_REL:
            lines.append("import c_scan")
        make_tree(tree, relative, "\n".join(lines) + "\n")
    return expected


def step_planted_cases(
    semgrep_bin: str, tmp: pathlib.Path, rule_file: pathlib.Path
) -> str | None:
    """Require exact finding locations for every refused and accepted form."""
    tree = tmp / "planted_repo"
    expected = plant_matrix(tree)
    data = run_semgrep(semgrep_bin, tree, rule_file, 1, tmp / "cfg", hermetic=True)
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


def tracked_python(root: pathlib.Path) -> list[str]:
    """List tracked Python paths, refusing Git failures and an empty inventory."""
    git_bin = resolve_executable("git")
    if not git_bin:
        raise ScanError("git not found: the drift step lists tracked Python")
    proc = subprocess.run(  # nosec B603 - git resolved from PATH to a file; list argv, no shell  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit, python.lang.security.audit.dangerous-subprocess-use-tainted-env-args.dangerous-subprocess-use-tainted-env-args - git resolved to an executable file, fixed list argv, no shell, explicit deadline
        [git_bin, "-C", str(root), "ls-files", "-z", "--", "*.py"],
        capture_output=True,
        env=scrubbed_env(),
        timeout=30.0,
        check=True,
    )
    paths = [path for path in proc.stdout.decode("utf-8").split("\0") if path]
    if not paths:
        raise ScanError(f"git ls-files listed no Python files under {root}")
    return paths


def scope_difference(wanted: set[str], observed: set[str]) -> str | None:
    """Name missing and extra paths when rule findings differ from importers."""
    if wanted != observed:
        return (
            f"scope drift: missing={sorted(wanted - observed)}, "
            f"extra={sorted(observed - wanted)}"
        )
    return None


def step_scope_drift(
    semgrep_bin: str,
    tmp: pathlib.Path,
    repo_root: pathlib.Path,
    rule_file: pathlib.Path,
) -> str | None:
    """Compare the rule's effective scope with parsed tracked Python imports."""
    paths = tracked_python(repo_root)
    previous = os.environ.get("GIT_DIR")
    try:
        os.environ["GIT_DIR"] = str(tmp / "missing-git-dir")
        poisoned = tracked_python(repo_root)
    finally:
        if previous is None:
            del os.environ["GIT_DIR"]
        else:
            os.environ["GIT_DIR"] = previous
    if poisoned != paths:
        return "drift inventory changed with poisoned GIT_DIR"
    print(f"drift environment: poisoned GIT_DIR; unchanged tracked={len(poisoned)}")
    sources = {path: (repo_root / path).read_bytes() for path in paths}
    importers = find_importers(sources)
    wanted = importers | READER_PATHS
    tree = tmp / "drift_repo"
    for path in paths:
        make_tree(tree, path, 'p.write_text("x")\n')
    new = "scripts/new-importer.py"
    if new in paths:
        return f"drift self-check path already tracked: {new}"
    make_tree(tree, new, 'import c_scan\np.write_text("x")\n')
    data = run_semgrep(semgrep_bin, tree, rule_file, 1, tmp / "cfg", hermetic=True)
    observed = {
        pathlib.PurePath(result["path"]).as_posix() for result in rule_results(data)
    }
    print(
        f"drift: tracked={len(paths)} importers={len(importers)} "
        f"expected={len(wanted)} findings={len(observed)}"
    )
    problem = scope_difference(wanted, observed)
    if problem:
        return problem
    augmented = sources | {new: (tree / new).read_bytes()}
    problem = scope_difference(find_importers(augmented) | READER_PATHS, observed)
    if problem is None or new not in problem:
        return f"drift self-check failed: {new}: {problem}"
    print(f"drift self-check: tracked={len(augmented)}; {problem}")
    return None


def step_planted_revert(
    semgrep_bin: str,
    tmp: pathlib.Path,
    repo_root: pathlib.Path,
    rule_file: pathlib.Path,
) -> str | None:
    """Require one finding at the checker fixture reverted to a text write."""
    source = (repo_root / CHECKER_REL).read_text(encoding="utf-8")
    if source.count(FIXTURE) != 1:
        return "step (b): expected exactly one byte-written patch fixture"
    line = source[: source.index(FIXTURE)].count("\n") + 1
    tree = tmp / "revert_repo"
    make_tree(tree, CHECKER_REL, source.replace(FIXTURE, TEXT_FIXTURE, 1))
    data = run_semgrep(semgrep_bin, tree, rule_file, 1, tmp / "cfg", hermetic=True)
    results = rule_results(data)
    if len(results) != 1:
        return f"step (b): expected exactly 1 finding, got {len(results)}"
    result = results[0]
    if result["path"] != CHECKER_REL or result["start"]["line"] != line:
        return f"step (b): finding outside reverted fixture: {result}"
    print(f"reverted fixture: {CHECKER_REL}:{line}; expected=1 observed=1")
    return None


def step_real_tree(
    semgrep_bin: str,
    tmp: pathlib.Path,
    repo_root: pathlib.Path,
    rule_file: pathlib.Path,
) -> str | None:
    """Require a clean tracked-tree scan that includes the checker source."""
    data = run_semgrep(
        semgrep_bin, repo_root, rule_file, 0, tmp / "cfg", hermetic=False
    )
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=pathlib.Path, default=ROOT)
    parser.add_argument("--rule-file", type=pathlib.Path)
    args = parser.parse_args(argv)
    repo_root = args.root.resolve()
    rule_file = (args.rule_file or repo_root / ".semgrep.yml").resolve()
    semgrep_bin = resolve_executable("semgrep")
    if not semgrep_bin:
        print("SKIP: semgrep not found")
        return 77
    for required in (rule_file, repo_root / CHECKER_REL):
        if not required.is_file():
            sys.stderr.write(f"missing file: {required}\n")
            return 1
    with tempfile.TemporaryDirectory() as directory:
        tmp = pathlib.Path(directory)
        (tmp / "cfg").mkdir()
        try:
            steps = (
                (step_importer_cases, ()),
                (step_planted_cases, (semgrep_bin, tmp, rule_file)),
                (step_scope_drift, (semgrep_bin, tmp, repo_root, rule_file)),
                (step_real_tree, (semgrep_bin, tmp, repo_root, rule_file)),
                (step_planted_revert, (semgrep_bin, tmp, repo_root, rule_file)),
            )
            for step, arguments in steps:
                problem = step(*arguments)
                if problem:
                    sys.stderr.write(problem + "\n")
                    return 1
        except (ScanError, OSError, subprocess.SubprocessError) as err:
            sys.stderr.write(f"{err}\n")
            return 1
    print(
        "semgrep strict reader fixture: importer cases, matrix, drift, revert and real tree ok"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
