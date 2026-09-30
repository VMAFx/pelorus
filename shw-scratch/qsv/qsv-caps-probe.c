/*
 * shw-3 qsv-caps-probe.c -- oneVPL capability probe for the Pelorus QSV paths on
 * one implementation (adapter).  Widens shw-2's vpl-mbqp-query.c (5 rows) to a
 * full parameter sweep, and adds the ROI / encode-statistics capability queries.
 *
 *  1. implementation description: name, API, device, runtime DLL path, encoder
 *     codecs/profiles/memory types.
 *  2. MBQP sweep: MFXVideoENCODE_Query(in == out, exactly like FFmpeg's
 *     ff_qsv_enc_init) with mfxExtCodingOption3::EnableMBQP=ON over codec x
 *     LowPower x IOPattern x TargetUsage x GopRefDist x bit depth x ext-buffer
 *     set, recording the status, the surviving EnableMBQP and LowPower.
 *  3. Query mode 1 (in = NULL): which CO3 fields the runtime reports configurable.
 *  4. ROI: Query mode 2 with mfxExtEncoderROI NumROI=256 (the documented way to
 *     read the max ROI count) per codec x LowPower x rate control x ROIMode.
 *  5. Stats flags: CO3 EncodedUnitsInfo=ON survival per codec x LowPower.
 *  6. Entry points: Query with LowPower ON / OFF and whether Init succeeds.
 *
 * Build (MSYS2 UCRT64):
 *   gcc -O1 -Wall -o qsv-caps-probe.exe qsv-caps-probe.c $(pkg-config --cflags --libs vpl)
 * Usage: qsv-caps-probe.exe <impl-index>
 */
#define ONEVPL_EXPERIMENTAL
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <vpl/mfx.h>

static const char *tri(mfxU16 v)
{
    switch (v) {
    case MFX_CODINGOPTION_ON: return "ON";
    case MFX_CODINGOPTION_OFF: return "OFF";
    case MFX_CODINGOPTION_ADAPTIVE: return "ADAPTIVE";
    case MFX_CODINGOPTION_UNKNOWN: return "UNSET";
    default: return "other";
    }
}

static const char *cname(mfxU32 c)
{
    return c == MFX_CODEC_HEVC ? "HEVC" : c == MFX_CODEC_AVC ? "AVC" : c == MFX_CODEC_AV1 ? "AV1" : "?";
}

static const char *rcname(mfxU16 rc)
{
    switch (rc) {
    case MFX_RATECONTROL_CQP: return "CQP";
    case MFX_RATECONTROL_VBR: return "VBR";
    case MFX_RATECONTROL_CBR: return "CBR";
    case MFX_RATECONTROL_ICQ: return "ICQ";
    default: return "?";
    }
}

typedef struct Cfg {
    mfxU32 codec;
    mfxU16 lp;        /* 0, ON, OFF */
    mfxU16 io;        /* MFX_IOPATTERN_IN_SYSTEM_MEMORY / VIDEO */
    mfxU16 tu;
    mfxU16 refdist;
    int bd;           /* 8 / 10 */
    int w, h;
    mfxU16 rc;
    int full_ext;     /* 0: CO3 only; 1: CO + CO2 + CO3 like FFmpeg (MBBRC/ExtBRC OFF) */
} Cfg;

typedef struct Bufs {
    mfxExtCodingOption co;
    mfxExtCodingOption2 co2;
    mfxExtCodingOption3 co3;
    mfxExtEncoderROI roi;
    mfxExtBuffer *ext[4];
} Bufs;

