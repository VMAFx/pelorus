/*
 * Copyright 2026 Lusoris
 *
 * SPDX-License-Identifier: LGPL-2.1-or-later
 */

/*
 * Direct regression coverage for the Pelorus QSV ROI patch.
 *
 * This file is copied into libavcodec/tests/ by qsv-roi-regression.sh.  It
 * includes qsvenc.c so the test can exercise the static ROI rasterizer without
 * requiring QSV hardware or an initialized oneVPL session.
 */

#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define ff_qsv_enc_init pelorus_test_ff_qsv_enc_init
#define ff_qsv_encode pelorus_test_ff_qsv_encode
#define ff_qsv_enc_close pelorus_test_ff_qsv_enc_close
#define ff_qsv_enc_hw_configs pelorus_test_ff_qsv_enc_hw_configs
#include "../qsvenc.c"

#define CHECK(condition)                                                                           \
    do {                                                                                           \
        if (!(condition)) {                                                                        \
            (void)fprintf(stderr, "check failed at %s:%d: %s\n", __FILE__, __LINE__, #condition);  \
            return 1;                                                                              \
        }                                                                                          \
    } while (0)

/* Count the one-time "EnableMBQP cleared" fallback warning the patch emits. */
static int mbqp_cleared_warnings;

static void count_warning_cb(void *avcl, int level, const char *fmt, va_list vl)
{
    char line[512];

    (void)avcl;
    if (level != AV_LOG_WARNING)
        return;
    (void)vsnprintf(line, sizeof(line), fmt, vl);
    if (strstr(line, "EnableMBQP is off after encoder initialization") &&
        strstr(line, "falling back to per-region mfxExtEncoderROI rectangles"))
        mbqp_cleared_warnings++;
}

static AVFrame *make_roi_frame(int width, int height, size_t nb_rois)
{
    AVFrame *frame = av_frame_alloc();
    AVFrameSideData *sd;
    AVRegionOfInterest *rois;

    if (!frame)
        return NULL;

    frame->width = width;
    frame->height = height;
    frame->format = AV_PIX_FMT_NV12;

    if (!nb_rois)
        return frame;

    sd = av_frame_new_side_data(frame, AV_FRAME_DATA_REGIONS_OF_INTEREST, nb_rois * sizeof(*rois));
    if (!sd) {
        av_frame_free(&frame);
        return NULL;
    }

    rois = (AVRegionOfInterest *)sd->data;
    for (size_t i = 0; i < nb_rois; i++)
        rois[i].self_size = sizeof(*rois);

    return frame;
}

static AVRegionOfInterest *frame_rois(AVFrame *frame)
{
    AVFrameSideData *sd = av_frame_get_side_data(frame, AV_FRAME_DATA_REGIONS_OF_INTEREST);

    return sd ? (AVRegionOfInterest *)sd->data : NULL;
}

static void set_roi(AVRegionOfInterest *roi, int left, int top, int right, int bottom,
                    AVRational qoffset)
{
    roi->left = left;
    roi->top = top;
    roi->right = right;
    roi->bottom = bottom;
    roi->qoffset = qoffset;
}

typedef struct RegressionContext {
    AVCodecContext avctx;
    QSVEncContext q;
    mfxEncodeCtrl ctrl_a;
    mfxEncodeCtrl ctrl_b;
    mfxEncodeCtrl ctrl_overlap;
    mfxEncodeCtrl ctrl_extended;
    mfxEncodeCtrl ctrl_bad_size;
    mfxEncodeCtrl ctrl_bad_entry;
    mfxEncodeCtrl ctrl_bad_den;
    mfxEncodeCtrl ctrl_bad_dims;
    mfxEncodeCtrl ctrl_interlaced;
    mfxExtBuffer *params_a[QSV_MAX_ENC_EXTPARAM];
    mfxExtBuffer *params_b[QSV_MAX_ENC_EXTPARAM];
    mfxExtBuffer *params_overlap[QSV_MAX_ENC_EXTPARAM];
    mfxExtBuffer *params_extended[QSV_MAX_ENC_EXTPARAM];
    mfxExtBuffer *params_bad_size[QSV_MAX_ENC_EXTPARAM];
    mfxExtBuffer *params_bad_entry[QSV_MAX_ENC_EXTPARAM];
    mfxExtBuffer *params_bad_den[QSV_MAX_ENC_EXTPARAM];
    mfxExtBuffer *params_bad_dims[QSV_MAX_ENC_EXTPARAM];
    mfxExtBuffer *params_interlaced[QSV_MAX_ENC_EXTPARAM];
    mfxExtCodingOption3 external_extco3;
    mfxExtBuffer *video_params[1];
    AVFrame *frame_a;
    AVFrame *frame_b;
    AVFrame *frame_overlap;
    AVFrame *frame_extended;
    AVFrame *frame_bad_size;
    AVFrame *frame_bad_entry;
    AVFrame *frame_bad_den;
    AVFrame *frame_bad_dims;
    AVFrame *frame_interlaced;
} RegressionContext;

static void init_context(RegressionContext *ctx)
{
    av_log_set_callback(count_warning_cb);

    ctx->ctrl_a.ExtParam = ctx->params_a;
    ctx->ctrl_b.ExtParam = ctx->params_b;
    ctx->ctrl_overlap.ExtParam = ctx->params_overlap;
    ctx->ctrl_extended.ExtParam = ctx->params_extended;
    ctx->ctrl_bad_size.ExtParam = ctx->params_bad_size;
    ctx->ctrl_bad_entry.ExtParam = ctx->params_bad_entry;
    ctx->ctrl_bad_den.ExtParam = ctx->params_bad_den;
    ctx->ctrl_bad_dims.ExtParam = ctx->params_bad_dims;
    ctx->ctrl_interlaced.ExtParam = ctx->params_interlaced;

    ctx->avctx.codec_id = AV_CODEC_ID_HEVC;
    ctx->avctx.width = 33;
    ctx->avctx.height = 33;
    ctx->avctx.sw_pix_fmt = AV_PIX_FMT_NV12;
    ctx->q.pelorus_roi = 1;
    ctx->q.param.mfx.FrameInfo.Width = 48;
    ctx->q.param.mfx.FrameInfo.Height = 64;
    ctx->q.param.mfx.FrameInfo.PicStruct = MFX_PICSTRUCT_PROGRESSIVE;
    ctx->q.param.mfx.RateControlMethod = MFX_RATECONTROL_CQP;
    ctx->q.ver.Major = 1;
    ctx->q.ver.Minor = 28;
    ctx->external_extco3.Header.BufferId = MFX_EXTBUFF_CODING_OPTION3;
    ctx->external_extco3.Header.BufferSz = sizeof(ctx->external_extco3);
    ctx->video_params[0] = (mfxExtBuffer *)&ctx->external_extco3;
    ctx->q.param.ExtParam = ctx->video_params;
    ctx->q.param.NumExtParam = FF_ARRAY_ELEMS(ctx->video_params);
}

static void cleanup_context(RegressionContext *ctx)
{
    free_encoder_ctrl(&ctx->ctrl_a);
    free_encoder_ctrl(&ctx->ctrl_b);
    free_encoder_ctrl(&ctx->ctrl_overlap);
    free_encoder_ctrl(&ctx->ctrl_extended);
    free_encoder_ctrl(&ctx->ctrl_bad_size);
    free_encoder_ctrl(&ctx->ctrl_bad_entry);
    free_encoder_ctrl(&ctx->ctrl_bad_den);
    free_encoder_ctrl(&ctx->ctrl_bad_dims);
    free_encoder_ctrl(&ctx->ctrl_interlaced);
    av_frame_free(&ctx->frame_a);
    av_frame_free(&ctx->frame_b);
    av_frame_free(&ctx->frame_overlap);
    av_frame_free(&ctx->frame_extended);
    av_frame_free(&ctx->frame_bad_size);
    av_frame_free(&ctx->frame_bad_entry);
    av_frame_free(&ctx->frame_bad_den);
    av_frame_free(&ctx->frame_bad_dims);
    av_frame_free(&ctx->frame_interlaced);
}

static int test_preconditions(RegressionContext *ctx)
{
    CHECK(qsvenc_pelorus_roi_mbqp_preconditions(&ctx->avctx, &ctx->q));
    ctx->q.ver.Minor = 27;
    CHECK(!qsvenc_pelorus_roi_mbqp_preconditions(&ctx->avctx, &ctx->q));
    ctx->q.ver.Minor = 28;
    ctx->avctx.codec_id = AV_CODEC_ID_H264;
    CHECK(!qsvenc_pelorus_roi_mbqp_preconditions(&ctx->avctx, &ctx->q));
    ctx->avctx.codec_id = AV_CODEC_ID_HEVC;
    ctx->q.param.mfx.RateControlMethod = MFX_RATECONTROL_VBR;
    CHECK(!qsvenc_pelorus_roi_mbqp_preconditions(&ctx->avctx, &ctx->q));
    ctx->q.param.mfx.RateControlMethod = MFX_RATECONTROL_CQP;
    ctx->q.param.mfx.FrameInfo.PicStruct = MFX_PICSTRUCT_FIELD_TFF;
    CHECK(!qsvenc_pelorus_roi_mbqp_preconditions(&ctx->avctx, &ctx->q));
    ctx->q.param.mfx.FrameInfo.PicStruct = MFX_PICSTRUCT_PROGRESSIVE;
    return 0;
}

static int test_fallback_warning(RegressionContext *ctx)
{
    ctx->frame_a = make_roi_frame(33, 33, 1);
    ctx->frame_b = make_roi_frame(33, 33, 1);
    CHECK(ctx->frame_a && ctx->frame_b);
    set_roi(frame_rois(ctx->frame_a), 0, 0, 33, 33, (AVRational){-1, 10});
    set_roi(frame_rois(ctx->frame_b), 0, 0, 33, 33, (AVRational){1, 10});

    /* AVQSVContext buffers replace internal buffers with the same BufferId.
     * An external CodingOption3 therefore controls the final attached value. */
    ctx->external_extco3.EnableMBQP = MFX_CODINGOPTION_UNKNOWN;
    qsvenc_pelorus_roi_update_mbqp_enabled(&ctx->avctx, &ctx->q);
    /* The runtime (or an external buffer) left EnableMBQP off although
     * pelorus_roi is on: the encoder must say so exactly once. */
    CHECK(!ctx->q.pelorus_roi_mbqp_enabled);
    CHECK(mbqp_cleared_warnings == 1);
    qsvenc_pelorus_roi_update_mbqp_enabled(&ctx->avctx, &ctx->q);
    CHECK(mbqp_cleared_warnings == 1);
    ctx->q.param.ExtParam = NULL;
    ctx->q.param.NumExtParam = 0;
    CHECK(!qsvenc_pelorus_roi_frame_uses_mbqp(&ctx->avctx, &ctx->q, ctx->frame_a));
    CHECK(qsvenc_setup_roi(&ctx->avctx, &ctx->q, ctx->frame_a, &ctx->ctrl_a) == 0);
    CHECK(ctx->ctrl_a.NumExtParam == 0);
    CHECK(set_roi_encode_ctrl(&ctx->avctx, ctx->frame_a, &ctx->ctrl_a) == 0);
    CHECK(ctx->ctrl_a.NumExtParam == 1);
    CHECK(ctx->ctrl_a.ExtParam[0]->BufferId == MFX_EXTBUFF_ENCODER_ROI);
    free_encoder_ctrl(&ctx->ctrl_a);
    return 0;
}

static int test_mbqp_version_downgrade(RegressionContext *ctx)
{
    ctx->q.ver.Minor = 27;
    CHECK(!qsvenc_pelorus_roi_frame_uses_mbqp(&ctx->avctx, &ctx->q, ctx->frame_a));
    CHECK(qsvenc_setup_roi(&ctx->avctx, &ctx->q, ctx->frame_a, &ctx->ctrl_a) == 0);
    CHECK(ctx->ctrl_a.NumExtParam == 0);
    CHECK(set_roi_encode_ctrl(&ctx->avctx, ctx->frame_a, &ctx->ctrl_a) == 0);
    CHECK(ctx->ctrl_a.NumExtParam == 1);
    CHECK(ctx->ctrl_a.ExtParam[0]->BufferId == MFX_EXTBUFF_ENCODER_ROI);
    free_encoder_ctrl(&ctx->ctrl_a);
    ctx->q.ver.Minor = 28;
    return 0;
}

static int test_mbqp_enable(RegressionContext *ctx)
{
    QSVEncContext q_idle = {0};

    ctx->q.param.ExtParam = ctx->video_params;
    ctx->q.param.NumExtParam = FF_ARRAY_ELEMS(ctx->video_params);
    ctx->external_extco3.EnableMBQP = MFX_CODINGOPTION_ON;
    qsvenc_pelorus_roi_update_mbqp_enabled(&ctx->avctx, &ctx->q);
    CHECK(ctx->q.pelorus_roi_mbqp_enabled);
    CHECK(mbqp_cleared_warnings == 1);

    /* No warning when the user never asked for pelorus_roi. */
    qsvenc_pelorus_roi_update_mbqp_enabled(&ctx->avctx, &q_idle);
    CHECK(!q_idle.pelorus_roi_mbqp_enabled);
    CHECK(mbqp_cleared_warnings == 1);

    ctx->q.param.ExtParam = NULL;
    ctx->q.param.NumExtParam = 0;
    CHECK(qsvenc_pelorus_roi_frame_uses_mbqp(&ctx->avctx, &ctx->q, ctx->frame_a));
    return test_mbqp_version_downgrade(ctx);
}

static int test_mbqp_raster_isolation(RegressionContext *ctx, const mfxExtMBQP *mbqp_a)
{
    mfxExtMBQP *mbqp_b;

    CHECK(mbqp_a->DeltaQP[0] == -5);
    CHECK(mbqp_a->DeltaQP[8] == -5);
    CHECK(mbqp_a->DeltaQP[9] == 0);
    CHECK(mbqp_a->DeltaQP[11] == 0);

    CHECK(qsvenc_setup_roi(&ctx->avctx, &ctx->q, ctx->frame_b, &ctx->ctrl_b) == 0);
    CHECK(ctx->ctrl_b.NumExtParam == 1);
    mbqp_b = (mfxExtMBQP *)ctx->ctrl_b.ExtParam[0];
    CHECK(mbqp_b != mbqp_a);
    CHECK(mbqp_b->DeltaQP != mbqp_a->DeltaQP);
    CHECK(mbqp_a->DeltaQP[0] == -5);
    CHECK(mbqp_b->DeltaQP[0] == 5);
    return 0;
}

static int test_mbqp_raster(RegressionContext *ctx)
{
    mfxExtMBQP *mbqp_a;

    CHECK(qsvenc_pelorus_roi_frame_uses_mbqp(&ctx->avctx, &ctx->q, ctx->frame_a));
    CHECK(qsvenc_setup_roi(&ctx->avctx, &ctx->q, ctx->frame_a, &ctx->ctrl_a) == 0);
    CHECK(ctx->ctrl_a.NumExtParam == 1);
    mbqp_a = (mfxExtMBQP *)ctx->ctrl_a.ExtParam[0];
    CHECK(mbqp_a->Header.BufferId == MFX_EXTBUFF_MBQP);
    CHECK(mbqp_a->Header.BufferSz == sizeof(*mbqp_a));
    CHECK(mbqp_a->Mode == MFX_MBQP_MODE_QP_DELTA);
    CHECK(mbqp_a->BlockSize == 16);
    CHECK(mbqp_a->Pitch == 3);
    CHECK(mbqp_a->NumQPAlloc == 12);
    CHECK(mbqp_a->DeltaQP == (mfxI8 *)(mbqp_a + 1));
    return test_mbqp_raster_isolation(ctx, mbqp_a);
}

static int test_overlap(RegressionContext *ctx)
{
    AVRegionOfInterest *rois;
    mfxExtMBQP *mbqp_overlap;

    ctx->frame_overlap = make_roi_frame(33, 33, 2);
    CHECK(ctx->frame_overlap);
    rois = frame_rois(ctx->frame_overlap);
    set_roi(&rois[0], 0, 0, 16, 16, (AVRational){-1, 10});
    set_roi(&rois[1], 0, 0, 33, 33, (AVRational){1, 10});
    CHECK(qsvenc_setup_roi(&ctx->avctx, &ctx->q, ctx->frame_overlap, &ctx->ctrl_overlap) == 0);
    CHECK(ctx->ctrl_overlap.NumExtParam == 1);
    mbqp_overlap = (mfxExtMBQP *)ctx->ctrl_overlap.ExtParam[0];
    CHECK(mbqp_overlap->DeltaQP[0] == -5);
    CHECK(mbqp_overlap->DeltaQP[1] == 5);
    CHECK(mbqp_overlap->DeltaQP[11] == 0);
    return 0;
}

static int test_extended_roi(RegressionContext *ctx)
{
    AVFrameSideData *sd;
    AVRegionOfInterest extended_roi = {0};

    ctx->frame_extended = make_roi_frame(33, 33, 0);
    CHECK(ctx->frame_extended);
    sd = av_frame_new_side_data(ctx->frame_extended, AV_FRAME_DATA_REGIONS_OF_INTEREST,
                                2 * (sizeof(extended_roi) + 1));
    CHECK(sd);
    extended_roi.self_size = sizeof(extended_roi) + 1;
    set_roi(&extended_roi, 0, 0, 16, 16, (AVRational){-1, 10});
    memcpy(sd->data, &extended_roi, sizeof(extended_roi));
    set_roi(&extended_roi, 16, 0, 33, 16, (AVRational){1, 10});
    memcpy(sd->data + extended_roi.self_size, &extended_roi, sizeof(extended_roi));
    CHECK(qsvenc_setup_roi(&ctx->avctx, &ctx->q, ctx->frame_extended, &ctx->ctrl_extended) == 0);
    CHECK(ctx->ctrl_extended.NumExtParam == 1);
    return 0;
}

static int test_invalid_roi_fields(RegressionContext *ctx)
{
    ctx->frame_bad_den = make_roi_frame(33, 33, 1);
    CHECK(ctx->frame_bad_den);
    set_roi(frame_rois(ctx->frame_bad_den), 0, 0, 16, 16, (AVRational){-1, 0});
    CHECK(qsvenc_setup_roi(&ctx->avctx, &ctx->q, ctx->frame_bad_den, &ctx->ctrl_bad_den) ==
          AVERROR(EINVAL));
    CHECK(ctx->ctrl_bad_den.NumExtParam == 0);

    ctx->frame_bad_dims = make_roi_frame(49, 33, 1);
    CHECK(ctx->frame_bad_dims);
    set_roi(frame_rois(ctx->frame_bad_dims), 0, 0, 49, 33, (AVRational){-1, 10});
    CHECK(qsvenc_setup_roi(&ctx->avctx, &ctx->q, ctx->frame_bad_dims, &ctx->ctrl_bad_dims) ==
          AVERROR(EINVAL));
    CHECK(ctx->ctrl_bad_dims.NumExtParam == 0);

    return 0;
}

static int test_invalid_inputs(RegressionContext *ctx)
{
    AVFrameSideData *sd;
    AVRegionOfInterest *rois;

    ctx->frame_bad_size = make_roi_frame(33, 33, 0);
    CHECK(ctx->frame_bad_size);
    sd = av_frame_new_side_data(ctx->frame_bad_size, AV_FRAME_DATA_REGIONS_OF_INTEREST, 1);
    CHECK(sd);
    CHECK(qsvenc_setup_roi(&ctx->avctx, &ctx->q, ctx->frame_bad_size, &ctx->ctrl_bad_size) ==
          AVERROR(EINVAL));
    CHECK(ctx->ctrl_bad_size.NumExtParam == 0);

    ctx->frame_bad_entry = make_roi_frame(33, 33, 2);
    CHECK(ctx->frame_bad_entry);
    rois = frame_rois(ctx->frame_bad_entry);
    set_roi(&rois[0], 0, 0, 16, 16, (AVRational){-1, 10});
    set_roi(&rois[1], 0, 0, 16, 16, (AVRational){1, 10});
    rois[1].self_size = sizeof(*rois) - 1;
    CHECK(qsvenc_setup_roi(&ctx->avctx, &ctx->q, ctx->frame_bad_entry, &ctx->ctrl_bad_entry) ==
          AVERROR(EINVAL));
    CHECK(ctx->ctrl_bad_entry.NumExtParam == 0);

    return test_invalid_roi_fields(ctx);
}

static int test_interlaced(RegressionContext *ctx)
{
    ctx->frame_interlaced = make_roi_frame(33, 33, 1);
    CHECK(ctx->frame_interlaced);
    set_roi(frame_rois(ctx->frame_interlaced), 0, 0, 33, 33, (AVRational){-1, 10});
    ctx->frame_interlaced->flags |= AV_FRAME_FLAG_INTERLACED;
    CHECK(!qsvenc_pelorus_roi_frame_uses_mbqp(&ctx->avctx, &ctx->q, ctx->frame_interlaced));
    CHECK(qsvenc_setup_roi(&ctx->avctx, &ctx->q, ctx->frame_interlaced, &ctx->ctrl_interlaced) ==
          0);
    CHECK(ctx->ctrl_interlaced.NumExtParam == 0);
    return 0;
}

int main(void)
{
    RegressionContext ctx = {0};
    int failed = 0;

    init_context(&ctx);

    if (test_preconditions(&ctx) || test_fallback_warning(&ctx) || test_mbqp_enable(&ctx) ||
        test_mbqp_raster(&ctx) || test_overlap(&ctx) || test_extended_roi(&ctx) ||
        test_invalid_inputs(&ctx) || test_interlaced(&ctx))
        failed = 1;

    cleanup_context(&ctx);

    if (failed)
        return 1;
    (void)puts("qsv roi regression: ok");
    return 0;
}
