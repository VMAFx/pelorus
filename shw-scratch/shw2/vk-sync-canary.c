/* shw-2 positive control for synchronization validation: two vkCmdFillBuffer
 * writes to the same range with no barrier must raise
 * SYNC-HAZARD-WRITE-AFTER-WRITE when VK_LAYER_VALIDATE_SYNC=1 is honoured.
 * Record-time only; nothing is submitted. */
#include <stdio.h>
#include <stdlib.h>
#include <vulkan/vulkan.h>

int main(int argc, char **argv)
{
    unsigned want = argc > 1 ? (unsigned)strtoul(argv[1], NULL, 10) : 0;
    VkApplicationInfo app = { VK_STRUCTURE_TYPE_APPLICATION_INFO, NULL, "pel-sync-canary", 1, NULL, 0,
                              VK_API_VERSION_1_3 };
    VkInstanceCreateInfo ici = { VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, NULL, 0, &app, 0, NULL, 0, NULL };
    VkInstance inst;
    if (vkCreateInstance(&ici, NULL, &inst) != VK_SUCCESS)
        return 2;
    uint32_t n = 8;
    VkPhysicalDevice pd[8];
    vkEnumeratePhysicalDevices(inst, &n, pd);
    if (want >= n)
        return 3;
    float prio = 1.0f;
    VkDeviceQueueCreateInfo qci = { VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO, NULL, 0, 0, 1, &prio };
    VkDeviceCreateInfo dci = { VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, NULL, 0, 1, &qci, 0, NULL, 0, NULL, NULL };
    VkDevice dev;
    if (vkCreateDevice(pd[want], &dci, NULL, &dev) != VK_SUCCESS)
        return 4;
    VkBufferCreateInfo bci = { VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO, NULL, 0, 4096,
                               VK_BUFFER_USAGE_TRANSFER_DST_BIT, VK_SHARING_MODE_EXCLUSIVE, 0, NULL };
    VkBuffer buf;
    vkCreateBuffer(dev, &bci, NULL, &buf);
    VkMemoryRequirements req;
    vkGetBufferMemoryRequirements(dev, buf, &req);
    VkPhysicalDeviceMemoryProperties mp;
    vkGetPhysicalDeviceMemoryProperties(pd[want], &mp);
    uint32_t type = 0;
    while (type < mp.memoryTypeCount && !(req.memoryTypeBits & (1u << type)))
        type++;
    VkMemoryAllocateInfo mai = { VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, NULL, req.size, type };
    VkDeviceMemory mem;
    vkAllocateMemory(dev, &mai, NULL, &mem);
    vkBindBufferMemory(dev, buf, mem, 0);
    VkCommandPoolCreateInfo cpci = { VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO, NULL, 0, 0 };
    VkCommandPool pool;
    vkCreateCommandPool(dev, &cpci, NULL, &pool);
    VkCommandBufferAllocateInfo cbai = { VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO, NULL, pool,
                                         VK_COMMAND_BUFFER_LEVEL_PRIMARY, 1 };
    VkCommandBuffer cb;
    vkAllocateCommandBuffers(dev, &cbai, &cb);
    VkCommandBufferBeginInfo bi = { VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO, NULL, 0, NULL };
    vkBeginCommandBuffer(cb, &bi);
    vkCmdFillBuffer(cb, buf, 0, 4096, 0x11111111u);
    vkCmdFillBuffer(cb, buf, 0, 4096, 0x22222222u); /* deliberate WAW hazard */
    vkEndCommandBuffer(cb);
    fprintf(stderr, "recorded two unsynchronized fills on device %u\n", want);
    vkDestroyCommandPool(dev, pool, NULL);
    vkDestroyBuffer(dev, buf, NULL);
    vkFreeMemory(dev, mem, NULL);
    vkDestroyDevice(dev, NULL);
    vkDestroyInstance(inst, NULL);
    return 0;
}
