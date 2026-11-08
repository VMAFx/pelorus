# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Planted self-test negative cases and verification harness for interop ABI history."""

from __future__ import annotations

from collections.abc import Callable
import json
from pathlib import Path
import shutil
import subprocess  # nosec B404 - fixed test binary argv, no shell, explicit timeout
import sys
import tempfile
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from interop_abi_history_manifest import (  # noqa: E402
    load_manifest_strict,
    run_reader_matrix,
    verify_fixtures_and_manifest,
)


class SelfTestError(Exception):
    """Raised when a self-test invariant or plant expectation is violated."""


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


def _run_flip(
    head_bin: Path, blob: Path, offset: str
) -> subprocess.CompletedProcess[str]:
    """Run the HEAD reader on blob with --flip offset; return the finished process."""
    return subprocess.run(  # nosec B603 - test binary execution without shell, explicit timeout  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - argv is a list without shell, explicit timeout
        [str(head_bin), "read", str(blob), "--flip", offset],
        capture_output=True,
        text=True,
        timeout=30,
    )


def _drop_head_writer(
    expected_fixtures: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Simulate an ABI bump at HEAD: drop HEAD as writer, and fixtures only HEAD wrote."""
    sim_expected: dict[str, dict[str, Any]] = {}
    for name, meta in expected_fixtures.items():
        writers = [w for w in meta["writers"] if w != "HEAD"]
        if writers:
            sim_expected[name] = {**meta, "writers": writers}
    return sim_expected


def _expect_files_unchanged(label: str, clean_dir: Path, plant_dir: Path) -> None:
    """Raise SelfTestError when a clean-copy file is missing or changed in plant_dir."""
    for p in clean_dir.iterdir():
        target = plant_dir / p.name
        if p.is_file() and (
            not target.is_file() or target.read_bytes() != p.read_bytes()
        ):
            raise SelfTestError(
                f"plant {label}: {p.name} missing or modified despite rejection"
            )


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
    write_fixtures_fn: Callable[[Path, dict[str, dict[str, Any]]], None],
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
    _expect_reject("P9", write_fixtures_fn, p9_dir, expected_fixtures)
    if p9_file.read_bytes() != bad_bytes:
        raise SelfTestError("plant P9: files were modified despite mismatch")
    print("ok: plant P9 verified: bytes unchanged on refused write")

    head_bin = binaries["HEAD"][0]
    p10_file = clean_dir / "abi-1.0.bin"
    res10 = _run_flip(head_bin, p10_file, "144")
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
    write_fixtures_fn: Callable[[Path, dict[str, dict[str, Any]]], None],
) -> None:
    """Simulate an ABI bump at HEAD: fixtures only HEAD wrote leave, released ones stay."""
    sim_built = dict(all_built_writers)
    sim_built["HEAD"] = all_built_writers["HEAD"] + 1
    sim_expected = _drop_head_writer(expected_fixtures)
    pos3_dir = _make_temp_copy(clean_dir, test_root / "pos3")
    dropped = sorted(set(expected_fixtures) - set(sim_expected))
    write_fixtures_fn(pos3_dir, sim_expected)
    for name in dropped:
        if (pos3_dir / name).exists():
            raise SelfTestError(
                f"Dropped file {name} still exists after write_fixtures"
            )
    for name in sim_expected:
        if (pos3_dir / name).read_bytes() != (clean_dir / name).read_bytes():
            raise SelfTestError(
                f"Kept file {name} differs from clean_dir after write_fixtures"
            )
    notes = verify_fixtures_and_manifest(pos3_dir, sim_expected, sim_built)
    if notes:
        raise SelfTestError(f"Superseding bump produced unexpected notes: {notes}")
    print(f"ok: superseding ABI bump passes after dropping never-released {dropped}")


def _test_plants_11_12(
    test_root: Path,
    clean_dir: Path,
    expected_fixtures: dict[str, dict[str, Any]],
    write_fixtures_fn: Callable[[Path, dict[str, dict[str, Any]]], None],
) -> None:
    """Plants P11, P12: --write refuses to delete released or unlisted fixtures."""
    p11_dir = _make_temp_copy(clean_dir, test_root / "p11")
    sim_expected_p11 = {
        k: v for k, v in expected_fixtures.items() if k != "abi-1.0.bin"
    }
    _expect_reject("P11", write_fixtures_fn, p11_dir, sim_expected_p11)
    _expect_files_unchanged("P11", clean_dir, p11_dir)

    p12_dir = _make_temp_copy(clean_dir, test_root / "p12")
    stray = p12_dir / "abi-1.9.bin"
    stray.write_bytes(b"\x00" * 32)
    _expect_reject(
        "P12",
        write_fixtures_fn,
        p12_dir,
        _drop_head_writer(expected_fixtures),
    )
    if not stray.is_file() or stray.read_bytes() != b"\x00" * 32:
        raise SelfTestError(
            "plant P12: abi-1.9.bin was modified or deleted despite rejection"
        )
    _expect_files_unchanged("P12", clean_dir, p12_dir)


def _test_plant_13_and_flip_bounds(
    clean_dir: Path, binaries: dict[str, tuple[Path, int]]
) -> None:
    """Plant P13 (bad --flip offsets give usage and rc 2) and the offset boundaries."""
    head_bin = binaries["HEAD"][0]
    p_file = clean_dir / "abi-1.0.bin"
    neg_cases = [
        "",
        "x",
        "-1",
        "+1",
        " 1",
        "1x",
        "4294967296",
        "00000000001",
        "00000000000000001x",
    ]
    for x in neg_cases:
        res = _run_flip(head_bin, p_file, x)
        if res.returncode != 2 or "Usage: abi_history read" not in res.stderr:
            raise SelfTestError(
                f"plant P13 passed for {x!r}: expected rc=2 and usage message, got rc={res.returncode}\n{res.stderr}"
            )
    print(
        f"ok: plant P13 rejected: {len(neg_cases)} invalid flip offsets rejected with usage error"
    )

    res_bnd = _run_flip(head_bin, p_file, "4294967295")
    if res_bnd.returncode != 2 or "Flip offset 4294967295 >= len" not in res_bnd.stderr:
        raise SelfTestError(
            f"positive boundary 4294967295 failed: expected rc=2 and range error, got rc={res_bnd.returncode}\n{res_bnd.stderr}"
        )
    res_pad = _run_flip(head_bin, p_file, "0000000144")
    if res_pad.returncode != 1:
        raise SelfTestError(
            f"positive boundary 0000000144 failed: expected rc=1, got rc={res_pad.returncode}\n{res_pad.stderr}"
        )
    print(
        "ok: positive boundary flip offsets verified: 4294967295 range-refused, 0000000144 flipped with rc=1"
    )


def run_self_test(
    binaries: dict[str, tuple[Path, int]],
    expected_fixtures: dict[str, dict[str, Any]],
    all_built_writers: dict[str, int],
    write_fixtures_fn: Callable[[Path, dict[str, dict[str, Any]]], None],
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
        write_fixtures_fn(clean_dir, expected_fixtures)

        _test_plants_1_to_5(test_root, clean_dir, expected_fixtures, all_built_writers)
        _test_plants_6_to_10(
            test_root,
            clean_dir,
            binaries,
            expected_fixtures,
            all_built_writers,
            write_fixtures_fn,
        )
        _test_plants_11_12(test_root, clean_dir, expected_fixtures, write_fixtures_fn)
        _test_plant_13_and_flip_bounds(clean_dir, binaries)
        _test_positives(test_root, clean_dir, expected_fixtures, all_built_writers)
        _test_supersede_positive(
            test_root,
            clean_dir,
            expected_fixtures,
            all_built_writers,
            write_fixtures_fn,
        )

    print(
        "Self-test passed: 13 plants (P1-P13), 3 positives and 2 flip-offset"
        " boundary cases verified as expected."
    )
