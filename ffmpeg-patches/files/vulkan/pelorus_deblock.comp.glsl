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
 * Pelorus re-encode deblock/dering compute shader.
 *
 * At the prior codec's block grid, apply a weak [1 2 1] low-pass *across* each
 * boundary, gated by the cross-boundary step: a small step is a block-edge
 * artefact (smooth it), a large step is real structure (preserve it). Luma only
 * by default; unselected planes pass through.
 *
 * Single source of truth for the algorithm. Before FFmpeg 9 this text lived
 * twice: as an inline-GLSL string built at runtime inside
 * vf_pelorus_deblock_vulkan.c, and as a standalone reference .comp. FFmpeg 9
 * removed the runtime GLSL builder (GLSLC/GLSLF/GLSLD and ff_vk_shader_init),
 * so the shader is now compiled to SPIR-V at build time and linked in — which
 * retires that duplication and the whole class of lockstep-drift defects.
 *
 * Storage-image loads are converted from FF_VK_REP_FLOAT's container UNORM
 * units into the logical sample domain before the edge gate or filter runs.
 * Results are converted back only at the imageStore boundary.
 */

#pragma shader_stage(compute)

#extension GL_EXT_shader_image_load_formatted : require
#extension GL_EXT_nonuniform_qualifier : require

/* Workgroup-size IDs 253/254/255 are reserved by ff_vk_shader_load(). */
layout (local_size_x_id = 253, local_size_y_id = 254, local_size_z_id = 255) in;

/* Const-folded by the C side via SPEC_LIST_ADD(). `planes` is the plane count;
 * `plane_mask` is the AVOption `planes` bitmask selecting which get deblocked
 * (the rest are copied through). Both were C-unrolled loop bounds before. */
layout (constant_id = 0) const uint planes     = 0;
layout (constant_id = 1) const uint plane_mask = 0x1;
layout (constant_id = 2) const uint semi_planar = 0;
layout (constant_id = 3) const uint sample_code_max = 0;

layout (push_constant, std430) uniform pushConstants {
    int   bsize;
    int   edge;
    float thr;
    float str;
    float sample_scale;
};

layout (set = 0, binding = 0) uniform readonly  image2D input_images[];
layout (set = 0, binding = 1) uniform writeonly image2D output_images[];

float pel_to_sample(float value) {
    return value * sample_scale;
}
float pel_to_storage(float value) {
    if (sample_code_max == 0u)
        return value / sample_scale;
    const float code_max = float(sample_code_max);
    return round(clamp(value, 0.0, 1.0) * code_max) / code_max / sample_scale;
}
uint pel_component_count(uint plane) {
    return (semi_planar != 0u && plane == 1u) ? 2u : 1u;
}
float pel_component(int idx, int comp, ivec2 p, ivec2 sz) {
    return pel_to_sample(
        imageLoad(input_images[idx], clamp(p, ivec2(0), sz - ivec2(1)))[comp]);
}

float deblock(ivec2 pos, int idx, int comp) {
    ivec2 sz = imageSize(output_images[idx]);
    int bs = max(bsize, 2);
    float c = pel_component(idx, comp, pos, sz);
    float result = c;
    int dx = min(pos.x % bs, bs - (pos.x % bs));
    int dy = min(pos.y % bs, bs - (pos.y % bs));
    if (dx <= edge) {
        float l = pel_component(idx, comp, pos + ivec2(-1, 0), sz);
        float r = pel_component(idx, comp, pos + ivec2( 1, 0), sz);
        if (abs(l - r) < thr) {
            float lp = (l + 2.0 * result + r) * 0.25;
            result = mix(result, lp, str);
        }
    }
    if (dy <= edge) {
        float u = pel_component(idx, comp, pos + ivec2(0, -1), sz);
        float d = pel_component(idx, comp, pos + ivec2(0,  1), sz);
        if (abs(u - d) < thr) {
            float lp = (u + 2.0 * result + d) * 0.25;
            result = mix(result, lp, str);
        }
    }
    return clamp(result, 0.0, 1.0);
}

void main()
{
    const ivec2 pos = ivec2(gl_GlobalInvocationID.xy);

    /* Preserves the pre-FFmpeg-9 unrolled semantics exactly: the C generator
     * emitted `size = imageSize(output_images[i]); if (!IS_WITHIN(pos, size))
     * return;` per plane, so the first plane that does not contain `pos` ends
     * the invocation. Subsampled chroma is therefore skipped for positions only
     * valid in luma, by design. */
    for (uint i = 0; i < planes; i++) {
        const ivec2 size = imageSize(output_images[i]);
        if (!all(lessThan(pos, size)))
            return;

        if ((plane_mask & (1u << i)) != 0u) {
            vec4 texel = imageLoad(input_images[i], pos);
            const uint ncomp = pel_component_count(i);
            for (uint c = 0u; c < ncomp; c++) {
                const int comp = int(c);
                texel[comp] = pel_to_storage(deblock(pos, int(i), comp));
            }
            imageStore(output_images[i], pos, texel);
        } else {
            imageStore(output_images[i], pos, imageLoad(input_images[i], pos));
        }
    }
}
