/*
 * shw-3 qsv-encode-probe.c -- raw oneVPL encoder harness (no FFmpeg) for the
 * questions FFmpeg's CLI cannot answer on its own:
 *
 *   mode=none    plain CQP encode
 *   mode=mbqp    per-frame mfxExtMBQP, one header+map per input-surface slot,
 *                released only when the surface unlocks (ADR-0146 ownership)
 *   mode=shared  ONE context-wide mfxExtMBQP whose map is repainted for frame
 *                N+1 right after frame N's EncodeFrameAsync returns -- the
 *                pre-ADR-0146 defect, to see whether the runtime reads the map
 *                at submit time or later
 *   mode=rect    per-frame mfxExtEncoderROI rectangle (FFmpeg's stock path)
 *   mode=sharedrect  ONE mfxExtEncoderROI repainted after each submission
 *
 * pattern=alt paints the delta on the TOP half for even frames and the BOTTOM
 * half for odd frames (pattern=top: always top), so a cross-frame map mix-up is
 * visible per frame.  The encoder's own statistics can be requested:
 *   stats=1  mfxExtEncodeStatsOutput (ONEVPL_EXPERIMENTAL, block+frame level)
 *   units=1  CO3.EncodedUnitsInfo + mfxExtEncodedUnitsInfo
 *   finfo=1  mfxExtAVCEncodedFrameInfo (AVC)
 *
 * Build (MSYS2 UCRT64):
 *   gcc -O1 -Wall -o qsv-encode-probe.exe qsv-encode-probe.c $(pkg-config --cflags --libs vpl)
 * Usage: qsv-encode-probe.exe key=value ...
 *   impl=0 codec=hevc|avc lp=0|on|off qp=30 w= h= n= in=file.nv12 out=file.es csv=file.csv
 *   async=4 mode=none|mbqp|shared|rect pattern=alt|top delta=12 refdist=1 skipquery=0
 *   enablembqp=auto|0|1 stats=0 units=0 finfo=0 qpmode=delta|value initmbqp=0 reuse=lock|sync
 */
#define ONEVPL_EXPERIMENTAL
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include <vpl/mfx.h>

#define MAXTASK 16
#define MAXSLOT 64

typedef struct Opt {
    unsigned impl;
    mfxU32 codec;
    mfxU16 lp;
    int qp, w, h, n, async, delta, refdist, skipquery, enablembqp, stats, units, finfo;
    int qpvalue, initmbqp, reuse_sync;
    const char *in, *out, *csv, *mode, *pattern;
} Opt;

typedef struct Slot {
    mfxFrameSurface1 s;
    uint8_t *buf;
    mfxEncodeCtrl ctrl;
    mfxExtBuffer *ctrlext[2];
    mfxExtMBQP *mbqp;         /* header + map, one allocation */
    mfxExtEncoderROI roi;
    int frame;                /* frame index last submitted from this slot */
} Slot;

typedef struct Task {
    mfxBitstream bs;
    mfxSyncPoint sp;
    int used;
    mfxExtBuffer *ext[3];
    mfxExtEncodeStatsOutput st;
    mfxExtEncodedUnitsInfo eu;
    mfxEncodedUnitInfo units[64];
    mfxExtAVCEncodedFrameInfo fi;
} Task;

static Opt o = { 0, MFX_CODEC_HEVC, 0, 30, 0, 0, 0, 4, 12, 1, 0, -1, 0, 0, 0, 0, 0, 0,
                 NULL, NULL, NULL, "none", "alt" };
static mfxSession S;
static mfxVideoParam P;
static FILE *fout, *fcsv;
static int blocks_w, blocks_h;
static int out_count;
#define MAXFRAMES 100000
static unsigned char synced[MAXFRAMES]; /* frame index -> bitstream synced */
static long early_reuse;                 /* slot reused before its frame was synced */
static FILE *freuse;

static void die(const char *what, mfxStatus st)
{
    fprintf(stderr, "FATAL %s: %d\n", what, (int)st);
    exit(2);
}

/* delta goes on the top half for even frames (or always, pattern=top) */
static int roi_top(int frame) { return strcmp(o.pattern, "top") == 0 || (frame % 2) == 0; }

