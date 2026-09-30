/* shw-2 attribution probe: VK_EXT_host_image_copy round trip on Intel Windows.
 *
 * Stock FFmpeg n9.0.2 corrupts a bare hwupload->hwdownload round trip of every
 * multi-plane YUV format on the Arc B580 and UHD 770 (Windows 101.9033/101.7092),
 * while disable_multiplane=1 is exact.  This program takes FFmpeg out of the loop:
 * it creates one image, uploads a known pattern plane by plane with
 * vkCopyMemoryToImageEXT and reads it back with vkCopyImageToMemoryEXT.
 *
 *   seq "clean": UNDEFINED -> GENERAL (host transition), copy in, copy out.
 *   seq "ffmpeg": mimics hwcontext_vulkan: UNDEFINED -> TRANSFER_DST_OPTIMAL
 *                 (switch_layout_host), then TRANSFER_DST_OPTIMAL -> GENERAL
 *                 (vulkan_transfer_host, the VUID-...-09230 transition), copy in,
 *                 copy out.
 *
 * usage: vk-hostcopy-repro.exe <device-index>
 * exit 0 = every supported case exact; 1 = at least one mismatch. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <vulkan/vulkan.h>

#define W 96
#define H 64
#define PITCH_PAD 32 /* extra texels per row, like FFmpeg's padded linesize */

static PFN_vkCopyMemoryToImageEXT pCopyMemoryToImage;
static PFN_vkCopyImageToMemoryEXT pCopyImageToMemory;
static PFN_vkTransitionImageLayoutEXT pTransition;

typedef struct Case {
    const char *name;
    VkFormat fmt;
    int planes;
    int texel_bytes[3]; /* bytes per texel of each plane view */
    int sub[3];         /* log2 chroma subsampling per plane */
} Case;

static const Case cases[] = {
    { "R8_UNORM (1 plane)", VK_FORMAT_R8_UNORM, 1, { 1 }, { 0 } },
    { "R8G8B8A8_UNORM (1 plane)", VK_FORMAT_R8G8B8A8_UNORM, 1, { 4 }, { 0 } },
    { "G8_B8_R8_3PLANE_420 (yuv420p)", VK_FORMAT_G8_B8_R8_3PLANE_420_UNORM, 3, { 1, 1, 1 }, { 0, 1, 1 } },
    { "G8_B8R8_2PLANE_420 (nv12)", VK_FORMAT_G8_B8R8_2PLANE_420_UNORM, 2, { 1, 2 }, { 0, 1 } },
    { "G16_B16R16_2PLANE_420 (p016)", VK_FORMAT_G16_B16R16_2PLANE_420_UNORM, 2, { 2, 4 }, { 0, 1 } },
};

static const char *layout_name(VkImageLayout l)
{
    switch (l) {
    case VK_IMAGE_LAYOUT_GENERAL: return "GENERAL";
    case VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL: return "TRANSFER_SRC_OPTIMAL";
    case VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL: return "TRANSFER_DST_OPTIMAL";
    case VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL: return "SHADER_READ_ONLY_OPTIMAL";
    case VK_IMAGE_LAYOUT_PRESENT_SRC_KHR: return "PRESENT_SRC_KHR";
    default: return "other";
    }
}

static int layout_listed(const VkImageLayout *list, uint32_t n, VkImageLayout l)
{
    for (uint32_t i = 0; i < n; i++)
        if (list[i] == l)
            return 1;
    return 0;
}

static int transition(VkDevice dev, VkImage img, VkImageLayout from, VkImageLayout to, int planes)
{
    VkHostImageLayoutTransitionInfoEXT t = {
        .sType = VK_STRUCTURE_TYPE_HOST_IMAGE_LAYOUT_TRANSITION_INFO_EXT,
        .image = img,
        .oldLayout = from,
        .newLayout = to,
        .subresourceRange = { .aspectMask = VK_IMAGE_ASPECT_COLOR_BIT, .levelCount = 1,
                              .layerCount = 1 },
    };
    (void)planes; /* FFmpeg uses COLOR for the whole multi-plane image, as here */
    return pTransition(dev, 1, &t) == VK_SUCCESS ? 0 : -1;
}

