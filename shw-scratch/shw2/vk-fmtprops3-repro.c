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
    const VkFormat fmts[] = { VK_FORMAT_R8_UNORM, VK_FORMAT_R16_UNORM,
                              VK_FORMAT_G8_B8R8_2PLANE_420_UNORM,
                              VK_FORMAT_G10X6_B10X6R10X6_2PLANE_420_UNORM_3PACK16,
                              VK_FORMAT_G8_B8_R8_3PLANE_420_UNORM, VK_FORMAT_R8G8B8A8_UNORM };
    int clobbered = 0;
    for (size_t i = 0; i < sizeof(fmts) / sizeof(fmts[0]); i++) {
        VkFormatProperties3 f3;
        memset(&f3, 0xAB, sizeof(f3));
        f3.sType = VK_STRUCTURE_TYPE_FORMAT_PROPERTIES_3;
        f3.pNext = NULL;
        VkFormatProperties2 f2 = { VK_STRUCTURE_TYPE_FORMAT_PROPERTIES_2, &f3, { 0 } };
        vkGetPhysicalDeviceFormatProperties2(pd[want], fmts[i], &f2);
        int bad = f3.sType != VK_STRUCTURE_TYPE_FORMAT_PROPERTIES_3 || f3.pNext != NULL ||
                  f2.sType != VK_STRUCTURE_TYPE_FORMAT_PROPERTIES_2 || f2.pNext != &f3;
        clobbered |= bad;
        printf("fmt %4d: after call f2.sType=%d f2.pNext==&f3:%d f3.sType=%d (expect %d) f3.pNext=%p "
               "optimal=0x%llx %s\n", fmts[i], f2.sType, f2.pNext == &f3, f3.sType,
               VK_STRUCTURE_TYPE_FORMAT_PROPERTIES_3, f3.pNext,
               (unsigned long long)f3.optimalTilingFeatures, bad ? "CLOBBERED" : "ok");
    }
    vkDestroyInstance(inst, NULL);
    return clobbered ? 1 : 0;
}
