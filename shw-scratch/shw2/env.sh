# shw-2 common environment (source from MSYS2 UCRT64 bash).
export PATH="/c/tmp/pel/prefix/bin:/ucrt64/bin:$PATH"
export FFM=/c/tmp/pel/prefix/bin/ffmpeg.exe
export FFP=/c/tmp/pel/prefix/bin/ffprobe.exe
export RES=/c/tmp/pel/shw-results/shw-2
export SCR=/c/tmp/pel/shw-scratch/shw2
export REPO=/c/tmp/pel/shw
# Validation layer choice: VVL="msys" -> MSYS2 VkLayer_khronos_validation 1.4.357
# (only that directory is searched for explicit layers); VVL="sdk" -> LunarG SDK
# 1.4.341.1 (registry-registered).  Windows-form paths: the Vulkan loader is native.
pel_vvl_env()
{
    case "${1:-msys}" in
        msys) echo 'VK_LAYER_PATH=C:\msys64\ucrt64\bin' ;;
        sdk)  echo 'VK_LAYER_PATH=C:\VulkanSDK\1.4.341.1\Bin' ;;
        *) echo "bad VVL $1" >&2; return 1 ;;
    esac
}
