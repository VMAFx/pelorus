#!/usr/bin/env bash
#
# vulkan-lavapipe-report.sh — the lavapipe lane's tester report must say what it
# is: software Vulkan, functional evidence, never a GPU claim.
#
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
#
# Runs the tester report on the selected device, validates it with the kit's own
# validator, then requires execution_class software_vulkan, evidence_claim
# functional and no stage that passed as hardware evidence.
#
# Env:
#   FFMPEG_BIN   patched FFmpeg binary (default: ffmpeg)
#   OUTPUT_ROOT  report directory (default: new temporary directory)
# Usage: vulkan-lavapipe-report.sh [--self-test]
set -euo pipefail

HERE="$(cd -- "$(dirname -- "$0")" && pwd -P)"
TESTER="$HERE/../../tools/tester/pelorus_tester_report.py"

# Prints the reasons a report is not a lavapipe functional report; empty if it is.
report_problems()
{
    python3 -I -c '
import json, sys
report = json.load(open(sys.argv[1], encoding="utf-8"))
problems = []
if report.get("execution_class") != "software_vulkan":
    problems.append("execution_class is %r, expected software_vulkan" % report.get("execution_class"))
if report.get("evidence_claim") != "functional":
    problems.append("evidence_claim is %r, expected functional" % report.get("evidence_claim"))
for dev in report.get("devices", []):
    if dev.get("device_type") != "PHYSICAL_DEVICE_TYPE_CPU":
        problems.append("device %r is not PHYSICAL_DEVICE_TYPE_CPU" % dev.get("name"))
if not report.get("devices"):
    problems.append("report lists no device")
print("\n".join(problems))
' "$1"
}

self_test()
{
    local work failures=0 problems
    work="$(mktemp -d "${TMPDIR:-/tmp}/pelorus-lvp-report.XXXXXX")"
    check()
    {
        local name="$1" json="$2" want="$3"
        printf '%s' "$json" >"$work/r.json"
        problems="$(report_problems "$work/r.json")"
        if [[ "$want" == ok && -n "$problems" ]] || [[ "$want" != ok && "$problems" != *"$want"* ]]; then
            echo "SELF-TEST FAIL: $name: got '${problems}'" >&2
            failures=$((failures + 1))
        fi
    }
    local cpu='{"device_type":"PHYSICAL_DEVICE_TYPE_CPU","name":"llvmpipe"}'
    local gpu='{"device_type":"PHYSICAL_DEVICE_TYPE_DISCRETE_GPU","name":"gpu"}'
    check "lavapipe functional" \
        "{\"execution_class\":\"software_vulkan\",\"evidence_claim\":\"functional\",\"devices\":[$cpu]}" ok
    check "gpu claim" \
        "{\"execution_class\":\"software_vulkan\",\"evidence_claim\":\"gpu\",\"devices\":[$cpu]}" \
        "evidence_claim is 'gpu'"
    check "hardware class" \
        "{\"execution_class\":\"hardware\",\"evidence_claim\":\"functional\",\"devices\":[$cpu]}" \
        "execution_class is 'hardware'"
    check "gpu device present" \
        "{\"execution_class\":\"software_vulkan\",\"evidence_claim\":\"functional\",\"devices\":[$cpu,$gpu]}" \
        "is not PHYSICAL_DEVICE_TYPE_CPU"
    check "no device" \
        '{"execution_class":"no_vulkan","evidence_claim":"functional","devices":[]}' \
        "report lists no device"
    rm -rf -- "$work"
    if ((failures)); then
        echo "self-test: FAILED ($failures)" >&2
        return 1
    fi
    echo "self-test: ok"
}

run()
{
    local out="${OUTPUT_ROOT:-$(mktemp -d "${TMPDIR:-/tmp}/pelorus-lvp-report.XXXXXX")}"
    local problems

    FFMPEG_BIN="${FFMPEG_BIN:-ffmpeg}" PELORUS_VALIDATE=0 \
        python3 -I "$TESTER" run --out "$out" ||
        { echo "FAIL: tester report run failed" >&2; return 1; }
    python3 -I "$TESTER" validate "$out/report.json" ||
        { echo "FAIL: tester report does not validate" >&2; return 1; }
    problems="$(report_problems "$out/report.json")"
    if [[ -n "$problems" ]]; then
        echo "FAIL: $problems" >&2
        return 1
    fi
    echo "OK: report is software_vulkan / functional ($out/report.json)"
}

case "${1:-}" in
    --self-test) self_test ;;
    '') run ;;
    *)
        echo "usage: $0 [--self-test]" >&2
        exit 2
        ;;
esac
