<!-- markdownlint-disable MD013 -->
# ADR-0240: Run documented zero-copy recipes verbatim as hardware tests

- **Status**: Accepted
- **Date**: 2026-11-02
- **Deciders**: Lusoris
- **Tags**: testing, docs, ffmpeg, zero-copy, gpu

## Context

`docs/usage/ffmpeg.md` documents zero-copy boundaries from `AV_PIX_FMT_VULKAN` into hardware encoders across Vulkan Video (`hevc_vulkan`), NVENC (derived CUDA device and separate device with software decode, 8-bit and 10-bit), and VAAPI/QSV (`tiling=drm`, NV12 and P010, on Intel and AMD).

A gap audit (2026-10-12) identified that while existing GPU smokes run specialized filter graphs (such as `nvenc-cuda-hop-smoke.sh` and `vulkan-drm-map-smoke.sh`), the exact documented recipe command lines had no executable test. The documented commands could drift from working engine semantics without detection.

## Decision

The documented recipe commands become executable tests executed directly against hardware devices:

1. **Document markers**: Recipe code blocks in sections linked by the boundary table carry an HTML comment marker `<!-- recipe-run: <id> -->` (for runnable recipes) or `<!-- recipe-run: none: <reason> -->` (for non-runnable recipes, such as software encoders requiring host round-trip).
2. **Smoke runner (`ffmpeg-patches/test/doc-recipes-smoke.py`)**:
   - Parses `docs/usage/ffmpeg.md` reusing `scripts/check-doc-recipes.py` parsing helpers (HISS-19).
   - Replaces only environment variables and paths (`ffmpeg` -> `FFMPEG_BIN`, `input.mkv` / `input-10bit.mkv` -> generated 120-frame test clips, `out.mkv` -> evidence output path, render nodes and driver variables per hardware variant, and `<filters>` placeholder with filter specifications).
   - Enforces zero `hwdownload` in logs, no auto-inserted scale on hardware frames, presence of claimed log needles from documentation prose, and exact 120-frame output decoding. The scale rule reads each `auto_scale` conversion line (`fmt:<in> -> fmt:<out>`): a conversion between two software formats is the CPU step a software-decode recipe requests with `format=` before `hwupload` and is allowed; one with a hardware format on either side fails, and every auto insert needs its own readable conversion line or fails closed. Each ffmpeg call runs without a shell under a deadline (60 s for probes and the encoder list, 600 s for clip generation, recipe runs and the output decode); a call past it counts as a failure. The runner generates only the input clips the selected recipes use, so an 8-bit recipe still runs on a host that cannot encode the 10-bit clip. The first hardware run showed why: the two software-decode NVENC recipes log `yuv420p -> nv12` and `yuv420p10le -> p010le` before the upload, which a blanket `auto_scale` refusal had reported as a zero-copy break.
   - Picks the Intel and the AMD render node by PCI vendor from `/sys/class/drm/renderD*/device/vendor` (`INTEL_RENDER_NODE` / `AMD_RENDER_NODE` override). Node numbers follow probe order and changed between boots on the reference workstation, so a fixed number would have aimed the iHD driver at the AMD card.
   - Generates the input clips with the first encoder that succeeds, in order libx265, libx264, `hevc_nvenc`, then `hevc_vaapi` on the Intel and the AMD node, so a busy or absent device falls through instead of ending the run.
   - Probes hardware devices before execution; unequipped devices result in per-variant skips; when every variant skips, exits 77 with a `SKIP: no recipe device: ...` line compatible with `named-skip.sh`.
   - `--check-doc`: Static gate ensuring all sections linked by the boundary table have recipe markers before every ffmpeg fence, each SPEC id has exactly one marker, that marker sits in the section the boundary table links for its encoder, and all markers have matching SPEC entries.
   - `--self-test`: In-memory defect injection verifying rejection of non-zero exit codes, `hwdownload` logs, auto scale on hardware frames, auto scale without a conversion line (alone, and next to a parsed software conversion), a hung recipe run, missing needles, frame count mismatches, and document structure defects (duplicate ids, a marker outside its section included). The stub ffmpeg lives in `ffmpeg-patches/test/doc-recipes-stub-ffmpeg.py` and runs through the Python interpreter, so the self-test passes on Windows. Every plant must end with exit code 1; a 77 would mean every variant skipped and the plant never reached its check. The clean stub run includes an allowed CPU conversion and must pass.
3. **Integration**: Registered in `meson.build` fast suite (`doc-recipes-self-test`, `doc-recipes-check-doc`, and skipping `doc-recipes`), added to `build-and-run.sh` with `DOC_RECIPES_SKIP`, and gated in CI and Makefile.

## Alternatives considered

| Option | Result | Why not chosen |
| --- | --- | --- |
| Keep separate smokes only | Smokes test specific filter configurations, but documented command lines remain untested | Documented syntax can drift unseen from underlying engine changes |
| CI GPU runners | Run GPU execution in GitHub Actions hosted runners | No hosted runners provide Intel Arc, AMD Radeon, and NVIDIA RTX GPUs; tests would be skip-only in CI |
| Run documented recipes verbatim on hardware with static doc gate (chosen) | Verifies exact documented syntax against real GPUs when present; static gate ensures complete doc coverage in CI | — |

## Consequences

- First hardware run (2026-11-02, FFmpeg n9.0.2 with the Pelorus stack): all seven runs passed on an RTX 4090 (three NVENC recipes, Vulkan to `hevc_vulkan`), an Arc A380 (VAAPI, QSV) and a Radeon iGPU (VAAPI); results recorded in `docs/usage/ffmpeg.md` under Hardware status.
- Any change to zero-copy boundaries or documented command syntax in `docs/usage/ffmpeg.md` is checked by `--check-doc`.
- Workstation and bench replays run the exact documented recipes against NVIDIA, AMD, and Intel hardware.
- Fast local test suite and CI runners verify static consistency and self-test invariants without requiring a GPU.

## References

- [ADR-0189](0189-smoke-named-skips.md): named-skip convention for smoke tests.
- [ADR-0191](0191-nvenc-hop-derived-vulkan-device.md): NVENC hop with derived Vulkan device.
- [Research 0172](../research/0172-zero-copy-audit.md): zero-copy boundary audit and hop matrix.
- Fleet order `rank-dispatch-20261102-0530-pelorus`, unit `TL1-VMAFx-pelorus-C10-4`, 2026-11-02 (paraphrased): give the zero-copy recipes in `docs/usage/ffmpeg.md` a test that runs them as documented, and land it.
