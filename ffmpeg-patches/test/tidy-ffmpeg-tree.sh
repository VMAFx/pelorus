#!/usr/bin/env bash
#
# tidy-ffmpeg-tree.sh — generate compile_commands.json from a configured FFmpeg
# tree and installed static prefix, then run clang-tidy over every unit in the
# "ffmpeg-tree" lane (.config/clang-tidy/ffmpeg-tree-files.txt, VMAFx/pelorus#94).
#
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
#
# Inputs (positional or environment):
#   $1 / FFMPEG_TREE         configured/built FFmpeg worktree directory (required)
#   $2 / FFMPEG_PREFIX       installed static FFmpeg prefix directory   (required)
#   $3 / LIST_FILE           newline-separated unit list file
#                            (default: .config/clang-tidy/ffmpeg-tree-files.txt)
#   $4 / OUTPUT_ROOT         directory for compile_commands.json and logs
#                            (default: $LOG_DIR/ffmpeg-tidy or mktemp -d)
#   $5 / CONFIG_FILE         clang-tidy configuration file
#                            (default: .config/clang-tidy/ffmpeg-tree.yaml)
#   COMPILE_COMMANDS_JSON    optional pre-built compile_commands.json to verify
#                            and consume instead of generating one
#
# Exit: 0 pass, 1 fail, 77 skip (reason printed on stdout).

set -euo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PATCHDIR="$(cd -- "$HERE/.." && pwd -P)"
ROOT="$(cd -- "$PATCHDIR/.." && pwd -P)"

FFMPEG_TREE="${1:-${FFMPEG_TREE:-}}"
FFMPEG_PREFIX="${2:-${FFMPEG_PREFIX:-}}"
LIST_FILE="${3:-${LIST_FILE:-$ROOT/.config/clang-tidy/ffmpeg-tree-files.txt}}"
OUTPUT_ROOT="${4:-${OUTPUT_ROOT:-${LOG_DIR:-}}}"
CONFIG_FILE="${5:-${CONFIG_FILE:-$ROOT/.config/clang-tidy/ffmpeg-tree.yaml}}"
SOURCE_ROOT="${SOURCE_ROOT:-$ROOT}"

pel_skip() {
    echo "SKIP: $*"
    exit 77
}

pel_fail() {
    echo "FAIL: $*" >&2
    if [[ -n "$OUTPUT_ROOT" ]]; then
        echo "Evidence: $OUTPUT_ROOT" >&2
    fi
    exit 1
}

if [[ "${MSYSTEM:-}" == "UCRT64" ]] || [[ "$(uname -o 2>/dev/null)" == "Msys" ]]; then
    pel_skip "Windows MSYS2 UCRT64 replay does not run the FFmpeg-tree clang-tidy lane"
fi

if [[ -z "$FFMPEG_TREE" ]]; then
    pel_fail "FFMPEG_TREE (or \$1) must name the configured FFmpeg tree"
fi
if [[ -z "$FFMPEG_PREFIX" ]]; then
    pel_fail "FFMPEG_PREFIX (or \$2) must name the installed static FFmpeg prefix"
fi
if [[ ! -d "$FFMPEG_TREE" ]]; then
    pel_fail "FFMPEG_TREE directory does not exist: $FFMPEG_TREE"
fi
if [[ ! -d "$FFMPEG_PREFIX" ]]; then
    pel_fail "FFMPEG_PREFIX directory does not exist: $FFMPEG_PREFIX"
fi
if [[ ! -f "$LIST_FILE" ]]; then
    pel_fail "LIST_FILE does not exist: $LIST_FILE"
fi
if [[ ! -f "$CONFIG_FILE" ]]; then
    pel_fail "CONFIG_FILE does not exist: $CONFIG_FILE"
fi

# oneVPL first: a job without it then prints one reason whether or not its
# runner image ships clang-tidy, so its FFMPEG_TIDY_SKIP text stays valid.
if ! pkg-config --exists vpl; then
    pel_skip "oneVPL pkg-config package (vpl) not available for QSV unit"
fi

if ! command -v clang-tidy >/dev/null 2>&1; then
    pel_skip "clang-tidy not available on host"
fi

FFMPEG_TREE="$(cd -- "$FFMPEG_TREE" && pwd -P)"
FFMPEG_PREFIX="$(cd -- "$FFMPEG_PREFIX" && pwd -P)"
SOURCE_ROOT="$(cd -- "$SOURCE_ROOT" && pwd -P)"

