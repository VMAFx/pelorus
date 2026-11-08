#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Check interop ABI history fixtures against released writers and readers.

Rebuilds the interop test binary against every release tag in Git history
and against the current tree, checks that released writers produce the
persisted fixtures under libpelorus/test/fixtures/interop-history/, and
executes every released and current reader across every persisted fixture.

Usage:
    python3 -I scripts/check-interop-abi-history.py [--write | --self-test]
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess  # nosec B404 - fixed git and compiler argv, no shell, explicit timeout
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


class SelfTestError(Exception):
    """Raised when a self-test plant unexpectedly passes or assertion fails."""


def get_repo_root() -> Path:
    """Return the repository root directory."""
    return Path(__file__).resolve().parent.parent


def get_git_tags(repo_root: Path) -> list[str]:
    """Return filtered release tags sorted lexically."""
    proc = subprocess.run(  # nosec B603 - fixed git argv, no shell, explicit timeout  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - argv is a list without shell, explicit timeout
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
    proc = subprocess.run(  # nosec B603 - fixed git argv, no shell, explicit timeout  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - argv is a list without shell, explicit timeout
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


def detect_abi_minor(header_path: Path) -> int:
    """Read PELORUS_ABI_MINOR from interop.h."""
    with open(header_path, encoding="utf-8") as f:
        for line in f:
            m = re.match(r"^#define\s+PELORUS_ABI_MINOR\s+(\d+)u?$", line.strip())
            if m:
                return int(m.group(1))
    raise ValueError(f"PELORUS_ABI_MINOR not found in {header_path}")


def get_cc() -> list[str]:
    """Return split compiler command from CC environment variable."""
    raw = os.environ.get("CC", "cc")
    cc = shlex.split(raw)
    if not cc:
        raise RuntimeError("CC environment variable is empty")
    return cc


def _run_compiler(cmd: list[str]) -> str:
    """Run compiler command; raise RuntimeError with stderr on failure."""
    proc = subprocess.run(  # nosec B603 - compiler command list without shell, explicit timeout  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - argv is a list without shell, explicit timeout
        cmd, capture_output=True, text=True, timeout=60
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"Compiler command failed (rc={proc.returncode}): {' '.join(cmd)}\n{proc.stderr}"
        )
    return proc.stderr


def compile_binary(
    cc: list[str],
    inc_dir: Path,
    src_c: Path,
    abi_c: Path,
    out_bin: Path,
    helper_dir: Path,
    tag: str | None = None,
) -> None:
    """Compile two object files and link abi_history binary."""
    abi_obj = out_bin.parent / "abi_history.o"
    interop_obj = out_bin.parent / "interop.o"
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
        str(abi_c),
        "-o",
        str(abi_obj),
    ]
    _run_compiler(cmd_abi)
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
    cmd_link = [*cc, str(abi_obj), str(interop_obj), "-o", str(out_bin)]
    _run_compiler(cmd_link)


def build_tag_binaries(
    repo_root: Path, tags: list[str], temp_dir: Path
) -> dict[str, tuple[Path, int]]:
    """Build abi_history binary for every tag and HEAD; return mapping to (binary, minor)."""
    cc = get_cc()
    abi_c = repo_root / "libpelorus" / "test" / "abi_history" / "abi_history.c"
    helper_dir = repo_root / "libpelorus" / "test" / "abi_history"
    results: dict[str, tuple[Path, int]] = {}

    for tag in tags:
        tag_dir = extract_tag_sources(repo_root, tag, temp_dir)
        inc_dir = tag_dir / "libpelorus" / "include"
        src_c = tag_dir / "libpelorus" / "src" / "interop.c"
        out_bin = tag_dir / "abi_history"
        compile_binary(cc, inc_dir, src_c, abi_c, out_bin, helper_dir, tag=tag)
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


def write_fixtures(
    fixtures_dir: Path, expected_fixtures: dict[str, dict[str, Any]]
) -> None:
    """Write binary fixtures append-only and rewrite manifest.json from built writers."""
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    for filename, meta in sorted(expected_fixtures.items()):
        file_path = fixtures_dir / filename
        if file_path.is_file():
            existing = file_path.read_bytes()
            if existing != meta["bytes"]:
                raise RuntimeError(
                    f"{filename} differs from writer output; persisted fixtures are append-only (ADR-0247)"
                )

    for filename, meta in sorted(expected_fixtures.items()):
        file_path = fixtures_dir / filename
        if not file_path.is_file():
            file_path.write_bytes(meta["bytes"])
            print(f"wrote {filename}")
        else:
            print(f"kept {filename}")

    manifest_rows = []
    for _, meta in sorted(expected_fixtures.items()):
        manifest_rows.append(
            {
                "abi_minor": meta["abi_minor"],
                "form": meta["form"],
                "writers": sorted(meta["writers"]),
                "sha256": meta["sha256"],
            }
        )
    manifest_path = fixtures_dir / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest_rows, f, indent=2)
        f.write("\n")
    print(f"Wrote manifest.json ({len(manifest_rows)} rows)")


