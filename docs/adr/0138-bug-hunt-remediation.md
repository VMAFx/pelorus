<!-- markdownlint-disable MD013 -->
# ADR-0138: Adversarial bug-hunt remediation — 9 confirmed defects fixed

- **Status**: Accepted
- **Date**: 2026-06-20
- **Deciders**: Lusoris

## Context

A systematic, adversarially-verified bug hunt swept the whole filter family, the
GPU dispatch lifecycle, the libpelorus interop ABI, and the patch stack: five
parallel finders (one per defect class) proposed 15 candidates; each was then
verified against the source by an independent agent that defaulted to
"not a bug". Nine were confirmed (two must-fix, six should-fix, one a duplicate).
Two are correctness defects that fire on **default** configurations.

## Decision

Fix all nine, with a conformance-fixture regression for the two parser hardenings.
Adopt the adversarially-verified hunt as a periodic practice (it complements the
on-device validation release gate of ADR-0129, which catches runtime GPU defects
the C-only patch CI cannot).

### The defects + fixes

| # | Sev | Defect | Fix |
|---|---|---|---|
| 1 | must-fix | `denoise` declares `stat_buffer` at binding 5; the SPIR-V statically references it but it is bound only inside `if(want_meta)` — so on the **default `meta=0`** path a used storage descriptor is left **unbound** (validation error / UB / device-lost on intolerant drivers). No `PARTIALLY_BOUND` escape exists in the FFmpeg helper. | Always allocate the (small) stat buffer and bind binding 5 every dispatch (mirroring the mv/conf 6/7 treatment) + add it as an exec dep on the async path; zero-fill + readback stay gated on `want_meta`. |
| 2 | must-fix | `denoise_dispatch` reads an uninitialized `FFVkExecContext *exec` on an early-`RET` fail path (the `if(exec)` cleanup guard sees garbage). | `FFVkExecContext *exec = NULL;`. |
| 3 | should-fix | `mc` block-SAD reduction OOB-writes `s_part[128]` on GPUs with `subgroupSize < 8` (8×8 subgroups would need up to 1024 slots). | Size `s_part` to the worst case `BLOCK_DIM*BLOCK_DIM` (=1024) in both the inline GLSL and the `.comp` (lockstep). |
| 4 | should-fix | `pel_blob_pack` computes `total_size` (a `uint32` wire field) with 32-bit arithmetic → a crafted oversized section wraps to an undersized allocation → heap overflow. | Accumulate in `uint64_t`, 8-align each section in 64-bit, `return PEL_ERR_RANGE` if `> UINT32_MAX` before allocating. No wire-format change. |
| 5 | should-fix | `denoise` MC consumer computes `cells = grid_cols * grid_rows` (signed `int`) from untrusted side-data → signed overflow → UB / bad size validation. | Compute in `uint64_t`, bound by a new `PEL_DENOISE_MAX_CELLS` cap, validate field sizes in 64-bit before any use. |
| 6 | should-fix | `pel_blob_find_section` returns a castable section pointer without checking the R5 8-byte-alignment invariant on the read side (the packer guarantees it on write). | Reject `(off & 7) != 0` with `PEL_ERR_ABI` before the bounds check. |
| 7 | should-fix | `mc` grows a libc-`calloc`'d Pelorus blob with `av_realloc` → cross-allocator UB (the blob is later `free`'d). | Use libc `realloc`; the error path already preserves the original for `pel_blob_free`. |
| 8 | should-fix | `analyze` `coalesce_roi` documents that it drops the lowest-**score** remainder when the `PEL_MAX_ROI` cap is hit, but actually truncates by **raster position** — so on a busy frame it can drop the strongest banding regions. | Collect all coalesced runs into a scratch buffer, sort by descending banding strength (most-negative qoffset), keep the top `max`. |

## Validation

`meson test --suite=fast` 11/11 (incl. the interop fixture, now with
`test_misaligned_offset` → `PEL_ERR_ABI` and `test_pack_size_overflow` →
`PEL_ERR_RANGE`, and the `pelorus_mc` SPIR-V compile validating the `s_part`
resize). Full n8.1.1 patch-stack apply + `make ffmpeg` clean; GPU smokes on
`vk:0` all pass — notably **denoise at the default `meta=0`** (the must-fix
binding-5 path), plus mc, analyze-roi, denoise meta=1 and denoise mc=1 (the
rewritten 64-bit cells path).

## Consequences

- The interop fixes (#4, #6) are defensive hardenings of the shared, vmafx-vendored
  parser — append-only safe (no struct/offset/wire change); vmafx picks them up on
  its next interop.c sync.
- No behaviour change on valid input; the must-fix descriptor binding makes the
  default denoise path valid Vulkan on strict drivers (it happened to be tolerated
  on the dev-box NVIDIA driver, masking the defect).

## References

- ADR-0129 (on-device validation release gate — the complementary runtime-defect
  gate). The hunt + verification transcripts are this PR's provenance.