OWNED_OUTPUT=0
if [[ -z "$OUTPUT_ROOT" ]]; then
    OUTPUT_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/pelorus-ffmpeg-tidy.XXXXXX")"
    OWNED_OUTPUT=1
else
    mkdir -p -- "$OUTPUT_ROOT"
fi
OUTPUT_ROOT="$(cd -- "$OUTPUT_ROOT" && pwd -P)"

cleanup() {
    local status=$?
    trap - EXIT
    if (( status == 0 && OWNED_OUTPUT )); then
        rm -rf -- "$OUTPUT_ROOT"
    fi
    exit "$status"
}
trap cleanup EXIT

PKG_DIRS="$FFMPEG_PREFIX/lib/pkgconfig"
if [[ -n "${PRIVATE_PREFIX:-}" && -d "${PRIVATE_PREFIX}/lib/pkgconfig" ]]; then
    PKG_DIRS="$PKG_DIRS:$PRIVATE_PREFIX/lib/pkgconfig"
elif [[ -d "$FFMPEG_PREFIX/../pelorus-prefix/lib/pkgconfig" ]]; then
    PKG_DIRS="$PKG_DIRS:$(cd -- "$FFMPEG_PREFIX/../pelorus-prefix/lib/pkgconfig" && pwd -P)"
fi
export PKG_CONFIG_PATH="${PKG_DIRS}${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"

STATIC_CFLAGS="$(pkg-config --static --cflags libavfilter libpelorus)" ||
    pel_fail "pkg-config --static --cflags libavfilter libpelorus failed"
if [[ -z "$STATIC_CFLAGS" ]]; then
    pel_fail "pkg-config --static --cflags libavfilter libpelorus returned empty flags"
fi
VPL_CFLAGS="$(pkg-config --cflags vpl)" ||
    pel_fail "pkg-config --cflags vpl failed"

UNITS=()
while IFS= read -r raw_line || [[ -n "$raw_line" ]]; do
    line="${raw_line%%#*}"
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    if [[ -z "$line" ]]; then
        continue
    fi
    UNITS+=("$line")
done < "$LIST_FILE"

