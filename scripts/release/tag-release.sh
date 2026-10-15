#!/usr/bin/env bash
#
# tag-release.sh — pre-tag release guard (ADR-0176).
#
# Refuse to tag a release candidate or final release before candidate legs
# are green, tag shape is valid, and meson.build version agrees.
#
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_PATH="$SCRIPT_DIR/$(basename "${BASH_SOURCE[0]}")"

SELF_TEST_TMP=""
cleanup() {
    if [ -n "${SELF_TEST_TMP:-}" ] && [ -d "${SELF_TEST_TMP:-}" ]; then
        rm -rf "$SELF_TEST_TMP"
    fi
}
trap cleanup EXIT

usage() {
    cat <<'EOF'
Usage: tag-release.sh vX.Y.Z[-rc.N] [--sha SHA] [--repo OWNER/NAME] [--runs-file FILE] [--dry-run]
       tag-release.sh --self-test

Options:
  --sha SHA          Target commit SHA (default: origin/master after git fetch)
  --repo OWNER/NAME  GitHub repository (default: VMAFx/pelorus)
  --runs-file FILE   Path to workflow runs JSON file (skips git fetch)
  --dry-run          Print tag command without creating the tag
  --self-test        Run internal regression and planted-defect test suite
  -h, --help         Show this help message
EOF
}

init_fixture_repo() {
    local repo="$1"
    mkdir -p "$repo"
    git -C "$repo" init -q
    git -C "$repo" config user.name "Test Committer"
    git -C "$repo" config user.email "test@example.com"
    git -C "$repo" config commit.gpgSign false
    git -C "$repo" config tag.gpgSign false

    cat <<'EOF' > "$repo/meson.build"
project(
  'pelorus',
  'c',
  version: '0.4.0-rc.1',
)
EOF
    git -C "$repo" add meson.build
    git -C "$repo" commit -q -m "rc.1 commit"

    cat <<'EOF' > "$repo/meson.build"
project(
  'pelorus',
  'c',
  version: '0.4.0',
)
EOF
    git -C "$repo" commit -q -am "mismatch commit"
}

write_fixture_runs() {
    local tmp="$1"
    local sha="$2"
    cat <<EOF > "$tmp/runs-green.json"
{"workflow_runs": [{"path": ".github/workflows/tester-publish.yml", "event": "workflow_dispatch", "head_sha": "$sha", "conclusion": "success", "created_at": "2026-10-15T12:00:00Z"}]}
EOF
    cat <<'EOF' > "$tmp/runs-missing.json"
{"workflow_runs": []}
EOF
    cat <<EOF > "$tmp/runs-red.json"
{"workflow_runs": [{"path": ".github/workflows/tester-publish.yml", "event": "workflow_dispatch", "head_sha": "$sha", "conclusion": "failure", "created_at": "2026-10-15T12:00:00Z"}]}
EOF
    cat <<EOF > "$tmp/runs-pr.json"
{"workflow_runs": [{"path": ".github/workflows/tester-publish.yml", "event": "pull_request", "head_sha": "$sha", "conclusion": "success", "created_at": "2026-10-15T12:00:00Z"}]}
EOF
}

run_planted_case() {
    local repo="$1"
    local name="$2"
    shift 2
    if (cd "$repo" && bash "$SCRIPT_PATH" "$@" >/dev/null 2>&1); then
        echo "self-test FAIL: $name was accepted (expected refusal)" >&2
        return 1
    fi
    echo "self-test PASS: $name refused as expected"
    return 0
}

run_green_case() {
    local repo="$1"
    local out="$2"
    shift 2
    if (cd "$repo" && bash "$SCRIPT_PATH" "$@" >"$out" 2>/dev/null); then
        if grep -q "git tag -s v0.4.0-rc.1" "$out"; then
            echo "self-test PASS: green case succeeded in --dry-run"
            return 0
        fi
        echo "self-test FAIL: green case missing tag command" >&2
    else
        echo "self-test FAIL: green case failed" >&2
    fi
    return 1
}

self_test() {
    SELF_TEST_TMP="$(mktemp -d)"
    local tmp="$SELF_TEST_TMP"
    local repo="$tmp/repo"
    init_fixture_repo "$repo"

    local commit_rc1 commit_mismatch failures=0
    commit_rc1="$(git -C "$repo" rev-parse HEAD~1)"
    commit_mismatch="$(git -C "$repo" rev-parse HEAD)"
    write_fixture_runs "$tmp" "$commit_rc1"

    run_planted_case "$repo" "missing tester run" v0.4.0-rc.1 --sha "$commit_rc1" --runs-file "$tmp/runs-missing.json" --dry-run || failures=$((failures + 1))
    run_planted_case "$repo" "red tester run" v0.4.0-rc.1 --sha "$commit_rc1" --runs-file "$tmp/runs-red.json" --dry-run || failures=$((failures + 1))
    run_planted_case "$repo" "pull_request-only run" v0.4.0-rc.1 --sha "$commit_rc1" --runs-file "$tmp/runs-pr.json" --dry-run || failures=$((failures + 1))
    run_planted_case "$repo" "version mismatch" v0.4.0-rc.1 --sha "$commit_mismatch" --runs-file "$tmp/runs-green.json" --dry-run || failures=$((failures + 1))
    run_planted_case "$repo" "bad shape" v0.4.0-beta.1 --sha "$commit_rc1" --runs-file "$tmp/runs-green.json" --dry-run || failures=$((failures + 1))

    git -C "$repo" tag v0.4.0-rc.1 "$commit_rc1"
    run_planted_case "$repo" "existing tag" v0.4.0-rc.1 --sha "$commit_rc1" --runs-file "$tmp/runs-green.json" --dry-run || failures=$((failures + 1))
    git -C "$repo" tag -d v0.4.0-rc.1 >/dev/null

    run_green_case "$repo" "$tmp/green.out" v0.4.0-rc.1 --sha "$commit_rc1" --runs-file "$tmp/runs-green.json" --dry-run || failures=$((failures + 1))

    if [ "$failures" -ne 0 ]; then
        echo "tag-release: self-test FAILED ($failures failure(s))" >&2
        return 1
    fi
    echo "tag-release: self-test passed (7 cases)"
}

