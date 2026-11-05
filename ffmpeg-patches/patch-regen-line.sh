#!/usr/bin/env bash
#
# patch-regen-line.sh — compute rebase-notes heading regenerates line
#
# SPDX-FileCopyrightText: 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
#

set -euo pipefail

print_patch_regen_line() {
    local repo_root="${1:-.}"
    local patch_glob="${2:-ffmpeg-patches/0*.patch}"
    local diff_files untracked_files
    # A failed git call must not read as "no patch regenerates".
    if ! diff_files=$(git -C "$repo_root" diff --name-only HEAD -- "$patch_glob"); then
        echo "patch-regen-line: git diff failed in $repo_root" >&2
        return 1
    fi
    if ! untracked_files=$(git -C "$repo_root" ls-files --others --exclude-standard -- "$patch_glob"); then
        echo "patch-regen-line: git ls-files failed in $repo_root" >&2
        return 1
    fi

    local items=()
    local files=()
    local f
    mapfile -t files < <(printf '%s\n%s\n' "$diff_files" "$untracked_files")
    for f in "${files[@]}"; do
        [[ -z "$f" ]] && continue
        local base
        base=$(basename -- "$f")
        local prefix="${base%%-*}"
        items+=("$prefix")
    done

    if [[ ${#items[@]} -eq 0 ]]; then
        echo "rebase-notes heading: no patch regenerates"
        return 0
    fi

    local sorted_unique=()
    mapfile -t sorted_unique < <(printf "%s\n" "${items[@]}" | sort -V -u)

    local joined=""
    local p
    for p in "${sorted_unique[@]}"; do
        if [[ -z "$joined" ]]; then
            joined="$p"
        else
            joined="$joined, $p"
        fi
    done

    echo "rebase-notes heading: regenerates $joined"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    print_patch_regen_line "$@"
fi
