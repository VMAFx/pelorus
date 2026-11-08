/**
 *  Copyright 2026 Lusoris
 *
 * SPDX-FileCopyrightText: 2026 Lusoris
 * SPDX-License-Identifier: EUPL-1.2
 */

/**
 * Historical interop side-data blob construction, section packing, and
 * auxiliary map serialization routines.
 */

#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "abi_history.h"
#include "abi_history_values.h"

/**
 * Append auxiliary map data to the blob buffer at an 8-byte aligned offset.
 * Returns 0 on success, 1 on buffer overflow or offset wrap.
 */
static int append_map(uint8_t *blob, size_t cap, uint32_t *end, const void *data, uint32_t size,
                      uint32_t *out_off)
{
    if (*end > UINT32_MAX - 7u) {
        return 1;
    }
    uint32_t off = (*end + 7u) & ~7u;
    if ((size_t)off > UINT32_MAX - size) {
        return 1;
    }
    if ((size_t)PELORUS_SIDEDATA_UUID_LEN + (size_t)off + (size_t)size > cap) {
        return 1;
    }
    memcpy(blob + PELORUS_SIDEDATA_UUID_LEN + off, data, size);
    *end = off + size;
    *out_off = off;
    return 0;
}

/**
 * Patch an existing section within the packed blob with updated header data.
 * Returns 0 on success, 1 on invalid pointer or out-of-bounds offset.
 */
static int patch_section(uint8_t *blob, size_t cap, const void *p, size_t got, const void *data,
                         size_t size)
{
    if (blob == NULL || p == NULL || data == NULL) {
        return 1;
    }
    if ((const uint8_t *)p < blob || (const uint8_t *)p >= blob + cap) {
        return 1;
    }
    if (size > got) {
        return 1;
    }
    size_t off = (size_t)((const uint8_t *)p - blob);
    if (off > cap || size > cap - off) {
        return 1;
    }
    memcpy(blob + off, data, size);
    return 0;
}

/**
 * Initialize base sections and populate PelorusPackSection entries.
 */
static void pack_sections_setup(PelorusPackSection *secs, int *out_nb, PelorusBandingSection *band,
                                PelorusVarianceSection *var, PelorusDenoiseSection *den,
                                PelorusFilmGrainSection *grain, PelorusMotionSection *mo)
{
    int nb = 0;
    abih_fill_banding(band);
    secs[nb].id = PEL_SEC_BANDING;
    secs[nb].data = band;
    secs[nb].size = (uint32_t)sizeof(*band);
    nb++;
    abih_fill_variance(var);
    secs[nb].id = PEL_SEC_VARIANCE;
    secs[nb].data = var;
    secs[nb].size = (uint32_t)sizeof(*var);
    nb++;
    abih_fill_denoise(den);
    secs[nb].id = PEL_SEC_DENOISE;
    secs[nb].data = den;
    secs[nb].size = (uint32_t)sizeof(*den);
    nb++;
    abih_fill_filmgrain(grain);
    secs[nb].id = PEL_SEC_FILMGRAIN;
    secs[nb].data = grain;
    secs[nb].size = (uint32_t)sizeof(*grain);
    nb++;
    abih_fill_motion(mo);
    secs[nb].id = PEL_SEC_MOTION;
    secs[nb].data = mo;
    secs[nb].size = (uint32_t)sizeof(*mo);
    nb++;
    *out_nb = nb;
}

#if PELORUS_ABI_MINOR >= 3
/**
 * Initialize ABI minor 3 sections and append them to pack entries.
 */
static void pack_sections_minor3(PelorusPackSection *secs, int *nb, PelorusQpReportSection *qp,
                                 PelorusMotionConfSection *conf, PelorusComplexitySection *cx)
{
    int i = *nb;
    abih_fill_qpreport(qp);
    secs[i].id = PEL_SEC_QPREPORT;
    secs[i].data = qp;
    secs[i].size = (uint32_t)sizeof(*qp);
    i++;
    abih_fill_motion_conf(conf);
    secs[i].id = PEL_SEC_MOTION_CONF;
    secs[i].data = conf;
    secs[i].size = (uint32_t)sizeof(*conf);
    i++;
    abih_fill_complexity(cx);
    secs[i].id = PEL_SEC_COMPLEXITY;
    secs[i].data = cx;
    secs[i].size = (uint32_t)sizeof(*cx);
    i++;
    *nb = i;
}
#endif

