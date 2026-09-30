#!/usr/bin/env python3
"""One-off edit: add reuse=lock|sync + early-reuse accounting to the encode
probe, and a relative (B-pyramid-aware) expectation mode to qpdump."""
import sys

BS = "\\"  # a single backslash, so the C sources get \n escapes


def rep(s, old, new, cnt=1):
    if s.count(old) != cnt:
        sys.exit(f"pattern count {s.count(old)} != {cnt}: {old!r}")
    return s.replace(old, new)


p = "qsv-encode-probe.c"
s = open(p, newline="").read()
s = rep(s, "    int qpvalue, initmbqp;\n", "    int qpvalue, initmbqp, reuse_sync;\n")
s = rep(s, "static Opt o = { 0, MFX_CODEC_HEVC, 0, 30, 0, 0, 0, 4, 12, 1, 0, -1, 0, 0, 0, 0, 0,\n",
        "static Opt o = { 0, MFX_CODEC_HEVC, 0, 30, 0, 0, 0, 4, 12, 1, 0, -1, 0, 0, 0, 0, 0, 0,\n")
s = rep(s, '        else if (!strcmp(k, "initmbqp")) o.initmbqp = atoi(v);\n',
        '        else if (!strcmp(k, "initmbqp")) o.initmbqp = atoi(v);\n'
        '        else if (!strcmp(k, "reuse")) o.reuse_sync = !strcmp(v, "sync");\n')
s = rep(s, "static int out_count;\n",
        "static int out_count;\n"
        "#define MAXFRAMES 100000\n"
        "static unsigned char synced[MAXFRAMES]; /* frame index -> bitstream synced */\n"
        "static long early_reuse;                 /* slot reused before its frame was synced */\n"
        "static FILE *freuse;\n")
s = rep(s, "    int frame = (int)t->bs.TimeStamp;\n",
        "    int frame = (int)t->bs.TimeStamp;\n"
        "    if (frame >= 0 && frame < MAXFRAMES)\n"
        "        synced[frame] = 1;\n")
s = rep(s, """            for (;;) {
                for (int i = 0; i < nslot && !sl; i++)
                    if (!slots[i].s.Data.Locked)
                        sl = &slots[i];
                if (sl)
                    break;""",
        """            for (;;) {
                /* reuse=lock: the oneVPL rule (user ext buffers may change once
                 * surface.Data.Locked is zero) -- also FFmpeg's clear_unused_frames().
                 * reuse=sync: additionally wait until the slot's previous frame has
                 * come out of SyncOperation. */
                for (int i = 0; i < nslot && !sl; i++)
                    if (!slots[i].s.Data.Locked &&
                        (!o.reuse_sync || slots[i].frame < 0 || synced[slots[i].frame]))
                        sl = &slots[i];
                if (sl)
                    break;""")
s = rep(s, "            sl->s.Data.TimeStamp = (mfxU64)f;",
        "            if (sl->frame >= 0 && !synced[sl->frame]) {\n"
        "                early_reuse++;\n"
        f'                fprintf(freuse, "%d,%d,%d{BS}n", f, (int)(sl - slots), sl->frame);\n'
        "            }\n"
        "            sl->s.Data.TimeStamp = (mfxU64)f;")
s = rep(s, """    if (!fin || !fout || !fcsv)
        die("fopen", MFX_ERR_NULL_PTR);""",
        """    char rpath[1024];
    snprintf(rpath, sizeof(rpath), "%s.reuse.csv", o.csv);
    freuse = fopen(rpath, "w");
    if (!fin || !fout || !fcsv || !freuse)
        die("fopen", MFX_ERR_NULL_PTR);
""" + f'    fprintf(freuse, "frame_submitted,slot,previous_frame_not_yet_synced{BS}n");')
s = rep(s, f'    fprintf(stderr, "done: %d outputs, first warning %d{BS}n", out_count, (int)first_warn);',
        f'    fprintf(stderr, "done: %d outputs, first warning %d, reuse=%s, early slot reuses '
        f'(previous frame not yet synced) %ld{BS}n",\n'
        '            out_count, (int)first_warn, o.reuse_sync ? "sync" : "lock", early_reuse);\n'
        "    fclose(freuse);")
s = rep(s, " *   enablembqp=auto|0|1 stats=0 units=0 finfo=0\n",
        " *   enablembqp=auto|0|1 stats=0 units=0 finfo=0 qpmode=delta|value initmbqp=0 reuse=lock|sync\n")
open(p, "w", newline="\n").write(s)

p = "qpdump.c"
s = open(p, newline="").read()
s = rep(s, """                int top_roi = expect && (!strcmp(expect, "top") || (idx % 2) == 0);
                int prev = -1000;""",
        """                int top_roi = expect && (!strncmp(expect, "top", 3) || (idx % 2) == 0);
                int rel = expect && strstr(expect, "rel") != NULL;
                int rbase = base_qp;
                if (rel) {
                    /* relative mode: roi_qp is a delta; the reference is the modal QP of
                     * the non-ROI half (absorbs B-pyramid QP offsets) */
                    int hist[64] = { 0 }, best = 0;
                    for (unsigned i = 0; i < p->nb_blocks; i++) {
                        AVVideoBlockParams *b = av_video_enc_params_block(p, i);
                        int q = p->qp + b->delta_qp;
                        int in_t = b->src_y + b->h <= half, in_b = b->src_y >= half;
                        if (((in_t && !top_roi) || (in_b && top_roi)) && q >= 0 && q < 64)
                            hist[q]++;
                    }
                    for (int q = 1; q < 64; q++)
                        if (hist[q] > hist[best])
                            best = q;
                    rbase = best;
                }
                int want_roi = rel ? rbase + roi_qp : roi_qp;
                int prev = -1000;""")
s = rep(s, "                        int want = ((in_top && top_roi) || (in_bot && !top_roi)) ? roi_qp : base_qp;",
        "                        int want = ((in_top && top_roi) || (in_bot && !top_roi)) ? want_roi : rbase;")
s = rep(s, " * Optional expectation check (argv[3..5]): expect=alt|top, roi QP, base QP.\n",
        " * Optional expectation check (argv[3..5]): expect=alt|top, roi QP, base QP;\n"
        " * altrel|toprel: roi QP is a delta over the modal QP of the non-ROI half.\n")
open(p, "w", newline="\n").write(s)
print("patched")
