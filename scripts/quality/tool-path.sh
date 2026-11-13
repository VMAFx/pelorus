# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2

# Resolve a quality tool from an explicit path or the build directories.
# shellcheck shell=bash
pelorus_tool_path() {
    local name="$1"
    local given="${2:-}"

    if [[ -n "$given" ]]; then
        printf '%s\n' "$given"
    elif [[ -x "${MESON_BUILD_ROOT:-build}/tools/$name" ]]; then
        printf '%s\n' "${MESON_BUILD_ROOT:-build}/tools/$name"
    elif [[ -x "build/tools/$name" ]]; then
        printf '%s\n' "build/tools/$name"
    else
        echo "FAIL: $name binary not found" >&2
        return 1
    fi
}
