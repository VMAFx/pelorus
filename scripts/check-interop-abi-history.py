#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Verification harness for interop ABI history fixtures and cross-tag compatibility."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess  # nosec B404 - fixed git and compiler argv, no shell, explicit timeout
import sys
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from interop_abi_history_manifest import (  # noqa: E402
    load_manifest_strict,
    run_reader_matrix,
    verify_fixtures_and_manifest,
)
from selftest_interop_abi_history import (  # noqa: E402
    SelfTestError,
    run_self_test as run_self_test_impl,
)

ROOT_FIXTURES_DIR = Path("libpelorus/test/fixtures/interop-history")
COMPILER_TIMEOUT_S = 30
GIT_TIMEOUT_S = 30
ABI_MINOR_RE = re.compile(r"#define\s+PELORUS_ABI_MINOR\s+(\d+)")
TAG_RE = re.compile(r"^v\d+\.\d+\.\d+(-rc\.\d+)?$")
ABI_HISTORY_SOURCES: tuple[str, ...] = (
    "abi_history.c",
    "abi_history_check.c",
    "abi_history_fill.c",
    "abi_history_pack.c",
)


def get_repo_root() -> Path:
    """Return top-level repository root directory."""
    return Path(__file__).resolve().parent.parent


def get_git_tags(repo_root: Path) -> list[str]:
    """Return sorted list of release and release-candidate git tags."""
    proc = subprocess.run(  # nosec B603 - fixed git argv, no shell, explicit timeout  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - argv is a list without shell, explicit timeout
        ["git", "tag", "--list", "v*"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_S,
        check=True,
    )
    raw_tags = proc.stdout.splitlines()
    tags = [t.strip() for t in raw_tags if TAG_RE.match(t.strip())]

    def _sort_key(t: str) -> list[int]:
        nums = [int(x) for x in re.findall(r"\d+", t)]
        rc = 999999 if "-rc." not in t else int(t.split("-rc.")[1])
        return nums[:3] + [rc]

    return sorted(tags, key=_sort_key)


def extract_tag_sources(repo_root: Path, tag: str, temp_dir: Path) -> Path:
    """Extract interop source files from historical tag into temp_dir."""
    dst = temp_dir / tag
    dst.mkdir(parents=True, exist_ok=True)
    files = [
        "libpelorus/include/pelorus/interop.h",
        "libpelorus/include/pelorus/pelorus.h",
        "libpelorus/src/interop.c",
    ]
    proc = subprocess.run(  # nosec B603 - fixed git argv, no shell, explicit timeout  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - argv is a list without shell, explicit timeout
        ["git", "archive", tag, *files],
        cwd=repo_root,
        capture_output=True,
        timeout=GIT_TIMEOUT_S,
        check=True,
    )
    import tarfile
    from io import BytesIO

    with tarfile.open(fileobj=BytesIO(proc.stdout), mode="r:") as tf:
        tf.extractall(path=dst, filter="data")
    return dst


def detect_abi_minor(interop_h: Path) -> int:
    """Parse PELORUS_ABI_MINOR value from interop.h."""
    content = interop_h.read_text(encoding="utf-8")
    m = ABI_MINOR_RE.search(content)
    if not m:
        raise ValueError(f"Could not find PELORUS_ABI_MINOR in {interop_h}")
    return int(m.group(1))


_SAFE_OPT_RE = re.compile(r"^[A-Za-z0-9_./+-]+$")


def get_cc() -> list[str]:
    """Resolve C compiler tokens from environment CC or default to cc."""
    raw = os.environ.get("CC", "cc").strip()
    if not raw:
        return ["cc"]
    tokens = raw.split()
    executable = shutil.which(tokens[0])
    if executable is None:
        raise RuntimeError(f"Compiler executable {tokens[0]!r} not found on PATH")
    if not Path(executable).is_file():
        raise RuntimeError(f"Compiler executable {executable!r} is not a regular file")
    for tok in tokens[1:]:
        if not _SAFE_OPT_RE.fullmatch(tok) or tok.startswith("-o"):
            raise RuntimeError(f"Refusing suspicious CC option token {tok!r}")
    return [executable, *tokens[1:]]


def _run_compiler(cmd: list[str]) -> str:
    """Execute compiler command list and return standard error string."""
    proc = subprocess.run(  # nosec B603 - compiler command list without shell, explicit timeout  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - argv is a list without shell, explicit timeout
        cmd,  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-tainted-env-args.dangerous-subprocess-use-tainted-env-args - CC resolved to an executable regular file, further tokens allow-listed options, list argv, no shell, explicit deadline
        capture_output=True,
        text=True,
        timeout=COMPILER_TIMEOUT_S,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"Compilation failed (rc={proc.returncode}):\nCommand: {' '.join(cmd)}\n{proc.stderr}"
        )
    return proc.stderr


def compile_binary(
    cc: list[str],
    inc_dir: Path,
    src_c: Path,
    abi_sources: tuple[Path, ...],
    out_bin: Path,
    helper_dir: Path,
    tag: str | None = None,
) -> None:
    """Compile objects and link abi_history binary."""
    abi_objs: list[Path] = []
    for src in abi_sources:
        obj = out_bin.parent / f"{src.stem}.o"
        cmd_abi = [
            *cc,
            "-std=c11",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-I",
            str(inc_dir),
            "-I",
            str(helper_dir),
            "-c",
            str(src),
            "-o",
            str(obj),
        ]
        _run_compiler(cmd_abi)
        abi_objs.append(obj)

    interop_obj = out_bin.parent / "interop.o"
    cmd_interop = [*cc, "-std=c11", "-Wall", "-Wextra"]
    if tag is None:
        cmd_interop.append("-Werror")
    cmd_interop.extend(["-I", str(inc_dir), "-c", str(src_c), "-o", str(interop_obj)])
    stderr = _run_compiler(cmd_interop)
    if tag is not None:
        warns = [line for line in stderr.splitlines() if line.strip()]
        if warns:
            print(
                f"note: {tag} interop.c: {len(warns)} compiler warning lines (immutable release source, not fatal)"
            )
    cmd_link = [
        *cc,
        *(str(obj) for obj in abi_objs),
        str(interop_obj),
        "-o",
        str(out_bin),
    ]
    _run_compiler(cmd_link)


def build_tag_binaries(
    repo_root: Path, tags: list[str], temp_dir: Path
) -> dict[str, tuple[Path, int]]:
    """Build abi_history binary for every tag and HEAD; return mapping to (binary, minor)."""
    cc = get_cc()
    abi_dir = repo_root / "libpelorus" / "test" / "abi_history"
    abi_sources = tuple(abi_dir / name for name in ABI_HISTORY_SOURCES)
    results: dict[str, tuple[Path, int]] = {}

    for tag in tags:
        tag_dir = extract_tag_sources(repo_root, tag, temp_dir)
        inc_dir = tag_dir / "libpelorus" / "include"
        src_c = tag_dir / "libpelorus" / "src" / "interop.c"
        out_bin = tag_dir / "abi_history"
        compile_binary(cc, inc_dir, src_c, abi_sources, out_bin, abi_dir, tag=tag)
        minor = detect_abi_minor(inc_dir / "pelorus" / "interop.h")
        results[tag] = (out_bin, minor)

    head_dir = temp_dir / "head"
    head_dir.mkdir(parents=True, exist_ok=True)
    head_bin = head_dir / "abi_history"
    compile_binary(
        cc,
        repo_root / "libpelorus" / "include",
        repo_root / "libpelorus" / "src" / "interop.c",
        abi_sources,
        head_bin,
        abi_dir,
        tag=None,
    )
    head_minor = detect_abi_minor(
        repo_root / "libpelorus" / "include" / "pelorus" / "interop.h"
    )
    results["HEAD"] = (head_bin, head_minor)
    return results


def emit_blob(bin_path: Path, form: str) -> bytes:
    """Run binary emit <form> and return raw bytes."""
    res = subprocess.run(  # nosec B603 - test binary execution without shell, explicit timeout  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - argv is a list without shell, explicit timeout
        [str(bin_path), "emit", form],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    return bytes.fromhex(res.stdout.strip())


def compute_expected_fixtures(
    binaries: dict[str, tuple[Path, int]],
) -> dict[str, dict[str, Any]]:
    """Compute expected fixtures for all built writers grouped by minor version."""
    minor_writers: dict[int, list[str]] = {}
    for name, (_, minor) in binaries.items():
        minor_writers.setdefault(minor, []).append(name)

    expected: dict[str, dict[str, Any]] = {}
    for minor, writers in sorted(minor_writers.items()):
        rep_writer = writers[0]
        rep_bin, _ = binaries[rep_writer]

        plain_bytes = emit_blob(rep_bin, "plain")
        plain_sha = hashlib.sha256(plain_bytes).hexdigest()
        plain_name = fixture_name(minor, "plain")
        expected[plain_name] = {
            "abi_minor": minor,
            "form": "plain",
            "bytes": plain_bytes,
            "sha256": plain_sha,
            "writers": writers,
        }

        if minor >= 5:
            carrier_bytes = emit_blob(rep_bin, "carrier")
            carrier_sha = hashlib.sha256(carrier_bytes).hexdigest()
            carrier_name = fixture_name(minor, "carrier")
            expected[carrier_name] = {
                "abi_minor": minor,
                "form": "carrier",
                "bytes": carrier_bytes,
                "sha256": carrier_sha,
                "writers": writers,
            }

        for other in writers[1:]:
            other_bin, _ = binaries[other]
            if emit_blob(other_bin, "plain") != plain_bytes:
                raise RuntimeError(
                    f"Writer {other} emitted differing plain bytes for minor {minor}"
                )
            if minor >= 5 and emit_blob(other_bin, "carrier") != carrier_bytes:
                raise RuntimeError(
                    f"Writer {other} emitted differing carrier bytes for minor {minor}"
                )

    return expected


def fixture_name(minor: int, form: str) -> str:
    """Return standard filename for an ABI fixture given minor and form."""
    return f"abi-1.{minor}-carrier.bin" if form == "carrier" else f"abi-1.{minor}.bin"


def _find_stale_fixture_files(
    fixtures_dir: Path, expected_fixtures: set[str]
) -> list[str]:
    """Find fixture files on disk that no writer produces in this run."""
    return sorted(
        p.name
        for p in fixtures_dir.iterdir()
        if p.is_file() and p.name.endswith(".bin") and p.name not in expected_fixtures
    )


def _verify_stale_is_head_only(
    stale: list[str], writers_by_name: dict[str, list[str]]
) -> None:
    """Verify stale files are recorded in manifest and produced only by HEAD."""
    for name in stale:
        if name not in writers_by_name:
            raise RuntimeError(
                f"{name} is not in manifest.json; --write deletes only files it recorded as never released"
            )
        if writers_by_name[name] != ["HEAD"]:
            raise RuntimeError(
                f"{name} was written by {writers_by_name[name]}; released fixtures never leave (ADR-0247). Fetch full tag history (git fetch --tags) and rerun"
            )


def _removable_stale_fixtures(
    fixtures_dir: Path, expected_fixtures: dict[str, dict[str, Any]]
) -> list[str]:
    """Return the fixture files --write may delete; raise before any change otherwise."""
    stale = _find_stale_fixture_files(fixtures_dir, set(expected_fixtures.keys()))
    if not stale:
        return stale
    manifest_path = fixtures_dir / "manifest.json"
    rows = load_manifest_strict(manifest_path) if manifest_path.is_file() else []
    writers_by_name = {
        fixture_name(row["abi_minor"], row["form"]): row["writers"] for row in rows
    }
    _verify_stale_is_head_only(stale, writers_by_name)
    return stale


def _check_append_only(
    fixtures_dir: Path, expected_fixtures: dict[str, dict[str, Any]]
) -> None:
    """Raise when an existing fixture's bytes differ from the writer output."""
    for filename, meta in sorted(expected_fixtures.items()):
        file_path = fixtures_dir / filename
        if file_path.is_file() and file_path.read_bytes() != meta["bytes"]:
            raise RuntimeError(
                f"{filename} differs from writer output; persisted fixtures are append-only (ADR-0247)"
            )


def write_fixtures(
    fixtures_dir: Path, expected_fixtures: dict[str, dict[str, Any]]
) -> None:
    """Write fixtures and manifest to fixtures_dir under append-only rules."""
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    _check_append_only(fixtures_dir, expected_fixtures)
    removable = _removable_stale_fixtures(fixtures_dir, expected_fixtures)

    for filename in removable:
        (fixtures_dir / filename).unlink()
        print(f"removed {filename} (never released, superseded)")

    manifest_rows = []
    for filename, meta in sorted(expected_fixtures.items()):
        file_path = fixtures_dir / filename
        already_existed = file_path.is_file()
        file_path.write_bytes(meta["bytes"])
        action = "kept" if already_existed else "wrote"
        print(f"{action} {filename}")
        manifest_rows.append(
            {
                "abi_minor": meta["abi_minor"],
                "form": meta["form"],
                "writers": meta["writers"],
                "sha256": meta["sha256"],
            }
        )

    manifest_path = fixtures_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest_rows, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Wrote manifest.json ({len(manifest_rows)} rows)")


def run_self_test(
    binaries: dict[str, tuple[Path, int]],
    expected_fixtures: dict[str, dict[str, Any]],
    all_built_writers: dict[str, int],
) -> None:
    """Run self-test with planted negative cases on temporary copies."""
    run_self_test_impl(
        binaries=binaries,
        expected_fixtures=expected_fixtures,
        all_built_writers=all_built_writers,
        write_fixtures_fn=write_fixtures,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments using argparse with mutually exclusive modes."""
    parser = argparse.ArgumentParser(
        description="Verify or write interop ABI history fixtures across release tags."
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--write",
        action="store_true",
        help="Write/update fixtures and manifest.json from built binaries",
    )
    mode_group.add_argument(
        "--self-test",
        action="store_true",
        help="Run self-tests against planted negative cases and exits",
    )
    parser.add_argument(
        "--fixtures-dir",
        type=Path,
        default=None,
        help="Directory containing fixture files and manifest.json",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    args = parse_args(argv)
    repo_root = get_repo_root()
    fixtures_dir = (
        args.fixtures_dir
        if args.fixtures_dir is not None
        else repo_root / ROOT_FIXTURES_DIR
    )

    tags = get_git_tags(repo_root)

    import tempfile

    with tempfile.TemporaryDirectory() as td:
        temp_dir = Path(td)
        binaries = build_tag_binaries(repo_root, tags, temp_dir)
        expected_fixtures = compute_expected_fixtures(binaries)
        all_built_writers = {name: minor for name, (_, minor) in binaries.items()}

        try:
            if args.self_test:
                run_self_test(binaries, expected_fixtures, all_built_writers)
                return 0

            if args.write:
                write_fixtures(fixtures_dir, expected_fixtures)
                return 0

            verify_fixtures_and_manifest(
                fixtures_dir, expected_fixtures, all_built_writers
            )
            run_reader_matrix(binaries, fixtures_dir)
            print(
                "All released writers, readers, and persisted fixtures verified successfully."
            )
            return 0
        except (
            RuntimeError,
            ValueError,
            SelfTestError,
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
        ) as err:
            print(f"Error: {err}", file=sys.stderr)
            return 1


if __name__ == "__main__":
    sys.exit(main())
