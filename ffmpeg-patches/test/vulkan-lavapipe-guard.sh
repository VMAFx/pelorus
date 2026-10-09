#!/usr/bin/env bash
#
# vulkan-lavapipe-guard.sh — refuse to run the lavapipe lane on anything but
# Mesa lavapipe with the device facts the Pelorus filters rely on.
#
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
#
# Checks `vulkaninfo` output (read from VULKANINFO_FILE, else run live):
#   - exactly one device, deviceType PHYSICAL_DEVICE_TYPE_CPU, driver llvmpipe
#   - every extension in vulkan-required-extensions.txt, each missing one named
#   - shaderStorageImageReadWithoutFormat (GL_EXT_shader_image_load_formatted,
#     nine shaders) and subgroup ARITHMETIC (GL_KHR_shader_subgroup_arithmetic,
#     pelorus_mc)
#   - the Khronos validation layer, because the lane runs with PELORUS_VALIDATE=1
# Every problem is reported; any problem exits 1. The lane is functional
# evidence on software Vulkan, never GPU evidence.
#
# Usage: vulkan-lavapipe-guard.sh [--self-test] [--disable RULE]
#   RULE is one of: device_count device_type driver extensions feature subgroup layer
#   (--disable switches one rule off; the self-test must then fail).
set -euo pipefail

HERE="$(cd -- "$(dirname -- "$0")" && pwd -P)"
REQUIRED_FILE="${PEL_REQUIRED_EXTENSIONS:-$HERE/vulkan-required-extensions.txt}"
DISABLED=" "
SELF_TEST=0
while (($#)); do
    case "$1" in
        --self-test) SELF_TEST=1 ;;
        --disable)
            shift
            DISABLED+="${1:?--disable needs a rule} "
            ;;
        *)
            echo "usage: $0 [--self-test] [--disable RULE]" >&2
            exit 2
            ;;
    esac
    shift
done

rule_on()
{
    [[ "$DISABLED" != *" $1 "* ]]
}