static void paint_map(mfxI8 *map, int frame)
{
    int top = roi_top(frame);
    int half_rows = (o.h / 2) / 16; /* rows fully inside the visible top half */
    /* qpmode=value writes absolute QPs (base elsewhere, base+delta in the ROI) */
    int base = o.qpvalue ? o.qp : 0;
    memset(map, base, (size_t)blocks_w * blocks_h);
    for (int y = 0; y < blocks_h; y++) {
        int in_top = y < half_rows;
        int in_bot = y * 16 >= o.h / 2 && y * 16 < o.h;
        if ((top && in_top) || (!top && in_bot))
            memset(map + (size_t)y * blocks_w, (mfxI8)(base + o.delta), (size_t)blocks_w);
    }
}

static void paint_rect(mfxExtEncoderROI *r, int frame)
{
    memset(r, 0, sizeof(*r));
    r->Header.BufferId = MFX_EXTBUFF_ENCODER_ROI;
    r->Header.BufferSz = sizeof(*r);
    r->NumROI = 1;
    r->ROIMode = MFX_ROI_MODE_QP_DELTA;
    r->ROI[0].Left = 0;
    r->ROI[0].Right = (mfxU32)P.mfx.FrameInfo.Width;
    if (roi_top(frame)) {
        r->ROI[0].Top = 0;
        r->ROI[0].Bottom = (mfxU32)(o.h / 2);
    } else {
        r->ROI[0].Top = (mfxU32)(o.h / 2);
        r->ROI[0].Bottom = (mfxU32)P.mfx.FrameInfo.Height;
    }
    r->ROI[0].DeltaQP = (mfxI16)o.delta;
}

static mfxExtMBQP *alloc_mbqp(void)
{
    size_t n = (size_t)blocks_w * blocks_h;
    mfxExtMBQP *m = calloc(1, sizeof(*m) + n);
    if (!m)
        die("calloc", MFX_ERR_MEMORY_ALLOC);
    m->Header.BufferId = MFX_EXTBUFF_MBQP;
    m->Header.BufferSz = sizeof(*m);
    m->Mode = o.qpvalue ? MFX_MBQP_MODE_QP_VALUE : MFX_MBQP_MODE_QP_DELTA;
    m->BlockSize = 16;
    m->Pitch = (mfxU32)blocks_w;
    m->NumQPAlloc = (mfxU32)n;
    m->DeltaQP = (mfxI8 *)(m + 1);
    return m;
}

