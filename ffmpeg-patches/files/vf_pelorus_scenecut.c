/*
 * Copyright 2026 Lusoris
 *
 * This file is part of FFmpeg.
 *
 * FFmpeg is free software; you can redistribute it and/or modify it under
 * the terms of the GNU Lesser General Public License as published by the
 * Free Software Foundation; either version 2.1 of the License, or (at
 * your option) any later version.
 *
 * FFmpeg is distributed in the hope that it will be useful, but WITHOUT
 * ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
 * FITNESS FOR A PARTICULAR PURPOSE. See the GNU Lesser General Public
 * License for more details.
 */

/**
 * @file
 * Pelorus scene-cut -> forced IDR (encoder steering, vendor-neutral).
 *
 * A scene cut that lands mid-GOP wastes bits: the encoder predicts across an
 * unrelated picture, then the next forced/periodic IDR re-sends what it could
 * have keyed on the cut. vf_pelorus_mc already measures the cut (it emits
 * has_scene_cut in PEL_SEC_MOTION). This filter is the cheap consumer: it reads
 * that flag from the Pelorus side data and, on a cut frame, sets
 * frame->pict_type = AV_PICTURE_TYPE_I (+ the key flag) so the downstream
 * encoder opens a fresh GOP exactly at the cut. No GPU work, no pixels touched,
 * codec-agnostic (x264/x265/NVENC/QSV/SVT all honour pict_type==I for a forced
 * keyframe). Run it after hwdownload, just before the encoder; the motion side
 * data rides the frame through av_frame_copy_props.
 *
 * Consumes the standard interop ABI (<pelorus/interop.h>); ADR-0126.
 */

#include "libavutil/frame.h"
#include "libavutil/opt.h"

#include "filters.h"
#include "video.h"

#include <pelorus/interop.h>

typedef struct PelorusScenecutContext {
    const AVClass *class;
    int force_idr; /* set pict_type=I on a Pelorus scene cut (default on)       */
} PelorusScenecutContext;

static int pelorus_scenecut_filter_frame(AVFilterLink *inlink, AVFrame *frame)
{
    AVFilterContext *ctx = inlink->dst;
    PelorusScenecutContext *s = ctx->priv;

    if (s->force_idr) {
        const AVFrameSideData *sd =
            av_frame_get_side_data(frame, AV_FRAME_DATA_SEI_UNREGISTERED);

        if (sd && sd->data && pel_blob_is_present(sd->data, sd->size)) {
            const void *p = NULL;
            size_t got = 0;

            if (pel_blob_find_section(sd->data, sd->size, PEL_SEC_MOTION,
                                      sizeof(PelorusMotionSection), &p, &got) == PEL_OK
                && p != NULL
                && got >= offsetof(PelorusMotionSection, has_scene_cut)
                          + sizeof(uint8_t)) {
                const PelorusMotionSection *mo = p;
                if (mo->has_scene_cut) {
                    frame->pict_type = AV_PICTURE_TYPE_I;
                    frame->flags |= AV_FRAME_FLAG_KEY;
                }
            }
        }
    }

    return ff_filter_frame(ctx->outputs[0], frame);
}

#define OFFSET(x) offsetof(PelorusScenecutContext, x)
#define FLAGS (AV_OPT_FLAG_FILTERING_PARAM | AV_OPT_FLAG_VIDEO_PARAM)
static const AVOption pelorus_scenecut_options[] = {
    { "force_idr", "force a keyframe (pict_type=I) on Pelorus scene-cut frames",
      OFFSET(force_idr), AV_OPT_TYPE_BOOL, { .i64 = 1 }, 0, 1, FLAGS },
    { NULL }
};

AVFILTER_DEFINE_CLASS(pelorus_scenecut);

static const AVFilterPad pelorus_scenecut_inputs[] = {
    {
        .name = "default",
        .type = AVMEDIA_TYPE_VIDEO,
        .filter_frame = &pelorus_scenecut_filter_frame,
    },
};

static const AVFilterPad pelorus_scenecut_outputs[] = {
    {
        .name = "default",
        .type = AVMEDIA_TYPE_VIDEO,
    },
};

const FFFilter ff_vf_pelorus_scenecut = {
    .p.name = "pelorus_scenecut",
    .p.description = NULL_IF_CONFIG_SMALL("Pelorus scene-cut -> forced IDR (reads PEL_SEC_MOTION)"),
    .p.priv_class = &pelorus_scenecut_class,
    .p.flags = AVFILTER_FLAG_METADATA_ONLY,
    .priv_size = sizeof(PelorusScenecutContext),
    FILTER_INPUTS(pelorus_scenecut_inputs),
    FILTER_OUTPUTS(pelorus_scenecut_outputs),
};
