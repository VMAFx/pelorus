#!/usr/bin/env bash
#
# check-python-lint.sh — lint Python code with pinned Ruff and audit GitHub
# Actions workflows with pinned zizmor (ADR-0243).
#
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
#
# Usage:
#   scripts/check-python-lint.sh --install DIR
#       Create a virtualenv at DIR (which must not exist) and install the
#       tools pinned in .config/python-lint/requirements.txt with hash
#       checking. Prints the environment's executable directory on stdout
#       (DIR/bin, or DIR/Scripts on Windows).
#   scripts/check-python-lint.sh --ensure DIR
#       Keep DIR usable: reuse it when its ruff and zizmor match the lock,
#       otherwise remove it (only when it is a virtualenv) and --install
#       afresh. Covers a lock bump and a half-finished install.
#   scripts/check-python-lint.sh
#       Run Ruff on the repository and zizmor on the workflows.
#   scripts/check-python-lint.sh --self-test
#       Run the self-test mutation battery against planted defects and clean
#       fixtures, verifying version refusal and rule codes.
#
# Env:
#   PYTHON_LINT_BIN   Directory containing ruff and zizmor binaries
#   PYTHON_LINT_ENV   Virtual environment made by --install; used for
#                     PYTHON_LINT_BIN when that is unset
#   RUFF              Ruff binary path (default: $PYTHON_LINT_BIN/ruff or ruff)
#   ZIZMOR            zizmor binary path (default: $PYTHON_LINT_BIN/zizmor or zizmor)
#   PIP_TIMEOUT       pip installation deadline in seconds (default 300)
#   LINT_TIMEOUT      lint run deadline in seconds (default 300)
set -euo pipefail
# A failing command inside $(...) must fail the assignment that reads it.
shopt -s inherit_errexit

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# The hashed lock is the one pin source: Renovate bumps it, --install reads it.
LOCK="$ROOT/.config/python-lint/requirements.txt"
[ -f "$LOCK" ] || { echo "check-python-lint: missing $LOCK" >&2; exit 1; }

RUFF_VERSION="$(sed -n 's/^ruff==\([^ ]*\) .*$/\1/p' "$LOCK")"
ZIZMOR_VERSION="$(sed -n 's/^zizmor==\([^ ]*\) .*$/\1/p' "$LOCK")"

[ -n "$RUFF_VERSION" ] || { echo "check-python-lint: missing ruff== line in $LOCK" >&2; exit 1; }
[ -n "$ZIZMOR_VERSION" ] || { echo "check-python-lint: missing zizmor== line in $LOCK" >&2; exit 1; }

PIP_TIMEOUT="${PIP_TIMEOUT:-300}"
LINT_TIMEOUT="${LINT_TIMEOUT:-300}"

# A Windows virtual environment keeps its executables in Scripts, every
# other platform in bin.
venv_bin_dir() {
    if [ -d "$1/Scripts" ] && [ ! -d "$1/bin" ]; then
        echo "$1/Scripts"
    else
        echo "$1/bin"
    fi
}

if [ -z "${PYTHON_LINT_BIN:-}" ] && [ -n "${PYTHON_LINT_ENV:-}" ]; then
    PYTHON_LINT_BIN="$(venv_bin_dir "$PYTHON_LINT_ENV")"
fi

if [ -n "${PYTHON_LINT_BIN:-}" ]; then
    RUFF="${RUFF:-$PYTHON_LINT_BIN/ruff}"
    ZIZMOR="${ZIZMOR:-$PYTHON_LINT_BIN/zizmor}"
else
    RUFF="${RUFF:-ruff}"
    ZIZMOR="${ZIZMOR:-zizmor}"
fi

fail() {
    echo "check-python-lint: $*" >&2
    exit 1
}

install_tools() {
    local dir="$1" bin
    [ ! -e "$dir" ] || fail "--install: '$dir' already exists"
    timeout "$PIP_TIMEOUT" python3 -m venv "$dir" \
        || fail "--install: python3 -m venv failed for '$dir'"
    bin="$(venv_bin_dir "$dir")"
    timeout "$PIP_TIMEOUT" "$bin/python" -m pip install \
        --quiet --require-hashes --no-deps --only-binary=:all: \
        -r "$LOCK" \
        || fail "--install: pip install failed"
    echo "$bin"
}

