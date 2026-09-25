/* Copyright 2026 Lusoris. BSD-2-Clause-Patent. */

/*
 * Direct regression coverage for the Pelorus QSV ROI patch.
 *
 * This file is copied into libavcodec/tests/ by qsv-roi-regression.sh.  It
 * includes qsvenc.c so the test can exercise the static ROI rasterizer without
 * requiring QSV hardware or an initialized oneVPL session.
 */

#include <stdint.h>
#include <stdio.h>

#define ff_qsv_enc_init pelorus_test_ff_qsv_enc_init
#define ff_qsv_encode pelorus_test_ff_qsv_encode
#define ff_qsv_enc_close pelorus_test_ff_qsv_enc_close
#define ff_qsv_enc_hw_configs pelorus_test_ff_qsv_enc_hw_configs
#include "../qsvenc.c"

#define CHECK(condition)                                                                           \
    do {                                                                                           \
        if (!(condition)) {                                                                        \
            fprintf(stderr, "check failed at %s:%d: %s\n", __FILE__, __LINE__, #condition);        \
            failed = 1;                                                                            \
            goto cleanup;                                                                          \
        }                                                                                          \
    } while (0)

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

int main(void)
{
    AVCodecContext avctx = {0};
    QSVEncContext q = {0};
    mfxEncodeCtrl ctrl_a = {0}, ctrl_b = {0}, ctrl_overlap = {0};
    mfxEncodeCtrl ctrl_extended = {0}, ctrl_bad_size = {0}, ctrl_bad_entry = {0};
    mfxEncodeCtrl ctrl_bad_den = {0}, ctrl_bad_dims = {0};
    mfxEncodeCtrl ctrl_interlaced = {0};
    mfxExtBuffer *params_a[QSV_MAX_ENC_EXTPARAM] = {0};
    mfxExtBuffer *params_b[QSV_MAX_ENC_EXTPARAM] = {0};
    mfxExtBuffer *params_overlap[QSV_MAX_ENC_EXTPARAM] = {0};
    mfxExtBuffer *params_extended[QSV_MAX_ENC_EXTPARAM] = {0};
    mfxExtBuffer *params_bad_size[QSV_MAX_ENC_EXTPARAM] = {0};
    mfxExtBuffer *params_bad_entry[QSV_MAX_ENC_EXTPARAM] = {0};
    mfxExtBuffer *params_bad_den[QSV_MAX_ENC_EXTPARAM] = {0};
    mfxExtBuffer *params_bad_dims[QSV_MAX_ENC_EXTPARAM] = {0};
    mfxExtBuffer *params_interlaced[QSV_MAX_ENC_EXTPARAM] = {0};
    mfxExtCodingOption3 external_extco3 = {0};
    mfxExtBuffer *video_params[] = {(mfxExtBuffer *)&external_extco3};
    AVFrame *frame_a = NULL, *frame_b = NULL, *frame_overlap = NULL;
    AVFrame *frame_extended = NULL, *frame_bad_size = NULL;
    AVFrame *frame_bad_entry = NULL;
    AVFrame *frame_bad_den = NULL, *frame_bad_dims = NULL;
    AVFrame *frame_interlaced = NULL;
    AVFrameSideData *sd;
    AVRegionOfInterest *rois;
    AVRegionOfInterest extended_roi = {0};
    mfxExtMBQP *mbqp_a, *mbqp_b, *mbqp_overlap;
    int failed = 0;

    ctrl_a.ExtParam = params_a;
    ctrl_b.ExtParam = params_b;
    ctrl_overlap.ExtParam = params_overlap;
    ctrl_extended.ExtParam = params_extended;
    ctrl_bad_size.ExtParam = params_bad_size;
    ctrl_bad_entry.ExtParam = params_bad_entry;
    ctrl_bad_den.ExtParam = params_bad_den;
    ctrl_bad_dims.ExtParam = params_bad_dims;
    ctrl_interlaced.ExtParam = params_interlaced;

    avctx.codec_id = AV_CODEC_ID_HEVC;
    avctx.width = 33;
    avctx.height = 33;
    avctx.sw_pix_fmt = AV_PIX_FMT_NV12;
    q.pelorus_roi = 1;
    q.param.mfx.FrameInfo.Width = 48;
    q.param.mfx.FrameInfo.Height = 64;
    q.param.mfx.FrameInfo.PicStruct = MFX_PICSTRUCT_PROGRESSIVE;
    q.param.mfx.RateControlMethod = MFX_RATECONTROL_CQP;
    q.ver.Major = 1;
    q.ver.Minor = 28;
    external_extco3.Header.BufferId = MFX_EXTBUFF_CODING_OPTION3;
    external_extco3.Header.BufferSz = sizeof(external_extco3);
    q.param.ExtParam = video_params;
    q.param.NumExtParam = FF_ARRAY_ELEMS(video_params);

    CHECK(qsvenc_pelorus_roi_mbqp_preconditions(&avctx, &q));
    q.ver.Minor = 27;
    CHECK(!qsvenc_pelorus_roi_mbqp_preconditions(&avctx, &q));
    q.ver.Minor = 28;
    avctx.codec_id = AV_CODEC_ID_H264;
    CHECK(!qsvenc_pelorus_roi_mbqp_preconditions(&avctx, &q));
    avctx.codec_id = AV_CODEC_ID_HEVC;
    q.param.mfx.RateControlMethod = MFX_RATECONTROL_VBR;
    CHECK(!qsvenc_pelorus_roi_mbqp_preconditions(&avctx, &q));
    q.param.mfx.RateControlMethod = MFX_RATECONTROL_CQP;
    q.param.mfx.FrameInfo.PicStruct = MFX_PICSTRUCT_FIELD_TFF;
    CHECK(!qsvenc_pelorus_roi_mbqp_preconditions(&avctx, &q));
    q.param.mfx.FrameInfo.PicStruct = MFX_PICSTRUCT_PROGRESSIVE;

    frame_a = make_roi_frame(33, 33, 1);
    frame_b = make_roi_frame(33, 33, 1);
    CHECK(frame_a && frame_b);
    set_roi(frame_rois(frame_a), 0, 0, 33, 33, (AVRational){-1, 10});
    set_roi(frame_rois(frame_b), 0, 0, 33, 33, (AVRational){1, 10});

    /* AVQSVContext buffers replace internal buffers with the same BufferId.
     * An external CodingOption3 therefore controls the final attached value. */
    external_extco3.EnableMBQP = MFX_CODINGOPTION_UNKNOWN;
    qsvenc_pelorus_roi_update_mbqp_enabled(&q);
    q.param.ExtParam = NULL;
    q.param.NumExtParam = 0;
    CHECK(!qsvenc_pelorus_roi_frame_uses_mbqp(&avctx, &q, frame_a));
    CHECK(qsvenc_setup_roi(&avctx, &q, frame_a, &ctrl_a) == 0);
    CHECK(ctrl_a.NumExtParam == 0);
    CHECK(set_roi_encode_ctrl(&avctx, frame_a, &ctrl_a) == 0);
    CHECK(ctrl_a.NumExtParam == 1);
    CHECK(ctrl_a.ExtParam[0]->BufferId == MFX_EXTBUFF_ENCODER_ROI);
    free_encoder_ctrl(&ctrl_a);

    q.param.ExtParam = video_params;
    q.param.NumExtParam = FF_ARRAY_ELEMS(video_params);
    external_extco3.EnableMBQP = MFX_CODINGOPTION_ON;
    qsvenc_pelorus_roi_update_mbqp_enabled(&q);
    q.param.ExtParam = NULL;
    q.param.NumExtParam = 0;
    CHECK(qsvenc_pelorus_roi_frame_uses_mbqp(&avctx, &q, frame_a));
    q.ver.Minor = 27;
    CHECK(!qsvenc_pelorus_roi_frame_uses_mbqp(&avctx, &q, frame_a));
    CHECK(qsvenc_setup_roi(&avctx, &q, frame_a, &ctrl_a) == 0);
    CHECK(ctrl_a.NumExtParam == 0);
    CHECK(set_roi_encode_ctrl(&avctx, frame_a, &ctrl_a) == 0);
    CHECK(ctrl_a.NumExtParam == 1);
    CHECK(ctrl_a.ExtParam[0]->BufferId == MFX_EXTBUFF_ENCODER_ROI);
    free_encoder_ctrl(&ctrl_a);
    q.ver.Minor = 28;
    CHECK(qsvenc_pelorus_roi_frame_uses_mbqp(&avctx, &q, frame_a));
    CHECK(qsvenc_setup_roi(&avctx, &q, frame_a, &ctrl_a) == 0);
    CHECK(ctrl_a.NumExtParam == 1);
    mbqp_a = (mfxExtMBQP *)ctrl_a.ExtParam[0];
    CHECK(mbqp_a->Header.BufferId == MFX_EXTBUFF_MBQP);
    CHECK(mbqp_a->Header.BufferSz == sizeof(*mbqp_a));
    CHECK(mbqp_a->Mode == MFX_MBQP_MODE_QP_DELTA);
    CHECK(mbqp_a->BlockSize == 16);
    CHECK(mbqp_a->Pitch == 3);
    CHECK(mbqp_a->NumQPAlloc == 12);
    CHECK(mbqp_a->DeltaQP == (mfxI8 *)(mbqp_a + 1));
    CHECK(mbqp_a->DeltaQP[0] == -5);
    CHECK(mbqp_a->DeltaQP[8] == -5);
    CHECK(mbqp_a->DeltaQP[9] == 0);
    CHECK(mbqp_a->DeltaQP[11] == 0);

    CHECK(qsvenc_setup_roi(&avctx, &q, frame_b, &ctrl_b) == 0);
    CHECK(ctrl_b.NumExtParam == 1);
    mbqp_b = (mfxExtMBQP *)ctrl_b.ExtParam[0];
    CHECK(mbqp_b != mbqp_a);
    CHECK(mbqp_b->DeltaQP != mbqp_a->DeltaQP);
    CHECK(mbqp_a->DeltaQP[0] == -5);
    CHECK(mbqp_b->DeltaQP[0] == 5);

    frame_overlap = make_roi_frame(33, 33, 2);
    CHECK(frame_overlap);
    rois = frame_rois(frame_overlap);
    set_roi(&rois[0], 0, 0, 16, 16, (AVRational){-1, 10});
    set_roi(&rois[1], 0, 0, 33, 33, (AVRational){1, 10});
    CHECK(qsvenc_setup_roi(&avctx, &q, frame_overlap, &ctrl_overlap) == 0);
    CHECK(ctrl_overlap.NumExtParam == 1);
    mbqp_overlap = (mfxExtMBQP *)ctrl_overlap.ExtParam[0];
    CHECK(mbqp_overlap->DeltaQP[0] == -5);
    CHECK(mbqp_overlap->DeltaQP[1] == 5);
    CHECK(mbqp_overlap->DeltaQP[11] == 0);

    frame_extended = make_roi_frame(33, 33, 0);
    CHECK(frame_extended);
    sd = av_frame_new_side_data(frame_extended, AV_FRAME_DATA_REGIONS_OF_INTEREST,
                                2 * (sizeof(extended_roi) + 1));
    CHECK(sd);
    extended_roi.self_size = sizeof(extended_roi) + 1;
    set_roi(&extended_roi, 0, 0, 16, 16, (AVRational){-1, 10});
    memcpy(sd->data, &extended_roi, sizeof(extended_roi));
    set_roi(&extended_roi, 16, 0, 33, 16, (AVRational){1, 10});
    memcpy(sd->data + extended_roi.self_size, &extended_roi, sizeof(extended_roi));
    CHECK(qsvenc_setup_roi(&avctx, &q, frame_extended, &ctrl_extended) == 0);
    CHECK(ctrl_extended.NumExtParam == 1);

    frame_bad_size = make_roi_frame(33, 33, 0);
    CHECK(frame_bad_size);
    sd = av_frame_new_side_data(frame_bad_size, AV_FRAME_DATA_REGIONS_OF_INTEREST, 1);
    CHECK(sd);
    CHECK(qsvenc_setup_roi(&avctx, &q, frame_bad_size, &ctrl_bad_size) == AVERROR(EINVAL));
    CHECK(ctrl_bad_size.NumExtParam == 0);

    frame_bad_entry = make_roi_frame(33, 33, 2);
    CHECK(frame_bad_entry);
    rois = frame_rois(frame_bad_entry);
    set_roi(&rois[0], 0, 0, 16, 16, (AVRational){-1, 10});
    set_roi(&rois[1], 0, 0, 16, 16, (AVRational){1, 10});
    rois[1].self_size = sizeof(*rois) - 1;
    CHECK(qsvenc_setup_roi(&avctx, &q, frame_bad_entry, &ctrl_bad_entry) == AVERROR(EINVAL));
    CHECK(ctrl_bad_entry.NumExtParam == 0);

    frame_bad_den = make_roi_frame(33, 33, 1);
    CHECK(frame_bad_den);
    set_roi(frame_rois(frame_bad_den), 0, 0, 16, 16, (AVRational){-1, 0});
    CHECK(qsvenc_setup_roi(&avctx, &q, frame_bad_den, &ctrl_bad_den) == AVERROR(EINVAL));
    CHECK(ctrl_bad_den.NumExtParam == 0);

    frame_bad_dims = make_roi_frame(49, 33, 1);
    CHECK(frame_bad_dims);
    set_roi(frame_rois(frame_bad_dims), 0, 0, 49, 33, (AVRational){-1, 10});
    CHECK(qsvenc_setup_roi(&avctx, &q, frame_bad_dims, &ctrl_bad_dims) == AVERROR(EINVAL));
    CHECK(ctrl_bad_dims.NumExtParam == 0);

    frame_interlaced = make_roi_frame(33, 33, 1);
    CHECK(frame_interlaced);
    set_roi(frame_rois(frame_interlaced), 0, 0, 33, 33, (AVRational){-1, 10});
    frame_interlaced->flags |= AV_FRAME_FLAG_INTERLACED;
    CHECK(!qsvenc_pelorus_roi_frame_uses_mbqp(&avctx, &q, frame_interlaced));
    CHECK(qsvenc_setup_roi(&avctx, &q, frame_interlaced, &ctrl_interlaced) == 0);
    CHECK(ctrl_interlaced.NumExtParam == 0);

cleanup:
    free_encoder_ctrl(&ctrl_a);
    free_encoder_ctrl(&ctrl_b);
    free_encoder_ctrl(&ctrl_overlap);
    free_encoder_ctrl(&ctrl_extended);
    free_encoder_ctrl(&ctrl_bad_size);
    free_encoder_ctrl(&ctrl_bad_entry);
    free_encoder_ctrl(&ctrl_bad_den);
    free_encoder_ctrl(&ctrl_bad_dims);
    free_encoder_ctrl(&ctrl_interlaced);
    av_frame_free(&frame_a);
    av_frame_free(&frame_b);
    av_frame_free(&frame_overlap);
    av_frame_free(&frame_extended);
    av_frame_free(&frame_bad_size);
    av_frame_free(&frame_bad_entry);
    av_frame_free(&frame_bad_den);
    av_frame_free(&frame_bad_dims);
    av_frame_free(&frame_interlaced);

    if (failed)
        return 1;
    puts("qsv roi regression: ok");
    return 0;
}
