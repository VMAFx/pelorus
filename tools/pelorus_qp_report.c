/**
 *
 *  Copyright 2026 Lusoris
 *
 *     Licensed under the BSD+Patent License (the "License");
 *     you may not use this file except in compliance with the License.
 *     You may obtain a copy of the License at
 *
 *         https://opensource.org/licenses/BSDplusPatent
 *
 *     Unless required by applicable law or agreed to in writing, software
 *     distributed under the License is distributed on an "AS IS" BASIS,
 *     WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 *     See the License for the specific language governing permissions and
 *     limitations under the License.
 *
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
 * per-frame delta-QP). Exits non-zero on any libpelorus error.
 */

#include "pelorus/interop.h"
#include "pelorus/pelorus.h"

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
    meta.producer_id = PEL_FOURCC('P', 'L', 'R', 'S');

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

/* Parse argv into a CSV path + optional requested QP. Returns 0 on success. */
static int parse_args(int argc, char **argv, const char **csv_path, double *requested_qp)
{
    int i;

    for (i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--requested-qp") == 0 && i + 1 < argc) {
            char *endp = NULL;
            *requested_qp = strtod(argv[i + 1], &endp);
            if (endp == argv[i + 1]) {
                (void)fprintf(stderr, "bad --requested-qp value\n");
                return -1;
            }
            i++;
        } else if (*csv_path == NULL) {
            *csv_path = argv[i];
        }
    }
    if (*csv_path == NULL) {
        (void)fprintf(stderr, "usage: %s <x265.csv> [--requested-qp N]\n", argv[0]);
        return -1;
    }
    return 0;
}

int main(int argc, char **argv)
{
    const char *csv_path = NULL;
    const float *req_ptr = NULL;
    float *req_buf = NULL;
    double requested_qp = -1.0;
    PelorusX265Frame *frames;
    size_t count = 0;
    PelorusQpReportSection qp;
    pel_result rc;

    if (parse_args(argc, argv, &csv_path, &requested_qp) != 0) {
        return EXIT_FAILURE;
    }

    frames = malloc(MAX_FRAMES * sizeof(*frames));
    if (frames == NULL) {
        return fail("alloc frames", PEL_ERR_NOMEM);
    }

    rc = pel_x265_csv_parse(csv_path, frames, MAX_FRAMES, &count);
    if (rc != PEL_OK && rc != PEL_ERR_RANGE) { /* RANGE = truncated, still usable */
        free(frames);
        return fail("parse csv", rc);
    }
    if (count == 0) {
        free(frames);
        (void)fprintf(stderr, "no frame rows parsed from %s\n", csv_path);
        return EXIT_FAILURE;
    }

    if (requested_qp >= 0.0) {
        size_t k;
        req_buf = malloc(count * sizeof(*req_buf));
        if (req_buf == NULL) {
            free(frames);
            return fail("alloc req", PEL_ERR_NOMEM);
        }
        for (k = 0; k < count; k++) {
            req_buf[k] = (float)requested_qp;
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