static void write_task(Task *t)
{
    mfxStatus st;
    do {
        st = MFXVideoCORE_SyncOperation(S, t->sp, 60000);
    } while (st == MFX_WRN_IN_EXECUTION);
    if (st < 0)
        die("SyncOperation", st);
    fwrite(t->bs.Data + t->bs.DataOffset, 1, t->bs.DataLength, fout);
    int frame = (int)t->bs.TimeStamp;
    if (frame >= 0 && frame < MAXFRAMES)
        synced[frame] = 1;
    double sq = -1, sq_top = -1, sq_bot = -1;
    long nintra = -1, ninter = -1, nskip = -1;
    int have_blk = 0;
    if (o.stats) {
        mfxEncodeStatsContainer *c = t->st.EncodeStatsContainer;
        if (c) {
            if (c->SynchronizeStatistics)
                c->SynchronizeStatistics(&c->RefInterface, 60000);
            if (c->EncodeFrameStats) {
                sq = c->EncodeFrameStats->Qp;
                nintra = c->EncodeFrameStats->NumIntraBlock;
                ninter = c->EncodeFrameStats->NumInterBlock;
                nskip = c->EncodeFrameStats->NumSkippedBlock;
            }
            if (c->EncodeBlkStats) {
                double st_top = 0, st_bot = 0;
                long nt = 0, nb = 0;
                mfxEncodeBlkStats *b = c->EncodeBlkStats;
                if (o.codec == MFX_CODEC_AVC && b->AVCMBArray) {
                    int mbw = (P.mfx.FrameInfo.Width + 15) / 16;
                    for (mfxU32 i = 0; i < b->NumMB; i++) {
                        int y = (int)(i / mbw) * 16;
                        if (y + 16 <= o.h / 2) { st_top += b->AVCMBArray[i].Qp; nt++; }
                        else if (y >= o.h / 2 && y < o.h) { st_bot += b->AVCMBArray[i].Qp; nb++; }
                    }
                    have_blk = 1;
                } else if (o.codec == MFX_CODEC_HEVC && b->HEVCCTUArray && b->NumCTU) {
                    /* CurrYAddr's unit is undocumented: if the largest address is
                     * below half the height it is a CTU row index, else pixels. */
                    int maxy = 0;
                    for (mfxU32 i = 0; i < b->NumCTU; i++)
                        if (b->HEVCCTUArray[i].CtuHeader.CurrYAddr > maxy)
                            maxy = b->HEVCCTUArray[i].CtuHeader.CurrYAddr;
                    int rows_unit = maxy < o.h / 2;
                    int ctu_px = rows_unit ? (P.mfx.FrameInfo.Height + maxy) / (maxy + 1) : 1;
                    ctu_px = ctu_px > 32 ? 64 : ctu_px > 16 ? 32 : ctu_px;
                    if (frame == 0)
                        fprintf(stderr, "HEVC CTU stats: NumCTU %u maxCurrYAddr %d -> %s (ctu %d px)\n",
                                b->NumCTU, maxy, rows_unit ? "row units" : "pixel units", ctu_px);
                    for (mfxU32 i = 0; i < b->NumCTU; i++) {
                        mfxCTUInfo *ctu = &b->HEVCCTUArray[i];
                        int ncu = ctu->CtuHeader.bitfields0.CUcountminus1 + 1;
                        int y0 = ctu->CtuHeader.CurrYAddr * (rows_unit ? ctu_px : 1);
                        int y1 = y0 + (rows_unit ? ctu_px : 32);
                        double q = 0;
                        for (int k = 0; k < ncu && k < 64; k++)
                            q += ctu->CuInfo[k].QP;
                        q /= ncu;
                        if (y1 <= o.h / 2) { st_top += q; nt++; }
                        else if (y0 >= o.h / 2 && y0 < o.h) { st_bot += q; nb++; }
                    }
                    have_blk = 1;
                }
                sq_top = nt ? st_top / nt : -1;
                sq_bot = nb ? st_bot / nb : -1;
            }
            c->RefInterface.Release(&c->RefInterface);
            t->st.EncodeStatsContainer = NULL;
        }
    }
    fprintf(fcsv, "%d,%d,%u,%u,%.3f,%.3f,%.3f,%d,%ld,%ld,%ld,%d,%d\n", frame, out_count++,
            t->bs.FrameType, t->bs.DataLength, sq, sq_top, sq_bot, have_blk, nintra, ninter,
            nskip, o.finfo ? (int)t->fi.QP : -1, o.units ? (int)t->eu.NumUnitsEncoded : -1);
    t->bs.DataLength = 0;
    t->bs.DataOffset = 0;
    t->used = 0;
    t->sp = NULL;
}