#if PELORUS_ABI_MINOR >= 4
/**
 * Initialize ABI minor 4 sections and append them to pack entries.
 */
static void pack_sections_minor4(PelorusPackSection *secs, int *nb, PelorusEncTelemetrySection *tlm,
                                 PelorusEncodeRecordSection *rec)
{
    int i = *nb;
    abih_fill_enc_telemetry(tlm);
    secs[i].id = PEL_SEC_ENC_TELEMETRY;
    secs[i].data = tlm;
    secs[i].size = (uint32_t)sizeof(*tlm);
    i++;
    abih_fill_encode_record(rec);
    secs[i].id = PEL_SEC_ENCODE_RECORD;
    secs[i].data = rec;
    secs[i].size = (uint32_t)sizeof(*rec);
    i++;
    *nb = i;
}
#endif

#if PELORUS_ABI_MINOR >= 6
/**
 * Initialize ABI minor 6 sections and append them to pack entries.
 */
static void pack_sections_minor6(PelorusPackSection *secs, int *nb, PelorusQualitySection *qual)
{
    int i = *nb;
    abih_fill_quality(qual);
    secs[i].id = PEL_SEC_QUALITY;
    secs[i].data = qual;
    secs[i].size = (uint32_t)sizeof(*qual);
    i++;
    *nb = i;
}
#endif

/**
 * Append auxiliary map for banding section and patch its section header.
 */
static int append_banding_map(uint8_t *blob, size_t cap, size_t packed_len, uint32_t *end,
                              PelorusBandingSection *band)
{
    const void *p = NULL;
    size_t got = 0;

    if (append_map(blob, cap, end, val_banding_cell_data, (uint32_t)sizeof(val_banding_cell_data),
                   &band->cell_data_offset) != 0) {
        return 1;
    }
    if (pel_blob_find_section(blob, packed_len, PEL_SEC_BANDING, sizeof(*band), &p, &got) !=
        PEL_OK) {
        return 1;
    }
    if (patch_section(blob, cap, p, got, band, sizeof(*band)) != 0) {
        return 1;
    }
    return 0;
}

/**
 * Append auxiliary maps for variance section and patch its section header.
 */
static int append_variance_maps(uint8_t *blob, size_t cap, size_t packed_len, uint32_t *end,
                                PelorusVarianceSection *var)
{
    const void *p = NULL;
    size_t got = 0;

    if (append_map(blob, cap, end, val_var_cell_data, (uint32_t)sizeof(val_var_cell_data),
                   &var->var_cell_offset) != 0) {
        return 1;
    }
    if (append_map(blob, cap, end, val_edge_cell_data, (uint32_t)sizeof(val_edge_cell_data),
                   &var->edge_cell_offset) != 0) {
        return 1;
    }
    if (pel_blob_find_section(blob, packed_len, PEL_SEC_VARIANCE, sizeof(*var), &p, &got) !=
        PEL_OK) {
        return 1;
    }
    if (patch_section(blob, cap, p, got, var, sizeof(*var)) != 0) {
        return 1;
    }
    return 0;
}

/**
 * Append auxiliary map for motion section and patch its section header.
 */
static int append_motion_maps(uint8_t *blob, size_t cap, size_t packed_len, uint32_t *end,
                              PelorusMotionSection *mo)
{
    const void *p = NULL;
    size_t got = 0;

    if (append_map(blob, cap, end, val_mv_field_data, (uint32_t)sizeof(val_mv_field_data),
                   &mo->mv_field_offset) != 0) {
        return 1;
    }
    if (pel_blob_find_section(blob, packed_len, PEL_SEC_MOTION, sizeof(*mo), &p, &got) != PEL_OK) {
        return 1;
    }
    if (patch_section(blob, cap, p, got, mo, sizeof(*mo)) != 0) {
        return 1;
    }
    return 0;
}

/**
 * Append auxiliary maps for base sections and patch section headers.
 * Returns 0 on success, 1 when a map exceeds the buffer or a section lookup fails.
 */
