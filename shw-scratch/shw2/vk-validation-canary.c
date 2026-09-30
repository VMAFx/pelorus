/* shw-2 positive control: prove VK_LAYER_KHRONOS_validation is really loaded
 * into a process that picks up vulkan-1.dll the same way ffmpeg.exe does, and
 * that its default callback reaches a captured stream.  The app requests NO
 * layer itself (like the matrix, which relies on VK_INSTANCE_LAYERS), then
 * makes a deliberately invalid vkCreateBuffer (size 0, usage 0). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <vulkan/vulkan.h>

int main(int argc, char **argv)
{
    unsigned want = argc > 1 ? (unsigned)strtoul(argv[1], NULL, 10) : 0;
    VkApplicationInfo app = { VK_STRUCTURE_TYPE_APPLICATION_INFO, NULL, "pel-vvl-canary", 1,
                              NULL, 0, VK_API_VERSION_1_3 };
    VkInstanceCreateInfo ici = { VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, NULL, 0, &app,
                                 0, NULL, 0, NULL };
    VkInstance inst;
    VkResult r = vkCreateInstance(&ici, NULL, &inst);
    if (r != VK_SUCCESS) { fprintf(stderr, "vkCreateInstance %d\n", r); return 2; }
    uint32_t n = 0;
    vkEnumeratePhysicalDevices(inst, &n, NULL);
    VkPhysicalDevice pd[8];
    if (n > 8) n = 8;
    vkEnumeratePhysicalDevices(inst, &n, pd);
    if (want >= n) { fprintf(stderr, "no device %u (have %u)\n", want, n); return 3; }
    VkPhysicalDeviceProperties props;
    vkGetPhysicalDeviceProperties(pd[want], &props);
    fprintf(stderr, "canary device %u: %s driver 0x%x\n", want, props.deviceName, props.driverVersion);
    float prio = 1.0f;
    VkDeviceQueueCreateInfo qci = { VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO, NULL, 0, 0, 1, &prio };
    VkDeviceCreateInfo dci = { VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, NULL, 0, 1, &qci,
                               0, NULL, 0, NULL, NULL };
    VkDevice dev;
    r = vkCreateDevice(pd[want], &dci, NULL, &dev);
    if (r != VK_SUCCESS) { fprintf(stderr, "vkCreateDevice %d\n", r); return 4; }
    /* Deliberately invalid: size must be > 0 and usage must be non-zero. */
    VkBufferCreateInfo bci = { VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO, NULL, 0, 0, 0,
                               VK_SHARING_MODE_EXCLUSIVE, 0, NULL };
    VkBuffer buf = VK_NULL_HANDLE;
    r = vkCreateBuffer(dev, &bci, NULL, &buf);
    fprintf(stderr, "vkCreateBuffer(size=0,usage=0) -> %d\n", r);
    if (buf != VK_NULL_HANDLE)
        vkDestroyBuffer(dev, buf, NULL);
    vkDestroyDevice(dev, NULL);
    vkDestroyInstance(inst, NULL);
    return 0;
}
