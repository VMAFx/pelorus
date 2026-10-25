#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Check PE file imports against reviewed allow-lists (ADR-0213)."""

from __future__ import annotations

import argparse
import fnmatch
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DLL_LIST = ROOT / "scripts" / "windows" / "tester-zip-dlls.txt"
VULKAN_LOADER_ERROR = (
    "Vulkan loader must come from the GPU driver, not the zip"
)


class DllAllowList:
    """Holds system and bundle DLL allow-lists from tester-zip-dlls.txt."""

    def __init__(self, system_patterns: list[str], bundle_dlls: dict[str, tuple[str, str]]):
        self.system_patterns = [p.lower() for p in system_patterns]
        self.bundle_dlls = bundle_dlls  # lower(name) -> (pkg_name, spdx_license)

    def is_system(self, name: str) -> bool:
        low = name.lower()
        for pat in self.system_patterns:
            if fnmatch.fnmatch(low, pat):
                return True
            if not pat.endswith(".dll") and fnmatch.fnmatch(low, f"{pat}.dll"):
                return True
        return False

    def is_bundle(self, name: str) -> bool:
        return name.lower() in self.bundle_dlls

    def bundle_info(self, name: str) -> tuple[str, str] | None:
        return self.bundle_dlls.get(name.lower())


def parse_dll_allow_list(path: Path) -> DllAllowList:
    if not path.is_file():
        raise FileNotFoundError(f"DLL allow-list file missing: {path}")

    system_patterns: list[str] = []
    bundle_dlls: dict[str, tuple[str, str]] = {}
    current_section: str | None = None

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current_section = line[1:-1].strip().lower()
            continue
        if current_section == "system":
            system_patterns.append(line)
        elif current_section == "bundle":
            parts = line.split(None, 2)
            if len(parts) != 3:
                raise ValueError(
                    f"[bundle] entry needs DLL, package and SPDX licence in {path}: {line}"
                )
            bundle_dlls[parts[0].lower()] = (parts[1], parts[2])
        else:
            raise ValueError(f"Unexpected entry outside section in {path}: {line}")

    if not system_patterns:
        raise ValueError(f"No [system] entries found in {path}")
    if not bundle_dlls:
        raise ValueError(f"No [bundle] entries found in {path}")

    return DllAllowList(system_patterns, bundle_dlls)


def extract_pe_imports_from_text(text: str) -> list[str]:
    """Extract imported DLL names from `objdump -p` stdout."""
    imports: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("DLL Name:"):
            parts = line.split(":", 1)
            if len(parts) == 2:
                dll = parts[1].strip()
                if dll:
                    imports.append(dll)
    return imports