static int append_base_maps(uint8_t *blob, size_t cap, size_t packed_len, uint32_t *end,
                            PelorusBandingSection *band, PelorusVarianceSection *var,
                            PelorusMotionSection *mo)
{
    if (append_banding_map(blob, cap, packed_len, end, band) != 0) {
        return 1;
    }
    if (append_variance_maps(blob, cap, packed_len, end, var) != 0) {
        return 1;
    }
    if (append_motion_maps(blob, cap, packed_len, end, mo) != 0) {
        return 1;
    }
    return 0;
}

#if PELORUS_ABI_MINOR >= 3
/**
 * Append auxiliary maps for minor 3 sections and patch section headers.
 * Returns 0 on success, 1 when a map exceeds the buffer or a section lookup fails.
 */
static int append_minor3_maps(uint8_t *blob, size_t cap, size_t packed_len, uint32_t *end,
                              PelorusQpReportSection *qp, PelorusMotionConfSection *conf)
{
    const void *p = NULL;
    size_t got = 0;

    if (append_map(blob, cap, end, val_qp_cell_data, (uint32_t)sizeof(val_qp_cell_data),
                   &qp->qp_cell_offset) != 0) {
        return 1;
    }
    if (pel_blob_find_section(blob, packed_len, PEL_SEC_QPREPORT, sizeof(*qp), &p, &got) !=
        PEL_OK) {
        return 1;
    }
    if (patch_section(blob, cap, p, got, qp, sizeof(*qp)) != 0) {
        return 1;
    }

    if (append_map(blob, cap, end, val_conf_field_data, (uint32_t)sizeof(val_conf_field_data),
                   &conf->conf_field_offset) != 0) {
        return 1;
    }
    if (pel_blob_find_section(blob, packed_len, PEL_SEC_MOTION_CONF, sizeof(*conf), &p, &got) !=
        PEL_OK) {
        return 1;
    }
    if (patch_section(blob, cap, p, got, conf, sizeof(*conf)) != 0) {
        return 1;
    }

    return 0;
}
#endif

#if PELORUS_ABI_MINOR >= 4
/**
 * Append auxiliary maps for minor 4 sections and patch section headers.
 * Returns 0 on success, 1 when a map exceeds the buffer or a section lookup fails.
 */
static int append_minor4_maps(uint8_t *blob, size_t cap, size_t packed_len, uint32_t *end,
                              PelorusEncTelemetrySection *tlm)
{
    const void *p = NULL;
    size_t got = 0;

    if (append_map(blob, cap, end, val_tlm_qp_map_data, (uint32_t)sizeof(val_tlm_qp_map_data),
                   &tlm->qp_map_offset) != 0) {
        return 1;
    }
    if (append_map(blob, cap, end, val_tlm_mode_map_data, (uint32_t)sizeof(val_tlm_mode_map_data),
                   &tlm->mode_map_offset) != 0) {
        return 1;
    }
    if (pel_blob_find_section(blob, packed_len, PEL_SEC_ENC_TELEMETRY, sizeof(*tlm), &p, &got) !=
        PEL_OK) {
        return 1;
    }
    if (patch_section(blob, cap, p, got, tlm, sizeof(*tlm)) != 0) {
        return 1;
    }

    return 0;
}
#endif

typedef struct HistorySections {
    PelorusBandingSection band;
    PelorusVarianceSection var;
    PelorusDenoiseSection den;
    PelorusFilmGrainSection grain;
    PelorusMotionSection mo;
#if PELORUS_ABI_MINOR >= 3
    PelorusQpReportSection qp;
    PelorusMotionConfSection conf;
    PelorusComplexitySection cx;
#endif
#if PELORUS_ABI_MINOR >= 4
    PelorusEncTelemetrySection tlm;
    PelorusEncodeRecordSection rec;
#endif
#if PELORUS_ABI_MINOR >= 6
    PelorusQualitySection qual;
#endif
} HistorySections;

/**
 * Append all auxiliary maps for all enabled sections and update offsets.
 * Returns 0 on success, 1 on failure.
 */
