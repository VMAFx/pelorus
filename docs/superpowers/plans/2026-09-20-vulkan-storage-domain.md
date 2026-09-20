# Vulkan Storage-Domain Correctness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` or `superpowers:executing-plans` and
> implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make every Pelorus Vulkan arithmetic filter bit-depth independent on
8/10/12-bit inputs and prevent component loss on semi-planar and packed image
views.

**Architecture:** A private FFmpeg header derives the storage-to-sample scale
from `AVPixFmtDescriptor`. Arithmetic shaders enter the true sample domain at
load and pixel transforms return to the storage domain at store. Physical
plane selection remains the AVOption contract; a selected semi-planar chroma
plane runs both U and V components, while scalar filters preserve unrelated
packed components.

**Non-goals:** Do not change public libpelorus ABI, AVOption names/ranges,
release version, encoder ROI patches, borderfix coordinate behavior, or claim
cross-vendor acceptance without executing on those devices.

**Ordering constraint:** ADR-0147 and its evidence land before implementation.
This branch may regenerate against its current n9.0.1 base for self-contained
review, but the final dependency integration must regenerate again from the
reviewed canonical sources at the immutable n9.0.2 commit in ADR-0144.

---

### Task 1: Land the decision and reproduction before code

**Files:**
- Create: `docs/adr/0147-vulkan-sample-domain-and-components.md`
- Create: `docs/research/0147-vulkan-storage-domain.md`
- Create: `docs/superpowers/plans/2026-09-20-vulkan-storage-domain.md`
- Modify: `docs/adr/README.md`

- [x] **Step 1: Reproduce the representation defect**

Record the 8/10/12 analyzer divergence from one normalized source, with the
exact FFmpeg Vulkan view mapping and pixel-descriptor depth/shift evidence.

- [x] **Step 2: Reproduce component loss**

Record distinct U/V before and after denoise for NV12/P010/P012 and an RGBA
texel before and after aa/dehalo/deblock.

- [x] **Step 3: Commit the decision artifacts alone**

```bash
git add docs/adr/0147-vulkan-sample-domain-and-components.md \
  docs/research/0147-vulkan-storage-domain.md \
  docs/superpowers/plans/2026-09-20-vulkan-storage-domain.md \
  docs/adr/README.md
git commit -m "docs(adr): define the Vulkan sample-domain contract"
```

### Task 2: Add failing family-level contract checks

**Files:**
- Create: `scripts/check-vulkan-storage-domain.py`
- Modify: `meson.build`

- [x] **Step 1: Enumerate the contract**

The checker must distinguish read-only arithmetic filters, pixel transforms,
and raw-copy/map exemptions. Require one shared helper include, a host-provided
`sample_scale`, shader-side conversion before arithmetic, inverse conversion
for transforms, and no post-readback MC scale.

- [x] **Step 2: Enforce component rules**

Require denoise/aa/dehalo/deblock to expose semi-planar specialization and a
per-component path, prohibit scalar-splat stores, and require read-modify-write
for scalar transforms. Confirm RED against the carried partial WIP.

### Task 3: Implement one descriptor-derived sample-domain boundary

**Files:**
- Create: `ffmpeg-patches/files/pelorus_vulkan_sample.h`
- Modify: `ffmpeg-patches/generate.sh`
- Modify: arithmetic filter C sources and canonical `.comp.glsl` shaders

- [x] **Step 1: Implement and table-check the scale helper**

Cover 8-bit, planar 10/12/16-bit, P010/P012/P016, invalid descriptors, and a
packed-format fallback. Use checked integer shifts and return 1.0 for shapes
the helper cannot prove.

- [ ] **Step 2: Fix read-only filters first**

Apply scale before any arithmetic in analyze and grain-estimate. Move MC scale
into the shader before SAD/candidate selection and remove host readback
rescaling. Recheck push-constant byte sizes and offsets.

- [ ] **Step 3: Fix pixel transforms**

Convert deband, denoise, aa, dehalo, and deblock loads into the sample domain;
clamp there and inverse-scale at stores. Keep unselected planes raw and
borderfix untouched.

### Task 4: Complete the component contract

**Files:**
- Modify: `vf_pelorus_{aa,dehalo,deblock,denoise}_vulkan.c`
- Modify: matching canonical shaders under `ffmpeg-patches/files/vulkan/`

- [ ] **Step 1: Detect semi-planar U/V layout once**

Derive the flag from `comp[1].plane == comp[2].plane`, pass it as a
specialization constant, and keep constant IDs below the reserved 253–255.

- [ ] **Step 2: Process both chroma components safely**

Parameterize scalar loads by component. All barrier-containing loops must have
uniform, specialization-time bounds. Use the physical-plane lane of the
per-plane parameter vectors for both U and V.

- [ ] **Step 3: Preserve unrelated components**

Use read-modify-write for scalar stores. Do not claim RGB filtering semantics;
prove that G/B/A survive when only the scalar component is defined.

### Task 5: Add executable format evidence and user-facing corrections

**Files:**
- Create: `ffmpeg-patches/test/vulkan-format-matrix.sh`
- Modify: `docs/backends/vulkan.md`
- Modify: relevant `docs/metrics/*.md`
- Modify: `docs/development/bench-results.md`
- Modify: `docs/rebase-notes.md`
- Modify: `ffmpeg-patches/AGENTS.md`
- Create: `changelog.d/fixed/0147-vulkan-storage-domain.md`

- [ ] **Step 1: Automate the GPU matrix**

Test normalized analyzer equivalence, 8/10/12 transform equivalence,
NV12/P010/P012 U/V survival, packed RGBA preservation, direct/tiled denoise,
lookahead/MC paths, selected and unselected planes, and validation layers when
available. A missing Vulkan device may skip in generic CI but must not be
reported as executed evidence.

- [ ] **Step 2: Correct documentation**

Replace the false “UNORM is inherently bit-depth agnostic” claim with the two
domain model, document physical-plane semantics, and preserve historical
benchmark context while marking invalid planar-10/12 conclusions.

### Task 6: Regenerate and verify the patch stack

**Files:**
- Modify: generated filter patches affected by canonical sources
- Modify: matching `.commit-msg-*.txt` where the numeric contract is described

- [ ] **Step 1: Format and compile before generation**

Run clang-format on touched C and compile every canonical shader. Run the
family checker and the Pelorus fast suite.

- [ ] **Step 2: Regenerate, inspect, and replay**

Regenerate from `files/`, verify only expected generated patches change, prove
a second generation is byte-identical, and run the complete 18-patch replay.

- [ ] **Step 3: Execute the hardware gate**

Run the format matrix on the RTX 4090 with validation enabled. Run Arc and
RADV where available; otherwise hand off those rows explicitly instead of
claiming them.

- [ ] **Step 4: Independent review**

Require Vulkan-shader, FFmpeg-patch, C, and documentation reviews. Resolve all
Critical/Important findings and leave a clean committed worktree.
