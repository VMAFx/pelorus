#!/usr/bin/env bash
#
# package-tester-zip.sh — package standalone Windows x64 tester zip artifact (ADR-0213).
#
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
#
# Usage:
#   package-tester-zip.sh <KEEP_DIR> <FFMPEG_SRC> <OUT_DIR>
#   package-tester-zip.sh --self-test
#
# Requirements:
#   MSYS2 UCRT64 environment (or environment with objdump, python3, zip/bsdtar).
#   Reads closure DLLs from $UCRT64_BIN (default /ucrt64/bin) via check-pe-imports.py --resolve.
#   Reads licence notices from $UCRT64_SHARE/licenses (default /ucrt64/share/licenses).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"

CHECK_PE="$HERE/check-pe-imports.py"
DLL_LIST="$HERE/tester-zip-dlls.txt"

UCRT64_BIN="${UCRT64_BIN:-/ucrt64/bin}"
UCRT64_SHARE="${UCRT64_SHARE:-/ucrt64/share}"
OBJDUMP="${OBJDUMP:-objdump}"

lookup_pkg_for_dll() {
    local target_dll="$1"
    python3 -I -c '
import sys
dll = sys.argv[1].lower()
list_path = sys.argv[2]
pkg = ""
section = None
with open(list_path, encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].lower()
            continue
        if section == "bundle":
            parts = line.split(None, 2)
            if parts and parts[0].lower() == dll:
                pkg = parts[1] if len(parts) > 1 else ""
                break
if not pkg:
    sys.exit(1)
print(pkg)
' "$target_dll" "$DLL_LIST"
}