static void fill_param(mfxVideoParam *p, const Cfg *c)
{
    memset(p, 0, sizeof(*p));
    p->IOPattern = c->io;
    p->AsyncDepth = 4;
    p->mfx.CodecId = c->codec;
    p->mfx.TargetUsage = c->tu;
    p->mfx.RateControlMethod = c->rc;
    if (c->rc == MFX_RATECONTROL_CQP) {
        p->mfx.QPI = 28;
        p->mfx.QPP = 30;
        p->mfx.QPB = 32;
    } else if (c->rc == MFX_RATECONTROL_ICQ) {
        p->mfx.ICQQuality = 25;
    } else {
        p->mfx.TargetKbps = 2000;
        p->mfx.MaxKbps = 4000;
    }
    p->mfx.GopPicSize = 60;
    p->mfx.GopRefDist = c->refdist;
    p->mfx.IdrInterval = 0;
    p->mfx.LowPower = c->lp;
    p->mfx.FrameInfo.FourCC = c->bd == 10 ? MFX_FOURCC_P010 : MFX_FOURCC_NV12;
    p->mfx.FrameInfo.BitDepthLuma = p->mfx.FrameInfo.BitDepthChroma = (mfxU16)c->bd;
    p->mfx.FrameInfo.Shift = c->bd == 10;
    p->mfx.FrameInfo.ChromaFormat = MFX_CHROMAFORMAT_YUV420;
    p->mfx.FrameInfo.PicStruct = MFX_PICSTRUCT_PROGRESSIVE;
    p->mfx.FrameInfo.Width = (mfxU16)((c->w + 15) & ~15);
    p->mfx.FrameInfo.Height = (mfxU16)((c->h + 15) & ~15);
    p->mfx.FrameInfo.CropW = (mfxU16)c->w;
    p->mfx.FrameInfo.CropH = (mfxU16)c->h;
    p->mfx.FrameInfo.FrameRateExtN = 30;
    p->mfx.FrameInfo.FrameRateExtD = 1;
    p->mfx.FrameInfo.AspectRatioW = p->mfx.FrameInfo.AspectRatioH = 1;
    if (c->codec == MFX_CODEC_HEVC)
        p->mfx.CodecProfile = c->bd == 10 ? MFX_PROFILE_HEVC_MAIN10 : MFX_PROFILE_HEVC_MAIN;
}

static void init_bufs(Bufs *b)
{
    memset(b, 0, sizeof(*b));
    b->co.Header.BufferId = MFX_EXTBUFF_CODING_OPTION;
    b->co.Header.BufferSz = sizeof(b->co);
    b->co2.Header.BufferId = MFX_EXTBUFF_CODING_OPTION2;
    b->co2.Header.BufferSz = sizeof(b->co2);
    b->co3.Header.BufferId = MFX_EXTBUFF_CODING_OPTION3;
    b->co3.Header.BufferSz = sizeof(b->co3);
    b->roi.Header.BufferId = MFX_EXTBUFF_ENCODER_ROI;
    b->roi.Header.BufferSz = sizeof(b->roi);
}

/* ---- 2. MBQP sweep ---------------------------------------------------- */
static int mbqp_query(mfxSession s, const Cfg *c, mfxStatus *st_out, mfxU16 *mbqp_out,
                      mfxU16 *lp_out, mfxU16 *co2_mbbrc)
{
    mfxVideoParam p;
    Bufs b;
    fill_param(&p, c);
    init_bufs(&b);
    b.co3.EnableMBQP = MFX_CODINGOPTION_ON;
    int n = 0;
    if (c->full_ext) {
        b.co2.MBBRC = MFX_CODINGOPTION_OFF;
        b.co2.ExtBRC = MFX_CODINGOPTION_OFF;
        b.co2.AdaptiveI = MFX_CODINGOPTION_OFF;
        b.co2.AdaptiveB = MFX_CODINGOPTION_OFF;
        b.ext[n++] = (mfxExtBuffer *)&b.co;
        b.ext[n++] = (mfxExtBuffer *)&b.co2;
    }
    b.ext[n++] = (mfxExtBuffer *)&b.co3;
    p.ExtParam = b.ext;
    p.NumExtParam = (mfxU16)n;
    *st_out = MFXVideoENCODE_Query(s, &p, &p); /* in == out, as FFmpeg does */
    *mbqp_out = b.co3.EnableMBQP;
    *lp_out = p.mfx.LowPower;
    *co2_mbbrc = b.co2.MBBRC;
    return 0;
}