static int run_case(VkPhysicalDevice pd, VkDevice dev, const VkPhysicalDeviceMemoryProperties *mp,
                    const Case *c, VkImageTiling tiling, int ffmpeg_seq,
                    const VkPhysicalDeviceHostImageCopyPropertiesEXT *hp)
{
    const char *tname = tiling == VK_IMAGE_TILING_OPTIMAL ? "optimal" : "linear";
    const char *sname = ffmpeg_seq ? "ffmpeg-seq" : "clean-seq";
    VkImageUsageFlags usage = VK_IMAGE_USAGE_TRANSFER_SRC_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT |
                              VK_IMAGE_USAGE_SAMPLED_BIT | VK_IMAGE_USAGE_STORAGE_BIT |
                              VK_IMAGE_USAGE_HOST_TRANSFER_BIT_EXT;
    VkImageCreateFlags flags = VK_IMAGE_CREATE_MUTABLE_FORMAT_BIT;
    if (c->planes > 1)
        flags |= VK_IMAGE_CREATE_EXTENDED_USAGE_BIT;

    VkPhysicalDeviceImageFormatInfo2 fi = { VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_IMAGE_FORMAT_INFO_2, NULL,
                                            c->fmt, VK_IMAGE_TYPE_2D, tiling, usage, flags };
    VkImageFormatProperties2 fp = { VK_STRUCTURE_TYPE_IMAGE_FORMAT_PROPERTIES_2 };
    VkResult r = vkGetPhysicalDeviceImageFormatProperties2(pd, &fi, &fp);
    if (r != VK_SUCCESS) {
        printf("  %-32s %-7s %-10s: unsupported (%d)\n", c->name, tname, sname, r);
        return 0;
    }

    VkImageCreateInfo ici = { VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO, NULL, flags, VK_IMAGE_TYPE_2D, c->fmt,
                              { W, H, 1 }, 1, 1, VK_SAMPLE_COUNT_1_BIT, tiling, usage,
                              VK_SHARING_MODE_EXCLUSIVE, 0, NULL, VK_IMAGE_LAYOUT_UNDEFINED };
    VkImage img;
    if ((r = vkCreateImage(dev, &ici, NULL, &img)) != VK_SUCCESS) {
        printf("  %-32s %-7s %-10s: vkCreateImage %d\n", c->name, tname, sname, r);
        return 1;
    }
    VkMemoryRequirements req;
    vkGetImageMemoryRequirements(dev, img, &req);
    uint32_t type = UINT32_MAX;
    for (uint32_t i = 0; i < mp->memoryTypeCount; i++)
        if ((req.memoryTypeBits & (1u << i)) &&
            (mp->memoryTypes[i].propertyFlags & VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT)) {
            type = i;
            break;
        }
    VkMemoryAllocateInfo mai = { VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, NULL, req.size, type };
    VkDeviceMemory mem;
    if (type == UINT32_MAX || (r = vkAllocateMemory(dev, &mai, NULL, &mem)) != VK_SUCCESS) {
        printf("  %-32s %-7s %-10s: vkAllocateMemory size=%llu type=%u -> %d\n", c->name, tname, sname,
               (unsigned long long)req.size, type, r);
        vkDestroyImage(dev, img, NULL);
        return 1;
    }
    vkBindImageMemory(dev, img, mem, 0);

    int bad = 0;
    if (ffmpeg_seq) {
        VkImageLayout mid = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
        if (!layout_listed(hp->pCopyDstLayouts, hp->copyDstLayoutCount, mid))
            printf("  (TRANSFER_DST_OPTIMAL not in pCopyDstLayouts; FFmpeg would fall back)\n");
        bad |= transition(dev, img, VK_IMAGE_LAYOUT_UNDEFINED, mid, c->planes);
        bad |= transition(dev, img, mid, VK_IMAGE_LAYOUT_GENERAL, c->planes);
    } else {
        bad |= transition(dev, img, VK_IMAGE_LAYOUT_UNDEFINED, VK_IMAGE_LAYOUT_GENERAL, c->planes);
    }

    size_t total_bad = 0, total = 0;
    unsigned char *src[3] = { 0 }, *dst[3] = { 0 };
    for (int p = 0; p < c->planes; p++) {
        int pw = W >> c->sub[p], ph = H >> c->sub[p];
        int row_texels = pw + PITCH_PAD;
        size_t bytes = (size_t)row_texels * c->texel_bytes[p] * ph;
        src[p] = malloc(bytes);
        dst[p] = calloc(1, bytes);
        for (size_t i = 0; i < bytes; i++)
            src[p][i] = (unsigned char)((p * 71 + i * 13 + (i / 97) * 7) & 0xff);
        VkMemoryToImageCopyEXT reg = { VK_STRUCTURE_TYPE_MEMORY_TO_IMAGE_COPY_EXT, NULL, src[p],
                                       (uint32_t)row_texels, 0,
                                       { c->planes > 1 ? (VkImageAspectFlags)(VK_IMAGE_ASPECT_PLANE_0_BIT << p)
                                                       : VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1 },
                                       { 0, 0, 0 }, { (uint32_t)pw, (uint32_t)ph, 1 } };
        VkCopyMemoryToImageInfoEXT ci = { VK_STRUCTURE_TYPE_COPY_MEMORY_TO_IMAGE_INFO_EXT, NULL, 0, img,
                                          VK_IMAGE_LAYOUT_GENERAL, 1, &reg };
        if ((r = pCopyMemoryToImage(dev, &ci)) != VK_SUCCESS)
            printf("  vkCopyMemoryToImageEXT plane %d -> %d\n", p, r), bad = 1;
    }
    for (int p = 0; p < c->planes; p++) {
        int pw = W >> c->sub[p], ph = H >> c->sub[p];
        int row_texels = pw + PITCH_PAD;
        VkImageToMemoryCopyEXT reg = { VK_STRUCTURE_TYPE_IMAGE_TO_MEMORY_COPY_EXT, NULL, dst[p],
                                       (uint32_t)row_texels, 0,
                                       { c->planes > 1 ? (VkImageAspectFlags)(VK_IMAGE_ASPECT_PLANE_0_BIT << p)
                                                       : VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1 },
                                       { 0, 0, 0 }, { (uint32_t)pw, (uint32_t)ph, 1 } };
        VkCopyImageToMemoryInfoEXT ci = { VK_STRUCTURE_TYPE_COPY_IMAGE_TO_MEMORY_INFO_EXT, NULL, 0, img,
                                          VK_IMAGE_LAYOUT_GENERAL, 1, &reg };
        if ((r = pCopyImageToMemory(dev, &ci)) != VK_SUCCESS)
            printf("  vkCopyImageToMemoryEXT plane %d -> %d\n", p, r), bad = 1;
        size_t nb = 0;
        for (int y = 0; y < ph; y++)
            for (int x = 0; x < pw * c->texel_bytes[p]; x++) {
                size_t o = (size_t)y * row_texels * c->texel_bytes[p] + x;
                nb += src[p][o] != dst[p][o];
            }
        total_bad += nb;
        total += (size_t)pw * c->texel_bytes[p] * ph;
        if (nb)
            printf("    plane %d: %zu/%zu bytes differ\n", p, nb, (size_t)pw * c->texel_bytes[p] * ph);
    }
    printf("  %-32s %-7s %-10s: %s (%zu/%zu bytes differ, mem size %llu)\n", c->name, tname, sname,
           (total_bad || bad) ? "MISMATCH" : "exact", total_bad, total, (unsigned long long)req.size);
    for (int p = 0; p < 3; p++)
        free(src[p]), free(dst[p]);
    vkDestroyImage(dev, img, NULL);
    vkFreeMemory(dev, mem, NULL);
    return (total_bad || bad) ? 1 : 0;
}