static int parse(int argc, char **argv)
{
    for (int i = 1; i < argc; i++) {
        char *eq = strchr(argv[i], '=');
        if (!eq)
            return -1;
        *eq = 0;
        const char *k = argv[i], *v = eq + 1;
        if (!strcmp(k, "impl")) o.impl = (unsigned)atoi(v);
        else if (!strcmp(k, "codec")) o.codec = !strcmp(v, "avc") ? MFX_CODEC_AVC : MFX_CODEC_HEVC;
        else if (!strcmp(k, "lp")) o.lp = !strcmp(v, "on") ? MFX_CODINGOPTION_ON : !strcmp(v, "off") ? MFX_CODINGOPTION_OFF : 0;
        else if (!strcmp(k, "qp")) o.qp = atoi(v);
        else if (!strcmp(k, "w")) o.w = atoi(v);
        else if (!strcmp(k, "h")) o.h = atoi(v);
        else if (!strcmp(k, "n")) o.n = atoi(v);
        else if (!strcmp(k, "in")) o.in = v;
        else if (!strcmp(k, "out")) o.out = v;
        else if (!strcmp(k, "csv")) o.csv = v;
        else if (!strcmp(k, "async")) o.async = atoi(v);
        else if (!strcmp(k, "mode")) o.mode = v;
        else if (!strcmp(k, "pattern")) o.pattern = v;
        else if (!strcmp(k, "delta")) o.delta = atoi(v);
        else if (!strcmp(k, "refdist")) o.refdist = atoi(v);
        else if (!strcmp(k, "skipquery")) o.skipquery = atoi(v);
        else if (!strcmp(k, "enablembqp")) o.enablembqp = !strcmp(v, "auto") ? -1 : atoi(v);
        else if (!strcmp(k, "stats")) o.stats = atoi(v);
        else if (!strcmp(k, "units")) o.units = atoi(v);
        else if (!strcmp(k, "finfo")) o.finfo = atoi(v);
        else if (!strcmp(k, "qpmode")) o.qpvalue = !strcmp(v, "value");
        else if (!strcmp(k, "initmbqp")) o.initmbqp = atoi(v);
        else if (!strcmp(k, "reuse")) o.reuse_sync = !strcmp(v, "sync");
        else return -1;
    }
    return (o.w > 0 && o.h > 0 && o.n > 0 && o.in && o.out && o.csv && o.async > 0 &&
            o.async <= MAXTASK) ? 0 : -1;
}

