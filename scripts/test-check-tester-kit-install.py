#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Self-test for the tester-kit packaging pre-CI gate."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess  # nosec B404 - run check-tester-kit-install script subprocess with explicit timeout
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
CHECK_SCRIPT = ROOT / "scripts" / "check-tester-kit-install.py"


def _entry(name: str, exe: Path, arg: str, workdir: Path) -> list[dict[str, object]]:
    """Build a mock Meson test metadata entry with a single argument."""
    return [
        {
            "name": name,
            "suite": ["fast"],
            "cmd": [str(exe), arg],
            "env": {},
            "workdir": str(workdir),
            "protocol": "exitcode",
            "timeout": 30,
            "extra_paths": [],
        }
    ]


def _run_check(
    build_dir: Path, source_dir: Path, json_path: Path
) -> subprocess.CompletedProcess[str]:
    """Execute check-tester-kit-install.py as a subprocess with explicit timeout."""
    return subprocess.run(  # nosec B603 - check-tester-kit-install argv list, no shell, explicit timeout  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - check-tester-kit-install argv list, no shell, explicit timeout
        [
            sys.executable,
            "-I",
            str(CHECK_SCRIPT),
            str(build_dir),
            "--tests-json",
            str(json_path),
            "--source-dir",
            str(source_dir),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30.0,
    )


def test_tester_kit_install_check() -> list[str]:
    """Test positive, negative (planted ..), and boundary (outside trees) cases."""
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="pelorus-test-kit-") as tmp_str:
        tmp = Path(tmp_str)
        build_dir = tmp / "build"
        source_dir = tmp / "source"
        outside_dir = tmp / "outside"
        build_dir.mkdir()
        source_dir.mkdir()
        outside_dir.mkdir()

        dummy_exe = build_dir / "stub_test"
        dummy_exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        dummy_exe.chmod(0o500)

        fix_file = source_dir / "fix" / "a.tbl"
        fix_file.parent.mkdir(parents=True)
        fix_file.write_text("table data\n", encoding="utf-8")

        outside_file = outside_dir / "other.tbl"
        outside_file.write_text("outside data\n", encoding="utf-8")

        (source_dir / "sub").mkdir(parents=True)

        cases = [
            ("positive-test", str(fix_file), True, ""),
            ("negative-test", f"{source_dir}/sub/../fix/a.tbl", False, "missing or unsafe test input"),
            ("boundary-test", str(outside_file), False, "outside build/source trees"),
        ]

        for name, fixture_arg, want_ok, want_text in cases:
            json_path = tmp / f"{name}.json"
            payload = _entry(name, dummy_exe, fixture_arg, build_dir)
            json_path.write_text(json.dumps(payload), encoding="utf-8")
            proc = _run_check(build_dir, source_dir, json_path)
            if want_ok and proc.returncode != 0:
                failures.append(f"{name} failed with rc {proc.returncode}: {proc.stderr.strip()}")
            elif not want_ok:
                if proc.returncode == 0:
                    failures.append(f"{name} unexpectedly succeeded (expected non-zero rc)")
                if want_text not in proc.stderr:
                    failures.append(f"{name} did not name '{want_text}': {proc.stderr.strip()}")

    return failures


def main() -> int:
    """Run self-tests and exit non-zero on failure."""
    failures = test_tester_kit_install_check()
    for failure in failures:
        print(f"FAIL: {failure}", file=sys.stderr)
    if failures:
        return 1
    print("PASS: check-tester-kit-install self-test")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
