#!/usr/bin/env bash
# Verify that the Vulkan matrix rejects unexpected VUIDs from either FFmpeg
# output stream while retaining the existing upstream allowlist behavior.
set -euo pipefail

HERE="$(cd -- "$(dirname -- "$0")" && pwd -P)"
RUN_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/pelorus-vulkan-validation.XXXXXX")"

cleanup()
{
    rm -rf -- "$RUN_ROOT"
}
trap cleanup EXIT

mkdir -p "$RUN_ROOT/bin"

cat >"$RUN_ROOT/bin/vulkaninfo" <<'EOF_VULKANINFO'
#!/usr/bin/env bash
echo 'VK_LAYER_KHRONOS_validation'
EOF_VULKANINFO

cat >"$RUN_ROOT/bin/ffmpeg" <<'EOF_FFMPEG'
#!/usr/bin/env bash
set -euo pipefail

if [[ " $* " == *' -filters '* ]]; then
    for filter in pelorus_analyze_vulkan pelorus_deblock_vulkan \
        pelorus_denoise_vulkan pelorus_aa_vulkan pelorus_dehalo_vulkan \
        pelorus_mc_vulkan; do
        printf ' ... %-30s V->V fake\n' "$filter"
    done
    exit 0
fi
if [[ " $* " == *' -version '* ]]; then
    echo 'ffmpeg version validation-self-test'
    exit 0
fi

count=0
if [[ -f "$PEL_FAKE_STATE" ]]; then
    read -r count <"$PEL_FAKE_STATE"
fi
count=$((count + 1))
printf '%s\n' "$count" >"$PEL_FAKE_STATE"

case "$PEL_FAKE_SCENARIO:$count" in
    device:1)
        echo 'Validation Error: VUID-vkCmdDispatch-imageLayout-00344'
        exit 0
        ;;
    pel-run:1)
        exit 0
        ;;
    pel-run:2)
        echo 'Validation Error: VUID-vkCmdDraw-None-09600'
        exit 0
        ;;
    pel-stderr:1)
        exit 0
        ;;
    pel-stderr:2)
        echo 'Validation Error: VUID-vkCmdDispatch-imageLayout-00344' >&2
        exit 0
        ;;
    allowlist:1)
        echo 'Validation Error: VUID-VkImageMemoryBarrier2-srcAccessMask-03909'
        echo 'Validation Error: VUID-VkCopyImageToMemoryInfo-srcImageLayout-09064'
        exit 0
        ;;
    allowlist:2)
        echo 'Validation Error: VUID-vkCmdDispatch-imageLayout-00344'
        exit 0
        ;;
esac

echo "unexpected invocation after $PEL_FAKE_SCENARIO stage $count" >&2
exit 65
EOF_FFMPEG
chmod +x "$RUN_ROOT/bin/ffmpeg" "$RUN_ROOT/bin/vulkaninfo"

run_case()
{
    local scenario="$1"
    local label="$2"
    local stream="$3"
    local vuid="$4"
    local case_root="$RUN_ROOT/$scenario"
    local status=0

    mkdir -p "$case_root/evidence"
    PATH="$RUN_ROOT/bin:$PATH" \
    PEL_FAKE_SCENARIO="$scenario" \
    PEL_FAKE_STATE="$case_root/state" \
    FFMPEG_BIN="$RUN_ROOT/bin/ffmpeg" \
    OUTPUT_ROOT="$case_root/evidence" \
    PELORUS_VALIDATE=1 \
        "$HERE/vulkan-format-matrix.sh" \
        >"$case_root/run.stdout" 2>"$case_root/run.stderr" || status=$?

    if ((status == 0)); then
        echo "FAIL: $scenario accepted an unexpected $stream VUID" >&2
        return 1
    fi
    grep -F \
        "Unexpected validation diagnostic in $case_root/evidence/${label}.${stream}: $vuid" \
        "$case_root/run.stderr" >/dev/null || {
        sed -n '1,120p' "$case_root/run.stderr" >&2
        echo "FAIL: $scenario did not attribute the $stream VUID" >&2
        return 1
    }
}

run_case device device-probe stdout VUID-vkCmdDispatch-imageLayout-00344
run_case pel-run analyze-yuv420p stdout \
    VUID-vkCmdDraw-None-09600
run_case pel-stderr analyze-yuv420p stderr \
    VUID-vkCmdDispatch-imageLayout-00344
run_case allowlist analyze-yuv420p stdout VUID-vkCmdDispatch-imageLayout-00344
grep -Fx 'VUID-VkImageMemoryBarrier2-srcAccessMask-03909' \
    "$RUN_ROOT/allowlist/evidence/validation-known-upstream.txt" >/dev/null
grep -Fx 'VUID-VkCopyImageToMemoryInfo-srcImageLayout-09064' \
    "$RUN_ROOT/allowlist/evidence/validation-known-upstream.txt" >/dev/null

echo 'Vulkan validation stream self-test: PASS'
