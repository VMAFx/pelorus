/**
 *
 *  Copyright 2026 Lusoris
 *
 * SPDX-License-Identifier: EUPL-1.2
 */

/*
 * pelorus_qp_report — end-to-end demonstrator for the closed-loop QP-feedback
 * reader (ADR-0122). Reads an x265 `--csv --csv-log-level 2` file, folds it
 * into a PEL_SEC_QPREPORT section, packs a real Pelorus side-data blob, parses
 * the section back out of the blob, and prints the honored values. This is the
 * runnable half of the encoder-steering loop on a box without a working HW
 * encoder (the QSV per-block path is HW-blocked; see ADR-0119/0122).
 *
 *   pelorus_qp_report <x265.csv> [--requested-qp N]
 *
 * With --requested-qp N, every frame is treated as having requested a flat QP
 * N and honored_fraction is computed (sign-agreement of requested vs honored
 * per-frame delta-QP). N must satisfy 0.0 <= N <= 69.0 (the x265 QP range);
 * -0 is normalised to 0.00.
 *
 * The report covers up to 4096 frames; if the CSV exceeds 4096 frame rows, a
 * warning is printed to stderr and the first 4096 frames are processed. Exits
 * non-zero on argument errors or any libpelorus failure.
 */

#include "pelorus/interop.h"
#include "pelorus/pelorus.h"
#include "pelorus_tool_common.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_FRAMES 4096u

static int fail(const char *what, pel_result rc)
{
    (void)fprintf(stderr, "pelorus_qp_report: %s: %s\n", what, pel_result_str(rc));
    return EXIT_FAILURE;
}

/* Print the populated section read back out of the blob. */
static void print_report(const PelorusQpReportSection *r, size_t blob_len, size_t count,
                         const float *req_ptr, double requested_qp)
{
    (void)printf("PEL_SEC_QPREPORT (read back from a %zu-byte Pelorus blob)\n", blob_len);
    (void)printf("  source            : x265 --csv (HEVC SW, PEL_QPSRC_NONE=%d)\n", PEL_QPSRC_NONE);
    (void)printf("  frames parsed     : %zu\n", count);
    (void)printf("  avg_qp (bit-wtd)  : %.4f\n", (double)r->avg_qp);
    (void)printf("  total_bits        : %llu\n", (unsigned long long)r->total_bits);
    (void)printf("  psnr_y/u/v (dB)   : %.3f / %.3f / %.3f\n", (double)r->psnr_y, (double)r->psnr_u,
                 (double)r->psnr_v);
    (void)printf("  qp_valid          : %u  (0 => frame-granular, no per-cell map)\n", r->qp_valid);
    if (req_ptr != NULL) {
        (void)printf("  requested QP      : %.2f (flat, every frame)\n", requested_qp);
        (void)printf("  honored_fraction  : %.4f  (sign-agreement requested vs honored "
                     "per-frame delta-QP)\n",
                     (double)r->honored_fraction);
    } else {
        (void)printf("  honored_fraction  : %.4f  (no requested map supplied)\n",
                     (double)r->honored_fraction);
    }
}

/* Pack the folded section into a real Pelorus blob, parse it back, and print it
 * — proving the populated section survives the wire ABI round-trip, not just
 * the in-memory fold. Returns a pel_result. */
static pel_result roundtrip_and_print(const PelorusQpReportSection *qp, size_t count,
                                      const float *req_ptr, double requested_qp)
{
    PelorusSideData meta;
    PelorusPackSection sec;
    uint8_t *blob = NULL;
    size_t blob_len = 0;
    const void *found = NULL;
    size_t found_sz = 0;
    pel_result rc;

    memset(&meta, 0, sizeof(meta));
    meta.plane_layout = PEL_LAYOUT_420;
    meta.bit_depth = 8;
    meta.grid_cols = 1; /* frame-granular: no per-cell map */
    meta.grid_rows = 1;
    /* PEL_FOURCC is the public interop.h macro; its char literals promote to int. */
    meta.producer_id = PEL_FOURCC('P', 'L', 'R', 'S'); // NOLINT(bugprone-signed-bitwise)

    sec.id = PEL_SEC_QPREPORT;
    sec.data = qp;
    sec.size = (uint32_t)sizeof(*qp);

    rc = pel_blob_pack(&meta, &sec, 1, &blob, &blob_len);
    if (rc != PEL_OK) {
        return rc;
    }
    rc = pel_blob_find_section(blob, blob_len, PEL_SEC_QPREPORT, sizeof(PelorusQpReportSection),
                               &found, &found_sz);
    if (rc != PEL_OK) {
        pel_blob_free(blob);
        return rc;
    }
    /* found_sz is min(producer, consumer) (R4); we produced and consume the same
     * header, so it must be the full struct before we dereference it. */
    if (found_sz < sizeof(PelorusQpReportSection)) {
        pel_blob_free(blob);
        return PEL_ERR_TRUNCATED;
    }
    print_report(found, blob_len, count, req_ptr, requested_qp);
    pel_blob_free(blob);
    return PEL_OK;
}