if (( ${#UNITS[@]} == 0 )); then
    pel_fail "no units listed in $LIST_FILE"
fi

COMPILE_DB="$OUTPUT_ROOT/compile_commands.json"

if [[ -n "${COMPILE_COMMANDS_JSON:-}" ]]; then
    if [[ ! -f "$COMPILE_COMMANDS_JSON" ]]; then
        pel_fail "COMPILE_COMMANDS_JSON does not exist: $COMPILE_COMMANDS_JSON"
    fi
    cp -- "$COMPILE_COMMANDS_JSON" "$COMPILE_DB"
else
    python3 -I - "$FFMPEG_TREE" "$SOURCE_ROOT" "$OUTPUT_ROOT" "$COMPILE_DB" \
        "${CC:-cc}" "$STATIC_CFLAGS" "$VPL_CFLAGS" "${UNITS[@]}" <<'PY'
import json
import pathlib
import shlex
import subprocess
import sys

ffmpeg_tree = pathlib.Path(sys.argv[1]).resolve()
source_root = pathlib.Path(sys.argv[2]).resolve()
output_root = pathlib.Path(sys.argv[3]).resolve()
compile_db_path = pathlib.Path(sys.argv[4]).resolve()
cc_bin = sys.argv[5]
static_cflags = shlex.split(sys.argv[6])
vpl_cflags = shlex.split(sys.argv[7])
units = sys.argv[8:]


def fail(msg: str) -> "NoReturn":
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def ffmpeg_object_for(rel: str) -> tuple[str, str]:
    name = pathlib.Path(rel).name
    if name == "h265_pelorus_fgs_bsf.c":
        return "libavcodec/bsf/pelorus_fgs.o", "libavcodec/bsf"
    if name.startswith("vf_pelorus_") and name.endswith(".c"):
        stem = name[:-2]
        return f"libavfilter/{stem}.o", "libavfilter"
    fail(f"no FFmpeg object mapping for {rel}")


entries = []
for rel in units:
    unit_path = source_root / rel
    if not unit_path.is_file():
        fail(f"listed unit does not exist: {unit_path}")

    if rel.startswith("ffmpeg-patches/files/"):
        obj, subdir = ffmpeg_object_for(rel)
        tree_src = ffmpeg_tree / (obj[:-2] + ".c")
        if not tree_src.is_file():
            fail(f"FFmpeg tree source missing for {rel}: {tree_src}")
        proc = subprocess.run(
            ["make", "-C", str(ffmpeg_tree), "-n", "-B", "V=1", obj],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            fail(
                f"make -n -B {obj} failed for {rel} (rc={proc.returncode}): "
                f"{proc.stderr.strip()}"
            )
        cmd_line = ""
        expected_src = obj[:-2] + ".c"
        for raw in proc.stdout.splitlines():
            stripped = raw.strip()
            if not stripped:
                continue
            tokens = shlex.split(stripped)
            if "-c" in tokens and tokens[-1] == expected_src:
                cmd_line = stripped
                break
        if not cmd_line:
            fail(f"make -n -B {obj} produced no compile command for {rel}")
        tokens = shlex.split(cmd_line)
        tokens[-1] = str(unit_path)
        tokens.extend(
            [
                f"-I{ffmpeg_tree / subdir}",
                f"-I{ffmpeg_tree / 'libavcodec'}",
                "-Wno-unknown-warning-option",
            ]
        )
        entries.append(
            {
                "directory": str(ffmpeg_tree),
                "file": str(unit_path),
                "command": shlex.join(tokens),
            }
        )
    elif rel.startswith("ffmpeg-patches/test/"):
        stem = pathlib.Path(rel).stem
        tokens = (
            shlex.split(cc_bin)
            + static_cflags
            + vpl_cflags
            + [
                f"-I{ffmpeg_tree}",
                f"-I{ffmpeg_tree / 'libavcodec'}",
                f"-I{ffmpeg_tree / 'libavcodec' / 'tests'}",
                "-Wno-unknown-warning-option",
                "-c",
                str(unit_path),
                "-o",
                str(output_root / f"{stem}.o"),
            ]
        )
        entries.append(
            {
                "directory": str(source_root),
                "file": str(unit_path),
                "command": shlex.join(tokens),
            }
        )
    else:
        fail(f"unit outside ffmpeg-patches/{{files,test}}/: {rel}")

compile_db_path.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
PY
fi

# Verify every listed unit has a non-empty compile command in compile_commands.json.
python3 -I - "$SOURCE_ROOT" "$COMPILE_DB" "${UNITS[@]}" <<'PY'
import json
import pathlib
import sys

source_root = pathlib.Path(sys.argv[1]).resolve()
compile_db_path = pathlib.Path(sys.argv[2]).resolve()
units = sys.argv[3:]

try:
    entries = json.loads(compile_db_path.read_text(encoding="utf-8"))
except (OSError, ValueError) as exc:
    print(f"FAIL: could not read {compile_db_path}: {exc}", file=sys.stderr)
    sys.exit(1)

if not isinstance(entries, list):
    print(f"FAIL: {compile_db_path} root must be a JSON array", file=sys.stderr)
    sys.exit(1)

by_file: dict[str, str] = {}
for item in entries:
    if not isinstance(item, dict):
        continue
    file_val = item.get("file")
    cmd_val = item.get("command")
    if isinstance(file_val, str) and isinstance(cmd_val, str) and cmd_val.strip():
        by_file[str(pathlib.Path(file_val).resolve())] = cmd_val.strip()

for rel in units:
    expected = str((source_root / rel).resolve())
    if expected not in by_file:
        print(
            f"FAIL: unit missing compile command in {compile_db_path}: {rel}",
            file=sys.stderr,
        )
        sys.exit(1)
PY

failed=0
for rel in "${UNITS[@]}"; do
    unit_abs="$SOURCE_ROOT/$rel"
    log_name="$(echo "$rel" | tr '/.' '__').log"
    unit_log="$OUTPUT_ROOT/$log_name"
    rc=0
    clang-tidy -p "$OUTPUT_ROOT" --config-file="$CONFIG_FILE" --quiet "$unit_abs" \
        >"$unit_log" 2>&1 || rc=$?
    if (( rc != 0 )) || grep -Eq ': (warning|error): ' "$unit_log"; then
        echo "FAIL: clang-tidy reported findings in $rel (exit $rc):" >&2
        cat "$unit_log" >&2
        failed=1
    else
        echo "tidy ok: $rel"
    fi
done

if (( failed != 0 )); then
    pel_fail "clang-tidy failed on one or more FFmpeg-tree units"
fi

echo "FFmpeg-tree clang-tidy lane: ${#UNITS[@]} units clean"
