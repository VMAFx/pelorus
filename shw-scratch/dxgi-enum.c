/*
 * shw-1: list DXGI adapters in IDXGIFactory1::EnumAdapters1 order. FFmpeg's
 * d3d11va hwcontext (and therefore QSV child_device / -qsv_device on Windows)
 * selects an adapter by this index. Scratch tool, not repo code.
 *
 * Build (MSYS2 UCRT64): gcc -O1 -o dxgi-enum.exe dxgi-enum.c -ldxgi -luuid
 */
#define COBJMACROS
#include <stdio.h>
#include <dxgi.h>

int main(void)
{
    IDXGIFactory1 *f = NULL;
    if (FAILED(CreateDXGIFactory1(&IID_IDXGIFactory1, (void **)&f))) {
        fprintf(stderr, "CreateDXGIFactory1 failed\n");
        return 1;
    }
    IDXGIAdapter1 *a = NULL;
    for (UINT i = 0; IDXGIFactory1_EnumAdapters1(f, i, &a) != DXGI_ERROR_NOT_FOUND; i++) {
        DXGI_ADAPTER_DESC1 d;
        IDXGIAdapter1_GetDesc1(a, &d);
        printf("dxgi[%u]: %ls vendor=0x%04x device=0x%04x rev=0x%02x "
               "VRAM=%llu MiB flags=0x%x LUID=%08lx%08lx\n",
               i, d.Description, d.VendorId, d.DeviceId, d.Revision,
               (unsigned long long)(d.DedicatedVideoMemory >> 20), d.Flags,
               (unsigned long)d.AdapterLuid.HighPart, (unsigned long)d.AdapterLuid.LowPart);
        IDXGIAdapter1_Release(a);
    }
    IDXGIFactory1_Release(f);
    return 0;
}
