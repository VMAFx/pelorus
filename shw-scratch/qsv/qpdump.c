/*
 * shw-3 qpdump.c -- decode an H.264 stream with libavcodec and print the
 * per-macroblock QP the bitstream actually carries (AV_FRAME_DATA_VIDEO_ENC_PARAMS,
 * exported by FFmpeg's h264 decoder), summarised per frame for the top and
 * bottom visible halves.  This is an exact, decoder-side check of where a
 * per-frame ROI / MBQP map landed.
 *
 * Optional expectation check (argv[3..5]): expect=alt|top, roi QP, base QP;
 * altrel|toprel: roi QP is a delta over the modal QP of the non-ROI half.
 * alt = ROI on the top half for even output frames, bottom half for odd ones.
 * The MB row straddling the half boundary is ignored.  A P_Skip macroblock has
 * no mb_qp_delta, so the decoder reports the running QP predictor (the last
 * coded MB's QP) for it; an off-pattern MB is therefore counted "explained"
 * when it repeats the previous MB's QP in raster order, and "unexplained"
 * otherwise.  A frame encoded with another frame's map produces unexplained
 * MBs at the first MB of each wrongly-quantised run, and far more off-pattern
 * MBs (about half a frame) than the at-most-a-row skip runs.
 *
 * Build (MSYS2 UCRT64, static FFmpeg in /c/tmp/pel/prefix):
 *   PKG_CONFIG_PATH=/c/tmp/pel/prefix/lib/pkgconfig gcc -O1 -o qpdump.exe qpdump.c \
 *       $(pkg-config --static --cflags --libs libavformat libavcodec libavutil)
 * Usage: qpdump.exe <file.h264|.mkv> [mapframe] [alt|top roi_qp base_qp]
 *        CSV to stdout; the full MB QP map of frame <mapframe> goes to stderr.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <libavcodec/avcodec.h>
#include <libavformat/avformat.h>
#include <libavutil/video_enc_params.h>

int main(int argc, char **argv)
{
    if (argc < 2)
        return 1;
    int mapframe = argc > 2 ? atoi(argv[2]) : -1;
    const char *expect = argc > 3 ? argv[3] : NULL;
    int roi_qp = argc > 4 ? atoi(argv[4]) : 0, base_qp = argc > 5 ? atoi(argv[5]) : 0;
    AVFormatContext *fc = NULL;
    if (avformat_open_input(&fc, argv[1], NULL, NULL) < 0 || avformat_find_stream_info(fc, NULL) < 0)
        return 2;
    int si = av_find_best_stream(fc, AVMEDIA_TYPE_VIDEO, -1, -1, NULL, 0);
    if (si < 0)
        return 3;
    const AVCodec *dec = avcodec_find_decoder(fc->streams[si]->codecpar->codec_id);
    AVCodecContext *c = avcodec_alloc_context3(dec);
    avcodec_parameters_to_context(c, fc->streams[si]->codecpar);
    c->export_side_data |= AV_CODEC_EXPORT_DATA_VIDEO_ENC_PARAMS;
    c->thread_count = 1;
    if (avcodec_open2(c, dec, NULL) < 0)
        return 4;
    AVPacket *pkt = av_packet_alloc();
    AVFrame *fr = av_frame_alloc();
    int idx = 0, eof = 0;
    long tot_off = 0, tot_unexp = 0, frames_unexp = 0, max_off = 0, frames_big = 0;
    int row_mbs = 0;
    int *qmap = NULL;
    printf("frame,pict_type,base_qp,top_mean,top_min,top_max,bot_mean,bot_min,bot_max,nblocks,n_off,n_unexplained\n");
    while (!eof) {
        int r = av_read_frame(fc, pkt);
        if (r < 0) {
            eof = 1;
            avcodec_send_packet(c, NULL);
        } else {
            if (pkt->stream_index == si)
                avcodec_send_packet(c, pkt);
            av_packet_unref(pkt);
        }
        while (avcodec_receive_frame(c, fr) == 0) {
            AVFrameSideData *sd = av_frame_get_side_data(fr, AV_FRAME_DATA_VIDEO_ENC_PARAMS);
            int half = fr->height / 2;
            double st = 0, sb = 0;
            int nt = 0, nb = 0, tmin = 99, tmax = -99, bmin = 99, bmax = -99, base = -1, nbk = 0;
            long n_off = -1, n_unexp = -1;
            if (sd) {
                AVVideoEncParams *p = (AVVideoEncParams *)sd->data;
                base = p->qp;
                nbk = (int)p->nb_blocks;
                row_mbs = (fr->width + 15) / 16;
                if (!qmap)
                    qmap = calloc(p->nb_blocks, sizeof(*qmap));
                int top_roi = expect && (!strncmp(expect, "top", 3) || (idx % 2) == 0);
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
                int prev = -1000;
                if (expect)
                    n_off = n_unexp = 0;
                for (unsigned i = 0; i < p->nb_blocks; i++) {
                    AVVideoBlockParams *b = av_video_enc_params_block(p, i);
                    int q = p->qp + b->delta_qp;
                    qmap[i] = q;
                    if (idx == mapframe)
                        fprintf(stderr, "%3d%s", q, (b->src_x + b->w >= fr->width) ? "\n" : "");
                    int in_top = b->src_y + b->h <= half, in_bot = b->src_y >= half;
                    if (in_top) {
                        st += q; nt++;
                        if (q < tmin) tmin = q;
                        if (q > tmax) tmax = q;
                    } else if (in_bot) {
                        sb += q; nb++;
                        if (q < bmin) bmin = q;
                        if (q > bmax) bmax = q;
                    }
                    if (expect && (in_top || in_bot)) {
                        int want = ((in_top && top_roi) || (in_bot && !top_roi)) ? want_roi : rbase;
                        if (q != want) {
                            n_off++;
                            if (q != prev)
                                n_unexp++;
                        }
                    }
                    prev = q;
                }
            }
            if (n_unexp > 0)
                frames_unexp++;
            if (n_off > 0)
                tot_off += n_off;
            if (n_off > max_off)
                max_off = n_off;
            if (n_off > 2L * row_mbs)
                frames_big++;
            if (n_unexp > 0)
                tot_unexp += n_unexp;
            printf("%d,%c,%d,%.3f,%d,%d,%.3f,%d,%d,%d,%ld,%ld\n", idx, av_get_picture_type_char(fr->pict_type),
                   base, nt ? st / nt : -1.0, tmin, tmax, nb ? sb / nb : -1.0, bmin, bmax, nbk, n_off, n_unexp);
            idx++;
            av_frame_unref(fr);
        }
    }
    if (expect)
        fprintf(stderr, "SUMMARY frames=%d off_pattern_MBs=%ld unexplained_MBs=%ld frames_with_unexplained=%ld "
                "max_off_per_frame=%ld frames_off_gt_2rows=%ld (row=%d MBs)\n",
                idx, tot_off, tot_unexp, frames_unexp, max_off, frames_big, row_mbs);
    free(qmap);
    av_frame_free(&fr);
    av_packet_free(&pkt);
    avcodec_free_context(&c);
    avformat_close_input(&fc);
    return 0;
}