check_binaries() {
    local ruff_ver zizmor_ver
    command -v "$RUFF" > /dev/null 2>&1 \
        || fail "ruff binary '$RUFF' not found (run scripts/check-python-lint.sh --install DIR)"
    command -v "$ZIZMOR" > /dev/null 2>&1 \
        || fail "zizmor binary '$ZIZMOR' not found (run scripts/check-python-lint.sh --install DIR)"
    ruff_ver="$(timeout "$LINT_TIMEOUT" "$RUFF" --version 2>&1)" || fail "failed to get ruff version"
    [ "$ruff_ver" = "ruff $RUFF_VERSION" ] \
        || fail "ruff '$ruff_ver' found, expected 'ruff $RUFF_VERSION'"
    zizmor_ver="$(timeout "$LINT_TIMEOUT" "$ZIZMOR" --version 2>&1)" || fail "failed to get zizmor version"
    [ "$zizmor_ver" = "zizmor $ZIZMOR_VERSION" ] \
        || fail "zizmor '$zizmor_ver' found, expected 'zizmor $ZIZMOR_VERSION'"
}

# An environment is current when both binaries in it report the locked
# versions; a lock bump or an install that stopped half-way fails this.
env_is_current() {
    local bin
    bin="$(venv_bin_dir "$1")"
    ( RUFF="$bin/ruff" ZIZMOR="$bin/zizmor" check_binaries ) > /dev/null 2>&1
}

ensure_tools() {
    local dir="$1"
    if env_is_current "$dir"; then
        venv_bin_dir "$dir"
        return 0
    fi
    if [ -e "$dir" ]; then
        # Remove only what --install made; never an arbitrary directory.
        [ -f "$dir/pyvenv.cfg" ] \
            || fail "--ensure: '$dir' exists but is no virtualenv; remove it by hand"
        rm -rf -- "$dir"
    fi
    install_tools "$dir"
}

# zizmor 1.32.0 exits 2 when a token variable is set but empty ("GitHub token
# cannot be empty"), even with --offline; drop the empty ones only.
drop_empty_tokens() {
    [ -n "${GH_TOKEN:-}" ] || unset GH_TOKEN
    [ -n "${GITHUB_TOKEN:-}" ] || unset GITHUB_TOKEN
    [ -n "${ZIZMOR_GITHUB_TOKEN:-}" ] || unset ZIZMOR_GITHUB_TOKEN
}

run_lint() {
    check_binaries
    local ruff_rc=0 zizmor_rc=0

    drop_empty_tokens
    timeout "$LINT_TIMEOUT" "$RUFF" check --no-cache --output-format concise --config "$ROOT/ruff.toml" "$ROOT" || ruff_rc=$?
    timeout "$LINT_TIMEOUT" "$ZIZMOR" --offline --no-progress --strict-collection --format plain "$ROOT" || zizmor_rc=$?

    if [ "$ruff_rc" -ne 0 ] || [ "$zizmor_rc" -ne 0 ]; then
        if [ "$ruff_rc" -ne 0 ] && [ "$zizmor_rc" -ne 0 ]; then
            fail "both ruff (exit $ruff_rc) and zizmor (exit $zizmor_rc) failed"
        elif [ "$ruff_rc" -ne 0 ]; then
            fail "ruff failed (exit $ruff_rc)"
        else
            fail "zizmor failed (exit $zizmor_rc)"
        fi
    fi
}

check_planted_ruff() {
    local name="$1" code="$2" file="$3" tmp="$4"
    local log="$tmp/ruff_${code}.log" rc=0
    timeout "$LINT_TIMEOUT" "$RUFF" check --no-cache --output-format concise --config "$tmp/ruff.toml" "$file" > "$log" 2>&1 || rc=$?
    if [ "$rc" -ne 0 ] && grep -q -- "$code" "$log"; then
        echo "PASS $name"
        return 0
    fi
    echo "FAIL $name" >&2
    return 1
}