int main(int argc, char **argv)
{
    if (parse(argc, argv) < 0) {
        fprintf(stderr, "bad arguments (see header)\n");
        return 1;
    }
    int is_mbqp = !strcmp(o.mode, "mbqp") || !strcmp(o.mode, "shared");
    int want_mbqp = o.enablembqp < 0 ? is_mbqp : o.enablembqp;

    mfxLoader L = MFXLoad();
    if (!L)
        die("MFXLoad", MFX_ERR_UNKNOWN);
    mfxStatus st = MFXCreateSession(L, o.impl, &S);
    if (st != MFX_ERR_NONE)
        die("MFXCreateSession", st);

    memset(&P, 0, sizeof(P));
    P.IOPattern = MFX_IOPATTERN_IN_SYSTEM_MEMORY;
    P.AsyncDepth = (mfxU16)o.async;
    P.mfx.CodecId = o.codec;
    P.mfx.TargetUsage = 4;
    P.mfx.LowPower = o.lp;
    P.mfx.RateControlMethod = MFX_RATECONTROL_CQP;
    P.mfx.QPI = P.mfx.QPP = P.mfx.QPB = (mfxU16)o.qp;
    P.mfx.GopPicSize = 600;
    P.mfx.GopRefDist = (mfxU16)o.refdist;
    P.mfx.IdrInterval = 0;
    P.mfx.FrameInfo.FourCC = MFX_FOURCC_NV12;
    P.mfx.FrameInfo.ChromaFormat = MFX_CHROMAFORMAT_YUV420;
    P.mfx.FrameInfo.PicStruct = MFX_PICSTRUCT_PROGRESSIVE;
    P.mfx.FrameInfo.Width = (mfxU16)((o.w + 15) & ~15);
    P.mfx.FrameInfo.Height = (mfxU16)((o.h + 15) & ~15);
    P.mfx.FrameInfo.CropW = (mfxU16)o.w;
    P.mfx.FrameInfo.CropH = (mfxU16)o.h;
    P.mfx.FrameInfo.FrameRateExtN = 30;
    P.mfx.FrameInfo.FrameRateExtD = 1;
    P.mfx.FrameInfo.AspectRatioW = P.mfx.FrameInfo.AspectRatioH = 1;
    blocks_w = (P.mfx.FrameInfo.Width + 15) / 16;
    blocks_h = (P.mfx.FrameInfo.Height + 15) / 16;

    mfxExtCodingOption3 co3;
    memset(&co3, 0, sizeof(co3));
    co3.Header.BufferId = MFX_EXTBUFF_CODING_OPTION3;
    co3.Header.BufferSz = sizeof(co3);
    if (want_mbqp)
        co3.EnableMBQP = MFX_CODINGOPTION_ON;
    if (o.units)
        co3.EncodedUnitsInfo = MFX_CODINGOPTION_ON;
    mfxExtBuffer *pext[2] = { (mfxExtBuffer *)&co3, NULL };
    P.ExtParam = pext;
    P.NumExtParam = 1;
    mfxExtMBQP initq;
    memset(&initq, 0, sizeof(initq));
    if (o.initmbqp) { /* init-time mfxExtMBQP: declares Mode/BlockSize, no map */
        initq.Header.BufferId = MFX_EXTBUFF_MBQP;
        initq.Header.BufferSz = sizeof(initq);
        initq.Mode = o.qpvalue ? MFX_MBQP_MODE_QP_VALUE : MFX_MBQP_MODE_QP_DELTA;
        initq.BlockSize = 16;
        pext[P.NumExtParam++] = (mfxExtBuffer *)&initq;
    }

    if (!o.skipquery) {
        st = MFXVideoENCODE_Query(S, &P, &P);
        fprintf(stderr, "Query status %d; EnableMBQP after Query 0x%x; EncodedUnitsInfo 0x%x\n",
                (int)st, co3.EnableMBQP, co3.EncodedUnitsInfo);
        if (st < 0)
            die("Query", st);
    }
    mfxFrameAllocRequest req;
    memset(&req, 0, sizeof(req));
    st = MFXVideoENCODE_QueryIOSurf(S, &P, &req);
    if (st < 0)
        die("QueryIOSurf", st);
    st = MFXVideoENCODE_Init(S, &P);
    fprintf(stderr, "Init status %d\n", (int)st);
    if (st < 0)
        die("Init", st);

    mfxVideoParam g;
    mfxExtCodingOption3 gco3;
    memset(&g, 0, sizeof(g));
    memset(&gco3, 0, sizeof(gco3));
    gco3.Header.BufferId = MFX_EXTBUFF_CODING_OPTION3;
    gco3.Header.BufferSz = sizeof(gco3);
    mfxExtBuffer *gext[1] = { (mfxExtBuffer *)&gco3 };
    g.ExtParam = gext;
    g.NumExtParam = 1;
    st = MFXVideoENCODE_GetVideoParam(S, &g);
    mfxVersion ver;
    MFXQueryVersion(S, &ver);
    fprintf(stderr,
            "GetVideoParam %d: API %u.%u LowPower 0x%x TU %u GopRefDist %u QPI/P/B %u/%u/%u "
            "AsyncDepth %u BufferSizeInKB %u EnableMBQP 0x%x EncodedUnitsInfo 0x%x surfaces %u\n",
            (int)st, ver.Major, ver.Minor, g.mfx.LowPower, g.mfx.TargetUsage, g.mfx.GopRefDist,
            g.mfx.QPI, g.mfx.QPP, g.mfx.QPB, g.AsyncDepth, g.mfx.BufferSizeInKB, gco3.EnableMBQP,
            gco3.EncodedUnitsInfo, req.NumFrameSuggested);

    int nslot = req.NumFrameSuggested + o.async + 8;
    if (nslot > MAXSLOT)
        nslot = MAXSLOT;
    static Slot slots[MAXSLOT];
    int pitch = (P.mfx.FrameInfo.Width + 63) & ~63;
    size_t fsz = (size_t)pitch * P.mfx.FrameInfo.Height * 3 / 2;
    for (int i = 0; i < nslot; i++) {
        Slot *sl = &slots[i];
        sl->buf = calloc(1, fsz);
        if (!sl->buf)
            die("calloc surface", MFX_ERR_MEMORY_ALLOC);
        sl->s.Info = P.mfx.FrameInfo;
        sl->s.Data.Y = sl->buf;
        sl->s.Data.UV = sl->buf + (size_t)pitch * P.mfx.FrameInfo.Height;
        sl->s.Data.Pitch = (mfxU16)pitch;
        sl->ctrl.ExtParam = sl->ctrlext;
        sl->frame = -1;
        if (!strcmp(o.mode, "mbqp"))
            sl->mbqp = alloc_mbqp();
    }
    /* shared (defective) mode state */
    mfxEncodeCtrl gctrl;
    mfxExtBuffer *gctrlext[1];
    mfxExtMBQP *gmbqp = NULL;
    memset(&gctrl, 0, sizeof(gctrl));
    static mfxExtEncoderROI groi;
    if (!strcmp(o.mode, "shared")) {
        gmbqp = alloc_mbqp();
        gctrlext[0] = (mfxExtBuffer *)gmbqp;
        gctrl.ExtParam = gctrlext;
        gctrl.NumExtParam = 1;
    } else if (!strcmp(o.mode, "sharedrect")) {
        gctrlext[0] = (mfxExtBuffer *)&groi;
        gctrl.ExtParam = gctrlext;
        gctrl.NumExtParam = 1;
    }

    mfxU32 maxlen = (mfxU32)g.mfx.BufferSizeInKB * (g.mfx.BRCParamMultiplier ? g.mfx.BRCParamMultiplier : 1) * 1000;
    if (maxlen < (mfxU32)(o.w * o.h * 2))
        maxlen = (mfxU32)(o.w * o.h * 2);
    static Task tasks[MAXTASK];
    for (int i = 0; i < o.async; i++) {
        Task *t = &tasks[i];
        t->bs.Data = malloc(maxlen);
        t->bs.MaxLength = maxlen;
        int ne = 0;
        if (o.stats) {
            t->st.Header.BufferId = MFX_EXTBUFF_ENCODESTATS;
            t->st.Header.BufferSz = sizeof(t->st);
            t->st.EncodeStatsFlags = MFX_ENCODESTATS_LEVEL_BLK | MFX_ENCODESTATS_LEVEL_FRAME;
            t->st.Mode = MFX_ENCODESTATS_MODE_DEFAULT;
            t->ext[ne++] = (mfxExtBuffer *)&t->st;
        }
        if (o.units) {
            t->eu.Header.BufferId = MFX_EXTBUFF_ENCODED_UNITS_INFO;
            t->eu.Header.BufferSz = sizeof(t->eu);
            t->eu.UnitInfo = t->units;
            t->eu.NumUnitsAlloc = 64;
            t->ext[ne++] = (mfxExtBuffer *)&t->eu;
        }
        if (o.finfo) {
            t->fi.Header.BufferId = MFX_EXTBUFF_ENCODED_FRAME_INFO;
            t->fi.Header.BufferSz = sizeof(t->fi);
            t->ext[ne++] = (mfxExtBuffer *)&t->fi;
        }
        t->bs.ExtParam = ne ? t->ext : NULL;
        t->bs.NumExtParam = (mfxU16)ne;
    }

    FILE *fin = fopen(o.in, "rb");
    fout = fopen(o.out, "wb");
    fcsv = fopen(o.csv, "w");
    char rpath[1024];
    snprintf(rpath, sizeof(rpath), "%s.reuse.csv", o.csv);
    freuse = fopen(rpath, "w");
    if (!fin || !fout || !fcsv || !freuse)
        die("fopen", MFX_ERR_NULL_PTR);
    fprintf(freuse, "frame_submitted,slot,previous_frame_not_yet_synced\n");
    fprintf(fcsv, "frame,out_idx,frame_type,bytes,stats_qp,stats_qp_top,stats_qp_bot,blk_stats,"
                  "n_intra,n_inter,n_skip,finfo_qp,units\n");
    size_t ysz = (size_t)o.w * o.h, csz = ysz / 2;
    uint8_t *rowbuf = malloc(ysz + csz);
    int head = 0, warned_busy = 0;
    mfxStatus first_warn = MFX_ERR_NONE;
    for (int f = 0; f <= o.n; f++) {
        int drain = f == o.n;
        Slot *sl = NULL;
        mfxEncodeCtrl *ctrl = NULL;
        if (!drain) {
            if (fread(rowbuf, 1, ysz + csz, fin) != ysz + csz) {
                fprintf(stderr, "short input at frame %d\n", f);
                break;
            }
            for (;;) {
                /* reuse=lock: the oneVPL rule (user ext buffers may change once
                 * surface.Data.Locked is zero) -- also FFmpeg's clear_unused_frames().
                 * reuse=sync: additionally wait until the slot's previous frame has
                 * come out of SyncOperation. */
                for (int i = 0; i < nslot && !sl; i++)
                    if (!slots[i].s.Data.Locked &&
                        (!o.reuse_sync || slots[i].frame < 0 || synced[slots[i].frame]))
                        sl = &slots[i];
                if (sl)
                    break;
                Task *old = &tasks[head];
                if (old->used) {
                    write_task(old);
                } else {
                    static int starve;
                    if (++starve > 10000)
                        die("slot starvation (no free slot, no pending task)", MFX_ERR_NOT_ENOUGH_BUFFER);
                    Sleep(1);
                }
            }
            for (int y = 0; y < o.h; y++)
                memcpy(sl->s.Data.Y + (size_t)y * pitch, rowbuf + (size_t)y * o.w, (size_t)o.w);
            for (int y = 0; y < o.h / 2; y++)
                memcpy(sl->s.Data.UV + (size_t)y * pitch, rowbuf + ysz + (size_t)y * o.w, (size_t)o.w);
            if (sl->frame >= 0 && !synced[sl->frame]) {
                early_reuse++;
                fprintf(freuse, "%d,%d,%d\n", f, (int)(sl - slots), sl->frame);
            }
            sl->s.Data.TimeStamp = (mfxU64)f;
            sl->s.Data.FrameOrder = (mfxU32)f;
            sl->frame = f;
            sl->ctrl.NumExtParam = 0;
            if (!strcmp(o.mode, "mbqp")) {
                paint_map(sl->mbqp->DeltaQP, f);
                sl->ctrlext[0] = (mfxExtBuffer *)sl->mbqp;
                sl->ctrl.NumExtParam = 1;
                ctrl = &sl->ctrl;
            } else if (!strcmp(o.mode, "rect")) {
                paint_rect(&sl->roi, f);
                sl->ctrlext[0] = (mfxExtBuffer *)&sl->roi;
                sl->ctrl.NumExtParam = 1;
                ctrl = &sl->ctrl;
            } else if (!strcmp(o.mode, "shared")) {
                /* the defect: repaint the one live map for THIS frame although
                 * earlier submissions may still reference it */
                paint_map(gmbqp->DeltaQP, f);
                ctrl = &gctrl;
            } else if (!strcmp(o.mode, "sharedrect")) {
                /* same hazard for the rectangle buffer: one mfxExtEncoderROI */
                paint_rect(&groi, f);
                ctrl = &gctrl;
            }
        }
        for (;;) {
            Task *t = &tasks[head];
            if (t->used)
                write_task(t);
            st = MFXVideoENCODE_EncodeFrameAsync(S, ctrl, drain ? NULL : &sl->s, &t->bs, &t->sp);
            if (st == MFX_WRN_DEVICE_BUSY) {
                if (!warned_busy++)
                    fprintf(stderr, "device busy (retrying)\n");
                Sleep(1);
                continue;
            }
            if (st > 0 && first_warn == MFX_ERR_NONE) {
                first_warn = st;
                fprintf(stderr, "EncodeFrameAsync warning %d at frame %d\n", (int)st, f);
            }
            if (st == MFX_ERR_MORE_DATA) {
                if (drain)
                    goto drained;
                break;
            }
            if (st < 0 && st != MFX_ERR_MORE_DATA)
                die("EncodeFrameAsync", st);
            if (t->sp) {
                t->used = 1;
                head = (head + 1) % o.async;

            }
            if (!drain)
                break;
        }
    }
drained:
    for (int i = 0; i < o.async; i++) {
        Task *t = &tasks[(head + i) % o.async];
        if (t->used)
            write_task(t);
    }
    fprintf(stderr, "done: %d outputs, first warning %d, reuse=%s, early slot reuses (previous frame not yet synced) %ld\n",
            out_count, (int)first_warn, o.reuse_sync ? "sync" : "lock", early_reuse);
    fclose(freuse);
    fclose(fout);
    fclose(fcsv);
    fclose(fin);
    MFXVideoENCODE_Close(S);
    MFXClose(S);
    MFXUnload(L);
    return 0;
}
