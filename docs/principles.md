<!-- markdownlint-disable MD013 -->
# Pelorus engineering principles

The coding contract for Pelorus. It mirrors the vmafx sibling's
`docs/principles.md` (shared DNA), retargeted from C-via-Netflix to original C.
Deviations require a PR comment justifying the exception; reviewers cite rule
IDs (`MEM30-C`, `EXP34-C`, …) on deviations.

## 1. Coding rules — NASA/JPL "Power of 10" (C, original)

NASA/JPL's *The Power of 10: Rules for Developing Safety-Critical Code*
(Gerard J. Holzmann), applied to C as written.

| # | Rule | Pelorus application |
|---|---|---|
| 1 | Simple control flow; no `goto` (except a single-level cleanup `goto fail`), no recursion | The `goto fail` cleanup idiom is allowed (FFmpeg/CERT-style single-exit); no other `goto`. |
| 2 | Bounded loops — a statically provable upper bound | Every loop over sections/planes/cells has a fixed bound; reject `section_count` etc. against caps before looping. |
| 3 | No dynamic allocation after init | libpelorus allocates once per blob in `pel_blob_pack`; hot per-frame paths reuse buffers. |
| 4 | A function fits a printed page (~75 lines) | `.clang-tidy` `readability-function-size`. |
| 5 | Assertion density — validate arguments at boundaries | Every public function null-checks and range-checks its inputs, returns `pel_result`. |
| 6 | Smallest scope for data | File-static where possible; no globals beyond `const` tables. |
| 7 | Check every return value; check every parameter | Non-void returns checked or `(void)`-cast. **Hard gate.** |
| 8 | Limited preprocessor — simple macros only | No token-pasting control-flow tricks; macros are constants/small helpers. **Hard gate (live in C).** |
| 9 | Restrict pointers — at most one level of dereference per expression; no function pointers in hot paths | Interop parsing uses explicit offsets, not pointer chasing. |
| 10 | Compile clean at max warnings, zero suppressions | `-Wall -Wextra -Werror` + clang-tidy; a `// NOLINT` carries an inline citation. **Hard gate.** |

- **Hard gates** (CI blocks on violation): rules 4, 7, 8, 10.
- **Guidance** (cite rule ID in review): rules 1, 2, 3, 5, 6, 9.

Reference: <https://spinroot.com/gerard/pdf/P10.pdf>

### 1.2 Banned functions

`gets`, `strcpy`, `strcat`, `sprintf`, `strtok`, `atoi`, `atof`, `rand`,
`system`. Use the bounded/checked equivalents (`snprintf`, `strtol`, a seeded
PRNG, etc.). The shader PRNG is an explicit integer hash, never `rand`.

## 2. Secure coding — SEI CERT C

SEI CERT C & C++ are **mandatory**; MISRA C:2012 is **informative**. Cite rule
IDs in review. The high-frequency ones for Pelorus:

- `MEM30-C` / `MEM31-C` — no use-after-free / no leak. `pel_blob_pack` owns its
  allocation; the FFmpeg filter hands it to an `AVBufferRef` with a matching
  free callback (`pel_blob_free`), never `av_free`.
- `EXP34-C` — never dereference null; all public entry points null-check.
- `INT30-C` / `INT32-C` — no unsigned wrap / signed overflow in size math;
  offset/size arithmetic in `pel_blob_find_section` is bounds-checked against
  the buffer length before use.
- `ARR30-C` — no out-of-bounds array access; section offsets validated
  (`off > image_len || sz > image_len - off`) before forming a pointer.
- `ERR33-C` — detect and handle standard-library errors (`calloc` checked).

Reference: <https://wiki.sei.cmu.edu/confluence/display/c/>

## 3. Style

K&R, 4-space indent, 100-column limit, `PointerAlignment: Right`. Enforced by
`.clang-format` (identical to vmafx). Names: `pel_` / `PELORUS_` /
`pelorus_` for the public surface; `PelorusXxx` for public structs.

## 4. Architecture decisions — C4 + ADRs

Non-trivial decisions get an ADR in [adr/](adr/) (Nygard format,
[adr/0000-template.md](adr/0000-template.md)). "Non-trivial" = another engineer
could reasonably have chosen differently. Cite the user request (`req`) or
popup answer in `## References`. Land the ADR **before** the implementing commit.

## 5. Vulkan-usage contract

- **Zero-copy is the point.** Frames stay in `AV_PIX_FMT_VULKAN` from decode
  through every `vf_pelorus_*` stage to the encoder. A drop to system RAM
  between filters defeats the pipeline — never insert an implicit `hwdownload`.
- **One queue family, lazy init.** Filters find a compute queue
  (`ff_vk_qf_find(VK_QUEUE_COMPUTE_BIT)`) and build their pipeline on the first
  frame (formats are only known then); guard with an `initialized` flag.
- **Validation-clean.** Debug builds run the Vulkan validation layers; zero
  unhandled `VK_ERROR_*`. Every shader compiles (glslang) in CI.
- **Push constants mirror the C struct byte-for-byte.** Group `vec4`/`uvec4`
  first, then 64-bit, then scalars (std430). The filter's push struct and its
  emitted GLSL block must match in field order and type.
- **Determinism.** Per-pixel randomness is a hash of `(coord, frame_seed)`, not
  GPU state; the same input + seed produces the same output.

## 6. Tooling

`.clang-format`, `.clang-tidy`, `.editorconfig`, Meson + Ninja, Conventional
Commits, SemVer, Keep-a-Changelog (rendered from `changelog.d/`). Every merged
commit: 0 warnings · clang-tidy clean · `meson test --suite=fast` green · the
deband shader compiles.

## 7. Testing

- Table-/case-driven C tests, no external framework (`exit` non-zero on fail).
- The interop ABI has a **shared conformance fixture**
  (`libpelorus/test/interop_test.c`) that vmafx runs against its vendored copy —
  an N+1-minor blob must stay parseable by an N-minor consumer (R4).
- Shaders are validated by compiling to SPIR-V (glslang) in CI.

## 8. Reference links (machine-resolvable)

- Power of 10: <https://spinroot.com/gerard/pdf/P10.pdf>
- SEI CERT C: <https://wiki.sei.cmu.edu/confluence/display/c/>
- FFmpeg Vulkan filters: `libavfilter/vf_gblur_vulkan.c`, `vulkan_filter.h`
- f3kdb / flash3kyuu_deband: the canonical debanding algorithm
- AV1 film grain: `libavutil/film_grain_params.h`
