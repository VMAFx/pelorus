#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Generate and verify .config/clang-tidy/lane-files.txt from compile_commands.json."""

from __future__ import annotations

import argparse
import difflib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LANE_FILE = REPO_ROOT / ".config" / "clang-tidy" / "lane-files.txt"


def extract_header_comment(lane_file: Path) -> str:
    """Return leading '#' lines from the lane file or default header if missing."""
    if not lane_file.is_file():
        return (
            '# clang-tidy lane "libpelorus" (.standards.yaml clang_tidy.lanes, ADR-0168).\n'
            "# `make tidy` and the ci.yml core job run clang-tidy over exactly these files,\n"
            "# so the lane the audit judges is the lane that runs. One path per line; these\n"
            "# are every C unit the Meson build compiles (libpelorus/src, libpelorus/test,\n"
            "# tools, and the ffmpeg-patches tests Meson builds without an FFmpeg tree).\n"
            '# The FFmpeg-tree filter, BSF, and replay consumer units live in the sibling\n'
            '# "ffmpeg-tree" lane (.config/clang-tidy/ffmpeg-tree-files.txt).\n'
        )
    lines: list[str] = []
    with open(lane_file, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("#"):
                lines.append(line)
            else:
                break
    return "".join(lines)


def collect_c_units(compile_db_path: Path, source_root: Path) -> list[str]:
    """Return sorted unique C units relative to source_root from compile_commands.json."""
    with open(compile_db_path, "r", encoding="utf-8") as f:
        db = json.load(f)
    units: set[str] = set()
    source_root_resolved = source_root.resolve()
    for entry in db:
        raw_file = entry.get("file", "")
        if not raw_file.endswith(".c"):
            continue
        fpath = Path(raw_file)
        if not fpath.is_absolute():
            entry_dir = entry.get("directory", "")
            fpath = Path(entry_dir) / fpath
        fpath = fpath.resolve()
        try:
            rel = fpath.relative_to(source_root_resolved).as_posix()
            units.add(rel)
        except ValueError:
            # File is outside source root
            continue
    return sorted(units)


def render_lane_content(lane_file: Path, units: list[str]) -> str:
    """Combine leading comment block and sorted unit list with trailing newline."""
    header = extract_header_comment(lane_file)
    return header + "".join(f"{u}\n" for u in units)


def find_compile_db(build_dir_str: str, source_root: Path) -> Path | None:
    """Find compile_commands.json in candidate directory or source_root subpath."""
    candidate = Path(build_dir_str)
    if candidate.is_file() and candidate.name == "compile_commands.json":
        return candidate.resolve()
    if (candidate / "compile_commands.json").is_file():
        return (candidate / "compile_commands.json").resolve()
    if not candidate.is_absolute():
        from_source = source_root / candidate / "compile_commands.json"
        if from_source.is_file():
            return from_source.resolve()
    return None


def do_check(lane_file: Path, rendered_text: str, units: list[str]) -> int:
    """Compare existing lane file against rendered list. Return 0 on match, 1 on diff."""
    existing_text = lane_file.read_text(encoding="utf-8") if lane_file.is_file() else ""
    if existing_text == rendered_text:
        print(f"lane-files.txt matches ({len(units)} units)")
        return 0

    existing_units = [
        line.strip()
        for line in existing_text.splitlines()
        if line.strip() and not line.startswith("#")
    ]
    added = [u for u in units if u not in existing_units]
    removed = [u for u in existing_units if u not in units]

    if added:
        print("added units:")
        for u in added:
            print(f"+ {u}")
    if removed:
        print("removed units:")
        for u in removed:
            print(f"- {u}")
    if not added and not removed:
        print("lane-files.txt formatting or ordering differs from rendered list:")
        diff = difflib.unified_diff(
            existing_text.splitlines(keepends=True),
            rendered_text.splitlines(keepends=True),
            fromfile=str(lane_file),
            tofile="rendered",
        )
        sys.stdout.write("".join(diff))
    return 1


def do_write(lane_file: Path, rendered_text: str, units: list[str]) -> int:
    """Write rendered list to lane file only if changed. Return 0."""
    existing_text = lane_file.read_text(encoding="utf-8") if lane_file.is_file() else None
    if existing_text == rendered_text:
        print("nothing written")
        return 0

    lane_file.parent.mkdir(parents=True, exist_ok=True)
    with open(lane_file, "w", encoding="utf-8") as f:
        f.write(rendered_text)
    print(f"updated {lane_file} ({len(units)} units)")
    return 0


HEADER = "# clang-tidy custom header\n# line two of header\n"


def _make_fixture(temp_root: Path) -> tuple[Path, Path, Path]:
    """Synthetic repository: two C units, one C++ unit, and a compile database.

    Entries: absolute "directory" + relative "file", an absolute "file", a
    .cpp entry that is ignored, and a duplicate of the first entry.
    """
    source_dir = temp_root / "repo"
    build_dir = source_dir / "build"
    src_dir = source_dir / "src"
    build_dir.mkdir(parents=True)
    src_dir.mkdir()
    (src_dir / "foo.c").write_text("int foo(void) { return 0; }\n")
    (src_dir / "bar.c").write_text("int bar(void) { return 1; }\n")
    (src_dir / "baz.cpp").write_text("int baz(void) { return 2; }\n")
    compile_db = [
        {"directory": str(build_dir), "file": "../src/foo.c"},
        {"directory": str(build_dir), "file": str(src_dir / "bar.c")},
        {"directory": str(build_dir), "file": "../src/baz.cpp"},
        {"directory": str(build_dir), "file": "../src/foo.c"},
    ]
    (build_dir / "compile_commands.json").write_text(json.dumps(compile_db, indent=2))
    lane_file = source_dir / ".config" / "clang-tidy" / "lane-files.txt"
    lane_file.parent.mkdir(parents=True)
    return source_dir, build_dir, lane_file


def _run_cli(
    source_dir: Path, lane_file: Path, build_dir: Path, *args: str
) -> subprocess.CompletedProcess[str]:
    """Run this script on the fixture with a deadline."""
    cmd = [
        sys.executable, "-I", str(Path(__file__).resolve()),
        "--source-dir", str(source_dir),
        "--lane-file", str(lane_file),
        "--build-dir", str(build_dir),
        *args,
    ]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False, shell=False)