_copy_binaries() {
    local keep_dir="$1"
    local stage_dir="$2"

    if [[ ! -f "$keep_dir/ffmpeg.exe" ]]; then
        echo "ERROR: missing ffmpeg.exe in KEEP_DIR: $keep_dir" >&2
        exit 1
    fi
    cp -- "$keep_dir/ffmpeg.exe" "$stage_dir/ffmpeg.exe"

    shopt -s nullglob
    local pelorus_dlls=("$keep_dir"/*pelorus*.dll)
    shopt -u nullglob
    if [[ "${#pelorus_dlls[@]}" -eq 0 ]]; then
        echo "ERROR: missing libpelorus DLL in KEEP_DIR: $keep_dir" >&2
        exit 1
    fi
    for dll in "${pelorus_dlls[@]}"; do
        cp -- "$dll" "$stage_dir/"
    done
}

_resolve_and_copy_closure() {
    local stage_dir="$1"
    local -n out_closure="$2"

    while IFS= read -r line || [[ -n "$line" ]]; do
        [[ -n "$line" ]] || continue
        out_closure+=("$line")
    done < <(python3 -I "$CHECK_PE" --resolve "$UCRT64_BIN" --dll-list "$DLL_LIST" --objdump "$OBJDUMP" "$stage_dir")

    for dll in "${out_closure[@]}"; do
        if [[ ! -f "$UCRT64_BIN/$dll" ]]; then
            echo "ERROR: resolved DLL missing in $UCRT64_BIN: $dll" >&2
            exit 1
        fi
        cp -- "$UCRT64_BIN/$dll" "$stage_dir/"
    done
}

_write_bundle_metadata() {
    local stage_dir="$1"
    local pelorus_commit="$2"

    if [[ ! -f "$ROOT/scripts/windows/run.cmd" ]]; then
        echo "ERROR: missing run.cmd in $ROOT/scripts/windows" >&2
        exit 1
    fi
    cp -- "$ROOT/scripts/windows/run.cmd" "$stage_dir/run.cmd"

    PATH="$stage_dir:$UCRT64_BIN:$PATH" "$stage_dir/ffmpeg.exe" -buildconf > "$stage_dir/buildconf.txt" 2>&1 || {
        echo "ERROR: ffmpeg -buildconf failed" >&2
        exit 1
    }

    cat > "$stage_dir/README.txt" <<EOF
Pelorus Tester Bundle (Windows x64)
===================================

This archive contains a standalone test build of FFmpeg linked against libpelorus
for Windows x64 (AMD64).

Vulkan GPU Driver Requirement:
------------------------------
This bundle requires a GPU vendor Vulkan driver installed on the host system.
The Vulkan loader (vulkan-1.dll) is NOT shipped in this zip: it must come from
the GPU vendor driver (NVIDIA, AMD, or Intel).
The wrapper run.cmd checks for vulkan-1.dll and refuses execution if it is absent.

Execution:
----------
Run through the run.cmd wrapper:
    run.cmd [ffmpeg options]

run.cmd enforces:
  - Architecture: refuses non-AMD64 platforms (exit code 2).
  - Vulkan loader: refuses if vulkan-1.dll is not reachable on PATH (exit code 3).

Windows SmartScreen:
--------------------
Because this is an unsigned community test build, Windows SmartScreen may show
an "unrecognized app" warning. To proceed, click "More info" and select "Run anyway",
or run via Command Prompt or PowerShell.

Build and Source Provenance:
----------------------------
  - Pelorus repository commit: ${pelorus_commit}
  - FFmpeg base tag:           ${FFMPEG_TAG:-unknown}
  - FFmpeg base commit:        ${FFMPEG_COMMIT:-unknown}
  - Patch series:              ffmpeg-patches/ (applied over VMAFx/ffmpeg-patches shared series)
  - Flavour:                   LGPL-2.1-or-later (built without --enable-gpl and without --enable-nonfree)

Configuration options are recorded in buildconf.txt.
Licence texts and third-party notices are located in licenses/.
EOF
}

_copy_bundle_licenses() {
    local ffmpeg_src="$1"
    local stage_dir="$2"
    shift 2
    local closure_dlls=("$@")

    local lic_dir="$stage_dir/licenses"
    mkdir -p "$lic_dir"

    local required
    for required in "$ffmpeg_src/COPYING.LGPLv2.1" "$ffmpeg_src/LICENSE.md" \
        "$ROOT/LICENSES/EUPL-1.2.txt"; do
        if [[ ! -f "$required" ]]; then
            echo "ERROR: missing licence text: $required" >&2
            exit 1
        fi
    done
    cp -- "$ffmpeg_src/COPYING.LGPLv2.1" "$lic_dir/COPYING.LGPLv2.1"
    cp -- "$ffmpeg_src/LICENSE.md" "$lic_dir/FFmpeg-LICENSE.md"
    cp -- "$ROOT/LICENSES/EUPL-1.2.txt" "$lic_dir/EUPL-1.2.txt"

    for dll in "${closure_dlls[@]}"; do
        local pkg
        if ! pkg="$(lookup_pkg_for_dll "$dll")"; then
            echo "ERROR: cannot determine package name for shipped DLL $dll" >&2
            exit 1
        fi

        local src_lic=""
        if [[ -d "$UCRT64_SHARE/licenses/$pkg" ]]; then
            src_lic="$UCRT64_SHARE/licenses/$pkg"
        elif [[ -d "$UCRT64_SHARE/licenses/${pkg#mingw-w64-ucrt-x86_64-}" ]]; then
            src_lic="$UCRT64_SHARE/licenses/${pkg#mingw-w64-ucrt-x86_64-}"
        fi

        if [[ -z "$src_lic" ]]; then
            echo "ERROR: missing licence directory for shipped DLL $dll in $UCRT64_SHARE/licenses (pkg: $pkg)" >&2
            exit 1
        fi

        mkdir -p "$lic_dir/$pkg"
        cp -r -- "$src_lic"/* "$lic_dir/$pkg/"
    done
}

_create_bundle_zip() {
    local stage_root="$1"
    local bundle_name="$2"
    local zip_path="$3"

    (
        cd "$stage_root"
        if command -v zip >/dev/null 2>&1; then
            zip -r -X "$zip_path" "$bundle_name" >/dev/null
        elif command -v bsdtar >/dev/null 2>&1; then
            bsdtar -a -cf "$zip_path" "$bundle_name"
        else
            echo "ERROR: neither zip nor bsdtar found" >&2
            exit 1
        fi
    )
}

run_package() {
    local keep_dir="$1"
    local ffmpeg_src="$2"
    local out_dir="$3"

    if [[ ! -d "$keep_dir" ]]; then
        echo "ERROR: KEEP_DIR is not a directory: $keep_dir" >&2
        exit 1
    fi
    if [[ ! -d "$ffmpeg_src" ]]; then
        echo "ERROR: FFMPEG_SRC is not a directory: $ffmpeg_src" >&2
        exit 1
    fi
    mkdir -p "$out_dir"

    # shellcheck source=build-config.env
    source "$ROOT/build-config.env"
    local pelorus_commit
    if ! pelorus_commit="$(git -C "$ROOT" rev-parse --verify HEAD)"; then
        echo "ERROR: cannot read the Pelorus commit in $ROOT" >&2
        exit 1
    fi
    local sha8="${pelorus_commit:0:8}"
    local bundle_name="pelorus-tester-windows-x64-${sha8}"
    local zip_path="${out_dir}/${bundle_name}.zip"

    if [[ -e "$zip_path" || -L "$zip_path" ]]; then
        echo "ERROR: refusing existing output zip: $zip_path" >&2
        exit 1
    fi

    local stage_root
    stage_root="$(mktemp -d "${TMPDIR:-/tmp}/pelorus-tester-pack.XXXXXX")"
    # shellcheck disable=SC2064
    trap "rm -rf -- '$stage_root'" EXIT

    local stage_dir="$stage_root/$bundle_name"
    mkdir -p "$stage_dir"

    _copy_binaries "$keep_dir" "$stage_dir"
    local closure_dlls=()
    _resolve_and_copy_closure "$stage_dir" closure_dlls
    _write_bundle_metadata "$stage_dir" "$pelorus_commit"
    _copy_bundle_licenses "$ffmpeg_src" "$stage_dir" "${closure_dlls[@]}"

    python3 -I "$CHECK_PE" --dll-list "$DLL_LIST" --objdump "$OBJDUMP" "$stage_dir"
    _create_bundle_zip "$stage_root" "$bundle_name" "$zip_path"

    echo "Created $zip_path"
}

_setup_self_test_fixtures() {
    local test_root="$1"
    local fake_keep="$test_root/keep"
    local fake_src="$test_root/ffmpeg-src"
    local fake_ucrt_bin="$test_root/ucrt64/bin"
    local fake_ucrt_share="$test_root/ucrt64/share"
    local fake_tools="$test_root/tools"
    mkdir -p "$fake_keep" "$fake_src" "$fake_ucrt_bin" "$fake_ucrt_share/licenses" "$fake_tools"

    cat > "$fake_keep/ffmpeg.exe" <<'EOF'
#!/bin/sh
if [ "$1" = "-buildconf" ]; then
    echo "configuration: --enable-vulkan"
    exit 0
fi
exit 0
EOF
    chmod +x "$fake_keep/ffmpeg.exe"
    touch "$fake_keep/libpelorus-1.dll"
    touch "$fake_src/COPYING.LGPLv2.1"
    touch "$fake_src/LICENSE.md"
    touch "$fake_ucrt_bin/libwinpthread-1.dll"
    mkdir -p "$fake_ucrt_share/licenses/mingw-w64-ucrt-x86_64-libwinpthread"
    echo "MIT" > "$fake_ucrt_share/licenses/mingw-w64-ucrt-x86_64-libwinpthread/COPYING"

    cat > "$fake_tools/fake-objdump" <<'EOF'
#!/bin/sh
file="$(basename "$2")"
case "$file" in
    ffmpeg.exe)
        echo "    DLL Name: KERNEL32.dll"
        echo "    DLL Name: libpelorus-1.dll"
        echo "    DLL Name: libwinpthread-1.dll"
        ;;
    libpelorus-1.dll)
        echo "    DLL Name: KERNEL32.dll"
        echo "    DLL Name: ucrtbase.dll"
        ;;
    libwinpthread-1.dll)
        echo "    DLL Name: KERNEL32.dll"
        ;;
    *)
        echo "    DLL Name: KERNEL32.dll"
        ;;
esac
exit 0
EOF
    chmod +x "$fake_tools/fake-objdump"
}

_package_fixture() {
    local test_root="$1"
    (
        UCRT64_BIN="$test_root/ucrt64/bin" \
        UCRT64_SHARE="$test_root/ucrt64/share" \
        OBJDUMP="$test_root/tools/fake-objdump" \
            run_package "$test_root/keep" "$test_root/ffmpeg-src" "$test_root/out"
    )
}

_run_self_test_cases() {
    local test_root="$1"
    local fake_src="$test_root/ffmpeg-src"
    local fake_ucrt_share="$test_root/ucrt64/share"
    local fake_out="$test_root/out"
    mkdir -p "$fake_out"

    _package_fixture "$test_root"

    shopt -s nullglob
    local zips=("$fake_out"/*.zip)
    shopt -u nullglob
    if [[ "${#zips[@]}" -ne 1 ]]; then
        echo "ERROR: self-test did not create zip" >&2
        return 1
    fi

    local refused=0
    _package_fixture "$test_root" 2>/dev/null || refused=1
    if (( ! refused )); then
        echo "ERROR: self-test did not refuse existing zip" >&2
        return 1
    fi

    rm -f "${zips[0]}"
    mv "$fake_src/COPYING.LGPLv2.1" "$fake_src/COPYING.moved"
    local ffmpeg_lic_refused=0
    _package_fixture "$test_root" 2>/dev/null || ffmpeg_lic_refused=1
    mv "$fake_src/COPYING.moved" "$fake_src/COPYING.LGPLv2.1"
    if (( ! ffmpeg_lic_refused )); then
        echo "ERROR: self-test did not fail on missing FFmpeg licence text" >&2
        return 1
    fi

    rm -rf "$fake_ucrt_share/licenses/mingw-w64-ucrt-x86_64-libwinpthread"
    local lic_refused=0
    _package_fixture "$test_root" 2>/dev/null || lic_refused=1
    if (( ! lic_refused )); then
        echo "ERROR: self-test did not fail on missing licence directory" >&2
        return 1
    fi
}

run_self_tests() {
    local test_root
    test_root="$(mktemp -d "${TMPDIR:-/tmp}/pelorus-pack-test.XXXXXX")"
    # shellcheck disable=SC2064
    trap "rm -rf -- '$test_root'" EXIT

    _setup_self_test_fixtures "$test_root"
    _run_self_test_cases "$test_root"

    echo "package-tester-zip: self-test passed (4 cases)"
    return 0
}

if [[ "${1:-}" == "--self-test" ]]; then
    run_self_tests
    exit 0
fi

if [[ $# -ne 3 ]]; then
    echo "Usage: $0 <KEEP_DIR> <FFMPEG_SRC> <OUT_DIR>" >&2
    echo "       $0 --self-test" >&2
    exit 1
fi

run_package "$1" "$2" "$3"