resolve_sha() {
    local target_sha="$1"
    local runs_file="$2"
    if [ -z "$target_sha" ]; then
        if [ -z "$runs_file" ]; then
            git fetch origin
        fi
        git rev-parse origin/master
    else
        git rev-parse "$target_sha"
    fi
}

check_tag_shape() {
    local tag="$1"
    if ! python3 -I "$SCRIPT_DIR/verify-release.py" tag "$tag" >/dev/null; then
        echo "error: tag '$tag' failed verification" >&2
        return 1
    fi
}

check_tag_exists() {
    local tag="$1"
    local runs_file="$2"
    if git rev-parse --verify --quiet "refs/tags/$tag" >/dev/null; then
        echo "error: tag '$tag' already exists locally" >&2
        return 1
    fi
    if [ -z "$runs_file" ] && git remote get-url origin >/dev/null 2>&1; then
        if [ -n "$(git ls-remote --tags origin "refs/tags/$tag" 2>/dev/null)" ]; then
            echo "error: tag '$tag' already exists on origin" >&2
            return 1
        fi
    fi
}

check_meson_version() {
    local sha="$1"
    local tag="$2"
    local content version expected="${tag#v}"
    if ! content="$(git show "$sha:meson.build" 2>/dev/null)"; then
        echo "error: could not read meson.build at commit $sha" >&2
        return 1
    fi
    version="$(printf '%s\n' "$content" | sed -n "s/^[[:space:]]*version:[[:space:]]*['\"]\([^'\"]*\)['\"].*$/\1/p")"
    if [ -z "$version" ]; then
        echo "error: could not extract version from meson.build at commit $sha" >&2
        return 1
    fi
    if [ "$version" != "$expected" ]; then
        echo "error: meson.build version at $sha is '$version', expected '$expected'" >&2
        return 1
    fi
}

check_candidate_legs() {
    local sha="$1"
    local repo="$2"
    local runs_file="$3"
    local ok=0
    if [ -n "$runs_file" ]; then
        if python3 -I "$SCRIPT_DIR/check-candidate-legs.py" --sha "$sha" --runs-file "$runs_file"; then
            ok=1
        fi
    else
        if python3 -I "$SCRIPT_DIR/check-candidate-legs.py" --sha "$sha" --repo "$repo"; then
            ok=1
        fi
    fi
    if [ "$ok" -eq 0 ]; then
        echo "hint: dispatch Tester publish (docs/development/release.md step 2)" >&2
        return 1
    fi
}

emit_tag() {
    local tag="$1"
    local sha="$2"
    local dry_run="$3"
    local tag_msg="Pelorus ${tag#v}"
    if [ "$dry_run" -eq 1 ]; then
        echo "git tag -s $tag $sha -m \"$tag_msg\""
        echo "git push origin $tag"
    else
        git tag -s "$tag" "$sha" -m "$tag_msg"
        echo "Created tag $tag pointing to $sha"
        echo "To push:"
        echo "  git push origin $tag"
    fi
}

main() {
    local tag="" sha="" repo="VMAFx/pelorus" runs_file="" dry_run=0 self_test_mode=0

    while [ $# -gt 0 ]; do
        case "$1" in
            --self-test) self_test_mode=1; shift ;;
            --sha) [ $# -ge 2 ] || { echo "error: --sha requires an argument" >&2; exit 2; }; sha="$2"; shift 2 ;;
            --repo) [ $# -ge 2 ] || { echo "error: --repo requires an argument" >&2; exit 2; }; repo="$2"; shift 2 ;;
            --runs-file) [ $# -ge 2 ] || { echo "error: --runs-file requires an argument" >&2; exit 2; }; runs_file="$2"; shift 2 ;;
            --dry-run) dry_run=1; shift ;;
            -h|--help) usage; exit 0 ;;
            -*) echo "error: unknown option: $1" >&2; usage >&2; exit 2 ;;
            *)
                if [ -z "$tag" ]; then tag="$1"; shift; else
                    echo "error: unexpected extra positional argument: $1" >&2; usage >&2; exit 2
                fi
                ;;
        esac
    done

    [ "$self_test_mode" -eq 1 ] && { self_test; return $?; }
    [ -z "$tag" ] && { echo "error: missing required tag argument" >&2; usage >&2; exit 2; }

    sha="$(resolve_sha "$sha" "$runs_file")"
    check_tag_shape "$tag"
    check_tag_exists "$tag" "$runs_file"
    check_meson_version "$sha" "$tag"
    check_candidate_legs "$sha" "$repo" "$runs_file"
    emit_tag "$tag" "$sha" "$dry_run"
}

main "$@"
