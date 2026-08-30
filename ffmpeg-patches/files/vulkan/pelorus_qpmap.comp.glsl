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

/*
 * Pelorus on-GPU quantization-map rasterizer (ADR-0114 Tier 2).
 *
 * Writes the VK_KHR_video_encode_quantization_map texel image DIRECTLY on the
 * GPU from the coalesced ROI rectangle list, eliminating the host per-texel
 * raster + staging upload of the original v0.1.0 path. The host uploads only
 * the compact rectangle list (tens of structs) into a small SSBO; this compute
 * pass rasterizes it into the map, recorded on the encode command buffer.
 *
 * ONE INVOCATION PER MAP TEXEL: a dispatch of ceil(qpmap_w/16) x
 * ceil(qpmap_h/16) workgroups covers the texel grid (W/texel.w by H/texel.h,
 * commonly 16x16 or 32x32 px per texel, so it is tiny). Each invocation owns
 * one texel, scans the rectangles in REVERSE so the first region wins on
 * overlap (matching the host rasterizer and libx265's reverse iteration), and
 * writes the winning delta-QP.
 *
 * Two driver-probed map kinds, selected by the `emphasis` push constant (the
 * active image is bound by the host; the inactive binding gets a 1x1
 * format-matching placeholder and is never written):
 *   - DELTA map (R8_SINT) under CQP: the signed per-texel dQP, stored verbatim.
 *   - EMPHASIS map (R8_UNORM) under CBR/VBR: a [0,1] value where a request for
 *     *lower* QP (negative dQP, "spend more bits") becomes a *higher* emphasis.
 *
 * Rectangles arrive pre-clamped to texel-block units with the dQP already
 * computed host-side in the SAME qoffset->dQP convention as the Tier-1
 * NVENC/QSV patches (qoffset scaled by the codec QP range, clamped to the
 * advertised dQP window) - so this shader is a pure rasterizer with no codec
 * policy baked in.
 *
 * Single source of truth for the rasterizer. Before FFmpeg 9 this text lived
 * twice: as an inline-GLSL string built at runtime inside vulkan_encode.c, and
 * as a standalone reference .comp. FFmpeg 9 removed the runtime GLSL builder
 * (GLSLC/GLSLF/GLSLD and ff_vk_shader_init), so the shader is compiled to
 * SPIR-V at build time and linked in - which retires that duplication.
 *
 * The binding order below MUST match the FFVulkanDescriptorSetBinding array in
 * pelorus_qpmap_build_shader() exactly; a mismatch is silent corruption, not a
 * compile error.
 */

#pragma shader_stage(compute)

/* Workgroup-size IDs 253/254/255 are reserved by ff_vk_shader_load(); the C
 * side passes { 16, 16, 1 } and dispatches ceil(w/16) x ceil(h/16). */
layout (local_size_x_id = 253, local_size_y_id = 254, local_size_z_id = 255) in;

/* Delta map (signed, R8_SINT) and emphasis map (unorm, R8_UNORM). Only the
 * binding selected by `emphasis` is written; both are declared because a
 * storage-image descriptor must carry a view whose format matches the
 * binding's qualifier. */
layout (set = 0, binding = 0, r8i) uniform writeonly iimage2D deltaImg;
layout (set = 0, binding = 1, r8)  uniform writeonly image2D  emphImg;

/* One ROI rectangle per record, pre-clamped to texel-block units (the
 * half-open box [x0,x1) x [y0,y1) in texels) with the signed dQP precomputed
 * host-side. Mirrors PelorusQpRect in libavcodec/vulkan_encode.h: 8 ints,
 * 32 bytes, std430. */
struct QpRect {
    int x0;
    int y0;
    int x1;
    int y1;
    int delta;   /* signed dQP in [qp_delta_min, qp_delta_max] */
    int _pad0;
    int _pad1;
    int _pad2;
};

layout (set = 0, binding = 2, std430) readonly buffer RoiBuf {
    QpRect rects[];
};

layout (push_constant, std430) uniform Params {
    int width;        /*  0  map width  in texels                            */
    int height;       /*  4  map height in texels                            */
    int nb_rects;     /*  8  number of ROI rectangles in the SSBO            */
    int emphasis;     /* 12  1 = write emphasis (R8_UNORM), 0 = delta (R8_SINT) */
    int qp_delta_min; /* 16  clamp floor (for the emphasis normalization)    */
    int qp_delta_max; /* 20  clamp ceil  (for the emphasis normalization)    */
};

void main()
{
    int x = int(gl_GlobalInvocationID.x);
    int y = int(gl_GlobalInvocationID.y);
    if (x >= width || y >= height)
        return;

    /* Reverse scan: the first ROI in the list wins on overlap. Default 0 (no
     * region => neutral: no dQP / lowest emphasis), matching pass-through. */
    int delta = 0;
    for (int i = nb_rects - 1; i >= 0; i--) {
        if (x >= rects[i].x0 && x < rects[i].x1 &&
            y >= rects[i].y0 && y < rects[i].y1)
            delta = rects[i].delta;
    }

    if (emphasis == 0) {
        imageStore(deltaImg, ivec2(x, y), ivec4(delta, 0, 0, 0));
    } else {
        int range = qp_delta_max - qp_delta_min;
        if (range <= 0)
            range = 1;
        /* lower QP (negative dQP) => more bits => higher emphasis */
        float emph = float(-delta - qp_delta_min) / float(range);
        emph = clamp(emph, 0.0, 1.0);
        imageStore(emphImg, ivec2(x, y), vec4(emph, 0.0, 0.0, 0.0));
    }
}
