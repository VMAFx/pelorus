#!/usr/bin/env bash
#
# check-gitleaks.sh — scan every ref of a Git repository for credentials with a
# pinned gitleaks release (HISS-12).
#
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
#
# Usage:
#   scripts/check-gitleaks.sh --install DIR
#       Download the pinned gitleaks release (linux x64) into the new directory
#       DIR, check its sha256 against GITLEAKS_SHA256 and unpack the binary.
#       Prints DIR/gitleaks on stdout.
#   scripts/check-gitleaks.sh [REPO]
#       Scan the full history of every ref in REPO (default: the repository
#       root): local branches, remote-tracking branches and tags, with the
#       rules of REPO/.gitleaks.toml. Exits 1 on any finding whose fingerprint
#       REPO/.gitleaksignore does not list.
#   scripts/check-gitleaks.sh --self-test
#       Accept a clean fixture, then refuse a secret planted on a side branch
#       that exists only as a remote-tracking ref, a secret marked
#       `gitleaks:allow`, a .gitleaks.toml that drops the default rules or
#       adds an allowlist, and a gitleaks binary of another version; accept
#       the planted secret once its fingerprint is listed in .gitleaksignore.
#
# Fails closed: a missing binary, a version other than GITLEAKS_VERSION, a
# missing .gitleaksignore, a .gitleaks.toml outside its allow-list (it may
# only extend the default rules and add rules of its own) or a gitleaks error
# all exit non-zero. Inline `gitleaks:allow` comments are not honoured; the
# only exception is a fingerprint line in .gitleaksignore.
#
# Env:
#   GITLEAKS          gitleaks binary (default: gitleaks on PATH)
#   GITLEAKS_TIMEOUT  scan deadline in seconds (default 600)
#   CURL_MAX_TIME     download deadline in seconds (default 120)
set -euo pipefail
# A failing command inside $(...) must fail the assignment that reads it.
shopt -s inherit_errexit

GITLEAKS_VERSION=8.30.1
GITLEAKS_SHA256=551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GITLEAKS="${GITLEAKS:-gitleaks}"
GITLEAKS_TIMEOUT="${GITLEAKS_TIMEOUT:-600}"
CURL_MAX_TIME="${CURL_MAX_TIME:-120}"
# A config passed through the environment would replace the default rules.
unset GITLEAKS_CONFIG GITLEAKS_CONFIG_TOML

fail() {
    echo "check-gitleaks: $*" >&2
    exit 1
}

install_gitleaks() {
    local dir="$1"
    local tarball="gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz"
    local url="https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/${tarball}"
    [ ! -e "$dir" ] || fail "--install: $dir already exists"
    mkdir -p "$dir"
    curl --fail --silent --show-error --location --proto '=https' \
        --max-time "$CURL_MAX_TIME" --output "$dir/$tarball" "$url"
    echo "$GITLEAKS_SHA256  $dir/$tarball" | sha256sum --check --strict --quiet \
        || fail "--install: $tarball does not match pinned sha256 $GITLEAKS_SHA256"
    tar -xzf "$dir/$tarball" -C "$dir" gitleaks
    rm -f "$dir/$tarball"
    echo "$dir/gitleaks"
}

check_binary() {
    local version
    command -v "$GITLEAKS" > /dev/null || fail "gitleaks binary '$GITLEAKS' not found (scripts/check-gitleaks.sh --install DIR)"
    version="$("$GITLEAKS" version)"
    [ "$version" = "$GITLEAKS_VERSION" ] \
        || fail "gitleaks $version found, $GITLEAKS_VERSION pinned"
}

# Allow-list of .gitleaks.toml: default rules kept, own rules added, nothing
# that would exempt a finding (allowlists live only in .gitleaksignore).
check_config() {
    python3 -I -c '
import sys, tomllib
path = sys.argv[1]
with open(path, "rb") as fh:
    cfg = tomllib.load(fh)
RULE_KEYS = {"id", "description", "regex", "keywords", "entropy", "secretGroup", "path", "tags"}
errors = []
if set(cfg) - {"title", "extend", "rules"}:
    errors.append("top-level keys %s" % sorted(set(cfg) - {"title", "extend", "rules"}))
if cfg.get("extend") != {"useDefault": True}:
    errors.append("[extend] must be exactly useDefault = true")
for rule in cfg.get("rules", []):
    if set(rule) - RULE_KEYS:
        errors.append("rule %s keys %s" % (rule.get("id"), sorted(set(rule) - RULE_KEYS)))
for e in errors:
    print("%s: %s outside the allow-list" % (path, e), file=sys.stderr)
sys.exit(1 if errors else 0)
' "$1" || fail "$1 may only extend the default rules and add rules; list exceptions in .gitleaksignore"
}

print_findings() {
    python3 -I -c '
import json, sys
for f in json.load(open(sys.argv[1], encoding="utf-8")):
    print("  " + f["Fingerprint"])
' "$1"
}

scan() {
    local repo="$1" report rc=0
    check_binary
    [ -f "$repo/.gitleaksignore" ] || fail "$repo/.gitleaksignore is missing"
    [ -f "$repo/.gitleaks.toml" ] || fail "$repo/.gitleaks.toml is missing"
    check_config "$repo/.gitleaks.toml"
    report="$(mktemp)"
    "$GITLEAKS" git --no-banner --no-color --redact --exit-code 2 \
        --ignore-gitleaks-allow --config "$repo/.gitleaks.toml" --timeout "$GITLEAKS_TIMEOUT" \
        --log-opts=--all --gitleaks-ignore-path "$repo/.gitleaksignore" \
        --report-format json --report-path "$report" "$repo" || rc=$?
    case "$rc" in
        0) rm -f "$report"; echo "check-gitleaks: no credentials in any ref of $repo" ;;
        2) echo "check-gitleaks: credentials found (commit:file:rule:line):" >&2
           print_findings "$report" >&2; rm -f "$report"; return 1 ;;
        *) rm -f "$report"; fail "gitleaks exited $rc" ;;
    esac
}