def make_subprocess_objdump_runner(objdump_bin: str) -> Callable[[Path], list[str]]:
    def runner(path: Path) -> list[str]:
        res = subprocess.run(
            [objdump_bin, "-p", str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if res.returncode != 0:
            raise RuntimeError(
                f"objdump -p failed for {path} (exit {res.returncode}): {res.stderr.strip()}"
            )
        return extract_pe_imports_from_text(res.stdout)

    return runner


def is_pe_file(path: Path) -> bool:
    name = path.name.lower()
    return name.endswith((".exe", ".dll"))


def is_allowed_bundle_file(name: str, allow: DllAllowList) -> bool:
    low = name.lower()
    if low == "ffmpeg.exe":
        return True
    if "pelorus" in low and low.endswith(".dll"):
        return True
    return allow.is_bundle(low)


def check_bundle_directory(
    bundle_dir: Path,
    allow: DllAllowList,
    objdump_runner: Callable[[Path], list[str]],
) -> list[str]:
    """Validate all PE files in `bundle_dir` and verify import closure."""
    errors: list[str] = []
    if not bundle_dir.is_dir():
        return [f"Bundle path is not a directory: {bundle_dir}"]

    pe_files = [p for p in bundle_dir.iterdir() if p.is_file() and is_pe_file(p)]
    if not pe_files:
        return [f"No PE files (.exe, .dll) found in {bundle_dir}"]

    bundle_filenames_lower = {p.name.lower(): p for p in pe_files}

    # 1. Verify every PE file itself is allowed to ship
    for pe in pe_files:
        if not is_allowed_bundle_file(pe.name, allow):
            errors.append(
                f"{pe.name}: file is not permitted in bundle (must be ffmpeg.exe, *pelorus*.dll, or listed in [bundle])"
            )

    # 2. Check imports of every PE file in the bundle
    for pe in pe_files:
        try:
            imports = objdump_runner(pe)
        except Exception as e:
            errors.append(f"{pe.name}: could not inspect imports: {e}")
            continue

        for imp in imports:
            low = imp.lower()
            if low == "vulkan-1.dll":
                errors.append(f"{pe.name}: imported {imp} ({VULKAN_LOADER_ERROR})")
            elif low in bundle_filenames_lower:
                # Present in bundle directory -> allowed
                continue
            elif allow.is_system(low):
                # Present in system allow-list -> allowed
                continue
            elif allow.is_bundle(low):
                # Allowed bundle DLL, but missing from bundle directory
                errors.append(
                    f"{pe.name}: imports bundle DLL {imp} but it is missing from {bundle_dir.name}"
                )
            else:
                errors.append(
                    f"{pe.name}: imports unknown DLL {imp} (not in system or bundle allow-list)"
                )

    return errors


def resolve_bundle_closure(
    roots: list[Path],
    ucrt64_bin: Path,
    allow: DllAllowList,
    objdump_runner: Callable[[Path], list[str]],
) -> tuple[list[str], list[str]]:
    """Compute the transitive closure of bundle DLLs needed by `roots`."""
    errors: list[str] = []
    needed_bundle_dlls: dict[str, str] = {}  # lowercase -> actual name in ucrt64_bin
    queue: list[Path] = list(roots)
    visited: set[str] = set()

    # Pre-index ucrt64_bin files for case-insensitive lookup
    ucrt_files: dict[str, Path] = {}
    if ucrt64_bin.is_dir():
        for p in ucrt64_bin.iterdir():
            if p.is_file():
                ucrt_files[p.name.lower()] = p

    while queue:
        current = queue.pop(0)
        curr_key = str(current.resolve())
        if curr_key in visited:
            continue
        visited.add(curr_key)

        try:
            imports = objdump_runner(current)
        except Exception as e:
            errors.append(f"{current.name}: could not inspect imports: {e}")
            continue

        for imp in imports:
            low = imp.lower()
            if low == "vulkan-1.dll":
                errors.append(f"{current.name}: imported {imp} ({VULKAN_LOADER_ERROR})")
            elif allow.is_system(low):
                continue
            elif allow.is_bundle(low):
                if low not in needed_bundle_dlls:
                    if low not in ucrt_files:
                        errors.append(
                            f"{current.name}: imports bundle DLL {imp}, but not found in {ucrt64_bin}"
                        )
                        continue
                    dll_path = ucrt_files[low]
                    needed_bundle_dlls[low] = dll_path.name
                    queue.append(dll_path)
            elif "pelorus" in low and low.endswith(".dll"):
                # pelorus DLL from KEEP_DIR, imports checked when root is queued
                continue
            else:
                errors.append(
                    f"{current.name}: imports unknown DLL {imp} (not in system or bundle allow-list)"
                )

    sorted_names = sorted(needed_bundle_dlls.values())
    return sorted_names, errors


def _run_bundle_tests(bundle: Path, allow: DllAllowList, fixtures: dict[str, list[str]], mock_runner) -> None:
    # Case 1: allowed closure passes
    for name in ("ffmpeg.exe", "libpelorus-1.dll", "libwinpthread-1.dll"):
        (bundle / name).write_bytes(b"MZ\x00")
    errs = check_bundle_directory(bundle, allow, mock_runner)
    assert not errs, f"Case 1 (allowed closure) failed: {errs}"

    # Case 2: unknown DLL refused
    (bundle / "bad_unknown.exe").write_bytes(b"MZ\x00")
    errs = check_bundle_directory(bundle, allow, mock_runner)
    assert any("unknown_secret.dll" in e for e in errs), f"Case 2 failed: {errs}"
    (bundle / "bad_unknown.exe").unlink()

    # Case 3: vulkan-1.dll refused
    (bundle / "bad_vulkan.exe").write_bytes(b"MZ\x00")
    errs = check_bundle_directory(bundle, allow, mock_runner)
    assert any(VULKAN_LOADER_ERROR in e for e in errs), f"Case 3 failed: {errs}"
    (bundle / "bad_vulkan.exe").unlink()

    # Case 4: case-insensitive system match
    fixtures["ffmpeg.exe"] = [
        "kernel32.DLL",
        "uSeR32.dLl",
        "API-MS-WIN-CRT-RUNTIME-L1-1-0.DLL",
    ]
    errs = check_bundle_directory(bundle, allow, mock_runner)
    assert not errs, f"Case 4 (case-insensitive system match) failed: {errs}"

    # Case 5: missing bundle DLL refused
    fixtures["ffmpeg.exe"] = ["KERNEL32.dll", "libpelorus-1.dll", "libwinpthread-1.dll"]
    (bundle / "libwinpthread-1.dll").unlink()
    errs = check_bundle_directory(bundle, allow, mock_runner)
    assert any("missing from" in e and "libwinpthread-1.dll" in e for e in errs), f"Case 5 failed: {errs}"


def _run_resolve_tests(tmp: Path, allow: DllAllowList, fixtures: dict[str, list[str]], mock_runner) -> None:
    # Case 6: resolve closure
    ucrt_bin = tmp / "ucrt64" / "bin"
    ucrt_bin.mkdir(parents=True)
    (ucrt_bin / "libwinpthread-1.dll").write_bytes(b"MZ\x00")
    (ucrt_bin / "libgcc_s_seh-1.dll").write_bytes(b"MZ\x00")

    fixtures["ffmpeg_chain.exe"] = ["KERNEL32.dll", "libgcc_s_seh-1.dll"]
    chain_root = tmp / "ffmpeg_chain.exe"
    chain_root.write_bytes(b"MZ\x00")

    resolved, r_errs = resolve_bundle_closure([chain_root], ucrt_bin, allow, mock_runner)
    assert not r_errs, f"Resolve failed: {r_errs}"
    assert resolved == ["libgcc_s_seh-1.dll", "libwinpthread-1.dll"], f"Unexpected resolved: {resolved}"

    # Case 7: resolve failure when bundle DLL missing in ucrt64_bin
    (ucrt_bin / "libwinpthread-1.dll").unlink()
    _, r_errs2 = resolve_bundle_closure([chain_root], ucrt_bin, allow, mock_runner)
    assert any("not found in" in e and "libwinpthread-1.dll" in e for e in r_errs2), f"Resolve missing DLL failed: {r_errs2}"

    # Case 8: resolve failure when vulkan-1.dll imported
    fixtures["bad_vulkan_chain.dll"] = ["vulkan-1.dll"]
    (ucrt_bin / "libgcc_s_seh-1.dll").unlink()
    (ucrt_bin / "bad_vulkan_chain.dll").write_bytes(b"MZ\x00")
    fixtures["ffmpeg_vulkan.exe"] = ["bad_vulkan_chain.dll"]
    vulkan_root = tmp / "ffmpeg_vulkan.exe"
    vulkan_root.write_bytes(b"MZ\x00")
    allow.bundle_dlls["bad_vulkan_chain.dll"] = ("test-pkg", "MIT")
    _, r_errs3 = resolve_bundle_closure([vulkan_root], ucrt_bin, allow, mock_runner)
    assert any(VULKAN_LOADER_ERROR in e for e in r_errs3), f"Resolve vulkan-1.dll failed: {r_errs3}"


def run_self_tests() -> int:
    """Run regression tests against mock fixtures."""
    allow = parse_dll_allow_list(DEFAULT_DLL_LIST)
    fixtures: dict[str, list[str]] = {
        "ffmpeg.exe": ["KERNEL32.dll", "libpelorus-1.dll", "libwinpthread-1.dll"],
        "libpelorus-1.dll": ["KERNEL32.dll", "ucrtbase.dll"],
        "libwinpthread-1.dll": ["KERNEL32.dll", "msvcrt.dll"],
        "libgcc_s_seh-1.dll": ["KERNEL32.dll", "libwinpthread-1.dll"],
        "bad_vulkan.exe": ["KERNEL32.dll", "vulkan-1.dll"],
        "bad_unknown.exe": ["KERNEL32.dll", "unknown_secret.dll"],
        "case_test.exe": [
            "kernel32.DLL",
            "uSeR32.dLl",
            "API-MS-WIN-CRT-RUNTIME-L1-1-0.DLL",
        ],
    }

    def mock_runner(path: Path) -> list[str]:
        name = path.name
        if name in fixtures:
            return list(fixtures[name])
        raise RuntimeError(f"Unknown mock PE: {name}")

    with tempfile.TemporaryDirectory(prefix="pelorus-pe-test-") as tmpdir:
        tmp = Path(tmpdir)
        bundle = tmp / "bundle"
        bundle.mkdir()
        _run_bundle_tests(bundle, allow, fixtures, mock_runner)
        _run_resolve_tests(tmp, allow, fixtures, mock_runner)

        # Case 9: a [bundle] row without its package and licence is refused
        short_list = tmp / "short-list.txt"
        short_list.write_text("[system]\nKERNEL32.dll\n[bundle]\nzlib1.dll\n", encoding="utf-8")
        try:
            parse_dll_allow_list(short_list)
        except ValueError as exc:
            if "needs DLL, package and SPDX licence" not in str(exc):
                raise AssertionError(f"Case 9 refused for the wrong reason: {exc}") from exc
        else:
            raise AssertionError("Case 9 (bundle row without licence) was accepted")

    print("check-pe-imports: self-test passed (9 cases)")
    return 0


def _handle_resolve(target_path: Path, ucrt_bin: Path, allow: DllAllowList, objdump_runner) -> int:
    if not ucrt_bin.is_dir():
        print(f"ERROR: --resolve path is not a directory: {ucrt_bin}", file=sys.stderr)
        return 1
    if target_path.is_dir():
        roots = [p for p in target_path.iterdir() if p.is_file() and is_pe_file(p)]
    elif target_path.is_file() and is_pe_file(target_path):
        roots = [target_path]
    else:
        print(f"ERROR: target is not a valid PE file or directory: {target_path}", file=sys.stderr)
        return 1

    resolved, errors = resolve_bundle_closure(roots, ucrt_bin, allow, objdump_runner)
    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1
    for name in resolved:
        print(name)
    return 0


def _handle_verify(target_path: Path, allow: DllAllowList, objdump_runner) -> int:
    if target_path.is_dir():
        errors = check_bundle_directory(target_path, allow, objdump_runner)
    elif target_path.is_file() and is_pe_file(target_path):
        errors = check_bundle_directory(target_path.parent, allow, objdump_runner)
    else:
        print(f"ERROR: target is not a valid PE file or directory: {target_path}", file=sys.stderr)
        return 1

    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1

    print(f"OK: all PE imports verified in {target_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "target",
        nargs="?",
        help="Bundle directory or PE file to check (default: check bundle directory)",
    )
    parser.add_argument(
        "--resolve",
        metavar="UCRT64_BIN",
        help="Resolve transitive bundle DLL closure from UCRT64 bin directory and print filenames",
    )
    parser.add_argument(
        "--dll-list",
        default=str(DEFAULT_DLL_LIST),
        help=f"Path to tester-zip-dlls.txt (default: {DEFAULT_DLL_LIST})",
    )
    parser.add_argument(
        "--objdump",
        default="objdump",
        help="Path to objdump executable (default: objdump)",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run self-test suite and exit",
    )
    args = parser.parse_args()

    if args.self_test:
        return run_self_tests()

    if not args.target:
        parser.error("target path is required unless running --self-test")

    target_path = Path(args.target).resolve()
    try:
        allow = parse_dll_allow_list(Path(args.dll_list).resolve())
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    objdump_runner = make_subprocess_objdump_runner(args.objdump)
    if args.resolve:
        return _handle_resolve(target_path, Path(args.resolve).resolve(), allow, objdump_runner)
    return _handle_verify(target_path, allow, objdump_runner)


if __name__ == "__main__":
    sys.exit(main())
