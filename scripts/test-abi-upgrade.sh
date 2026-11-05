#!/usr/bin/env bash
#
# test-abi-upgrade.sh — cross-version interop ABI upgrade test
#
# SPDX-FileCopyrightText: 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2

set -euo pipefail

export GIT_CONFIG_GLOBAL=/dev/null
export GIT_CONFIG_NOSYSTEM=1
unset GIT_COMMITTER_NAME GIT_COMMITTER_EMAIL GIT_COMMITTER_DATE
for _git_var in $(git rev-parse --local-env-vars); do
    unset "$_git_var"
done
unset _git_var

OLD_REF="v0.4.1"
BUILD_DIR=""
SELF_TEST=0
EXPLICIT_OLD_REF=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --old-ref)
            OLD_REF="$2"
            EXPLICIT_OLD_REF=1
            shift 2
            ;;
        --build-dir)
            BUILD_DIR="$2"
            shift 2
            ;;
        --self-test)
            SELF_TEST=1
            shift
            ;;
        *)
            echo "Unknown argument: $1" >&2
            exit 2
            ;;
    esac
done

if [[ -z "$BUILD_DIR" ]]; then
    BUILD_DIR="build-upgrade"
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$BUILD_DIR"
BUILD_DIR="$(cd "$BUILD_DIR" && pwd)"

OLD_TREE=""
cleanup() {
    local rc=$?
    if [[ -n "${OLD_TREE:-}" && -d "${OLD_TREE:-}" ]]; then
        if ! timeout 600 env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE \
            -u GIT_OBJECT_DIRECTORY -u GIT_ALTERNATE_OBJECT_DIRECTORIES \
            -u GIT_COMMON_DIR \
            git -C "$REPO_ROOT" -c core.hooksPath=/dev/null \
            worktree remove --force "$OLD_TREE"; then
            echo "warning: could not remove worktree $OLD_TREE" >&2
            if [[ $rc -eq 0 ]]; then rc=1; fi
        fi
        if [[ -d "$OLD_TREE" ]] && ! rm -rf "$OLD_TREE"; then
            echo "warning: could not delete $OLD_TREE" >&2
            if [[ $rc -eq 0 ]]; then rc=1; fi
        fi
    fi
    exit "$rc"
}
trap cleanup EXIT

build_new_tree() {
    if [[ ! -f "$BUILD_DIR/new/build.ninja" ]]; then
        timeout 600 meson setup "$BUILD_DIR/new" "$REPO_ROOT"
    fi
    timeout 600 ninja -C "$BUILD_DIR/new" -j4
    timeout 600 cc -O2 -Wall -Wextra -Werror \
        -I"$REPO_ROOT/libpelorus/include" \
        "$REPO_ROOT/tools/abi-upgrade/abi_upgrade_probe.c" \
        -L"$BUILD_DIR/new/libpelorus" -lpelorus \
        -Wl,-rpath,"$BUILD_DIR/new/libpelorus" \
        -o "$BUILD_DIR/probe_new"
}

run_self_test() {
    build_new_tree
    local st_valid="$BUILD_DIR/self_test_valid.bin"
    local st_corrupt="$BUILD_DIR/self_test_corrupt.bin"
    local st_trunc="$BUILD_DIR/self_test_trunc.bin"

    rm -f "$st_valid" "$st_corrupt" "$st_trunc"
    timeout 600 "$BUILD_DIR/probe_new" pack "$st_valid"

    cp "$st_valid" "$st_corrupt"
    timeout 600 python3 -I -B -c '
import sys
with open(sys.argv[1], "r+b") as f:
    f.seek(300)
    b = f.read(1)
    f.seek(300)
    f.write(bytes([b[0] ^ 0xFF]))
' "$st_corrupt"
    local corrupt_rc=0
    set +e
    local corrupt_out
    corrupt_out="$(timeout 600 "$BUILD_DIR/probe_new" read "$st_corrupt" 2>/dev/null)"
    corrupt_rc=$?
    set -e
    if [[ $corrupt_rc -ne 1 || "$corrupt_out" != *"status=mismatch"* ]]; then
        echo "error: self-test failed: corrupt section payload expected rc=1, got rc=$corrupt_rc" >&2
        exit 1
    fi

    timeout 600 head -c 32 "$st_valid" > "$st_trunc"
    local trunc_rc=0
    set +e
    timeout 600 "$BUILD_DIR/probe_new" read "$st_trunc" >/dev/null 2>&1
    trunc_rc=$?
    set -e
    if [[ $trunc_rc -ne 1 ]]; then
        echo "error: self-test failed: truncated header expected rc=1, got rc=$trunc_rc" >&2
        exit 1
    fi

    echo "self-test: ok"
}

if [[ $SELF_TEST -eq 1 ]]; then
    run_self_test
    if [[ $EXPLICIT_OLD_REF -eq 0 ]]; then
        exit 0
    fi
fi

OLD_TREE="$(mktemp -d "${TMPDIR:-/tmp}/pelorus-abi-upgrade-old-XXXXXX")"
timeout 600 env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE \
    -u GIT_OBJECT_DIRECTORY -u GIT_ALTERNATE_OBJECT_DIRECTORIES \
    -u GIT_COMMON_DIR \
    git -C "$REPO_ROOT" -c core.hooksPath=/dev/null \
    worktree add --detach "$OLD_TREE" "$OLD_REF"

build_new_tree

rm -rf "$BUILD_DIR/old"
timeout 600 meson setup "$BUILD_DIR/old" "$OLD_TREE"
timeout 600 ninja -C "$BUILD_DIR/old" -j4
timeout 600 cc -O2 -Wall -Wextra -Werror \
    -I"$OLD_TREE/libpelorus/include" \
    "$REPO_ROOT/tools/abi-upgrade/abi_upgrade_probe.c" \
    -L"$BUILD_DIR/old/libpelorus" -lpelorus \
    -Wl,-rpath,"$BUILD_DIR/old/libpelorus" \
    -o "$BUILD_DIR/probe_old"

# 4 cross cases
rm -f "$BUILD_DIR/blob_new.bin" "$BUILD_DIR/blob_old.bin"
echo "case=new-pack->new-read"
if ! timeout 600 "$BUILD_DIR/probe_new" pack "$BUILD_DIR/blob_new.bin"; then
    echo "error: case new-pack->new-read pack failed" >&2
    exit 1
fi
if ! timeout 600 "$BUILD_DIR/probe_new" read "$BUILD_DIR/blob_new.bin"; then
    echo "error: case new-pack->new-read read failed" >&2
    exit 1
fi

echo "case=new-pack->old-read"
if ! timeout 600 "$BUILD_DIR/probe_old" read "$BUILD_DIR/blob_new.bin"; then
    echo "error: case new-pack->old-read read failed" >&2
    exit 1
fi

echo "case=old-pack->new-read"
if ! timeout 600 "$BUILD_DIR/probe_old" pack "$BUILD_DIR/blob_old.bin"; then
    echo "error: case old-pack->new-read pack failed" >&2
    exit 1
fi
if ! timeout 600 "$BUILD_DIR/probe_new" read "$BUILD_DIR/blob_old.bin"; then
    echo "error: case old-pack->new-read read failed" >&2
    exit 1
fi

echo "case=old-pack->old-read"
if ! timeout 600 "$BUILD_DIR/probe_old" read "$BUILD_DIR/blob_old.bin"; then
    echo "error: case old-pack->old-read read failed" >&2
    exit 1
fi
