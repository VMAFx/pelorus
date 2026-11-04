#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Check and render generated baseline counts and engine pins in documentation.

Marked spans:
  <!-- gen:baseline-count -->N<!-- /gen:baseline-count -->
  <!-- gen:engine-pin -->SHA<!-- /gen:engine-pin -->

Rules:
1. Baseline count is read from .standards-baseline.json:
   - 'total_infractions' must be an integer;
   - len(infractions) must strictly equal total_infractions;
   - any mismatch or missing key fails closed.
2. Engine pin is read from PRAETOR_REF declared in .github/workflows/standards-gate.yml
   (matching the engine pin launcher in .config/lefthook/engine.sh).
3. Check mode (default) verifies marked spans match the generated values and
   exits with code 1 upon drift, printing file:line of each offender.
4. --write updates files in place when drift is found.
5. --self-test runs a battery of planted positive and negative cases in a temp tree.

Usage:
    python3 -I scripts/docs/render-counts.py [--write] [file ...]
    python3 -I scripts/docs/render-counts.py --self-test
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

BASELINE_MARKER_RE = re.compile(
    r"<!--\s*gen:baseline-count\s*-->.*?<!--\s*/gen:baseline-count\s*-->"
)
ENGINE_PIN_MARKER_RE = re.compile(
    r"<!--\s*gen:engine-pin\s*-->.*?<!--\s*/gen:engine-pin\s*-->"
)
WORKFLOW_PIN_RE = re.compile(r"^\s*PRAETOR_REF:\s*([0-9a-fA-F]{7,40})\s*$")