static void mbqp_sweep(mfxSession s)
{
    static const mfxU32 codecs[] = { MFX_CODEC_HEVC, MFX_CODEC_AVC };
    static const mfxU16 lps[] = { 0, MFX_CODINGOPTION_ON, MFX_CODINGOPTION_OFF };
    static const mfxU16 ios[] = { MFX_IOPATTERN_IN_SYSTEM_MEMORY, MFX_IOPATTERN_IN_VIDEO_MEMORY };
    static const mfxU16 tus[] = { 1, 4, 7 };
    static const mfxU16 rds[] = { 1, 4 };
    static const int bds[] = { 8, 10 };
    printf("\n== 2. MBQP sweep: Query(in==out) with CO3.EnableMBQP=ON, CQP 28/30/32, 1920x1080\n");
    printf("codec lp    io    tu rd bd ext  | status mbqp_after lp_after\n");
    for (unsigned ci = 0; ci < 2; ci++) {
        int kept = 0, total = 0;
        for (unsigned li = 0; li < 3; li++)
        for (unsigned ii = 0; ii < 2; ii++)
        for (unsigned ti = 0; ti < 3; ti++)
        for (unsigned ri = 0; ri < 2; ri++)
        for (unsigned bi = 0; bi < 2; bi++)
        for (int fe = 0; fe < 2; fe++) {
            if (codecs[ci] == MFX_CODEC_AVC && bds[bi] == 10)
                continue;
            Cfg c = { codecs[ci], lps[li], ios[ii], tus[ti], rds[ri], bds[bi], 1920, 1080,
                      MFX_RATECONTROL_CQP, fe };
            mfxStatus st;
            mfxU16 mbqp, lp, mbbrc;
            mbqp_query(s, &c, &st, &mbqp, &lp, &mbbrc);
            total++;
            kept += (st >= 0 && mbqp == MFX_CODINGOPTION_ON);
            printf("%-5s %-5s %-5s %2u %2u %2d %-4s | %6d %-10s %s\n", cname(c.codec), tri(c.lp),
                   c.io == MFX_IOPATTERN_IN_SYSTEM_MEMORY ? "sys" : "video", c.tu, c.refdist, c.bd,
                   fe ? "full" : "co3", (int)st, tri(mbqp), tri(lp));
        }
        printf("SUMMARY %s: EnableMBQP=ON survived Query in %d of %d configurations\n",
               cname(codecs[ci]), kept, total);
    }
    /* Resolution sweep (HEVC, default LP) */
    static const int res[][2] = { { 320, 240 }, { 640, 360 }, { 1280, 720 }, { 3840, 2160 } };
    for (unsigned i = 0; i < 4; i++) {
        Cfg c = { MFX_CODEC_HEVC, 0, MFX_IOPATTERN_IN_SYSTEM_MEMORY, 4, 1, 8, res[i][0], res[i][1],
                  MFX_RATECONTROL_CQP, 0 };
        mfxStatus st;
        mfxU16 mbqp, lp, mbbrc;
        mbqp_query(s, &c, &st, &mbqp, &lp, &mbbrc);
        printf("HEVC %dx%d CQP default-LP: status %d EnableMBQP %s\n", res[i][0], res[i][1], (int)st,
               tri(mbqp));
    }
    /* Rate-control sweep: MBQP is documented CQP-only; confirm on HEVC/AVC. */
    static const mfxU16 rcs[] = { MFX_RATECONTROL_VBR, MFX_RATECONTROL_CBR, MFX_RATECONTROL_ICQ };
    for (unsigned ci = 0; ci < 2; ci++)
        for (unsigned r = 0; r < 3; r++) {
            Cfg c = { codecs[ci], 0, MFX_IOPATTERN_IN_SYSTEM_MEMORY, 4, 1, 8, 1920, 1080, rcs[r], 0 };
            mfxStatus st;
            mfxU16 mbqp, lp, mbbrc;
            mbqp_query(s, &c, &st, &mbqp, &lp, &mbbrc);
            printf("%s %s: status %d EnableMBQP %s\n", cname(codecs[ci]), rcname(rcs[r]), (int)st,
                   tri(mbqp));
        }
}

/* ---- 3. Query mode 1 ---------------------------------------------------- */
static void mode1(mfxSession s, mfxU32 codec)
{
    mfxVideoParam out;
    Bufs b;
    memset(&out, 0, sizeof(out));
    init_bufs(&b);
    b.ext[0] = (mfxExtBuffer *)&b.co2;
    b.ext[1] = (mfxExtBuffer *)&b.co3;
    b.ext[2] = (mfxExtBuffer *)&b.roi;
    out.ExtParam = b.ext;
    out.NumExtParam = 3;
    out.mfx.CodecId = codec;
    mfxStatus st = MFXVideoENCODE_Query(s, NULL, &out);
    printf("%s mode-1 Query status %d: LowPower=%u RateControlMethod=%u CO3.EnableMBQP=%u "
           "CO3.EncodedUnitsInfo=%u CO3.EnableQPOffset=%u CO3.MBDisableSkipMap=%u "
           "CO2.MBBRC=%u CO2.ExtBRC=%u ROI.NumROI=%u ROI.ROIMode=%u\n",
           cname(codec), (int)st, out.mfx.LowPower, out.mfx.RateControlMethod, b.co3.EnableMBQP,
           b.co3.EncodedUnitsInfo, b.co3.EnableQPOffset, b.co3.MBDisableSkipMap, b.co2.MBBRC,
           b.co2.ExtBRC, b.roi.NumROI, b.roi.ROIMode);
}