# Fixture git: no user or system config, no inherited repository variables.
fx_git() {
    env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE -u GIT_OBJECT_DIRECTORY \
        GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 \
        GIT_AUTHOR_NAME=self-test GIT_AUTHOR_EMAIL=self-test@pelorus.invalid \
        GIT_COMMITTER_NAME=self-test GIT_COMMITTER_EMAIL=self-test@pelorus.invalid \
        git -c commit.gpgSign=false -c tag.gpgSign=false "$@"
}

fx_commit() {
    local repo="$1" file="$2" text="$3"
    printf '%s\n' "$text" > "$repo/$file"
    fx_git -C "$repo" add "$file"
    fx_git -C "$repo" commit -q -m "add $file"
}

# A token shaped like a GitHub personal access token, made at run time so
# that this file holds none.
fake_token() {
    python3 -I -c '
import secrets, string
print("ghp" + "_" + "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(36)))
'
}

# Each scan runs as its own process: errexit stays on inside it, and its
# exit cannot end the self-test.
expect_refused() {
    local name="$1" expect="$2" log="$3"
    shift 3
    if "$@" > "$log" 2>&1; then
        fail "self-test: $name was accepted"
    fi
    grep -q -- "$expect" "$log" || fail "self-test: $name failed elsewhere: $(tail -n 1 "$log")"
    echo "self-test: $name refused ($expect)"
}

TMP=""

self_test() {
    local tmp origin clone side
    check_binary
    TMP="$(mktemp -d)"
    trap 'rm -rf "$TMP"' EXIT
    tmp="$TMP"
    origin="$tmp/origin"
    fx_git init -q -b master "$origin"
    fx_commit "$origin" README "clean fixture"
    : > "$origin/.gitleaksignore"
    cp "$ROOT/.gitleaks.toml" "$origin/.gitleaks.toml"
    fx_git -C "$origin" add .gitleaksignore .gitleaks.toml
    fx_git -C "$origin" commit -q -m "add gitleaks configuration"
    "$0" "$origin" > "$tmp/clean.log" 2>&1 || fail "self-test: clean fixture was refused: $(tail -n 3 "$tmp/clean.log")"
    echo "self-test: clean fixture accepted"

    fx_git -C "$origin" checkout -q -b side
    fx_commit "$origin" leak.env "TOKEN=$(fake_token)"
    side="$(fx_git -C "$origin" rev-parse HEAD)"
    fx_git -C "$origin" checkout -q master
    clone="$tmp/clone"
    fx_git clone -q "$origin" "$clone"
    fx_git -C "$clone" show-ref --verify -q refs/remotes/origin/side \
        || fail "self-test: fixture clone has no refs/remotes/origin/side"
    ! fx_git -C "$clone" show-ref --verify -q refs/heads/side \
        || fail "self-test: fixture side branch is local, not remote-tracking"
    expect_refused "secret on a remote-tracking side branch" "$side:leak.env:github-pat:1" \
        "$tmp/side.log" "$0" "$clone"

    echo "$side:leak.env:github-pat:1" > "$clone/.gitleaksignore"
    "$0" "$clone" > "$tmp/ignored.log" 2>&1 || fail "self-test: listed fingerprint was refused: $(tail -n 3 "$tmp/ignored.log")"
    echo "self-test: secret accepted once its fingerprint is listed"
    : > "$clone/.gitleaksignore"

    fx_commit "$clone" allow.env "TOKEN=$(fake_token) # gitleaks:allow"
    expect_refused "secret marked gitleaks:allow" ":allow.env:github-pat:1" \
        "$tmp/allow.log" "$0" "$clone"

    printf '[extend]\nuseDefault = false\n' > "$origin/.gitleaks.toml"
    expect_refused ".gitleaks.toml without the default rules" "must be exactly useDefault = true" \
        "$tmp/nodefault.log" "$0" "$origin"
    printf '[extend]\nuseDefault = true\n[[allowlists]]\npaths = ["leak"]\n' > "$origin/.gitleaks.toml"
    expect_refused ".gitleaks.toml with an allowlist" "top-level keys \\['allowlists'\\]" \
        "$tmp/allowlist.log" "$0" "$origin"
    printf '[extend]\nuseDefault = true\n[[rules]]\nid = "x"\nregex = "x"\n[rules.allowlist]\npaths = ["leak"]\n' > "$origin/.gitleaks.toml"
    expect_refused "rule with its own allowlist" "rule x keys \\['allowlist'\\]" \
        "$tmp/ruleallow.log" "$0" "$origin"
    cp "$ROOT/.gitleaks.toml" "$origin/.gitleaks.toml"

    printf '#!/bin/sh\necho 0.0.0\n' > "$tmp/old-gitleaks"
    chmod +x "$tmp/old-gitleaks"
    expect_refused "gitleaks of another version" "gitleaks 0.0.0 found" \
        "$tmp/version.log" env GITLEAKS="$tmp/old-gitleaks" "$0" "$origin"
    echo "self-test: all cases behaved"
}

case "${1:-}" in
    --install) [ $# -eq 2 ] || fail "usage: $0 --install DIR"; install_gitleaks "$2" ;;
    --self-test) [ $# -eq 1 ] || fail "usage: $0 --self-test"; self_test ;;
    -*) fail "usage: $0 [--install DIR | --self-test | REPO]" ;;
    *) [ $# -le 1 ] || fail "usage: $0 [REPO]"; scan "${1:-$ROOT}" ;;
esac
