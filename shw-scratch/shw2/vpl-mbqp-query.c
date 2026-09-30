/*
 * shw-2: does the oneVPL runtime accept mfxExtCodingOption3::EnableMBQP=ON for
 * HEVC CQP?  FFmpeg's qsvenc calls MFXVideoENCODE_Query(session, &param, &param)
 * (in == out), so a runtime that does not support MBQP silently rewrites the
 * request to OFF; Pelorus patch 0005 then caches "not enabled" and uses the
 * stock mfxExtEncoderROI rectangles.  This probe mirrors that Query on impl N.
 *
 * Build (MSYS2 UCRT64):
 *   gcc -O1 -o vpl-mbqp-query.exe vpl-mbqp-query.c $(pkg-config --cflags --libs vpl)
 * Usage: vpl-mbqp-query.exe <impl-index 0|1>
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <vpl/mfx.h>

static const char *cs(mfxU16 v)
{
    return v == MFX_CODINGOPTION_ON ? "ON" : v == MFX_CODINGOPTION_OFF ? "OFF" :
           v == MFX_CODINGOPTION_UNKNOWN ? "UNKNOWN(0)" : "other";
}

static int try_query(mfxSession s, mfxU32 codec, mfxU16 lowpower, int w, int h)
{
    mfxExtCodingOption3 co3;
    memset(&co3, 0, sizeof(co3));
    co3.Header.BufferId = MFX_EXTBUFF_CODING_OPTION3;
    co3.Header.BufferSz = sizeof(co3);
    co3.EnableMBQP = MFX_CODINGOPTION_ON;
    mfxExtBuffer *ext[1] = { (mfxExtBuffer *)&co3 };

    mfxVideoParam p;
    memset(&p, 0, sizeof(p));
    p.IOPattern = MFX_IOPATTERN_IN_SYSTEM_MEMORY;
    p.AsyncDepth = 4;
    p.mfx.CodecId = codec;
    p.mfx.TargetUsage = MFX_TARGETUSAGE_BALANCED;
    p.mfx.RateControlMethod = MFX_RATECONTROL_CQP;
    p.mfx.QPI = p.mfx.QPP = p.mfx.QPB = 30;
    p.mfx.GopRefDist = 1;
    p.mfx.LowPower = lowpower;
    p.mfx.FrameInfo.FourCC = MFX_FOURCC_NV12;
    p.mfx.FrameInfo.ChromaFormat = MFX_CHROMAFORMAT_YUV420;
    p.mfx.FrameInfo.PicStruct = MFX_PICSTRUCT_PROGRESSIVE;
    p.mfx.FrameInfo.Width = (mfxU16)((w + 15) & ~15);
    p.mfx.FrameInfo.Height = (mfxU16)((h + 15) & ~15);
    p.mfx.FrameInfo.CropW = (mfxU16)w;
    p.mfx.FrameInfo.CropH = (mfxU16)h;
    p.mfx.FrameInfo.FrameRateExtN = 10;
    p.mfx.FrameInfo.FrameRateExtD = 1;
    p.ExtParam = ext;
    p.NumExtParam = 1;

    mfxStatus st = MFXVideoENCODE_Query(s, &p, &p); /* in == out, as FFmpeg does */
    printf("  %s LowPower=%s %dx%d CQP: Query status=%d, EnableMBQP after Query=%s\n",
           codec == MFX_CODEC_HEVC ? "HEVC" : "AVC",
           lowpower == MFX_CODINGOPTION_ON ? "ON" : lowpower == MFX_CODINGOPTION_OFF ? "OFF" : "unset",
           w, h, (int)st, cs(co3.EnableMBQP));
    if (st >= MFX_ERR_NONE) {
        mfxStatus ist = MFXVideoENCODE_Init(s, &p);
        printf("      Init status=%d, EnableMBQP in final param=%s\n", (int)ist, cs(co3.EnableMBQP));
        MFXVideoENCODE_Close(s);
    }
    return 0;
}

int main(int argc, char **argv)
{
    mfxU32 want = argc > 1 ? (mfxU32)strtoul(argv[1], NULL, 10) : 0;
    mfxLoader loader = MFXLoad();
    if (!loader)
        return 1;
    mfxHDL h = NULL;
    if (MFXEnumImplementations(loader, want, MFX_IMPLCAPS_IMPLDESCSTRUCTURE, &h) != MFX_ERR_NONE) {
        fprintf(stderr, "no impl %u\n", want);
        return 2;
    }
    mfxImplDescription *d = (mfxImplDescription *)h;
    printf("impl[%u] %s API %u.%u DeviceID=%s\n", want, d->ImplName, d->ApiVersion.Major,
           d->ApiVersion.Minor, d->Dev.DeviceID);
    MFXDispReleaseImplDescription(loader, h);
    mfxSession s = NULL;
    mfxStatus st = MFXCreateSession(loader, want, &s);
    if (st != MFX_ERR_NONE) {
        fprintf(stderr, "MFXCreateSession %d\n", (int)st);
        return 3;
    }
    mfxVersion v;
    MFXQueryVersion(s, &v);
    printf("session API %u.%u\n", v.Major, v.Minor);
    try_query(s, MFX_CODEC_HEVC, 0, 640, 360);
    try_query(s, MFX_CODEC_HEVC, MFX_CODINGOPTION_ON, 640, 360);
    try_query(s, MFX_CODEC_HEVC, MFX_CODINGOPTION_OFF, 640, 360);
    try_query(s, MFX_CODEC_HEVC, 0, 1920, 1080);
    try_query(s, MFX_CODEC_AVC, 0, 640, 360);
    MFXClose(s);
    MFXUnload(loader);
    return 0;
}
