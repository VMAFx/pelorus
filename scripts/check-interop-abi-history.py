#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Check interop ABI history fixtures against released writers and readers.

Rebuilds the interop test binary against every release tag in Git history
and against the current tree, checks that released writers produce the
persisted fixtures under libpelorus/test/fixtures/interop-history/, and
executes every released and current reader across every persisted fixture.

Usage:
    python3 -I scripts/check-interop-abi-history.py [--write] [--self-test]
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess  # nosec B404
import sys
import tarfile
import tempfile
from typing import Any

TAG_PATTERN = re.compile(r"^v\d+\.\d+\.\d+(-rc\.\d+)?$")
ARCHIVE_PATHS = (
    "libpelorus/include/pelorus/interop.h",
    "libpelorus/include/pelorus/pelorus.h",
    "libpelorus/src/interop.c",
)


def get_repo_root() -> Path:
    """Return the repository root directory."""
    return Path(__file__).resolve().parent.parent


def get_git_tags(repo_root: Path) -> list[str]:
    """Return filtered release tags sorted lexically."""
    proc = subprocess.run(
        ["git", "-C", str(repo_root), "tag", "-l"],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    tags = [
        line.strip()
        for line in proc.stdout.splitlines()
        if TAG_PATTERN.fullmatch(line.strip())
    ]
    if not tags:
        raise RuntimeError("Zero release tags found (shallow clone or missing tags)")
    return sorted(tags)


def extract_tag_sources(repo_root: Path, tag: str, dest_dir: Path) -> Path:
    """Extract required interop sources for tag into dest_dir."""
    proc = subprocess.run(
        ["git", "-C", str(repo_root), "archive", tag, *ARCHIVE_PATHS],
        capture_output=True,
        check=True,
        timeout=30,
    )
    tag_root = dest_dir / tag
    tag_root.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as tf:
        for member in tf.getmembers():
            if not member.isfile() or member.name not in ARCHIVE_PATHS:
                continue
            if ".." in member.name:
                raise ValueError(f"Unsafe path in archive: {member.name}")
            target = tag_root / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            f_in = tf.extractfile(member)
            if f_in is not None:
                with open(target, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
    return tag_root


def compile_binary(
    cc: str, inc_dir: Path, src_c: Path, abi_c: Path, out_bin: Path, helper_dir: Path
) -> None:
    """Compile abi_history binary with strict flags."""
    cmd = [
        cc,
        "-std=c11",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-I",
        str(inc_dir),
        "-I",
        str(helper_dir),
        str(src_c),
        str(abi_c),
        "-o",
        str(out_bin),
    ]
    subprocess.run(cmd, check=True, timeout=60, capture_output=True)


def build_tag_binaries(
    repo_root: Path, tags: list[str], temp_dir: Path
) -> dict[str, tuple[Path, int]]:
    """Build abi_history binary for every tag and HEAD; return mapping to (binary, minor)."""
    cc = os.environ.get("CC", "cc")
    abi_c = repo_root / "libpelorus" / "test" / "abi_history" / "abi_history.c"
    helper_dir = repo_root / "libpelorus" / "test" / "abi_history"
    results: dict[str, tuple[Path, int]] = {}

    for tag in tags:
        tag_dir = extract_tag_sources(repo_root, tag, temp_dir)
        inc_dir = tag_dir / "libpelorus" / "include"
        src_c = tag_dir / "libpelorus" / "src" / "interop.c"
        out_bin = tag_dir / "abi_history"
        compile_binary(cc, inc_dir, src_c, abi_c, out_bin, helper_dir)
        minor = detect_abi_minor(inc_dir / "pelorus" / "interop.h")
        results[tag] = (out_bin, minor)

    head_dir = temp_dir / "head"
    head_dir.mkdir(parents=True, exist_ok=True)
    head_bin = head_dir / "abi_history"
    compile_binary(
        cc,
        repo_root / "libpelorus" / "include",
        repo_root / "libpelorus" / "src" / "interop.c",
        abi_c,
        head_bin,
        helper_dir,
    )
    head_minor = detect_abi_minor(
        repo_root / "libpelorus" / "include" / "pelorus" / "interop.h"
    )
    results["HEAD"] = (head_bin, head_minor)
    return results


def detect_abi_minor(header_path: Path) -> int:
    """Read PELORUS_ABI_MINOR from interop.h."""
    with open(header_path, encoding="utf-8") as f:
        for line in f:
            m = re.match(r"^#define\s+PELORUS_ABI_MINOR\s+(\d+)u?$", line.strip())
            if m:
                return int(m.group(1))
    raise ValueError(f"PELORUS_ABI_MINOR not found in {header_path}")


def emit_blob(bin_path: Path, form: str) -> bytes:
    """Run binary emit <form> and return raw bytes."""
    res = subprocess.run(
        [str(bin_path), "emit", form], capture_output=True, text=True, timeout=30
    )
    if res.returncode != 0:
        raise RuntimeError(f"emit {form} failed for {bin_path}: {res.stderr}")
    return bytes.fromhex(res.stdout.strip())


def compute_expected_fixtures(
    binaries: dict[str, tuple[Path, int]],
) -> dict[str, dict[str, Any]]:
    """Compute expected fixtures from tag/HEAD binaries, verifying intra-minor consistency."""
    minor_to_tags: dict[int, list[str]] = {}
    for name, (_, minor) in binaries.items():
        minor_to_tags.setdefault(minor, []).append(name)

    fixtures: dict[str, dict[str, Any]] = {}
    for minor, writers in sorted(minor_to_tags.items()):
        # Check plain form
        first_writer = writers[0]
        plain_bytes = emit_blob(binaries[first_writer][0], "plain")
        for other in writers[1:]:
            other_bytes = emit_blob(binaries[other][0], "plain")
            if other_bytes != plain_bytes:
                raise RuntimeError(
                    f"Tags disagree on ABI minor {minor} plain blob: {first_writer} vs {other}"
                )
        plain_name = f"abi-1.{minor}.bin"
        fixtures[plain_name] = {
            "abi_minor": minor,
            "form": "plain",
            "writers": sorted(writers),
            "bytes": plain_bytes,
            "sha256": hashlib.sha256(plain_bytes).hexdigest(),
        }

        # Check carrier form if minor >= 5
        if minor >= 5:
            carrier_bytes = emit_blob(binaries[first_writer][0], "carrier")
            for other in writers[1:]:
                other_bytes = emit_blob(binaries[other][0], "carrier")
                if other_bytes != carrier_bytes:
                    raise RuntimeError(
                        f"Tags disagree on ABI minor {minor} carrier blob: {first_writer} vs {other}"
                    )
            carrier_name = f"abi-1.{minor}-carrier.bin"
            fixtures[carrier_name] = {
                "abi_minor": minor,
                "form": "carrier",
                "writers": sorted(writers),
                "bytes": carrier_bytes,
                "sha256": hashlib.sha256(carrier_bytes).hexdigest(),
            }
    return fixtures


def write_fixtures(fixtures_dir: Path, fixtures: dict[str, dict[str, Any]]) -> None:
    """Write binary fixtures and manifest.json to fixtures_dir."""
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    manifest_rows = []
    for filename, meta in sorted(fixtures.items()):
        file_path = fixtures_dir / filename
        with open(file_path, "wb") as f:
            f.write(meta["bytes"])
        manifest_rows.append(
            {
                "abi_minor": meta["abi_minor"],
                "form": meta["form"],
                "writers": meta["writers"],
                "sha256": meta["sha256"],
            }
        )
    manifest_path = fixtures_dir / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest_rows, f, indent=2)
        f.write("\n")
    print(f"Wrote {len(fixtures)} fixtures and manifest to {fixtures_dir}")


def verify_fixtures_and_manifest(
    fixtures_dir: Path,
    expected_fixtures: dict[str, dict[str, Any]],
    all_known_writers: set[str],
) -> None:
    """Verify committed files match expected fixtures and manifest.json."""
    manifest_path = fixtures_dir / "manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError(f"Missing manifest: {manifest_path}")

    with open(manifest_path, encoding="utf-8") as f:
        manifest_data = json.load(f)

    if not isinstance(manifest_data, list):
        raise ValueError("manifest.json must be a JSON list")

    manifest_map: dict[tuple[int, str], dict[str, Any]] = {}
    for entry in manifest_data:
        key = (entry["abi_minor"], entry["form"])
        if key in manifest_map:
            raise ValueError(f"Duplicate entry in manifest: {key}")
        manifest_map[key] = entry
        for w in entry.get("writers", []):
            if w not in all_known_writers:
                raise RuntimeError(f"Manifest names unknown tag or writer: {w}")

    for filename, expected in expected_fixtures.items():
        file_path = fixtures_dir / filename
        if not file_path.is_file():
            raise RuntimeError(f"Missing committed fixture: {file_path}")
        with open(file_path, "rb") as f:
            content = f.read()
        if content != expected["bytes"]:
            raise RuntimeError(
                f"Committed fixture {filename} does not match expected writer bytes"
            )
        key = (expected["abi_minor"], expected["form"])
        if key not in manifest_map:
            raise RuntimeError(f"Fixture {filename} missing from manifest.json")
        man_entry = manifest_map[key]
        if man_entry["sha256"] != expected["sha256"]:
            raise RuntimeError(f"Manifest sha256 mismatch for {filename}")
        if set(man_entry.get("writers", [])) != set(expected["writers"]):
            raise RuntimeError(f"Manifest writers mismatch for {filename}")


def run_reader_matrix(
    binaries: dict[str, tuple[Path, int]], fixtures_dir: Path
) -> list[tuple[str, str, int]]:
    """Run every reader binary across every fixture in fixtures_dir."""
    fixture_files = sorted(
        [p for p in fixtures_dir.iterdir() if p.is_file() and p.name.endswith(".bin")]
    )
    if not fixture_files:
        raise RuntimeError(f"No .bin fixtures found in {fixtures_dir}")

    results = []
    print("Reader x Fixture verification matrix:")
    print(f"{'Reader':<14} | {'Fixture':<22} | {'rc':<3}")
    print("-" * 45)
    for r_name in sorted(binaries.keys()):
        r_bin, _ = binaries[r_name]
        for f_path in fixture_files:
            res = subprocess.run(
                [str(r_bin), "read", str(f_path)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            results.append((r_name, f_path.name, res.returncode))
            print(f"{r_name:<14} | {f_path.name:<22} | {res.returncode:<3}")
            if res.returncode != 0:
                print(
                    f"Reader failure details ({r_name} on {f_path.name}):\n{res.stderr}"
                )
                raise RuntimeError(f"Reader check failed for {r_name} on {f_path.name}")
    return results


def _test_planted_fixture_edits(
    tf_dir: Path,
    expected_fixtures: dict[str, dict[str, Any]],
    known_writers: set[str],
) -> None:
    """Verify planted mutations on fixtures and manifest are rejected."""
    # Plant 1: flipped byte in a fixture file
    plant1_file = tf_dir / "abi-1.0.bin"
    with open(plant1_file, "r+b") as f:
        data = bytearray(f.read())
        data[64] ^= 0x01
        f.seek(0)
        f.write(data)
    try:
        verify_fixtures_and_manifest(tf_dir, expected_fixtures, known_writers)
        raise RuntimeError("Self-test plant 1 (flipped byte) unexpectedly passed")
    except RuntimeError:
        pass  # Expected failure
    write_fixtures(tf_dir, expected_fixtures)

    # Plant 2: removed fixture file
    plant2_file = tf_dir / "abi-1.3.bin"
    plant2_file.unlink()
    try:
        verify_fixtures_and_manifest(tf_dir, expected_fixtures, known_writers)
        raise RuntimeError("Self-test plant 2 (removed fixture) unexpectedly passed")
    except RuntimeError:
        pass  # Expected failure
    write_fixtures(tf_dir, expected_fixtures)

    # Plant 3: manifest names unknown tag
    man_path = tf_dir / "manifest.json"
    with open(man_path, encoding="utf-8") as f:
        mdata = json.load(f)
    mdata[0]["writers"].append("v99.99.99")
    with open(man_path, "w", encoding="utf-8") as f:
        json.dump(mdata, f, indent=2)
    try:
        verify_fixtures_and_manifest(tf_dir, expected_fixtures, known_writers)
        raise RuntimeError(
            "Self-test plant 3 (unknown tag in manifest) unexpectedly passed"
        )
    except RuntimeError:
        pass  # Expected failure


def run_self_test(
    repo_root: Path,
    binaries: dict[str, tuple[Path, int]],
    expected_fixtures: dict[str, dict[str, Any]],
    known_writers: set[str],
) -> None:
    """Run self-test with planted negative cases on temporary copies."""
    print("Running self-test (planted negative cases)...")
    with tempfile.TemporaryDirectory() as td:
        tf_dir = Path(td) / "fixtures"
        write_fixtures(tf_dir, expected_fixtures)

        # Baseline clean check must pass
        verify_fixtures_and_manifest(tf_dir, expected_fixtures, known_writers)

        # Plant 1-3: fixture and manifest mutations
        _test_planted_fixture_edits(tf_dir, expected_fixtures, known_writers)

        # Plant 4: reader with --flip flag on clean fixture must fail (rc 1)
        head_bin = binaries["HEAD"][0]
        clean_fix = (
            repo_root
            / "libpelorus"
            / "test"
            / "fixtures"
            / "interop-history"
            / "abi-1.0.bin"
        )
        if not clean_fix.is_file():
            clean_fix = tf_dir / "abi-1.0.bin"
            write_fixtures(tf_dir, expected_fixtures)
        flip_res = subprocess.run(
            [str(head_bin), "read", str(clean_fix), "--flip", "144"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if flip_res.returncode != 1:
            raise RuntimeError(
                f"Self-test plant 4 (reader --flip) returned rc={flip_res.returncode}, expected 1"
            )

    print("Self-test passed: all 4 planted negative cases failed as expected.")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments using argparse."""
    parser = argparse.ArgumentParser(
        description="Verify interop ABI fixtures against released writers and readers."
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Regenerate persisted fixtures and manifest.json from writers",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run self-test with planted negative cases on temp copies",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Main entry point."""
    if sys.platform in ("win32", "cygwin"):
        print(
            "skip: check-interop-abi-history requires POSIX C compiler and git archive (HISS-21)"
        )
        return 0

    args = parse_args(argv)
    repo_root = get_repo_root()
    tags = get_git_tags(repo_root)
    fixtures_dir = repo_root / "libpelorus" / "test" / "fixtures" / "interop-history"

    with tempfile.TemporaryDirectory() as td:
        temp_dir = Path(td)
        binaries = build_tag_binaries(repo_root, tags, temp_dir)
        expected_fixtures = compute_expected_fixtures(binaries)
        known_writers = set(tags) | {"HEAD"}

        if args.write:
            write_fixtures(fixtures_dir, expected_fixtures)
            return 0

        if args.self_test:
            run_self_test(repo_root, binaries, expected_fixtures, known_writers)
            return 0

        verify_fixtures_and_manifest(fixtures_dir, expected_fixtures, known_writers)
        run_reader_matrix(binaries, fixtures_dir)

    print(
        "All released writers, readers, and persisted fixtures verified successfully."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