# Prints the extension names of the list; a malformed row prints "!<row>" and
# makes the exit status non-zero.
required_extensions()
{
    awk -F'|' '
        /^[[:space:]]*(#|$)/ { next }
        { gsub(/^[[:space:]]+|[[:space:]]+$/, "", $1) }
        $1 !~ /^VK_[A-Za-z0-9_]+$/ { bad = 1; print "!" $0; next }
        { print $1 }
        END { exit bad }' "$1"
}

has_device_extension()
{
    awk -v want="$1" '
        /Device Extensions:/ { in_ext = 1; next }
        in_ext && $1 !~ /^VK_/ { in_ext = 0 }
        in_ext && $1 == want { found = 1 }
        END { exit !found }' "$2"
}

# Prints one line per problem on stdout; returns 1 when there is any.
check_text()
{
    local text_file="$1"
    local required_file="$2"
    local problems=0
    local count ext

    problem()
    {
        echo "$*"
        problems=$((problems + 1))
    }

    if ! count="$(grep -c '^[[:space:]]*deviceType[[:space:]]*=' "$text_file")"; then
        count=0 # grep -c prints 0 and exits 1 when no line matches
    fi
    if rule_on device_count && [[ "$count" != 1 ]]; then
        problem "expected exactly one Vulkan device (set VK_DRIVER_FILES to the lavapipe ICD), found $count"
    fi
    if rule_on device_type &&
        grep '^[[:space:]]*deviceType[[:space:]]*=' "$text_file" |
        grep -qv 'PHYSICAL_DEVICE_TYPE_CPU'; then
        problem "a device is not PHYSICAL_DEVICE_TYPE_CPU; the lane is software-Vulkan only"
    fi
    if rule_on driver &&
        ! grep -q '^[[:space:]]*driverName[[:space:]]*=[[:space:]]*llvmpipe' "$text_file"; then
        problem "driverName is not llvmpipe"
    fi
    if rule_on extensions; then
        while IFS= read -r ext; do
            if [[ "$ext" == '!'* ]]; then
                problem "required-extension list has a malformed row: ${ext#!}"
            elif ! has_device_extension "$ext" "$text_file"; then
                problem "required Vulkan device extension missing: $ext"
            fi
        done < <(required_extensions "$required_file")
    fi
    if rule_on feature &&
        ! grep -Eq '^[[:space:]]*shaderStorageImageReadWithoutFormat[[:space:]]*=[[:space:]]*true' "$text_file"; then
        problem "required device feature missing: shaderStorageImageReadWithoutFormat"
    fi
    if rule_on subgroup && ! grep -q 'SUBGROUP_FEATURE_ARITHMETIC_BIT' "$text_file"; then
        problem "required subgroup operation missing: SUBGROUP_FEATURE_ARITHMETIC_BIT"
    fi
    if rule_on layer && ! grep -q 'VK_LAYER_KHRONOS_validation' "$text_file"; then
        problem "required Vulkan layer missing: VK_LAYER_KHRONOS_validation"
    fi
    ((problems == 0))
}

good_text()
{
    cat <<'EOT'
Layers: count = 1
	VK_LAYER_KHRONOS_validation (Khronos Validation Layer) Vulkan version 1.4.363, layer version 1
Device Properties and Extensions:
GPU0:
	deviceType        = PHYSICAL_DEVICE_TYPE_CPU
	driverName        = llvmpipe
	subgroupSupportedOperations: count = 2
		SUBGROUP_FEATURE_BASIC_BIT
		SUBGROUP_FEATURE_ARITHMETIC_BIT
	shaderStorageImageReadWithoutFormat     = true
Device Extensions: count = 4
	VK_EXT_host_image_copy                             : extension revision 1
	VK_EXT_shader_object                               : extension revision 1
	VK_KHR_push_descriptor                             : extension revision 2
	VK_KHR_swapchain                                   : extension revision 70
EOT
}

# One planted defect per case; each must be rejected with its expected text.
self_test()
{
    local work failures=0 out

    work="$(mktemp -d "${TMPDIR:-/tmp}/pelorus-lvp-guard.XXXXXX")"
    good_text >"$work/good"

    expect_reject()
    {
        local name="$1" sedexpr="$2" want="$3"
        sed "$sedexpr" "$work/good" >"$work/bad"
        if cmp -s "$work/good" "$work/bad"; then
            echo "SELF-TEST FAIL: $name: mutation changed nothing" >&2
            failures=$((failures + 1))
        elif out="$(check_text "$work/bad" "$REQUIRED_FILE")" || [[ "$out" != *"$want"* ]]; then
            echo "SELF-TEST FAIL: $name: accepted or wrong message: $out" >&2
            failures=$((failures + 1))
        fi
    }

    if ! out="$(check_text "$work/good" "$REQUIRED_FILE")"; then
        echo "SELF-TEST FAIL: good device text rejected: $out" >&2
        failures=$((failures + 1))
    fi
    expect_reject "no shader object" '/VK_EXT_shader_object/d' \
        "required Vulkan device extension missing: VK_EXT_shader_object"
    expect_reject "no push descriptor" '/VK_KHR_push_descriptor/d' \
        "required Vulkan device extension missing: VK_KHR_push_descriptor"
    expect_reject "no host image copy" '/VK_EXT_host_image_copy/d' \
        "required Vulkan device extension missing: VK_EXT_host_image_copy"
    expect_reject "extension only in another section" \
        's/^Device Extensions:/Instance Extensions:/' \
        "required Vulkan device extension missing: VK_EXT_shader_object"
    expect_reject "gpu device" \
        's/PHYSICAL_DEVICE_TYPE_CPU/PHYSICAL_DEVICE_TYPE_DISCRETE_GPU/' \
        "not PHYSICAL_DEVICE_TYPE_CPU"
    expect_reject "second device" \
        's/^GPU0:/GPU0:\n\tdeviceType = PHYSICAL_DEVICE_TYPE_CPU/' \
        "exactly one Vulkan device"
    expect_reject "other software driver" 's/llvmpipe/swiftshader/' \
        "driverName is not llvmpipe"
    expect_reject "no formatted image read" \
        's/shaderStorageImageReadWithoutFormat     = true/shaderStorageImageReadWithoutFormat     = false/' \
        "shaderStorageImageReadWithoutFormat"
    expect_reject "no subgroup arithmetic" '/ARITHMETIC_BIT/d' \
        "SUBGROUP_FEATURE_ARITHMETIC_BIT"
    expect_reject "no validation layer" '/VK_LAYER_KHRONOS_validation/d' \
        "VK_LAYER_KHRONOS_validation"

    rm -rf -- "$work"
    if ((failures)); then
        echo "self-test: FAILED ($failures)" >&2
        return 1
    fi
    echo "self-test: ok"
}

if ((SELF_TEST)); then
    self_test
    exit
fi

[[ -r "$REQUIRED_FILE" ]] || {
    echo "FAIL: required-extension list is unreadable: $REQUIRED_FILE" >&2
    exit 1
}
WORK="$(mktemp -d "${TMPDIR:-/tmp}/pelorus-lvp-guard.XXXXXX")"
trap 'rm -rf -- "$WORK"' EXIT
if [[ -n "${VULKANINFO_FILE:-}" ]]; then
    cp -- "$VULKANINFO_FILE" "$WORK/info"
else
    command -v vulkaninfo >/dev/null 2>&1 || {
        echo "FAIL: vulkaninfo not found; install vulkan-tools" >&2
        exit 1
    }
    vulkaninfo >"$WORK/info" 2>"$WORK/info.err" || {
        cat "$WORK/info.err" >&2
        echo "FAIL: vulkaninfo failed" >&2
        exit 1
    }
fi
if out="$(check_text "$WORK/info" "$REQUIRED_FILE")"; then
    echo "OK: single llvmpipe CPU device, required extensions, features and layer present"
else
    while IFS= read -r line; do
        echo "FAIL: $line" >&2
    done <<<"$out"
    exit 1
fi
