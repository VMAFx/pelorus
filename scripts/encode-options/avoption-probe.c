/*
 * Copyright 2026 Lusoris
 * SPDX-License-Identifier: EUPL-1.2
 *
 * avoption-probe.c — print the video encoding AVOptions of AVCodecContext and
 * of each named encoder's private class as TSV, one option per line:
 * owner, name, type, offset, unit, default, min, max, help. Input for
 * gen-avoption-tables.py (ADR-0200). Build against the pinned FFmpeg:
 *
 *   cc -o avoption-probe avoption-probe.c -lavcodec -lavutil
 *   ./avoption-probe libx264 libx265 ... > avoptions.tsv
 */
#include <stdio.h>

#include <libavcodec/avcodec.h>
#include <libavutil/opt.h>

static const char *type_name(enum AVOptionType t)
{
    switch (t) {
    case AV_OPT_TYPE_FLAGS:
        return "flags";
    case AV_OPT_TYPE_INT:
        return "int";
    case AV_OPT_TYPE_INT64:
        return "int64";
    case AV_OPT_TYPE_DOUBLE:
        return "double";
    case AV_OPT_TYPE_FLOAT:
        return "float";
    case AV_OPT_TYPE_STRING:
        return "string";
    case AV_OPT_TYPE_RATIONAL:
        return "rational";
    case AV_OPT_TYPE_BINARY:
        return "binary";
    case AV_OPT_TYPE_DICT:
        return "dict";
    case AV_OPT_TYPE_UINT64:
        return "uint64";
    case AV_OPT_TYPE_CONST:
        return "const";
    case AV_OPT_TYPE_IMAGE_SIZE:
        return "size";
    case AV_OPT_TYPE_PIXEL_FMT:
        return "pixfmt";
    case AV_OPT_TYPE_BOOL:
        return "bool";
    case AV_OPT_TYPE_UINT:
        return "uint";
    case AV_OPT_TYPE_VIDEO_RATE:
        return "vrate";
    case AV_OPT_TYPE_DURATION:
        return "duration";
    default:
        return "other";
    }
}

static void dump(const char *owner, const AVClass *cls)
{
    const AVOption *o = NULL;
    const int want = AV_OPT_FLAG_ENCODING_PARAM | AV_OPT_FLAG_VIDEO_PARAM;

    /* av_opt_next reads an object whose first member is its AVClass pointer */
    while ((o = av_opt_next((const void *)&cls, o))) {
        if (o->type != AV_OPT_TYPE_CONST && (o->flags & want) != want) {
            continue;
        }
        printf("%s\t%s\t%s\t%d\t%s\t", owner, o->name, type_name(o->type), o->offset,
               o->unit ? o->unit : "");
        if (o->type == AV_OPT_TYPE_CONST || o->type == AV_OPT_TYPE_INT ||
            o->type == AV_OPT_TYPE_INT64 || o->type == AV_OPT_TYPE_BOOL ||
            o->type == AV_OPT_TYPE_FLAGS || o->type == AV_OPT_TYPE_UINT) {
            printf("%lld", (long long)o->default_val.i64);
        } else if (o->type == AV_OPT_TYPE_DOUBLE || o->type == AV_OPT_TYPE_FLOAT) {
            printf("%g", o->default_val.dbl);
        } else if (o->type == AV_OPT_TYPE_STRING) {
            printf("%s", o->default_val.str ? o->default_val.str : "");
        }
        printf("\t%g\t%g\t%s\n", o->min, o->max, o->help ? o->help : "");
    }
}

int main(int argc, char **argv)
{
    dump("avctx", avcodec_get_class());
    for (int i = 1; i < argc; i++) {
        const AVCodec *c = avcodec_find_encoder_by_name(argv[i]);
        if (!c) {
            (void)fprintf(stderr, "avoption-probe: no encoder %s\n", argv[i]);
            return 1;
        }
        if (c->priv_class) {
            dump(argv[i], c->priv_class);
        }
    }
    return 0;
}
