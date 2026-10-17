#!/usr/bin/env bash
#
# check-publish-ref.sh — decide which commit a Tester publish dispatch may
# publish (ADR-0176, ADR-0199).
#
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
#
# Usage:
#   scripts/release/check-publish-ref.sh REF RUNNER_SHA
#       Run inside a full clone (all remote-tracking branches fetched). Resolve
#       REF (commit, branch or tag; empty = RUNNER_SHA) and print `sha=<sha>`.
#       RUNNER_SHA is the commit whose workflow file runs the dispatch; it must
#       be reachable from origin/master. The published commit must be
#       reachable from origin/master, or from a maintained patch branch
#       origin/lts-* (protected by the same ruleset as master). A patch-branch
#       commit must be given as its full 40-hex SHA, so the run name, which
#       carries the input, names the published commit exactly.
#   scripts/release/check-publish-ref.sh --self-test
#       Accept a master commit by name and by SHA, and an lts-* commit by full
#       SHA; refuse an lts-* commit by branch name or short SHA, a commit only
#       on a side branch, a runner commit off master, a malformed ref and a
#       ref that names no commit.
#
# Fails closed: anything not proven reachable is refused.
set -euo pipefail
# A failing command inside $(...) must fail the assignment that reads it.
shopt -s inherit_errexit

SELF="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"

fail() {
    echo "::error::$*" >&2
    exit 1
}

# True when $1 is reachable from any origin/lts-* branch.
on_patch_branch() {
    local commit="$1" branch
    while IFS= read -r branch; do
        [ -n "$branch" ] || continue
        if git merge-base --is-ancestor "$commit" "$branch"; then
            return 0
        fi
    done < <(git for-each-ref --format='%(refname)' 'refs/remotes/origin/lts-*')
    return 1
}

check() {
    local ref="$1" runner="$2" sha="" candidate
    [ -n "$ref" ] || ref="$runner"
    if ! printf '%s' "$ref" | grep -Eqx '[A-Za-z0-9._/-]{1,100}'; then
        fail "ref '${ref}' is not a plain commit, branch or tag name"
    fi
    for candidate in "$ref" "origin/$ref" "refs/tags/$ref"; do
        if sha="$(git rev-parse --verify --quiet --end-of-options "${candidate}^{commit}")"; then
            break
        fi
        sha=""
    done
    [ -n "$sha" ] || fail "ref '${ref}' does not name a commit"
    if ! git merge-base --is-ancestor "$runner" origin/master; then
        fail "runner commit ${runner} is not reachable from master; dispatch from master"
    fi
    if git merge-base --is-ancestor "$sha" origin/master; then
        printf 'sha=%s\n' "$sha"
        return 0
    fi
    if on_patch_branch "$sha"; then
        if [ "$ref" != "$sha" ]; then
            fail "commit ${sha} is on a patch branch only; pass its full SHA as ref so the run name names it"
        fi
        printf 'sha=%s\n' "$sha"
        return 0
    fi
    fail "commit ${sha} is not reachable from master or an lts-* branch; tester images are evidence for those commits only"
}

# Isolated fixture git: no global or system config, no inherited repository
# variables, so a run under a hook never writes into the real repository.
fixture_git() {
    fixture_env git "$@"
}

fixture_env() {
    env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE -u GIT_OBJECT_DIRECTORY \
        -u GIT_ALTERNATE_OBJECT_DIRECTORIES -u GIT_COMMON_DIR \
        GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 \
        GIT_AUTHOR_NAME=self-test GIT_AUTHOR_EMAIL=self-test@pelorus.invalid \
        GIT_COMMITTER_NAME=self-test GIT_COMMITTER_EMAIL=self-test@pelorus.invalid \
        "$@"
}

expect_ok() {
    local name="$1" ref="$2" runner="$3" want="$4" out
    out="$(cd "$CLONE" && fixture_env "$SELF" "$ref" "$runner" 2>&1)" \
        || fail "self-test: $name was refused: $out"
    [ "$out" = "sha=$want" ] || fail "self-test: $name printed '$out', want 'sha=$want'"
    echo "self-test: $name accepted"
}

expect_refused() {
    local name="$1" ref="$2" runner="$3" expect="$4" out
    if out="$(cd "$CLONE" && fixture_env "$SELF" "$ref" "$runner" 2>&1)"; then
        fail "self-test: $name was accepted: $out"
    fi
    printf '%s' "$out" | grep -q -- "$expect" || fail "self-test: $name failed elsewhere: $out"
    echo "self-test: $name refused ($expect)"
}

self_test() {
    local tmp origin m1 m2 lts side
    TMP="$(mktemp -d)"
    trap 'rm -rf "$TMP"' EXIT
    tmp="$TMP"
    origin="$tmp/origin"
    CLONE="$tmp/clone"
    fixture_git init -q -b master "$origin"
    fixture_git -C "$origin" commit -q --allow-empty -m m1
    m1="$(fixture_git -C "$origin" rev-parse HEAD)"
    fixture_git -C "$origin" tag v0.4.0
    fixture_git -C "$origin" commit -q --allow-empty -m m2
    m2="$(fixture_git -C "$origin" rev-parse HEAD)"
    fixture_git -C "$origin" switch -q -c lts-0.4 "$m1"
    fixture_git -C "$origin" commit -q --allow-empty -m patch
    lts="$(fixture_git -C "$origin" rev-parse HEAD)"
    fixture_git -C "$origin" switch -q -c side "$m1"
    fixture_git -C "$origin" commit -q --allow-empty -m side
    side="$(fixture_git -C "$origin" rev-parse HEAD)"
    fixture_git -C "$origin" switch -q master
    fixture_git clone -q "$origin" "$CLONE"
    fixture_git -C "$CLONE" rev-parse --verify --quiet refs/remotes/origin/lts-0.4 >/dev/null \
        || fail "self-test: fixture clone has no refs/remotes/origin/lts-0.4"

    expect_ok "empty ref (runner commit)" "" "$m2" "$m2"
    expect_ok "master tag" "v0.4.0" "$m2" "$m1"
    expect_ok "master branch" "master" "$m2" "$m2"
    expect_ok "lts commit by full sha" "$lts" "$m2" "$lts"
    expect_refused "lts commit by branch name" "lts-0.4" "$m2" "full SHA"
    expect_refused "lts commit by short sha" "${lts:0:12}" "$m2" "full SHA"
    expect_refused "side-branch commit" "$side" "$m2" "not reachable from master or an lts-"
    expect_refused "runner off master" "$m1" "$lts" "runner commit"
    expect_refused "malformed ref" 'a;b' "$m2" "not a plain"
    expect_refused "unknown ref" "no-such-ref" "$m2" "does not name a commit"
    echo "self-test: all cases behaved"
}

case "${1:-}" in
    --self-test) [ $# -eq 1 ] || fail "usage: $0 --self-test"; self_test ;;
    -*) fail "usage: $0 REF RUNNER_SHA | --self-test" ;;
    *) [ $# -eq 2 ] || fail "usage: $0 REF RUNNER_SHA | --self-test"; check "$1" "$2" ;;
esac