/* ---- 4. ROI max count --------------------------------------------------- */
static void roi_query(mfxSession s, mfxU32 codec, mfxU16 lp, mfxU16 rc, mfxU16 mode)
{
    Cfg c = { codec, lp, MFX_IOPATTERN_IN_SYSTEM_MEMORY, 4, 1, 8, 1280, 720, rc, 0 };
    mfxVideoParam p;
    Bufs b;
    fill_param(&p, &c);
    init_bufs(&b);
    b.roi.NumROI = 256;
    b.roi.ROIMode = mode;
    for (int i = 0; i < 256; i++) {
        /* 256 disjoint 32x32 cells from the top-left; all valid in 1280x720 */
        int col = i % 40, row = i / 40;
        b.roi.ROI[i].Left = (mfxU32)(col * 32);
        b.roi.ROI[i].Top = (mfxU32)(row * 32);
        b.roi.ROI[i].Right = (mfxU32)(col * 32 + 32);
        b.roi.ROI[i].Bottom = (mfxU32)(row * 32 + 32);
        if (mode == MFX_ROI_MODE_QP_DELTA)
            b.roi.ROI[i].DeltaQP = -8;
        else
            b.roi.ROI[i].Priority = 2;
    }
    b.ext[0] = (mfxExtBuffer *)&b.roi;
    p.ExtParam = b.ext;
    p.NumExtParam = 1;
    mfxStatus st = MFXVideoENCODE_Query(s, &p, &p);
    printf("%-4s lp=%-5s %-3s ROIMode=%-8s: Query status %3d -> NumROI %3u ROIMode %u (LowPower %s)\n",
           cname(codec), tri(lp), rcname(rc), mode == MFX_ROI_MODE_QP_DELTA ? "QP_DELTA" : "PRIORITY",
           (int)st, b.roi.NumROI, b.roi.ROIMode, tri(p.mfx.LowPower));
}

/* ---- 5. stats flags ------------------------------------------------------ */
static void stats_query(mfxSession s, mfxU32 codec, mfxU16 lp)
{
    Cfg c = { codec, lp, MFX_IOPATTERN_IN_SYSTEM_MEMORY, 4, 1, 8, 1280, 720, MFX_RATECONTROL_CQP, 0 };
    mfxVideoParam p;
    Bufs b;
    fill_param(&p, &c);
    init_bufs(&b);
    b.co3.EncodedUnitsInfo = MFX_CODINGOPTION_ON;
    b.ext[0] = (mfxExtBuffer *)&b.co3;
    p.ExtParam = b.ext;
    p.NumExtParam = 1;
    mfxStatus st = MFXVideoENCODE_Query(s, &p, &p);
    printf("%-4s lp=%-5s CQP: CO3.EncodedUnitsInfo=ON -> status %d, after %s\n", cname(codec),
           tri(lp), (int)st, tri(b.co3.EncodedUnitsInfo));
}

/* ---- 6. entry points ---------------------------------------------------- */
static void entry(mfxSession s, mfxU32 codec, mfxU16 lp)
{
    Cfg c = { codec, lp, MFX_IOPATTERN_IN_SYSTEM_MEMORY, 4, 1, 8, 1280, 720, MFX_RATECONTROL_CQP, 0 };
    mfxVideoParam p, q;
    fill_param(&p, &c);
    q = p;
    mfxStatus st = MFXVideoENCODE_Query(s, &p, &q);
    mfxStatus ist = MFXVideoENCODE_Init(s, &p);
    mfxVideoParam g;
    memset(&g, 0, sizeof(g));
    mfxStatus gst = ist >= 0 ? MFXVideoENCODE_GetVideoParam(s, &g) : MFX_ERR_NOT_INITIALIZED;
    printf("%-4s LowPower request %-5s: Query %d (LowPower out %s) Init %d GetVideoParam %d -> LowPower %s TU %u\n",
           cname(codec), tri(lp), (int)st, tri(q.mfx.LowPower), (int)ist, (int)gst,
           gst >= 0 ? tri(g.mfx.LowPower) : "-", gst >= 0 ? g.mfx.TargetUsage : 0);
    if (ist >= 0)
        MFXVideoENCODE_Close(s);
}