self_test_ruff() {
    local tmp="$1" failed=0

    cat << 'EOF' > "$tmp/case_s110.py"
def test():
    try:
        f()
    except Exception:
        pass
EOF
    check_planted_ruff "ruff S110" "S110" "$tmp/case_s110.py" "$tmp" || failed=1

    cat << 'EOF' > "$tmp/case_sim103.py"
def test(x):
    if x:
        return True
    return False
EOF
    check_planted_ruff "ruff SIM103" "SIM103" "$tmp/case_sim103.py" "$tmp" || failed=1

    cat << 'EOF' > "$tmp/case_e721.py"
def test(a, b):
    return type(a) == type(b)
EOF
    check_planted_ruff "ruff E721" "E721" "$tmp/case_e721.py" "$tmp" || failed=1

    cat << 'EOF' > "$tmp/case_pgh004.py"
x = 1  # noqa
EOF
    check_planted_ruff "ruff PGH004" "PGH004" "$tmp/case_pgh004.py" "$tmp" || failed=1

    cat << 'EOF' > "$tmp/clean.py"
def test_s110():
    try:
        pass
    except OSError as exc:
        print(exc)


def test_sim103(x):
    return bool(x)


def test_e721(a, b):
    return isinstance(a, type(b))


x = 1  # noqa: E501
EOF
    local clean_log="$tmp/ruff_clean.log" clean_rc=0
    timeout "$LINT_TIMEOUT" "$RUFF" check --no-cache --output-format concise --config "$tmp/ruff.toml" "$tmp/clean.py" > "$clean_log" 2>&1 || clean_rc=$?
    if [ "$clean_rc" -eq 0 ]; then
        echo "PASS ruff clean"
    else
        echo "FAIL ruff clean" >&2
        failed=1
    fi

    return "$failed"
}

self_test_zizmor() {
    local tmp="$1" failed=0
    local wf_dir="$tmp/.github/workflows"
    mkdir -p "$wf_dir"

    drop_empty_tokens

    cat << 'EOF' > "$wf_dir/bad.yml"
name: Bad
on: issues
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: echo "${{ github.event.issue.title }}"
EOF
    local bad_log="$tmp/zizmor_bad.log" bad_rc=0
    timeout "$LINT_TIMEOUT" "$ZIZMOR" --offline --no-progress --strict-collection --format plain "$tmp" > "$bad_log" 2>&1 || bad_rc=$?
    if [ "$bad_rc" -ne 0 ] && grep -q -- "template-injection" "$bad_log"; then
        echo "PASS zizmor template-injection"
    else
        echo "FAIL zizmor template-injection" >&2
        failed=1
    fi

    rm -f "$wf_dir/bad.yml"
    cat << 'EOF' > "$wf_dir/clean.yml"
name: Clean
on: push
permissions:
  contents: read
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: echo "hello"
EOF
    local clean_log="$tmp/zizmor_clean.log" clean_rc=0
    timeout "$LINT_TIMEOUT" "$ZIZMOR" --offline --no-progress --strict-collection --format plain "$tmp" > "$clean_log" 2>&1 || clean_rc=$?
    if [ "$clean_rc" -eq 0 ]; then
        echo "PASS zizmor clean"
    else
        echo "FAIL zizmor clean" >&2
        failed=1
    fi

    return "$failed"
}

# --strict-collection: a workflow zizmor cannot parse must fail the scan, not
# be skipped with a warning while a valid neighbour passes.
self_test_zizmor_strict() {
    local tmp="$1" failed=0
    local wf_dir="$tmp/.github/workflows"
    mkdir -p "$wf_dir"
    drop_empty_tokens
    printf 'name: [unclosed\non: push\n' > "$wf_dir/broken.yml"
    cat << 'EOF' > "$wf_dir/fine.yml"
name: Fine
on: push
permissions:
  contents: read
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: echo "hello"
EOF
    local broken_log="$tmp/zizmor_broken.log" broken_rc=0
    timeout "$LINT_TIMEOUT" "$ZIZMOR" --offline --no-progress --strict-collection --format plain "$tmp" > "$broken_log" 2>&1 || broken_rc=$?
    if [ "$broken_rc" -ne 0 ] && grep -q -- "broken.yml" "$broken_log"; then
        echo "PASS zizmor unparseable workflow"
    else
        echo "FAIL zizmor unparseable workflow" >&2
        failed=1
    fi
    rm -f "$wf_dir/broken.yml" "$wf_dir/fine.yml"
    return "$failed"
}

