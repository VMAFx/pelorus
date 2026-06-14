#!/usr/bin/env bash
#
# next-free.sh — atomically reserve the next ADR number.
#
# Copyright 2026 Lusoris. BSD-2-Clause-Patent.
#
# Local-first allocator (a trimmed form of vmafx's remote-aware version): scans
# docs/adr/ for the highest NNNN among *.md and *.md.stub and returns max+1,
# biasing upward to avoid gaps from unpushed claims.
#
#   next-free.sh                 print the next free number
#   next-free.sh --claim <slug>  create docs/adr/NNNN-<slug>.md.stub and print NNNN
#   next-free.sh --release <NNNN> remove the stub for NNNN
#
# Rename the .md.stub to .md when you commit the real ADR. The stub prevents
# parallel agents from claiming the same number.
set -euo pipefail

ADR_DIR="$(cd "$(dirname "$0")/../../docs/adr" && pwd)"

highest() {
    local max=0 n
    shopt -s nullglob
    for f in "$ADR_DIR"/[0-9][0-9][0-9][0-9]-*.md "$ADR_DIR"/[0-9][0-9][0-9][0-9]-*.md.stub; do
        n=$(basename "$f"); n=${n%%-*}; n=$((10#$n))
        (( n > max )) && max=$n
    done
    echo "$max"
}

case "${1:-}" in
    --claim)
        slug="${2:?usage: next-free.sh --claim <slug>}"
        next=$(( $(highest) + 1 ))
        printf -v num '%04d' "$next"
        stub="$ADR_DIR/$num-$slug.md.stub"
        [ -e "$stub" ] && { echo "stub already exists: $stub" >&2; exit 1; }
        : > "$stub"
        echo "claimed ADR-$num (stub: $stub); rename to .md on commit" >&2
        echo "$num"
        ;;
    --release)
        num="${2:?usage: next-free.sh --release <NNNN>}"
        rm -f "$ADR_DIR/$num"-*.md.stub
        echo "released $num" >&2
        ;;
    "")
        printf '%04d\n' "$(( $(highest) + 1 ))"
        ;;
    *)
        echo "usage: next-free.sh [--claim <slug> | --release <NNNN>]" >&2
        exit 2
        ;;
esac