def read_baseline_count(baseline_path: Path) -> int:
    """Read and validate total_infractions from .standards-baseline.json."""
    if not baseline_path.is_file():
        raise ValueError(f"baseline file does not exist: {baseline_path}")
    try:
        data = json.loads(baseline_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed baseline JSON in {baseline_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"malformed baseline in {baseline_path}: expected JSON object")
    if "total_infractions" not in data:
        raise ValueError(f"malformed baseline in {baseline_path}: missing 'total_infractions' key")
    total = data["total_infractions"]
    if not isinstance(total, int) or total < 0:
        raise ValueError(f"malformed baseline in {baseline_path}: 'total_infractions' must be a non-negative int")
    if "infractions" not in data or not isinstance(data["infractions"], list):
        raise ValueError(f"malformed baseline in {baseline_path}: missing or invalid 'infractions' list")
    infractions = data["infractions"]
    if len(infractions) != total:
        raise ValueError(
            f"baseline mismatch in {baseline_path}: len(infractions)={len(infractions)} != total_infractions={total}"
        )
    return total


def read_engine_pin(workflow_path: Path) -> str:
    """Read and validate PRAETOR_REF pin from workflow configuration."""
    if not workflow_path.is_file():
        raise ValueError(f"workflow file does not exist: {workflow_path}")
    pin = None
    for line in workflow_path.read_text(encoding="utf-8").splitlines():
        m = WORKFLOW_PIN_RE.match(line)
        if m:
            if pin is not None and pin != m.group(1):
                raise ValueError(
                    f"ambiguous PRAETOR_REF declarations in {workflow_path}: '{pin}' vs '{m.group(1)}'"
                )
            pin = m.group(1)
    if pin is None:
        raise ValueError(f"no PRAETOR_REF declaration found in {workflow_path}")
    return pin


def render_content(content: str, baseline_count: int, engine_pin: str) -> str:
    """Replace marked spans with current generated values."""
    rendered = BASELINE_MARKER_RE.sub(
        f"<!-- gen:baseline-count -->{baseline_count}<!-- /gen:baseline-count -->",
        content,
    )
    rendered = ENGINE_PIN_MARKER_RE.sub(
        f"<!-- gen:engine-pin -->{engine_pin}<!-- /gen:engine-pin -->",
        rendered,
    )
    return rendered


def find_default_files(root: Path) -> list[Path]:
    """Find tracked/standard documentation files containing markers."""
    files: list[Path] = []
    readme = root / "README.md"
    if readme.is_file():
        files.append(readme)
    docs_dir = root / "docs"
    if docs_dir.is_dir():
        for path in sorted(docs_dir.glob("**/*.md")):
            if path.is_file():
                files.append(path)
    return files


def check_or_write_file(
    path: Path,
    baseline_count: int,
    engine_pin: str,
    write: bool = False,
) -> list[str]:
    """Check or rewrite a file; return list of drift messages.

    An unreadable file is reported as a finding instead of aborting the scan.
    """
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return [f"{path}: cannot read: {exc}"]
    if not BASELINE_MARKER_RE.search(content) and not ENGINE_PIN_MARKER_RE.search(content):
        return []

    rendered = render_content(content, baseline_count, engine_pin)
    if content == rendered:
        return []

    if write:
        path.write_text(rendered, encoding="utf-8")
        return []

    # Check mode: locate line numbers of drifts
    orig_lines = content.splitlines()
    rendered_lines = rendered.splitlines()
    drifts: list[str] = []
    for line_no, (orig, rend) in enumerate(zip(orig_lines, rendered_lines), 1):
        if orig != rend:
            drifts.append(f"{path}:{line_no}: drifted generated span (got {orig.strip()!r}, want {rend.strip()!r})")
    if len(orig_lines) != len(rendered_lines):
        drifts.append(f"{path}: line count changed ({len(orig_lines)} vs {len(rendered_lines)})")
    return drifts


_PIN = "c565cba"
_DRIFT_DOCS = (
    ("baseline-count", "Count is <!-- gen:baseline-count -->42<!-- /gen:baseline-count -->.\n"),
    ("engine-pin", "Pin is <!-- gen:engine-pin -->deadbeef<!-- /gen:engine-pin -->.\n"),
)
_BAD_INPUTS = (
    ("missing baseline", read_baseline_count, "missing-baseline.json", None),
    ("malformed JSON", read_baseline_count, "bad.json", "{broken"),
    ("missing total_infractions", read_baseline_count, "no-key.json", json.dumps({"infractions": []})),
    (
        "infractions count mismatch",
        read_baseline_count,
        "mismatch.json",
        json.dumps({"total_infractions": 5, "infractions": []}),
    ),
    ("missing workflow", read_engine_pin, "missing.yml", None),
    ("workflow without PRAETOR_REF", read_engine_pin, "no_pin.yml", "name: CI\n"),
)


def _case_drift(tmppath: Path) -> str | None:
    """Plant each drifted span: the check refuses it and --write restores it."""
    test_doc = tmppath / "test.md"
    for name, text in _DRIFT_DOCS:
        test_doc.write_text(text, encoding="utf-8")
        drifts = check_or_write_file(test_doc, 0, _PIN, write=False)
        if not drifts:
            return f"drifted {name} not detected"
        print(f"self-test PASS: drifted {name} detected: {drifts[0]}")
        check_or_write_file(test_doc, 0, _PIN, write=True)
        if check_or_write_file(test_doc, 0, _PIN, write=False):
            return f"--write did not fix drifted {name}"
        print(f"self-test PASS: --write restored drifted {name}")
    return None


def _case_bad_inputs(tmppath: Path) -> str | None:
    """Plant each missing or malformed input: the reader refuses it."""
    for label, reader, name, text in _BAD_INPUTS:
        path = tmppath / name
        if text is not None:
            path.write_text(text, encoding="utf-8")
        try:
            reader(path)
        except ValueError as exc:
            print(f"self-test PASS: {label} rejected ({exc})")
            continue
        return f"{label} was accepted"
    return None


def _case_argparse(_tmppath: Path) -> str | None:
    """A flag without its value exits with status 2."""
    try:
        parse_args(["--baseline"])
    except SystemExit as exc:
        if exc.code != 2:
            return f"missing --baseline value want exit 2, got {exc.code}"
        print("self-test PASS: missing argument rejected with exit 2")
        return None
    return "missing --baseline value did not exit"


def _case_unreadable(tmppath: Path) -> str | None:
    """A missing or non-UTF-8 doc file is a finding, and the scan goes on."""
    bad = tmppath / "latin1.md"
    bad.write_bytes(b"caf\xe9 <!-- gen:baseline-count -->0<!-- /gen:baseline-count -->\n")
    for path in (tmppath / "missing.md", bad):
        found = check_or_write_file(path, 0, "v0")
        if len(found) != 1 or "cannot read" not in found[0]:
            return f"unreadable {path.name} gave {found}"
        print(f"self-test PASS: unreadable {path.name} reported")
    return None


def self_test() -> int:
    """Execute planted positive and negative tests."""
    with tempfile.TemporaryDirectory() as tmp:
        for case in (_case_drift, _case_bad_inputs, _case_unreadable, _case_argparse):
            failure = case(Path(tmp))
            if failure:
                print(f"self-test FAIL: {failure}", file=sys.stderr)
                return 1
    print("self-test passed")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", help="run self-test suite")
    parser.add_argument("--write", action="store_true", help="rewrite files in-place with generated values")
    parser.add_argument(
        "--baseline",
        type=Path,
        default=Path(".standards-baseline.json"),
        help="path to .standards-baseline.json",
    )
    parser.add_argument(
        "--workflow",
        type=Path,
        default=Path(".github/workflows/standards-gate.yml"),
        help="path to workflow declaring PRAETOR_REF",
    )
    parser.add_argument(
        "files",
        nargs="*",
        type=Path,
        help="documentation files to check/render (default: README.md and docs/**/*.md)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Main execution entry point."""
    args = parse_args(argv)
    if args.self_test:
        return self_test()

    try:
        baseline_count = read_baseline_count(args.baseline)
    except ValueError as exc:
        print(f"render-counts: error reading baseline: {exc}", file=sys.stderr)
        return 1

    try:
        engine_pin = read_engine_pin(args.workflow)
    except ValueError as exc:
        print(f"render-counts: error reading engine pin: {exc}", file=sys.stderr)
        return 1

    target_files = args.files if args.files else find_default_files(Path("."))
    all_drifts: list[str] = []
    for file_path in target_files:
        all_drifts.extend(check_or_write_file(file_path, baseline_count, engine_pin, write=args.write))

    if all_drifts:
        for drift in all_drifts:
            print(drift, file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
