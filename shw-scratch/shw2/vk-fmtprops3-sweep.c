/* shw-2 attribution probe for VUID-VkFormatProperties2-pNext-pNext seen from
 * stock FFmpeg hwcontext_vulkan (vkfmt_from_pixfmt2 reuses one
 * VkFormatProperties2 -> VkFormatProperties3 chain for a second query).
 * Question: does the driver clobber the chained VkFormatProperties3 header
 * (sType/pNext) during the first call?  Run WITHOUT the validation layer. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <vulkan/vulkan.h>

int main(int argc, char **argv)
{
    unsigned want = argc > 1 ? (unsigned)strtoul(argv[1], NULL, 10) : 0;
    VkApplicationInfo app = { VK_STRUCTURE_TYPE_APPLICATION_INFO, NULL, "pel-fmtprops3", 1,
                              NULL, 0, VK_API_VERSION_1_3 };
    VkInstanceCreateInfo ici = { VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, NULL, 0, &app,
                                 0, NULL, 0, NULL };
    VkInstance inst;
    if (vkCreateInstance(&ici, NULL, &inst) != VK_SUCCESS) return 2;
    uint32_t n = 8;
    VkPhysicalDevice pd[8];
    vkEnumeratePhysicalDevices(inst, &n, pd);
    if (want >= n) return 3;
    VkPhysicalDeviceProperties props;
    vkGetPhysicalDeviceProperties(pd[want], &props);
    printf("device %u: %s driver 0x%x api %u.%u.%u\n", want, props.deviceName, props.driverVersion,
           VK_API_VERSION_MAJOR(props.apiVersion), VK_API_VERSION_MINOR(props.apiVersion),
           VK_API_VERSION_PATCH(props.apiVersion));
    int clobbered = 0, calls = 0;
    const struct { int lo, hi; } ranges[] = { { 1, 184 }, { 1000156000, 1000156033 },
                                              { 1000330000, 1000330003 }, { 1000340000, 1000340001 } };
    for (size_t r = 0; r < sizeof(ranges) / sizeof(ranges[0]); r++)
        for (int f = ranges[r].lo; f <= ranges[r].hi; f++) {
            VkFormatProperties3 f3 = { VK_STRUCTURE_TYPE_FORMAT_PROPERTIES_3, NULL, 0, 0, 0 };
            VkFormatProperties2 f2 = { VK_STRUCTURE_TYPE_FORMAT_PROPERTIES_2, &f3, { 0 } };
            vkGetPhysicalDeviceFormatProperties2(pd[want], (VkFormat)f, &f2);
            calls++;
            if (f3.sType != VK_STRUCTURE_TYPE_FORMAT_PROPERTIES_3 || f3.pNext != NULL ||
                f2.pNext != &f3) {
                clobbered++;
                printf("fmt %d: CLOBBERED f2.pNext==&f3:%d f3.sType=%d f3.pNext=%p\n", f,
                       f2.pNext == &f3, f3.sType, f3.pNext);
            }
        }
    printf("%d queries, %d clobbered the chained VkFormatProperties3 header\n", calls, clobbered);
    vkDestroyInstance(inst, NULL);
    return clobbered ? 1 : 0;
}