static int append_all_maps(uint8_t *blob_out, size_t cap, size_t packed_len, uint32_t *end,
                           HistorySections *s)
{
    if (append_base_maps(blob_out, cap, packed_len, end, &s->band, &s->var, &s->mo) != 0) {
        return 1;
    }
#if PELORUS_ABI_MINOR >= 3
    if (append_minor3_maps(blob_out, cap, packed_len, end, &s->qp, &s->conf) != 0) {
        return 1;
    }
#endif
#if PELORUS_ABI_MINOR >= 4
    if (append_minor4_maps(blob_out, cap, packed_len, end, &s->tlm) != 0) {
        return 1;
    }
#endif
    return 0;
}

/**
 * Fill all section descriptors and pack entries for all enabled sections.
 */
static void setup_all_sections(PelorusPackSection *secs, int *nb, HistorySections *s)
{
    pack_sections_setup(secs, nb, &s->band, &s->var, &s->den, &s->grain, &s->mo);
#if PELORUS_ABI_MINOR >= 3
    pack_sections_minor3(secs, nb, &s->qp, &s->conf, &s->cx);
#endif
#if PELORUS_ABI_MINOR >= 4
    pack_sections_minor4(secs, nb, &s->tlm, &s->rec);
#endif
#if PELORUS_ABI_MINOR >= 6
    pack_sections_minor6(secs, nb, &s->qual);
#endif
}

/**
 * Initialize top-level PelorusSideData metadata with deterministic values.
 */
static void init_meta(PelorusSideData *meta)
{
    memset(meta, 0, sizeof(*meta));
    meta->frame_pts = VAL_FRAME_PTS;
    meta->plane_layout = (uint8_t)VAL_PLANE_LAYOUT;
    meta->bit_depth = (uint8_t)VAL_BIT_DEPTH;
    meta->grid_cols = (uint16_t)VAL_GRID_COLS;
    meta->grid_rows = (uint16_t)VAL_GRID_ROWS;
    meta->producer_id = VAL_PRODUCER_ID;
}

/**
 * Pack metadata and sections into an allocated blob and copy to destination.
 * Returns PEL_OK on success or an error code on packing/capacity failure.
 */
static pel_result invoke_pack(const PelorusSideData *meta, const PelorusPackSection *secs, int nb,
                              uint8_t *blob_out, size_t cap, size_t *out_packed_len)
{
    uint8_t *packed = NULL;
    size_t packed_len = 0;
    pel_result rc = pel_blob_pack(meta, secs, nb, &packed, &packed_len);
    if (rc != PEL_OK) {
        return rc;
    }
    if (packed_len > cap) {
        pel_blob_free(packed);
        return PEL_ERR_RANGE;
    }
    memset(blob_out, 0, cap);
    memcpy(blob_out, packed, packed_len);
    pel_blob_free(packed);
    *out_packed_len = packed_len;
    return PEL_OK;
}

/**
 * Construct full deterministic history blob with sections and auxiliary maps.
 * Returns PEL_OK on success or an error code on packing or bounds error.
 */
pel_result abih_pack_history_blob(uint8_t *blob_out, size_t cap, size_t *out_len)
{
    PelorusSideData meta;
    PelorusSideData hdr;
    PelorusPackSection secs[16];
    HistorySections s;
    size_t packed_len = 0;
    int nb = 0;
    uint32_t end;
    pel_result rc;

    init_meta(&meta);
    setup_all_sections(secs, &nb, &s);
    rc = invoke_pack(&meta, secs, nb, blob_out, cap, &packed_len);
    if (rc != PEL_OK) {
        return rc;
    }
    end = (uint32_t)(packed_len - PELORUS_SIDEDATA_UUID_LEN);
    if (append_all_maps(blob_out, cap, packed_len, &end, &s) != 0) {
        return PEL_ERR_RANGE;
    }
    if (packed_len < (size_t)PELORUS_SIDEDATA_UUID_LEN + sizeof(hdr)) {
        return PEL_ERR_TRUNCATED;
    }
    memcpy(&hdr, blob_out + PELORUS_SIDEDATA_UUID_LEN, sizeof(hdr));
    hdr.total_size = end;
    memcpy(blob_out + PELORUS_SIDEDATA_UUID_LEN, &hdr, sizeof(hdr));
    *out_len = (size_t)PELORUS_SIDEDATA_UUID_LEN + end;
    return PEL_OK;
}