/* Per-frame requested-QP array filled with one value; NULL on allocation failure. */
static float *requested_qp_buffer(double requested_qp, size_t count)
{
    float *buf = malloc(count * sizeof(*buf));
    size_t k;

    if (buf == NULL) {
        return NULL;
    }
    for (k = 0; k < count; k++) {
        buf[k] = (float)requested_qp;
    }
    return buf;
}

/* Parse and validate command-line arguments. Returns 0 on success, or -1 on error. */
static int parse_and_validate_args(int argc, char **argv, const char **csv_path,
                                   double *requested_qp)
{
    const PelorusOpt opts[] = {
        {"--requested-qp", PELORUS_OPT_DOUBLE, requested_qp, 0, NULL, NULL},
    };
    const PelorusPositional pos = {"<x265.csv>", csv_path, 1};

    if (pelorus_tool_parse_args("pelorus_qp_report", argc, argv, opts,
                                sizeof(opts) / sizeof(opts[0]), &pos) != 0) {
        (void)fprintf(stderr, "usage: %s <x265.csv> [--requested-qp N]\n", argv[0]);
        return -1;
    }

    if (!isnan(*requested_qp)) {
        *requested_qp += 0.0;
        if (*requested_qp < 0.0 || *requested_qp > 69.0) {
            (void)fprintf(stderr,
                          "pelorus_qp_report: --requested-qp %g is outside 0..69 "
                          "(the x265 QP range)\n",
                          *requested_qp);
            (void)fprintf(stderr, "usage: %s <x265.csv> [--requested-qp N]\n", argv[0]);
            return -1;
        }
    }
    return 0;
}

/* Allocate and parse frames from CSV. Returns EXIT_SUCCESS or EXIT_FAILURE. */
static int load_frames(const char *csv_path, PelorusX265Frame **out_frames, size_t *out_count)
{
    PelorusX265Frame *frames = malloc(MAX_FRAMES * sizeof(*frames));
    pel_result rc;

    if (frames == NULL) {
        return fail("alloc frames", PEL_ERR_NOMEM);
    }

    rc = pel_x265_csv_parse(csv_path, frames, MAX_FRAMES, out_count);
    if (rc == PEL_ERR_RANGE) {
        /* Truncation is named on stderr (no silent fallback); the report continues,
         * covering the first MAX_FRAMES parsed frames. */
        (void)fprintf(stderr,
                      "pelorus_qp_report: warning: %s has more than %u frame rows; "
                      "the report covers the first %u\n",
                      csv_path, MAX_FRAMES, MAX_FRAMES);
    } else if (rc != PEL_OK) {
        free(frames);
        return fail("parse csv", rc);
    }
    if (*out_count == 0) {
        free(frames);
        (void)fprintf(stderr, "no frame rows parsed from %s\n", csv_path);
        return EXIT_FAILURE;
    }
    *out_frames = frames;
    return EXIT_SUCCESS;
}

/* Run pelorus_qp_report: parse the x265 CSV named in argv, fold it into a QP report, then round-trip and print the report blob.
 * Returns EXIT_SUCCESS on success or EXIT_FAILURE on error. */
static int run(int argc, char **argv)
{
    const char *csv_path = NULL;
    const float *req_ptr = NULL;
    float *req_buf = NULL;
    double requested_qp = NAN;
    PelorusX265Frame *frames = NULL;
    size_t count = 0;
    PelorusQpReportSection qp;
    pel_result rc;

    if (parse_and_validate_args(argc, argv, &csv_path, &requested_qp) != 0) {
        return EXIT_FAILURE;
    }
    if (load_frames(csv_path, &frames, &count) != EXIT_SUCCESS) {
        return EXIT_FAILURE;
    }

    if (!isnan(requested_qp)) {
        req_buf = requested_qp_buffer(requested_qp, count);
        if (req_buf == NULL) {
            free(frames);
            return fail("alloc req", PEL_ERR_NOMEM);
        }
        req_ptr = req_buf;
    }

    rc = pel_qp_report_from_x265_frames(frames, count, req_ptr, &qp);
    if (rc != PEL_OK) {
        free(frames);
        free(req_buf);
        return fail("fold report", rc);
    }

    rc = roundtrip_and_print(&qp, count, req_ptr, requested_qp);
    free(frames);
    free(req_buf);
    if (rc != PEL_OK) {
        return fail("blob round-trip", rc);
    }
    return EXIT_SUCCESS;
}

int main(int argc, char **argv)
{
    return pelorus_tool_main(argc, argv, "pelorus_qp_report: cannot read the command line as UTF-8",
                             run);
}
