#!/usr/bin/env bash
#
# next-free.sh — atomically reserve the next ADR number.
#
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
#
# Local-first allocator: scans all local worktrees and known branch tips (refs/heads
# and refs/remotes) for the highest NNNN among *.md and *.md.stub, returning max+1.
# No network access. Run 'git fetch origin' first to see other machines' pushed
# branches; this script reads only local refs.
#
#   next-free.sh                  print the next free number
#   next-free.sh --claim <slug>   create docs/adr/NNNN-<slug>.md.stub and print NNNN
#   next-free.sh --release <NNNN> remove the stub for NNNN
#
# Rename the .md.stub to .md when you commit the real ADR. The stub prevents
# parallel agents from claiming the same number.
set -euo pipefail
shopt -s inherit_errexit

unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE

scan_worktrees() {
    local repo_root="$1"
    local wt_max=0
    local line_count=0
    local max_wt_lines=5000
    local wt_count=0
    local max_worktrees=500
    local file_count=0
    local max_files=10000
    local line wt_path fname f n wt_out

    if ! wt_out="$(git -C "$repo_root" worktree list --porcelain)"; then
        echo "error: git worktree list failed" >&2
        exit 1
    fi

    shopt -s nullglob
    while IFS= read -r line; do
        [ -z "$line" ] && continue
        (( ++line_count > max_wt_lines )) && {
            echo "error: worktree list line cap exceeded ($max_wt_lines)" >&2
            exit 1
        }
        case "$line" in
            worktree\ *)
                (( ++wt_count > max_worktrees )) && {
                    echo "error: worktree limit exceeded ($max_worktrees)" >&2
                    exit 1
                }
                wt_path="${line#worktree }"
                [ -d "$wt_path/docs/adr" ] || continue
                file_count=0
                for f in "$wt_path"/docs/adr/[0-9][0-9][0-9][0-9]-*.md "$wt_path"/docs/adr/[0-9][0-9][0-9][0-9]-*.md.stub; do
                    (( ++file_count > max_files )) && {
                        echo "error: file limit exceeded in $wt_path/docs/adr ($max_files)" >&2
                        exit 1
                    }
                    fname="${f##*/}"
                    n="${fname%%-*}"
                    n=$((10#$n))
                    (( n > wt_max )) && wt_max=$n
                done
                ;;
        esac
    done <<< "$wt_out"
    echo "$wt_max"
}

scan_branches() {
    local repo_root="$1"
    local br_max=0
    local ref_count=0
    local max_refs=2000
    local sha entry fname n refs_out
    local -A seen_shas=()
    local sha_list=()

    if ! refs_out="$(git -C "$repo_root" for-each-ref --format='%(objectname)' refs/heads refs/remotes)"; then
        echo "error: git for-each-ref failed" >&2
        exit 1
    fi

    while IFS= read -r sha; do
        [ -z "$sha" ] && continue
        (( ++ref_count > max_refs )) && {
            echo "error: ref cap exceeded ($max_refs); delete stale branches or remote-tracking refs (git remote prune origin)" >&2
            exit 1
        }
        if [[ -z "${seen_shas["$sha"]:-}" ]]; then
            seen_shas["$sha"]=1
            sha_list+=( "$sha" )
        fi
    done <<< "$refs_out"

    local sha_idx=0
    local tree_line_count=0
    local max_tree_lines=10000
    local tree_out

    for sha in "${sha_list[@]}"; do
        (( ++sha_idx > max_refs )) && break
        if ! tree_out="$(git -C "$repo_root" ls-tree --name-only "$sha" -- docs/adr/)"; then
            echo "error: git ls-tree failed for commit $sha" >&2
            exit 1
        fi
        tree_line_count=0
        while IFS= read -r entry; do
            [ -z "$entry" ] && continue
            (( ++tree_line_count > max_tree_lines )) && {
                echo "error: tree entry cap exceeded in commit $sha ($max_tree_lines)" >&2
                exit 1
            }
            fname="${entry##*/}"
            if [[ "$fname" =~ ^([0-9]{4})-.*\.md(\.stub)?$ ]]; then
                n=$((10#${BASH_REMATCH[1]}))
                (( n > br_max )) && br_max=$n
            fi
        done <<< "$tree_out"
    done
    echo "$br_max"
}

highest() {
    local repo_root="$1"
    local wt_max br_max max
    wt_max="$(scan_worktrees "$repo_root")" || exit 1
    br_max="$(scan_branches "$repo_root")" || exit 1
    max=$wt_max
    (( br_max > max )) && max=$br_max
    echo "$max"
}

do_claim() {
    local repo_root="$1"
    local slug="${2:-}"
    if [ "$#" -ne 2 ] || [ -z "$slug" ]; then
        echo "usage: next-free.sh --claim <slug>" >&2
        exit 2
    fi
    if [[ ! "$slug" =~ ^[a-z0-9][a-z0-9-]*$ ]]; then
        echo "error: invalid slug '$slug' (must match ^[a-z0-9][a-z0-9-]*$)" >&2
        exit 2
    fi
    local adr_dir="$repo_root/docs/adr"
    local high next num stub
    high="$(highest "$repo_root")" || exit 1
    next=$(( high + 1 ))
    printf -v num '%04d' "$next"
    mkdir -p "$adr_dir"
    stub="$adr_dir/$num-$slug.md.stub"
    if [ -e "$stub" ]; then
        echo "stub already exists: $stub" >&2
        exit 1
    fi
    : > "$stub"
    echo "claimed ADR-$num (stub: $stub); rename to .md on commit" >&2
    echo "$num"
}

do_release() {
    local repo_root="$1"
    local num="${2:-}"
    if [ "$#" -ne 2 ] || [ -z "$num" ]; then
        echo "usage: next-free.sh --release <NNNN>" >&2
        exit 2
    fi
    if [[ ! "$num" =~ ^[0-9]{4}$ ]]; then
        echo "error: invalid ADR number '$num' (must match ^[0-9]{4}$)" >&2
        exit 2
    fi
    local adr_dir="$repo_root/docs/adr"
    shopt -s nullglob
    local stubs=( "$adr_dir/$num"-*.md.stub )
    if (( ${#stubs[@]} == 0 )); then
        echo "error: no stub found for ADR-$num to release" >&2
        exit 1
    fi
    rm -f "${stubs[@]}"
    echo "released $num" >&2
}

main() {
    local repo_root
    if ! repo_root="$(git -C "$(dirname "$0")" rev-parse --show-toplevel 2>/dev/null)"; then
        echo "error: not inside a git repository" >&2
        exit 1
    fi

    case "${1:-}" in
        --claim)
            shift
            do_claim "$repo_root" "$@"
            ;;
        --release)
            shift
            do_release "$repo_root" "$@"
            ;;
        "")
            if [ "$#" -gt 0 ]; then
                echo "usage: next-free.sh [--claim <slug> | --release <NNNN>]" >&2
                exit 2
            fi
            local high
            high="$(highest "$repo_root")" || exit 1
            printf '%04d\n' "$(( high + 1 ))"
            ;;
        *)
            echo "usage: next-free.sh [--claim <slug> | --release <NNNN>]" >&2
            exit 2
            ;;
    esac
}

main "$@"