self_test_version() {
    local tmp="$1" failed=0
    local stub="$tmp/stub-ruff"
    printf '#!/bin/sh\necho "ruff 0.0.1"\n' > "$stub"
    chmod +x "$stub"

    local stub_log="$tmp/stub.log"
    if ( RUFF="$stub" check_binaries ) > "$stub_log" 2>&1; then
        echo "FAIL version refusal" >&2
        failed=1
    else
        if grep -q -- "expected 'ruff $RUFF_VERSION'" "$stub_log"; then
            echo "PASS version refusal"
        else
            echo "FAIL version refusal" >&2
            failed=1
        fi
    fi

    return "$failed"
}

self_test_layout() {
    local tmp="$1" failed=0 got
    mkdir -p "$tmp/env-windows/Scripts" "$tmp/env-posix/bin"
    got="$(venv_bin_dir "$tmp/env-windows")"
    if [ "$got" = "$tmp/env-windows/Scripts" ]; then
        echo "PASS venv layout Scripts"
    else
        echo "FAIL venv layout Scripts" >&2
        failed=1
    fi
    got="$(venv_bin_dir "$tmp/env-posix")"
    if [ "$got" = "$tmp/env-posix/bin" ]; then
        echo "PASS venv layout bin"
    else
        echo "FAIL venv layout bin" >&2
        failed=1
    fi
    return "$failed"
}

# Stub environment: pyvenv.cfg plus ruff and zizmor that print a version.
make_stub_env() {
    mkdir -p "$1/bin"
    : > "$1/pyvenv.cfg"
    printf '#!/bin/sh\necho "ruff %s"\n' "$2" > "$1/bin/ruff"
    printf '#!/bin/sh\necho "zizmor %s"\n' "$3" > "$1/bin/zizmor"
    chmod +x "$1/bin/ruff" "$1/bin/zizmor"
}

self_test_ensure() {
    local tmp="$1" failed=0 env
    env="$tmp/env-current"
    make_stub_env "$env" "$RUFF_VERSION" "$ZIZMOR_VERSION"
    if env_is_current "$env"; then
        echo "PASS ensure keeps current env"
    else
        echo "FAIL ensure keeps current env" >&2
        failed=1
    fi
    env="$tmp/env-stale"
    make_stub_env "$env" "0.0.1" "$ZIZMOR_VERSION"
    if env_is_current "$env"; then
        echo "FAIL ensure replaces stale env" >&2
        failed=1
    else
        echo "PASS ensure replaces stale env"
    fi
    env="$tmp/env-partial"
    mkdir -p "$env/bin"
    : > "$env/pyvenv.cfg"
    if env_is_current "$env"; then
        echo "FAIL ensure replaces partial env" >&2
        failed=1
    else
        echo "PASS ensure replaces partial env"
    fi
    env="$tmp/not-a-venv"
    mkdir -p "$env"
    : > "$env/keep"
    if ( ensure_tools "$env" ) > /dev/null 2>&1 || [ ! -f "$env/keep" ]; then
        echo "FAIL ensure refuses non-venv dir" >&2
        failed=1
    else
        echo "PASS ensure refuses non-venv dir"
    fi
    return "$failed"
}

# Global, not local: the EXIT trap runs after self_test has returned.
SELF_TEST_DIR=""

self_test() {
    check_binaries
    local tmp failed=0
    SELF_TEST_DIR="$(mktemp -d)"
    trap 'rm -rf "$SELF_TEST_DIR"' EXIT
    tmp="$SELF_TEST_DIR"

    cp "$ROOT/ruff.toml" "$tmp/ruff.toml"

    self_test_ruff "$tmp" || failed=1
    self_test_zizmor "$tmp" || failed=1
    self_test_zizmor_strict "$tmp" || failed=1
    self_test_version "$tmp" || failed=1
    self_test_layout "$tmp" || failed=1
    self_test_ensure "$tmp" || failed=1

    [ "$failed" -eq 0 ] || exit 1
}

case "${1:-}" in
    --install)
        if [ $# -ne 2 ]; then
            echo "usage: $0 --install DIR" >&2
            exit 2
        fi
        install_tools "$2"
        ;;
    --ensure)
        if [ $# -ne 2 ]; then
            echo "usage: $0 --ensure DIR" >&2
            exit 2
        fi
        ensure_tools "$2"
        ;;
    --self-test)
        if [ $# -ne 1 ]; then
            echo "usage: $0 --self-test" >&2
            exit 2
        fi
        self_test
        ;;
    "")
        run_lint
        ;;
    *)
        echo "usage: $0 [--install DIR | --ensure DIR | --self-test]" >&2
        exit 2
        ;;
esac