def _expect(cond: bool, msg: str) -> None:
    """Fail a planted case; unlike assert, this still runs under python -O."""
    if not cond:
        raise AssertionError(msg)


def _self_test_check(source_dir: Path, build_dir: Path, lane_file: Path) -> None:
    """Planted cases 1-3: match, missing unit, extra unit."""
    lane_file.write_text(HEADER + "src/bar.c\nsrc/foo.c\n")
    res = _run_cli(source_dir, lane_file, build_dir, "--check")
    _expect(res.returncode == 0, f"Planted match check failed: {res.stderr}\n{res.stdout}")
    _expect("lane-files.txt matches (2 units)" in res.stdout, f"Unexpected stdout: {res.stdout}")

    lane_file.write_text(HEADER + "src/bar.c\n")
    res = _run_cli(source_dir, lane_file, build_dir, "--check")
    _expect(res.returncode == 1, f"Planted missing unit should exit 1, got {res.returncode}")
    _expect("+ src/foo.c" in res.stdout, f"Expected '+ src/foo.c' in stdout: {res.stdout}")

    lane_file.write_text(HEADER + "src/bar.c\nsrc/extra.c\nsrc/foo.c\n")
    res = _run_cli(source_dir, lane_file, build_dir, "--check")
    _expect(res.returncode == 1, f"Planted extra unit should exit 1, got {res.returncode}")
    _expect("- src/extra.c" in res.stdout, f"Expected '- src/extra.c' in stdout: {res.stdout}")