def _refuse_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Refuse duplicate keys in JSON object."""
    res: dict[str, Any] = {}
    for k, v in pairs:
        if k in res:
            raise ValueError(f"Duplicate JSON key: {k!r}")
        res[k] = v
    return res


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
    writers = row["writers"]
    if (
        not isinstance(writers, list)
        or len(writers) == 0
        or not all(isinstance(w, str) for w in writers)
    ):
        raise ValueError(f"row {idx}: invalid writers: {writers!r}")
    sha = row["sha256"]
    if (
        not isinstance(sha, str)
        or len(sha) != 64
        or not all(c in "0123456789abcdef" for c in sha)
    ):
        raise ValueError(f"row {idx}: invalid sha256: {sha!r}")


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


def verify_fixtures_and_manifest(
    fixtures_dir: Path,
    expected_fixtures: dict[str, dict[str, Any]],
    all_built_writers: dict[str, int],
) -> list[str]:
    """Verify committed files match expected fixtures and manifest.json."""
    manifest_data = load_manifest_strict(fixtures_dir / "manifest.json")
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

    notes = _check_fixture_files_and_manifest(
        fixtures_dir, expected_fixtures, manifest_map
    )

    for p in fixtures_dir.iterdir():
        if p.is_file() and p.name.endswith(".bin") and p.name not in expected_fixtures:
            raise RuntimeError(
                f"Orphan fixture file with no built writer: {p.name}; no release tag "
                "or HEAD writes it. A never-released minor's fixture leaves with the "
                "ABI bump that supersedes it; a released minor's fixture never leaves "
                "(ADR-0247)"
            )

    expected_keys = {(m["abi_minor"], m["form"]) for m in expected_fixtures.values()}
    for row in manifest_data:
        if (row["abi_minor"], row["form"]) not in expected_keys:
            raise RuntimeError(
                f"Orphan manifest row with no built writer: minor {row['abi_minor']} ({row['form']})"
            )
    return notes


def run_reader_matrix(
    binaries: dict[str, tuple[Path, int]],
    fixtures_dir: Path,
    print_table: bool = True,
) -> list[tuple[str, str, int]]:
    """Run every reader binary across every fixture in fixtures_dir."""
    fixture_files = sorted(
        [p for p in fixtures_dir.iterdir() if p.is_file() and p.name.endswith(".bin")]
    )
    if not fixture_files:
        raise RuntimeError(f"No .bin fixtures found in {fixtures_dir}")

    results = []
    if print_table:
        print("Reader x Fixture verification matrix:")
        print(f"{'Reader':<14} | {'Fixture':<22} | {'rc':<3}")
        print("-" * 45)
    for r_name in sorted(binaries.keys()):
        r_bin, _ = binaries[r_name]
        for f_path in fixture_files:
            res = subprocess.run(  # nosec B603 - test binary execution without shell, explicit timeout  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - argv is a list without shell, explicit timeout
                [str(r_bin), "read", str(f_path)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            results.append((r_name, f_path.name, res.returncode))
            if print_table:
                print(f"{r_name:<14} | {f_path.name:<22} | {res.returncode:<3}")
            if res.returncode != 0:
                if print_table:
                    print(
                        f"Reader failure details ({r_name} on {f_path.name}):\n{res.stderr}"
                    )
                raise RuntimeError(f"Reader check failed for {r_name} on {f_path.name}")
    return results


def _expect_reject(label: str, fn: Any, *args: Any, **kwargs: Any) -> None:
    """Assert that fn raises RuntimeError or ValueError, otherwise raise SelfTestError."""
    try:
        fn(*args, **kwargs)
    except (RuntimeError, ValueError) as err:
        print(f"ok: plant {label} rejected: {err}")
        return
    raise SelfTestError(f"plant {label} passed")


def _make_temp_copy(src: Path, dst: Path) -> Path:
    """Create fresh temporary copy of directory tree."""
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    return dst


def _test_plants_1_to_5(
    test_root: Path,
    clean_dir: Path,
    expected_fixtures: dict[str, dict[str, Any]],
    all_built_writers: dict[str, int],
) -> None:
    """Run plants P1 through P5 on fresh temporary fixture directories."""
    p1_dir = _make_temp_copy(clean_dir, test_root / "p1")
    p1_file = p1_dir / "abi-1.0.bin"
    d1 = bytearray(p1_file.read_bytes())
    d1[64] ^= 0x01
    p1_file.write_bytes(d1)
    _expect_reject(
        "P1", verify_fixtures_and_manifest, p1_dir, expected_fixtures, all_built_writers
    )

    p2_dir = _make_temp_copy(clean_dir, test_root / "p2")
    (p2_dir / "abi-1.3.bin").unlink()
    _expect_reject(
        "P2", verify_fixtures_and_manifest, p2_dir, expected_fixtures, all_built_writers
    )

    p3_dir = _make_temp_copy(clean_dir, test_root / "p3")
    m3 = load_manifest_strict(p3_dir / "manifest.json")
    m3[0]["writers"].append("v99.99.99")
    (p3_dir / "manifest.json").write_text(
        json.dumps(m3, indent=2) + "\n", encoding="utf-8"
    )
    _expect_reject(
        "P3", verify_fixtures_and_manifest, p3_dir, expected_fixtures, all_built_writers
    )

    p4_dir = _make_temp_copy(clean_dir, test_root / "p4")
    m4 = load_manifest_strict(p4_dir / "manifest.json")
    for row in m4:
        if row["abi_minor"] == 0:
            row["writers"].append("HEAD")
    (p4_dir / "manifest.json").write_text(
        json.dumps(m4, indent=2) + "\n", encoding="utf-8"
    )
    _expect_reject(
        "P4", verify_fixtures_and_manifest, p4_dir, expected_fixtures, all_built_writers
    )

    p5_dir = _make_temp_copy(clean_dir, test_root / "p5")
    (p5_dir / "abi-1.2.bin").write_bytes(b"\x00" * 32)
    _expect_reject(
        "P5", verify_fixtures_and_manifest, p5_dir, expected_fixtures, all_built_writers
    )


def _test_plants_6_to_10(
    test_root: Path,
    clean_dir: Path,
    binaries: dict[str, tuple[Path, int]],
    expected_fixtures: dict[str, dict[str, Any]],
    all_built_writers: dict[str, int],
) -> None:
    """Run plants P6 through P10 on fresh temporary fixture directories."""
    p6_dir = _make_temp_copy(clean_dir, test_root / "p6")
    txt = (p6_dir / "manifest.json").read_text(encoding="utf-8")
    txt = txt.replace('"abi_minor": 0,', '"abi_minor": 0,\n    "abi_minor": 0,', 1)
    (p6_dir / "manifest.json").write_text(txt, encoding="utf-8")
    _expect_reject(
        "P6", verify_fixtures_and_manifest, p6_dir, expected_fixtures, all_built_writers
    )

    p7_dir = _make_temp_copy(clean_dir, test_root / "p7")
    m7 = load_manifest_strict(p7_dir / "manifest.json")
    m7[0]["extra_key"] = 123
    (p7_dir / "manifest.json").write_text(
        json.dumps(m7, indent=2) + "\n", encoding="utf-8"
    )
    _expect_reject(
        "P7", verify_fixtures_and_manifest, p7_dir, expected_fixtures, all_built_writers
    )

    p8_dir = _make_temp_copy(clean_dir, test_root / "p8")
    d8 = bytearray((p8_dir / "abi-1.0.bin").read_bytes())
    d8[144] ^= 0x01
    (p8_dir / "abi-1.0.bin").write_bytes(d8)
    _expect_reject("P8", run_reader_matrix, binaries, p8_dir, False)

    p9_dir = _make_temp_copy(clean_dir, test_root / "p9")
    p9_file = p9_dir / "abi-1.0.bin"
    bad_bytes = b"corrupted_header" + p9_file.read_bytes()[16:]
    p9_file.write_bytes(bad_bytes)
    _expect_reject("P9", write_fixtures, p9_dir, expected_fixtures)
    if p9_file.read_bytes() != bad_bytes:
        raise SelfTestError("plant P9: files were modified despite mismatch")
    print("ok: plant P9 verified: bytes unchanged on refused write")

    head_bin = binaries["HEAD"][0]
    p10_file = clean_dir / "abi-1.0.bin"
    res10 = subprocess.run(  # nosec B603 - test binary execution without shell, explicit timeout  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - argv is a list without shell, explicit timeout
        [str(head_bin), "read", str(p10_file), "--flip", "144"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if res10.returncode != 1:
        raise SelfTestError(f"plant P10 passed: expected rc 1, got {res10.returncode}")
    print("ok: plant P10 rejected: reader returned rc=1 with --flip 144")


def _test_positives(
    test_root: Path,
    clean_dir: Path,
    expected_fixtures: dict[str, dict[str, Any]],
    all_built_writers: dict[str, int],
) -> None:
    """Verify clean copy passes with zero notes and unlisted tag returns exactly one note."""
    clean_notes = verify_fixtures_and_manifest(
        clean_dir, expected_fixtures, all_built_writers
    )
    if clean_notes:
        raise SelfTestError(f"Clean copy produced unexpected notes: {clean_notes}")
    print("ok: clean copy passed with zero notes")

    pos2_dir = _make_temp_copy(clean_dir, test_root / "pos2")
    m_pos = load_manifest_strict(pos2_dir / "manifest.json")
    target_tag = "v0.2.0"
    for row in m_pos:
        if target_tag in row["writers"]:
            row["writers"].remove(target_tag)
            break
    (pos2_dir / "manifest.json").write_text(
        json.dumps(m_pos, indent=2) + "\n", encoding="utf-8"
    )
    notes = verify_fixtures_and_manifest(pos2_dir, expected_fixtures, all_built_writers)
    if len(notes) != 1 or target_tag not in notes[0]:
        raise SelfTestError(f"Expected 1 note naming {target_tag}, got {notes}")
    print(f"ok: positive note test passed: {notes[0]}")


def _test_supersede_positive(
    test_root: Path,
    clean_dir: Path,
    expected_fixtures: dict[str, dict[str, Any]],
    all_built_writers: dict[str, int],
) -> None:
    """Simulate an ABI bump at HEAD: fixtures only HEAD wrote leave, released ones stay."""
    sim_built = dict(all_built_writers)
    sim_built["HEAD"] = all_built_writers["HEAD"] + 1
    sim_expected: dict[str, dict[str, Any]] = {}
    for name, meta in expected_fixtures.items():
        writers = [w for w in meta["writers"] if w != "HEAD"]
        if writers:
            sim_expected[name] = {**meta, "writers": writers}
    pos3_dir = _make_temp_copy(clean_dir, test_root / "pos3")
    dropped = sorted(set(expected_fixtures) - set(sim_expected))
    for name in dropped:
        (pos3_dir / name).unlink()
    rows = []
    for row in load_manifest_strict(pos3_dir / "manifest.json"):
        writers = [w for w in row["writers"] if w != "HEAD"]
        if writers:
            rows.append({**row, "writers": writers})
    (pos3_dir / "manifest.json").write_text(
        json.dumps(rows, indent=2) + "\n", encoding="utf-8"
    )
    notes = verify_fixtures_and_manifest(pos3_dir, sim_expected, sim_built)
    if notes:
        raise SelfTestError(f"Superseding bump produced unexpected notes: {notes}")
    print(f"ok: superseding ABI bump passes after dropping never-released {dropped}")


def run_self_test(
    binaries: dict[str, tuple[Path, int]],
    expected_fixtures: dict[str, dict[str, Any]],
    all_built_writers: dict[str, int],
) -> None:
    """Run self-test with planted negative cases on temporary copies."""
    print("Running self-test (planted negative cases)...")
    try:
        _expect_reject("meta", lambda: None)
        raise RuntimeError("Meta-check failed: SelfTestError was not raised")
    except SelfTestError:
        print("ok: meta-check passed: _expect_reject caught vacuity")

    with tempfile.TemporaryDirectory() as td:
        test_root = Path(td)
        clean_dir = test_root / "clean"
        write_fixtures(clean_dir, expected_fixtures)

        _test_plants_1_to_5(test_root, clean_dir, expected_fixtures, all_built_writers)
        _test_plants_6_to_10(
            test_root, clean_dir, binaries, expected_fixtures, all_built_writers
        )
        _test_positives(test_root, clean_dir, expected_fixtures, all_built_writers)
        _test_supersede_positive(
            test_root, clean_dir, expected_fixtures, all_built_writers
        )

    print("Self-test passed: all plants and positives verified as expected.")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments using argparse with mutually exclusive modes."""
    parser = argparse.ArgumentParser(
        description="Verify interop ABI fixtures against released writers and readers."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--write",
        action="store_true",
        help="Regenerate persisted fixtures and manifest.json from writers",
    )
    group.add_argument(
        "--self-test",
        action="store_true",
        help="Run self-test with planted negative cases on temp copies",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Main entry point."""
    try:
        args = parse_args(argv)
        if sys.platform in ("win32", "cygwin"):
            print(
                "skip: check-interop-abi-history requires POSIX C compiler and git archive (HISS-21)"
            )
            return 0

        repo_root = get_repo_root()
        tags = get_git_tags(repo_root)
        fixtures_dir = (
            repo_root / "libpelorus" / "test" / "fixtures" / "interop-history"
        )

        with tempfile.TemporaryDirectory() as td:
            temp_dir = Path(td)
            binaries = build_tag_binaries(repo_root, tags, temp_dir)
            expected_fixtures = compute_expected_fixtures(binaries)
            all_built_writers = {name: minor for name, (_, minor) in binaries.items()}

            if args.write:
                write_fixtures(fixtures_dir, expected_fixtures)
                return 0

            if args.self_test:
                run_self_test(binaries, expected_fixtures, all_built_writers)
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
        SelfTestError,
        RuntimeError,
        ValueError,
        OSError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as err:
        sys.stderr.write(f"FAIL: {err}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