int main(int argc, char **argv)
{
    mfxU32 want = argc > 1 ? (mfxU32)strtoul(argv[1], NULL, 10) : 0;
    mfxLoader loader = MFXLoad();
    if (!loader)
        return 1;
    mfxHDL h = NULL;
    printf("== 1. implementations\n");
    for (mfxU32 i = 0; MFXEnumImplementations(loader, i, MFX_IMPLCAPS_IMPLDESCSTRUCTURE, &h) == MFX_ERR_NONE; i++) {
        mfxImplDescription *d = (mfxImplDescription *)h;
        mfxHDL ph = NULL;
        const char *path = "?";
        if (MFXEnumImplementations(loader, i, MFX_IMPLCAPS_IMPLPATH, &ph) == MFX_ERR_NONE && ph)
            path = (const char *)ph;
        printf("impl[%u] %s API %u.%u Accel=0x%x VendorID=0x%x DeviceID=%s path=%s\n", i, d->ImplName,
               d->ApiVersion.Major, d->ApiVersion.Minor, d->AccelerationMode, d->VendorID,
               d->Dev.DeviceID, path);
        if (i == want) {
            for (mfxU16 ci = 0; ci < d->Enc.NumCodecs; ci++) {
                mfxEncoderDescription *e = &d->Enc;
                printf("  enc codec %.4s maxlevel %u bidir %u profiles:", (char *)&e->Codecs[ci].CodecID,
                       e->Codecs[ci].MaxcodecLevel, e->Codecs[ci].BiDirectionalPrediction);
                for (mfxU16 pi = 0; pi < e->Codecs[ci].NumProfiles; pi++) {
                    printf(" %u[", e->Codecs[ci].Profiles[pi].Profile);
                    for (mfxU16 mi = 0; mi < e->Codecs[ci].Profiles[pi].NumMemTypes; mi++)
                        printf("%smem%d", mi ? "," : "", (int)e->Codecs[ci].Profiles[pi].MemDesc[mi].MemHandleType);
                    printf("]");
                }
                printf("\n");
            }
        }
        if (ph)
            MFXDispReleaseImplDescription(loader, ph);
        MFXDispReleaseImplDescription(loader, h);
    }
    mfxSession s = NULL;
    mfxStatus st = MFXCreateSession(loader, want, &s);
    if (st != MFX_ERR_NONE) {
        fprintf(stderr, "MFXCreateSession(%u) failed %d\n", want, (int)st);
        return 3;
    }
    mfxVersion v;
    MFXQueryVersion(s, &v);
    mfxIMPL impl;
    MFXQueryIMPL(s, &impl);
    printf("session impl[%u] API %u.%u MFXQueryIMPL=0x%x\n", want, v.Major, v.Minor, (unsigned)impl);

    printf("\n== 6. entry points (Query + Init + GetVideoParam, 1280x720 CQP sysmem)\n");
    static const mfxU16 lps[] = { 0, MFX_CODINGOPTION_ON, MFX_CODINGOPTION_OFF };
    for (int c = 0; c < 3; c++) {
        mfxU32 codec = c == 0 ? MFX_CODEC_HEVC : c == 1 ? MFX_CODEC_AVC : MFX_CODEC_AV1;
        for (int l = 0; l < 3; l++)
            entry(s, codec, lps[l]);
    }

    mbqp_sweep(s);

    printf("\n== 3. Query mode 1 (in=NULL): nonzero = runtime reports the field configurable\n");
    mode1(s, MFX_CODEC_HEVC);
    mode1(s, MFX_CODEC_AVC);
    mode1(s, MFX_CODEC_AV1);

    printf("\n== 4. ROI: Query mode 2 with NumROI=256 (runtime writes back its max), 1280x720\n");
    static const mfxU16 rcs[] = { MFX_RATECONTROL_CQP, MFX_RATECONTROL_VBR, MFX_RATECONTROL_ICQ };
    for (int c = 0; c < 2; c++)
        for (int l = 0; l < 3; l++)
            for (int r = 0; r < 3; r++) {
                mfxU32 codec = c == 0 ? MFX_CODEC_HEVC : MFX_CODEC_AVC;
                roi_query(s, codec, lps[l], rcs[r], MFX_ROI_MODE_QP_DELTA);
                roi_query(s, codec, lps[l], rcs[r], MFX_ROI_MODE_PRIORITY);
            }

    printf("\n== 5. encode-statistics flags\n");
    for (int c = 0; c < 2; c++)
        for (int l = 0; l < 3; l++)
            stats_query(s, c == 0 ? MFX_CODEC_HEVC : MFX_CODEC_AVC, lps[l]);

    MFXClose(s);
    MFXUnload(loader);
    return 0;
}
