/*
 * SPDX-FileCopyrightText: 2026 Lusoris
 * SPDX-License-Identifier: EUPL-1.2
 *
 * Manual recorder, intentionally outside Meson (no libaom build dependency).
 * From the repository root, with libaom 3.15.2 installed:
 * cc -std=c11 -Wall -Wextra -Werror libpelorus/test/fixtures/telemetry/libaom-record.c -o build/libaom-record $(pkg-config --cflags --libs aom)
 * build/libaom-record > libpelorus/test/fixtures/telemetry/libaom-3.15.2-cq32.txt
 *
 * Columns: display_index decode_index flags bytes quantizer_64 overall Y U V.
 * Lag zero, no altref, one thread: require exactly one frame and one PSNR
 * packet per submitted image, independently of their packet order. Query QP
 * after draining packets, before encoding another frame. No stderr scraping.
 */
#include <aom/aom_encoder.h>
#include <aom/aomcx.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define FRAME_COUNT 10u
#define EDGE 64u
#define PACKET_BOUND 8u

static int codec_ok(aom_codec_err_t rc, const char *operation)
{
    if (rc != AOM_CODEC_OK) {
        (void)fprintf(stderr, "%s: %s\n", operation, aom_codec_err_to_string(rc));
        return 0;
    }
    return 1;
}

static void fill_pattern(aom_image_t *image, unsigned frame)
{
    for (unsigned plane = 0u; plane < 3u; plane++) {
        const unsigned edge = plane == 0u ? EDGE : EDGE / 2u;
        for (unsigned y = 0u; y < edge; y++) {
            for (unsigned x = 0u; x < edge; x++) {
                image->planes[plane][y * (unsigned)image->stride[plane] + x] =
                    (unsigned char)((x * 3u + y * 5u + frame * 11u + plane * 37u) & 255u);
            }
        }
    }
}

static int record_packets(aom_codec_ctx_t *codec, unsigned index)
{
    aom_codec_iter_t iter = NULL;
    unsigned frames = 0u;
    unsigned psnrs = 0u;
    uint32_t flags = 0u;
    size_t bytes = 0u;
    double psnr[4] = {0.0};
    int quantizer = -1;
    int drained = 0;

    for (unsigned i = 0u; i < PACKET_BOUND; i++) {
        const aom_codec_cx_pkt_t *pkt = aom_codec_get_cx_data(codec, &iter);
        if (pkt == NULL) {
            drained = 1;
            break;
        }
        if (pkt->kind == AOM_CODEC_CX_FRAME_PKT) {
            if (pkt->data.frame.pts != (aom_codec_pts_t)index) {
                return 0;
            }
            frames++;
            flags = pkt->data.frame.flags;
            bytes = pkt->data.frame.sz;
        } else if (pkt->kind == AOM_CODEC_PSNR_PKT) {
            psnrs++;
            for (unsigned j = 0u; j < 4u; j++) {
                psnr[j] = pkt->data.psnr.psnr[j];
            }
        }
    }
    if (!drained || frames != 1u || psnrs != 1u ||
        !codec_ok(aom_codec_control(codec, AOME_GET_LAST_QUANTIZER_64, &quantizer), "get QP")) {
        return 0;
    }
    return printf("%u %u %" PRIu32 " %zu %d %.17g %.17g %.17g %.17g\n", index, index, flags, bytes,
                  quantizer, psnr[0], psnr[1], psnr[2], psnr[3]) >= 0;
}

static int encode_frames(aom_codec_ctx_t *codec, aom_image_t *image)
{
    if (!codec_ok(aom_codec_control(codec, AOME_SET_CPUUSED, 6), "cpu-used") ||
        !codec_ok(aom_codec_control(codec, AOME_SET_CQ_LEVEL, 32u), "cq-level") ||
        !codec_ok(aom_codec_control(codec, AOME_SET_ENABLEAUTOALTREF, 0u), "altref") ||
        !codec_ok(aom_codec_control(codec, AV1E_SET_AQ_MODE, 0u), "aq-mode")) {
        return 0;
    }
    for (unsigned i = 0u; i < FRAME_COUNT; i++) {
        fill_pattern(image, i);
        if (!codec_ok(aom_codec_encode(codec, image, (aom_codec_pts_t)i, 1u, 0u), "encode") ||
            !record_packets(codec, i)) {
            return 0;
        }
    }
    if (!codec_ok(aom_codec_encode(codec, NULL, 0, 0u, 0u), "flush")) {
        return 0;
    }
    aom_codec_iter_t iter = NULL;
    return aom_codec_get_cx_data(codec, &iter) == NULL; /* lag zero: nothing pending */
}

int main(void)
{
    aom_codec_enc_cfg_t cfg;
    aom_codec_ctx_t codec;
    aom_image_t image;
    int ok;

    if (strcmp(aom_codec_version_str(), "v3.15.2") != 0) {
        (void)fprintf(stderr, "requires v3.15.2, got %s\n", aom_codec_version_str());
        return EXIT_FAILURE;
    }
    if (!codec_ok(aom_codec_enc_config_default(aom_codec_av1_cx(), &cfg, AOM_USAGE_GOOD_QUALITY),
                  "default config")) {
        return EXIT_FAILURE;
    }
    cfg.g_w = EDGE;
    cfg.g_h = EDGE;
    cfg.g_threads = 1u;
    cfg.g_lag_in_frames = 0u;
    cfg.g_timebase.num = 1;
    cfg.g_timebase.den = 25;
    cfg.g_bit_depth = AOM_BITS_8;
    cfg.g_input_bit_depth = 8u;
    cfg.rc_end_usage = AOM_Q;
    cfg.rc_min_quantizer = 32u;
    cfg.rc_max_quantizer = 32u;
    if (aom_img_alloc(&image, AOM_IMG_FMT_I420, EDGE, EDGE, 1u) == NULL) {
        return EXIT_FAILURE;
    }
    if (!codec_ok(aom_codec_enc_init(&codec, aom_codec_av1_cx(), &cfg, AOM_CODEC_USE_PSNR),
                  "encoder init")) {
        aom_img_free(&image);
        return EXIT_FAILURE;
    }
    ok = encode_frames(&codec, &image);
    if (!codec_ok(aom_codec_destroy(&codec), "destroy")) {
        ok = 0;
    }
    aom_img_free(&image);
    return ok && fflush(stdout) == 0 ? EXIT_SUCCESS : EXIT_FAILURE;
}