def _self_test_write(source_dir: Path, build_dir: Path, lane_file: Path) -> None:
    """Planted cases 4-6: write fixes drift, a second write writes nothing, header kept."""
    res_write = _run_cli(source_dir, lane_file, build_dir, "--write")
    _expect(res_write.returncode == 0, f"--write failed: {res_write.stderr}")
    res_check = _run_cli(source_dir, lane_file, build_dir, "--check")
    _expect(res_check.returncode == 0, f"--check after --write failed: {res_check.stderr}\n{res_check.stdout}")
    _expect("lane-files.txt matches (2 units)" in res_check.stdout, f"Unexpected stdout: {res_check.stdout}")

    time.sleep(0.02)
    before = lane_file.stat()
    res_write2 = _run_cli(source_dir, lane_file, build_dir, "--write")
    _expect(res_write2.returncode == 0, f"second write failed: {res_write2.stderr}")
    _expect("nothing written" in res_write2.stdout, f"expected 'nothing written', got {res_write2.stdout}")
    after = lane_file.stat()
    _expect(after.st_mtime_ns == before.st_mtime_ns, f"second write modified mtime: {before.st_mtime_ns} vs {after.st_mtime_ns}")
    _expect(after.st_size == before.st_size, f"second write modified size: {before.st_size} vs {after.st_size}")

    content = lane_file.read_text()
    _expect(content.startswith(HEADER), f"Header comment not preserved: {content}")


def _self_test_missing_db(temp_root: Path, source_dir: Path, lane_file: Path) -> None:
    """Planted case 7: a missing compile database exits 2 and names meson setup."""
    res = _run_cli(source_dir, lane_file, temp_root / "nonexistent_build", "--check")
    _expect(res.returncode == 2, f"expected exit code 2 for missing compile db, got {res.returncode}")
    _expect("meson setup" in res.stderr, f"expected 'meson setup' in stderr, got: {res.stderr}")


def run_self_test() -> int:
    """Run hermetic self-tests with synthetic build environment and planted cases."""
    with tempfile.TemporaryDirectory() as td:
        temp_root = Path(td).resolve()
        source_dir, build_dir, lane_file = _make_fixture(temp_root)
        _self_test_check(source_dir, build_dir, lane_file)
        _self_test_write(source_dir, build_dir, lane_file)
        _self_test_missing_db(temp_root, source_dir, lane_file)
    print("gen-tidy-lane-files: self-test PASS (all 7 planted cases)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Command line: --check or --write, or --self-test."""
    parser = argparse.ArgumentParser(
        description="Generate or verify .config/clang-tidy/lane-files.txt from compile_commands.json."
    )
    parser.add_argument(
        "--build-dir",
        default="build",
        help="Meson build directory holding compile_commands.json (default: build)",
    )
    parser.add_argument("--self-test", action="store_true", help="Run hermetic self-tests")
    parser.add_argument("--source-dir", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--lane-file", default=None, help=argparse.SUPPRESS)
    action_group = parser.add_mutually_exclusive_group()
    action_group.add_argument(
        "--check", action="store_true", help="Check lane-files.txt against compile_commands.json"
    )
    action_group.add_argument(
        "--write", action="store_true", help="Write lane-files.txt if changed"
    )
    return parser


def run_lane(args: argparse.Namespace) -> int:
    """Render the lane list and check or write it; 2 when the compile database is missing."""
    source_root = Path(args.source_dir).resolve() if args.source_dir else REPO_ROOT
    lane_file = (
        Path(args.lane_file).resolve()
        if args.lane_file
        else (source_root / ".config" / "clang-tidy" / "lane-files.txt")
    )
    compile_db = find_compile_db(args.build_dir, source_root)
    if compile_db is None:
        sys.stderr.write(
            f"ERROR: compile_commands.json not found in '{args.build_dir}'. "
            f"Run 'meson setup {args.build_dir}' first to generate it.\n"
        )
        return 2
    units = collect_c_units(compile_db, source_root)
    rendered_text = render_lane_content(lane_file, units)
    if args.check:
        return do_check(lane_file, rendered_text, units)
    return do_write(lane_file, rendered_text, units)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.self_test:
        return run_self_test()
    if not args.check and not args.write:
        parser.error("one of --check or --write is required (or use --self-test)")
    return run_lane(args)


if __name__ == "__main__":
    sys.exit(main())