int main(int argc, char **argv)
{
    unsigned want = argc > 1 ? (unsigned)strtoul(argv[1], NULL, 10) : 0;
    VkApplicationInfo app = { VK_STRUCTURE_TYPE_APPLICATION_INFO, NULL, "pel-hostcopy", 1, NULL, 0,
                              VK_API_VERSION_1_3 };
    VkInstanceCreateInfo ici = { VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, NULL, 0, &app, 0, NULL, 0, NULL };
    VkInstance inst;
    if (vkCreateInstance(&ici, NULL, &inst) != VK_SUCCESS)
        return 2;
    uint32_t n = 8;
    VkPhysicalDevice pds[8];
    vkEnumeratePhysicalDevices(inst, &n, pds);
    if (want >= n)
        return 3;
    VkPhysicalDevice pd = pds[want];

    VkImageLayout src_l[32], dst_l[32];
    VkPhysicalDeviceHostImageCopyPropertiesEXT hp = { VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_HOST_IMAGE_COPY_PROPERTIES_EXT };
    hp.copySrcLayoutCount = 32; hp.pCopySrcLayouts = src_l;
    hp.copyDstLayoutCount = 32; hp.pCopyDstLayouts = dst_l;
    VkPhysicalDeviceProperties2 props = { VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2, &hp };
    vkGetPhysicalDeviceProperties2(pd, &props);
    printf("device %u: %s driver 0x%x\n", want, props.properties.deviceName, props.properties.driverVersion);
    printf("pCopySrcLayouts:");
    for (uint32_t i = 0; i < hp.copySrcLayoutCount; i++) printf(" %s", layout_name(src_l[i]));
    printf("\npCopyDstLayouts:");
    for (uint32_t i = 0; i < hp.copyDstLayoutCount; i++) printf(" %s", layout_name(dst_l[i]));
    printf("\nidenticalMemoryTypeRequirements=%u\n", hp.identicalMemoryTypeRequirements);

    VkPhysicalDeviceHostImageCopyFeaturesEXT hf = { VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_HOST_IMAGE_COPY_FEATURES_EXT,
                                                    NULL, VK_TRUE };
    VkPhysicalDeviceVulkan11Features v11 = { VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_1_FEATURES, &hf };
    v11.samplerYcbcrConversion = VK_TRUE;
    float prio = 1.0f;
    VkDeviceQueueCreateInfo qci = { VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO, NULL, 0, 0, 1, &prio };
    const char *exts[] = { VK_EXT_HOST_IMAGE_COPY_EXTENSION_NAME };
    VkDeviceCreateInfo dci = { VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, &v11, 0, 1, &qci, 0, NULL, 1, exts, NULL };
    VkDevice dev;
    VkResult r = vkCreateDevice(pd, &dci, NULL, &dev);
    if (r != VK_SUCCESS) {
        printf("vkCreateDevice %d\n", r);
        return 4;
    }
    pCopyMemoryToImage = (PFN_vkCopyMemoryToImageEXT)vkGetDeviceProcAddr(dev, "vkCopyMemoryToImageEXT");
    pCopyImageToMemory = (PFN_vkCopyImageToMemoryEXT)vkGetDeviceProcAddr(dev, "vkCopyImageToMemoryEXT");
    pTransition = (PFN_vkTransitionImageLayoutEXT)vkGetDeviceProcAddr(dev, "vkTransitionImageLayoutEXT");
    if (!pCopyMemoryToImage || !pCopyImageToMemory || !pTransition)
        return 5;
    VkPhysicalDeviceMemoryProperties mp;
    vkGetPhysicalDeviceMemoryProperties(pd, &mp);

    int fails = 0;
    for (size_t i = 0; i < sizeof(cases) / sizeof(cases[0]); i++)
        for (int t = 0; t < 2; t++)
            for (int s = 0; s < 2; s++)
                fails += run_case(pd, dev, &mp, &cases[i], t ? VK_IMAGE_TILING_LINEAR : VK_IMAGE_TILING_OPTIMAL,
                                  s, &hp);
    printf("%d failing case(s)\n", fails);
    vkDestroyDevice(dev, NULL);
    vkDestroyInstance(inst, NULL);
    return fails ? 1 : 0;
}
