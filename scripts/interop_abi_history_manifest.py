# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Manifest verification, duplicate detection, and reader matrix runner for ABI history."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess  # nosec B404 - fixed reader argv, no shell, explicit timeout
import sys
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


def _refuse_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Refuse duplicate JSON object keys to prevent accidental overriding."""
    res: dict[str, Any] = {}
    for k, v in pairs:
        if k in res:
            raise ValueError(f"Duplicate JSON key: {k!r}")
        res[k] = v
    return res


def _validate_row_writers(idx: int, writers: Any) -> None:
    """Validate that writers in a manifest row is a non-empty list of strings."""
    if not isinstance(writers, list) or len(writers) == 0:
        raise ValueError(f"row {idx}: invalid writers: {writers!r}")
    if not all(isinstance(w, str) for w in writers):
        raise ValueError(f"row {idx}: invalid writers: {writers!r}")


def _validate_row_sha256(idx: int, sha: Any) -> None:
    """Validate that sha256 in a manifest row is a 64-character hex string."""
    if not isinstance(sha, str) or len(sha) != 64:
        raise ValueError(f"row {idx}: invalid sha256: {sha!r}")
    if not all(c in "0123456789abcdef" for c in sha):
        raise ValueError(f"row {idx}: invalid sha256: {sha!r}")


def _validate_manifest_row(idx: int, row: Any) -> None:
    """Validate a single row in manifest.json."""
    if not isinstance(row, dict):
        raise ValueError(f"row {idx}: not a JSON object")
    allowed = {"abi_minor", "form", "writers", "sha256"}
    if set(row.keys()) != allowed:
        raise ValueError(f"row {idx}: invalid keys: {sorted(row.keys())}")
    minor = row["abi_minor"]
    if not isinstance(minor, int) or isinstance(minor, bool) or minor < 0:
        raise ValueError(f"row {idx}: invalid abi_minor: {minor!r}")
    if row["form"] not in ("plain", "carrier"):
        raise ValueError(f"row {idx}: invalid form: {row['form']!r}")
    _validate_row_writers(idx, row["writers"])
    _validate_row_sha256(idx, row["sha256"])


def load_manifest_strict(manifest_path: Path) -> list[dict[str, Any]]:
    """Load manifest.json with strict validation against duplicate keys and schema."""
    if not manifest_path.is_file():
        raise RuntimeError(f"Missing manifest: {manifest_path}")
    with open(manifest_path, encoding="utf-8") as f:
        data = json.load(f, object_pairs_hook=_refuse_duplicate_pairs)
    if not isinstance(data, list):
        raise ValueError("manifest.json must be a JSON list")
    for idx, row in enumerate(data):
        _validate_manifest_row(idx, row)
    return data


def _check_fixture_files_and_manifest(
    fixtures_dir: Path,
    expected_fixtures: dict[str, dict[str, Any]],
    manifest_map: dict[tuple[int, str], dict[str, Any]],
) -> list[str]:
    """Check fixture files and hashes against manifest rows, collecting notes."""
    notes: list[str] = []
    for filename, expected in expected_fixtures.items():
        file_path = fixtures_dir / filename
        if not file_path.is_file():
            raise RuntimeError(f"Missing fixture file: {file_path}")
        if file_path.read_bytes() != expected["bytes"]:
            raise RuntimeError(
                f"Fixture {filename} does not match expected writer bytes"
            )
        key = (expected["abi_minor"], expected["form"])
        if key not in manifest_map:
            raise RuntimeError(f"Fixture {filename} missing from manifest.json")
        man_row = manifest_map[key]
        if man_row["sha256"] != expected["sha256"]:
            raise RuntimeError(f"Manifest sha256 mismatch for {filename}")
        man_writers = set(man_row["writers"])
        for w in expected["writers"]:
            if w not in man_writers:
                msg = (
                    f"note: {w} writes abi-1.{expected['abi_minor']} "
                    f"({expected['form']}) but manifest.json does not list it; next --write adds it"
                )
                notes.append(msg)
                print(msg)
    return notes


def _build_manifest_map(
    manifest_data: list[dict[str, Any]],
    all_built_writers: dict[str, int],
) -> dict[tuple[int, str], dict[str, Any]]:
    """Build unique minor/form map from manifest and verify declared writers."""
    manifest_map: dict[tuple[int, str], dict[str, Any]] = {}
    for idx, row in enumerate(manifest_data):
        key = (row["abi_minor"], row["form"])
        if key in manifest_map:
            raise ValueError(f"row {idx}: duplicate entry for minor {key[0]} {key[1]}")
        manifest_map[key] = row
        for w in row["writers"]:
            if w not in all_built_writers:
                raise RuntimeError(
                    f"Writer {w} listed for minor {row['abi_minor']} is unknown or does not exist"
                )
            built_m = all_built_writers[w]
            if built_m != row["abi_minor"]:
                raise RuntimeError(
                    f"Writer {w} listed for minor {row['abi_minor']} built to minor {built_m}"
                )
    return manifest_map


def _check_orphan_fixtures(
    fixtures_dir: Path,
    expected_fixtures: dict[str, dict[str, Any]],
) -> None:
    """Raise error if unbuilt orphan fixture files exist on disk."""
    for p in fixtures_dir.iterdir():
        if p.is_file() and p.name.endswith(".bin") and p.name not in expected_fixtures:
            raise RuntimeError(
                f"Orphan fixture file with no built writer: {p.name}; no release tag "
                "or HEAD writes it. A never-released minor's fixture leaves with the "
                "ABI bump that supersedes it; a released minor's fixture never leaves "
                "(ADR-0247)"
            )


def _check_orphan_manifest_rows(
    manifest_data: list[dict[str, Any]],
    expected_fixtures: dict[str, dict[str, Any]],
) -> None:
    """Raise error if manifest rows exist for which no writer was built."""
    expected_keys = {(m["abi_minor"], m["form"]) for m in expected_fixtures.values()}
    for row in manifest_data:
        if (row["abi_minor"], row["form"]) not in expected_keys:
            raise RuntimeError(
                f"Orphan manifest row with no built writer: minor {row['abi_minor']} ({row['form']})"
            )


def verify_fixtures_and_manifest(
    fixtures_dir: Path,
    expected_fixtures: dict[str, dict[str, Any]],
    all_built_writers: dict[str, int],
) -> list[str]:
    """Verify committed files match expected fixtures and manifest.json."""
    manifest_data = load_manifest_strict(fixtures_dir / "manifest.json")
    manifest_map = _build_manifest_map(manifest_data, all_built_writers)
    notes = _check_fixture_files_and_manifest(
        fixtures_dir, expected_fixtures, manifest_map
    )
    _check_orphan_fixtures(fixtures_dir, expected_fixtures)
    _check_orphan_manifest_rows(manifest_data, expected_fixtures)
    return notes


def _run_one_reader(r_bin: Path, r_name: str, f_path: Path, print_table: bool) -> int:
    """Execute one reader binary against a fixture file and return exit code."""
    res = subprocess.run(  # nosec B603 - test binary execution without shell, explicit timeout  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - argv is a list without shell, explicit timeout
        [str(r_bin), "read", str(f_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if print_table:
        print(f"{r_name:<14} | {f_path.name:<22} | {res.returncode:<3}")
    if res.returncode != 0:
        if print_table:
            print(f"Reader failure details ({r_name} on {f_path.name}):\n{res.stderr}")
        raise RuntimeError(f"Reader check failed for {r_name} on {f_path.name}")
    return res.returncode


def _find_fixture_files(fixtures_dir: Path) -> list[Path]:
    """Find all .bin fixture files under fixtures_dir in sorted order."""
    fixture_files = sorted(
        [p for p in fixtures_dir.iterdir() if p.is_file() and p.name.endswith(".bin")]
    )
    if not fixture_files:
        raise RuntimeError(f"No .bin fixtures found in {fixtures_dir}")
    return fixture_files


def run_reader_matrix(
    binaries: dict[str, tuple[Path, int]],
    fixtures_dir: Path,
    print_table: bool = True,
) -> list[tuple[str, str, int]]:
    """Run every reader binary across every fixture in fixtures_dir."""
    fixture_files = _find_fixture_files(fixtures_dir)
    results: list[tuple[str, str, int]] = []
    if print_table:
        print("Reader x Fixture verification matrix:")
        print(f"{'Reader':<14} | {'Fixture':<22} | {'rc':<3}")
        print("-" * 45)
    for r_name in sorted(binaries.keys()):
        r_bin, _ = binaries[r_name]
        for f_path in fixture_files:
            rc = _run_one_reader(r_bin, r_name, f_path, print_table)
            results.append((r_name, f_path.name, rc))
    return results
