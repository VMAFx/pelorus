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
 * Pelorus smart-deband (f3kdb-style) compute shader.
 *
 * Single source of truth for the deband algorithm. Before FFmpeg 9 this text
 * lived twice: as an inline-GLSL string built at runtime inside
 * vf_pelorus_deband_vulkan.c, and as a standalone reference .comp. FFmpeg 9
 * removed the runtime GLSL builder (GLSLC/GLSLF/GLSLD and ff_vk_shader_init),
 * so the shader is now compiled to SPIR-V at build time and linked in — which
 * retires that duplication and the whole class of lockstep-drift defects.
 */

#pragma shader_stage(compute)

#extension GL_EXT_shader_image_load_formatted : require
#extension GL_EXT_nonuniform_qualifier : require

/* Workgroup-size IDs 253/254/255 are reserved by ff_vk_shader_load(). */
layout (local_size_x_id = 253, local_size_y_id = 254, local_size_z_id = 255) in;

/* Const-folded by the C side via SPEC_LIST_ADD(). `planes` is the plane count;
 * `plane_mask` is the AVOption `planes` bitmask selecting which get debanded
 * (the rest are copied through). Both were C-unrolled loop bounds before. */
layout (constant_id = 0) const uint planes     = 0;
layout (constant_id = 1) const uint plane_mask = 0xf;

layout (push_constant, std430) uniform pushConstants {
    vec4  thr;
    vec4  grain;
    int   range;
    int   sample_mode;
    int   blur_mode;
    int   dither_mode;
    float softness;
    float detail_thr;
    int   flags;
    uint  frame_seed;
};

layout (set = 0, binding = 0) uniform readonly  image2D input_images[];
layout (set = 0, binding = 1) uniform writeonly image2D output_images[];

float frand(vec2 p) {
    return fract(sin(p.x * 12.9898 + p.y * 78.233) * 43758.545);
}
float hash3(ivec2 p, uint s) {
    uint h = uint(p.x) * 73856093u ^ uint(p.y) * 19349663u ^ s * 83492791u;
    h ^= h >> 13; h *= 0x5bd1e995u; h ^= h >> 15;
    return float(h & 0x00FFFFFFu) / float(0x01000000u);
}
float urand(ivec2 p, uint s, int salt) {
    return hash3(p + ivec2(salt * 101, salt * 131), s + uint(salt) * 2654435761u);
}
float tpdf(ivec2 p, uint s, int salt) {
    return urand(p, s, salt) + urand(p, s, salt + 57) - 1.0;
}
float bayer8(ivec2 p) {
    int x = p.x & 7; int y = p.y & 7; int v = 0;
    for (int b = 0; b < 3; b++) {
        v = (v << 2) | (((x >> (2 - b)) & 1) << 1)
                     | (((x >> (2 - b)) & 1) ^ ((y >> (2 - b)) & 1));
    }
    return float(v) / 64.0;
}
vec4 pel_fetch(int idx, ivec2 p, ivec2 sz) {
    return imageLoad(input_images[idx], clamp(p, ivec2(0), sz - ivec2(1)));
}
void deband(const ivec2 pos, const int idx, float thr_p, float grain_p) {
    const float TWO_PI = 6.28318530718;
    const float GOLDEN = 2.39996322973;
    ivec2 sz = imageSize(output_images[idx]);
    vec4 S = imageLoad(input_images[idx], pos);
    bool dynG = (flags & 1) != 0;
    bool protectD = (flags & 2) != 0;
    float r = dynG ? hash3(pos, frame_seed) : frand(vec2(pos));
    float dir = r * TWO_PI;
    if (sample_mode == 4) dir += float(frame_seed) * GOLDEN;
    float dist = r * float(range);
    ivec2 off = ivec2(round(cos(dir) * dist), round(sin(dir) * dist));
    vec4 r0; vec4 r1; vec4 r2; vec4 r3; vec4 avg; int N;
    if (sample_mode == 1) {
        r0 = pel_fetch(idx, pos + ivec2(0, off.y), sz);
        r1 = pel_fetch(idx, pos - ivec2(0, off.y), sz);
        avg = (r0 + r1) * 0.5; N = 2;
    } else if (sample_mode == 3) {
        r0 = pel_fetch(idx, pos + ivec2(off.x, 0), sz);
        r1 = pel_fetch(idx, pos - ivec2(off.x, 0), sz);
        avg = (r0 + r1) * 0.5; N = 2;
    } else {
        r0 = pel_fetch(idx, pos + ivec2( off.x,  off.y), sz);
        r1 = pel_fetch(idx, pos + ivec2( off.x, -off.y), sz);
        r2 = pel_fetch(idx, pos + ivec2(-off.x, -off.y), sz);
        r3 = pel_fetch(idx, pos + ivec2(-off.x,  off.y), sz);
        avg = (r0 + r1 + r2 + r3) * 0.25; N = 4;
    }
    vec4 thr_v = vec4(thr_p);
    vec4 d = abs(S - avg);
    vec4 maxd = (N == 4) ? max(max(abs(S - r0), abs(S - r1)),
                                max(abs(S - r2), abs(S - r3)))
                         : max(abs(S - r0), abs(S - r1));
    vec4 w;
    if (blur_mode == 0 && softness > 0.0) {
        w = smoothstep(vec4(0.0), vec4(1.0),
                       clamp(1.0 - d / max(thr_v, vec4(1e-6)), 0.0, 1.0));
    } else if (blur_mode == 0) {
        w = vec4(lessThan(d, thr_v));
    } else {
        w = vec4(lessThan(maxd, thr_v));
    }
    if (protectD) {
        float cnt = float(N + 1);
        vec4 mean = (S + r0 + r1 + ((N == 4) ? (r2 + r3) : vec4(0.0))) / cnt;
        vec4 acc = (S - mean) * (S - mean)
                 + (r0 - mean) * (r0 - mean) + (r1 - mean) * (r1 - mean);
        if (N == 4) acc += (r2 - mean) * (r2 - mean) + (r3 - mean) * (r3 - mean);
        vec4 activity = sqrt(acc / cnt);
        vec4 protect = smoothstep(vec4(detail_thr), vec4(detail_thr * 2.0), activity);
        w *= (vec4(1.0) - protect);
    }
    vec4 base = mix(S, avg, w);
    if (dither_mode != 0 && grain_p > 0.0) {
        float n = (dither_mode == 1) ? (bayer8(pos) - 0.5) * 2.0
                                     : tpdf(pos, dynG ? frame_seed : 0u, idx);
        base += vec4(n * grain_p) * mix(vec4(0.25), vec4(1.0), w);
    }
    imageStore(output_images[idx], pos, clamp(base, vec4(0.0), vec4(1.0)));
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
            deband(pos, int(i), thr[i], grain[i]);
        else
            imageStore(output_images[i], pos, imageLoad(input_images[i], pos));
    }
}
