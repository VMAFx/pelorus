/*
 * shw-1: enumerate oneVPL implementations visible to the libvpl dispatcher
 * (MFXEnumImplementations), print each implementation's description and its
 * extended device ID (DXGI adapter index / PCI device), so the QSV selector for
 * the Arc B580 vs the UHD 770 can be mapped. Scratch tool, not repo code.
 *
 * Build (MSYS2 UCRT64):
 *   gcc -O1 -o vpl-enum.exe vpl-enum.c $(pkg-config --cflags --libs vpl)
 */
#include <stdio.h>
#include <vpl/mfx.h>

static const char *accel_name(mfxAccelerationMode m)
{
    switch (m) {
    case MFX_ACCEL_MODE_NA: return "NA";
    case MFX_ACCEL_MODE_VIA_D3D9: return "D3D9";
    case MFX_ACCEL_MODE_VIA_D3D11: return "D3D11";
    case MFX_ACCEL_MODE_VIA_VAAPI: return "VAAPI";
    default: return "other";
    }
}

int main(void)
{
    mfxLoader loader = MFXLoad();
    if (!loader) {
        fprintf(stderr, "MFXLoad failed\n");
        return 1;
    }
    for (mfxU32 i = 0;; i++) {
        mfxHDL h = NULL;
        mfxStatus st = MFXEnumImplementations(loader, i, MFX_IMPLCAPS_IMPLDESCSTRUCTURE, &h);
        if (st != MFX_ERR_NONE)
            break;
        mfxImplDescription *d = (mfxImplDescription *)h;
        printf("impl[%u]: ImplName=\"%s\" Impl=%s ApiVersion=%u.%u VendorID=0x%04x "
               "VendorImplID=0x%04x License=\"%s\" Keywords=\"%s\"\n",
               i, d->ImplName, d->Impl == MFX_IMPL_TYPE_HARDWARE ? "HW" : "SW",
               d->ApiVersion.Major, d->ApiVersion.Minor, d->VendorID, d->VendorImplID,
               d->License, d->Keywords);
        printf("  Dev.DeviceID=\"%s\" MediaAdapterType=%u NumSubDevices=%u AccelModes:",
               d->Dev.DeviceID, (unsigned)d->Dev.MediaAdapterType, d->Dev.NumSubDevices);
        for (mfxU32 k = 0; k < d->AccelerationModeDescription.NumAccelerationModes; k++)
            printf(" %s", accel_name(d->AccelerationModeDescription.Mode[k]));
        printf("\n  Enc codecs:");
        for (mfxU32 k = 0; k < d->Enc.NumCodecs; k++) {
            mfxU32 id = d->Enc.Codecs[k].CodecID;
            printf(" %c%c%c%c(maxLevel %u)", (int)(id & 0xff), (int)((id >> 8) & 0xff),
                   (int)((id >> 16) & 0xff), (int)((id >> 24) & 0xff),
                   (unsigned)d->Enc.Codecs[k].MaxcodecLevel);
        }
        printf("\n");
        MFXDispReleaseImplDescription(loader, h);

        mfxHDL hp = NULL;
        if (MFXEnumImplementations(loader, i, MFX_IMPLCAPS_IMPLPATH, &hp) == MFX_ERR_NONE) {
            printf("  path=%s\n", (const char *)hp);
            MFXDispReleaseImplDescription(loader, hp);
        }
        mfxHDL hx = NULL;
        if (MFXEnumImplementations(loader, i, MFX_IMPLCAPS_DEVICE_ID_EXTENDED, &hx) ==
            MFX_ERR_NONE) {
            mfxExtendedDeviceId *x = (mfxExtendedDeviceId *)hx;
            printf("  ext: VendorID=0x%04x DeviceID=0x%04x PCI=%04x:%02x:%02x.%x "
                   "DRMRender=%u LUIDValid=%u LUIDDeviceNodeMask=%u DeviceName=\"%s\"\n",
                   x->VendorID, x->DeviceID, x->PCIDomain, x->PCIBus, x->PCIDevice,
                   x->PCIFunction, x->DRMRenderNodeNum, x->LUIDValid, x->LUIDDeviceNodeMask,
                   x->DeviceName);
            if (x->LUIDValid) {
                printf("  LUID=");
                for (int b = 7; b >= 0; b--)
                    printf("%02x", x->DeviceLUID[b]);
                printf("\n");
            }
            MFXDispReleaseImplDescription(loader, hx);
        }
    }
    MFXUnload(loader);
    return 0;
}
