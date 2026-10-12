#!/usr/bin/env bash
#
# named-skip.sh — judge the exit status of a smoke that exits 77 to skip
# (ADR-0189). Exit 0 passes. Exit 77 passes only when the caller named the
# skip: ALLOWED is not empty and the smoke's last "SKIP: " line contains it as
# a fixed string. Any other status, an unnamed skip, or a skip for another
# reason fails, so a host whose device or binary went missing never reports
# green by accident.
#
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
#
# Usage:
#   named-skip.sh NAME STATUS LOG ALLOWED   exit 0 pass, 1 fail
#   named-skip.sh --self-test               plants each case
set -euo pipefail

# judge NAME STATUS LOG ALLOWED
judge()
{
    local name="$1" status="$2" log="$3" allowed="$4" reason

    if [[ "$status" == 0 ]]; then
        return 0
    fi
    if [[ "$status" != 77 ]]; then
        echo "ERROR: $name failed (exit $status)" >&2
        return 1
    fi
    if [[ ! -r "$log" ]]; then
        echo "ERROR: $name exited 77 and its log $log is not readable" >&2
        return 1
    fi
    reason="$(awk '/^SKIP: / { line = $0 } END { print line }' "$log")"
    if [[ -z "$reason" ]]; then
        echo "ERROR: $name exited 77 without a SKIP line in $log" >&2
        return 1
    fi
    if [[ -z "$allowed" ]]; then
        echo "ERROR: $name skipped and no skip was named: $reason" >&2
        return 1
    fi
    if [[ "$reason" != *"$allowed"* ]]; then
        echo "ERROR: $name skipped for another reason than the named one" \
            "('$allowed'): $reason" >&2
        return 1
    fi
    echo "$name skipped as named ('$allowed'): $reason"
}

self_test()
{
    local root log failures=0 label status text allowed want got
    root="$(mktemp -d "${TMPDIR:-/tmp}/pelorus-named-skip.XXXXXX")"
    log="$root/smoke.log"
    # label | status | smoke output | named skip | expected status
    local -a cases=(
        "pass|0||x|0"
        "pass, nothing named|0|||0"
        "named skip|77|SKIP: no libcuda: Cannot load libcuda.so.1|Cannot load libcuda.so.1|0"
        "unnamed skip|77|SKIP: no libcuda: Cannot load libcuda.so.1||1"
        "skip for another reason|77|SKIP: no usable Vulkan device|Cannot load libcuda.so.1|1"
        "77 without a SKIP line|77|done|Cannot load libcuda.so.1|1"
        "last SKIP line decides|77|SKIP: Cannot load libcuda.so.1\nSKIP: no usable Vulkan device|Cannot load libcuda.so.1|1"
        "failure|1|SKIP: Cannot load libcuda.so.1|Cannot load libcuda.so.1|1"
    )
    for entry in "${cases[@]}"; do
        IFS='|' read -r label status text allowed want <<<"$entry"
        printf '%b\n' "$text" >"$log"
        got=0
        judge "self-test" "$status" "$log" "$allowed" >/dev/null 2>&1 || got=$?
        if [[ "$got" != "$want" ]]; then
            echo "self-test FAIL: $label: status $got, want $want"
            failures=$((failures + 1))
        fi
    done
    got=0
    judge "self-test" 77 "$root/absent.log" "x" >/dev/null 2>&1 || got=$?
    if [[ "$got" != 1 ]]; then
        echo "self-test FAIL: missing log: status $got, want 1"
        failures=$((failures + 1))
    fi
    rm -rf -- "$root"
    echo "self-test: $((${#cases[@]} + 1)) cases, $failures failures"
    [[ "$failures" == 0 ]]
}

if [[ "${1:-}" == --self-test && $# == 1 ]]; then
    self_test
    exit
fi
if [[ $# != 4 ]]; then
    echo "usage: named-skip.sh NAME STATUS LOG ALLOWED | --self-test" >&2
    exit 2
fi
judge "$@"
