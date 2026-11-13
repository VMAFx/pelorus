#!/usr/bin/env bash
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2

# Check quality-tool lookup precedence, failures, and caller skip behaviour.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/pelorus-tool-path-test.XXXXXX")"
trap 'rm -rf "$TMP_DIR"' EXIT
failures=0

check() {
    local name="$1" expected="$2" actual="$3"
    if [[ "$expected" == "$actual" ]]; then
        printf 'ok %s\n' "$name" >&2
    else
        printf 'FAIL %s: %q vs %q\n' "$name" "$expected" "$actual" >&2
        failures=$((failures + 1))
    fi
}

run_case() {
    local name="$1" cwd="$2" build_root="$3" case_path="$4"
    local expected_out="$5" expected_err="$6" expected_rc="$7"
    local rc actual_out actual_err
    shift 7
    if (
        cd "$cwd"
        if [[ "$build_root" == unset ]]; then
            unset MESON_BUILD_ROOT
        else
            export MESON_BUILD_ROOT="$build_root"
        fi
        PATH="$case_path" "$@"
    ) >"$TMP_DIR/stdout" 2>"$TMP_DIR/stderr"; then
        rc=0
    else
        rc=$?
    fi
    # Preserve trailing newlines so extra output cannot pass unnoticed.
    actual_out="$(
        cat "$TMP_DIR/stdout"
        printf '.'
    )"
    actual_err="$(
        cat "$TMP_DIR/stderr"
        printf '.'
    )"
    check "$name stdout" "$expected_out." "$actual_out"
    check "$name stderr" "$expected_err." "$actual_err"
    check "$name rc" "$expected_rc" "$rc"
}

lookup() {
    local name="$1" cwd="$2" build_root="$3" expected_out="$4" expected_err="$5" expected_rc="$6"
    local tool="$7" given="$8"
    # Positional parameters expand in the child shell.
    # shellcheck disable=SC2016
    run_case "$name" "$cwd" "$build_root" "$PATH" "$expected_out" "$expected_err" "$expected_rc" \
        "$BASH" -c 'source "$1"; pelorus_tool_path "$2" "$3"' \
        _ "$SCRIPT_DIR/tool-path.sh" "$tool" "$given"
}

mkdir -p "$TMP_DIR/empty" "$TMP_DIR/empty-path" "$TMP_DIR/cwd/build/tools" \
    "$TMP_DIR/mbr/tools" "$TMP_DIR/nonexec/tools" "$TMP_DIR/stubs"
for file in "$TMP_DIR/cwd/build/tools/pelorus_zones" "$TMP_DIR/mbr/tools/pelorus_zones" \
    "$TMP_DIR/nonexec/tools/pelorus_zones"; do
    printf '#!/bin/sh\nexit 0\n' >"$file"
    chmod 755 "$file"
done
chmod 644 "$TMP_DIR/nonexec/tools/pelorus_zones"
for tool in x265 ffmpeg vmaf; do
    printf '#!/bin/sh\nexit 0\n' >"$TMP_DIR/stubs/$tool"
    chmod 755 "$TMP_DIR/stubs/$tool"
done

lookup 'a explicit nonexistent' "$TMP_DIR/empty" "$TMP_DIR/none" \
    $'/no/such/pelorus_zones\n' '' 0 pelorus_zones /no/such/pelorus_zones
lookup 'b explicit precedence' "$TMP_DIR/cwd" "$TMP_DIR/mbr" \
    $'/no/such/pelorus_zones\n' '' 0 pelorus_zones /no/such/pelorus_zones
lookup 'c build-root precedence' "$TMP_DIR/cwd" "$TMP_DIR/mbr" \
    "$TMP_DIR/mbr/tools/pelorus_zones"$'\n' '' 0 pelorus_zones ''
lookup 'd default build-root' "$TMP_DIR/cwd" unset \
    $'build/tools/pelorus_zones\n' '' 0 pelorus_zones ''
lookup 'e nonexecutable fallback' "$TMP_DIR/cwd" "$TMP_DIR/nonexec" \
    $'build/tools/pelorus_zones\n' '' 0 pelorus_zones ''
lookup 'f missing zones' "$TMP_DIR/empty" "$TMP_DIR/none" \
    '' $'FAIL: pelorus_zones binary not found\n' 1 pelorus_zones ''
lookup 'g missing qloop' "$TMP_DIR/empty" "$TMP_DIR/none" \
    '' $'FAIL: pelorus_qloop binary not found\n' 1 pelorus_qloop ''
# Positional parameters and command substitution expand in the child shell.
# shellcheck disable=SC2016
run_case 'h errexit caller' "$TMP_DIR/empty" "$TMP_DIR/none" "$PATH" \
    '' $'FAIL: pelorus_zones binary not found\n' 1 \
    "$BASH" -c 'set -euo pipefail; source "$1"; X="$(pelorus_tool_path pelorus_zones "")"; echo after' \
    _ "$SCRIPT_DIR/tool-path.sh"

for script in x265-zone-honour qloop-x265-reference; do
    if [[ "$script" == x265-zone-honour ]]; then
        skip=$'SKIP: x265 or ffmpeg not found\n'
        tool=pelorus_zones
    else
        skip=$'SKIP: x265, ffmpeg or vmaf not found\n'
        tool=pelorus_qloop
    fi
    run_case "i $script skip" "$TMP_DIR/empty" "$TMP_DIR/none" "$TMP_DIR/empty-path" \
        "$skip" '' 77 "$BASH" "$SCRIPT_DIR/$script.sh"
    run_case "j $script lookup" "$TMP_DIR/empty" "$TMP_DIR/none" "$TMP_DIR/stubs:$PATH" \
        '' "FAIL: $tool binary not found"$'\n' 1 "$BASH" "$SCRIPT_DIR/$script.sh"
done

if [[ "$failures" -ne 0 ]]; then
    exit 1
fi
