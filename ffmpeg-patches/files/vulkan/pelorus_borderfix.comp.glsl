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
 * Pelorus dirty-line / border repair (fillborders=smear) compute shader.
 *
 * Single source of truth for the borderfix algorithm. Before FFmpeg 9 this
 * text lived twice: as an inline-GLSL string built at runtime inside
 * vf_pelorus_borderfix_vulkan.c, and as a standalone reference .comp. FFmpeg 9
 * removed the runtime GLSL builder (GLSLC/GLSLF/GLSLD and ff_vk_shader_init),
 * so the shader is now compiled to SPIR-V at build time and linked in — which
 * retires that duplication and the whole class of lockstep-drift defects.
 *
 * The clean interior is [left, width-1-right] x [top, height-1-bottom]; a
 * pixel inside a dirty band clamps onto the nearest clean edge (smear). The
 * clamp lower bound is capped so a degenerate band wider than the frame still
 * yields a valid coordinate. Band widths are in each plane's own pixels.
 */

#pragma shader_stage(compute)

#extension GL_EXT_shader_image_load_formatted : require
#extension GL_EXT_nonuniform_qualifier : require

/* Workgroup-size IDs 253/254/255 are reserved by ff_vk_shader_load(). */
layout (local_size_x_id = 253, local_size_y_id = 254, local_size_z_id = 255) in;

/* Const-folded by the C side via SPEC_LIST_ADD(). `planes` is the plane count;
 * `plane_mask` is the AVOption `planes` bitmask selecting which get repaired
 * (the rest are copied through). Both were C-unrolled loop bounds before. */
layout (constant_id = 0) const uint planes     = 0;
layout (constant_id = 1) const uint plane_mask = 0xf;

layout (push_constant, std430) uniform pushConstants {
    int left;
    int right;
    int top;
    int bottom;
};

layout (set = 0, binding = 0) uniform readonly  image2D input_images[];
layout (set = 0, binding = 1) uniform writeonly image2D output_images[];

void borderfix(ivec2 pos, int idx) {
    ivec2 sz = imageSize(output_images[idx]);
    int cx = clamp(pos.x, min(left, sz.x - 1), max(sz.x - 1 - right, 0));
    int cy = clamp(pos.y, min(top, sz.y - 1), max(sz.y - 1 - bottom, 0));
    imageStore(output_images[idx], pos,
               imageLoad(input_images[idx], ivec2(cx, cy)));
}

void main()
{
    const ivec2 pos = ivec2(gl_GlobalInvocationID.xy);

    /* Preserves the pre-FFmpeg-9 unrolled semantics exactly: the C generator
     * emitted `if (!IS_WITHIN(pos, size)) return;` per plane, so the first
     * plane that does not contain `pos` ends the invocation. Subsampled chroma
     * is therefore skipped for positions only valid in luma, by design. */
    for (uint i = 0; i < planes; i++) {
        const ivec2 size = imageSize(output_images[i]);
        if (!all(lessThan(pos, size)))
            return;

        if ((plane_mask & (1u << i)) != 0u)
            borderfix(pos, int(i));
        else
            imageStore(output_images[i], pos, imageLoad(input_images[i], pos));
    }
}
