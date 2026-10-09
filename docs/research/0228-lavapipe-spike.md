<!-- markdownlint-disable MD013 MD060 -->
# Mesa lavapipe as the hosted-CI Vulkan device for the Pelorus filters

**Date:** 2026-10-09

**Decision:** go (hosted lavapipe lane in `ci.yml`, job `lavapipe`); input to [ADR-0173](../adr/0173-tester-programme.md) and
[#228](https://github.com/VMAFx/pelorus/issues/228)

**Scope:** Mesa 26.2.2 lavapipe (LLVM 21.1.8), Vulkan validation layer 1.4.363 from
the host, FFmpeg `n9.0.2-18-g91f2c4ce94` (pin `946fcce0` plus the 18-patch stack,
used for the baseline and the root cause) and the 21-patch stack of this
repository (used for the go case), libpelorus 0.4.0, x86-64 workstation.

## Verdict: go

All ten filters run on lavapipe with the Khronos validation layer on, and no
validation message is raised. On the 21-patch stack (patch 0021, #255) the go
case measured on 2026-10-09:

| Check | Result |
| --- | --- |
| nine Vulkan filters, 320x180 x5 and 1280x720 x3, `PELORUS_VALIDATE=1` | 18 of 18 runs exit 0, 0 VUIDs, 0 `Validation Error` lines |
| `pelorus_scenecut` | exit 0; metadata-only on software frames, never touches Vulkan, so lavapipe says nothing about it |
| format matrix (`vulkan-format-matrix.sh`, `PELORUS_VALIDATE=1`) | exit 0, 64 `PASS` lines, 0 VUIDs, no allow-list hit, 31 s wall |
| tester report (`pelorus_tester_report.py run`, lavapipe only) | `execution_class` `software_vulkan`, `evidence_claim` `functional`, validates |
| required extension hidden from `vulkaninfo` | guard exits 1 with `required Vulkan device extension missing: VK_EXT_shader_object` |

Before 0021 the same runs reported `09059` and `09064` on every filter, plus
`00344` and `09600` on `pelorus_grain_estimate_vulkan` (the baseline below).
All four came from one FFmpeg barrier, not from Pelorus code (see
[Root cause](#root-cause-of-the-lavapipe-layout-vuids)); 0021 removes it, and
`09059` and `09064` left the shared allow-list with it. The hosted lane is
described under [Cost and a CI lane](#cost-and-a-ci-lane). Its result is
functional evidence on software Vulkan and never GPU evidence.

## Baseline per filter

This is the failing-first baseline, measured on the 18-patch stack before 0021.

Each row ran `testsrc2` through `format=yuv420p,hwupload,<filter>,hwdownload`
on lavapipe only (`VK_DRIVER_FILES` pointing at the lavapipe ICD, so the local
RTX 4090 was not visible). Time is wall clock for the whole `ffmpeg` process,
including start-up, one run each, on a workstation under load.

| Filter | Exit | 320x180 x5, validation off | 1280x720 x3, validation off | VUIDs with `PELORUS_VALIDATE=1` |
| --- | --- | --- | --- | --- |
| `pelorus_deband_vulkan` | 0 | 0.12 s | 0.10 s | 09064, 09059 (320x180); 09064 (720p) |
| `pelorus_analyze_vulkan` | 0 | 0.11 s | 0.10 s | 09064, 09059 |
| `pelorus_denoise_vulkan` | 0 | 0.37 s | 0.38 s | 09064, 09059 (320x180); 09064 (720p) |
| `pelorus_grain_estimate_vulkan` | 0 | 0.08 s | 0.10 s | 09064, 09059, **00344, 09600** |
| `pelorus_mc_vulkan` | 0 | 0.28 s | 0.56 s | 09064, 09059 |
| `pelorus_dehalo_vulkan` | 0 | 0.15 s | 0.17 s | 09064, 09059 (320x180); 09064 (720p) |
| `pelorus_aa_vulkan` | 0 | 0.12 s | 0.35 s | 09064, 09059 (320x180); 09064 (720p) |
| `pelorus_deblock_vulkan` | 0 | 0.15 s | 0.07 s | 09064, 09059 (320x180); 09064 (720p) |
| `pelorus_borderfix_vulkan` | 0 | 0.16 s | 0.07 s | 09064, 09059 (320x180); 09064 (720p) |
| `pelorus_scenecut` | 0 | 0.00 s | 0.01 s | none (software frames, never touches Vulkan) |

VUID short names: `09064` is `VUID-VkCopyImageToMemoryInfo-srcImageLayout-09064`,
`09059` is `VUID-VkCopyMemoryToImageInfo-dstImageLayout-09059`, `00344` is
`VUID-vkCmdDispatch-imageLayout-00344`, `09600` is `VUID-vkCmdDraw-None-09600`.

No shader failed to compile, no filter failed to initialise, and no required
extension was missing. `pelorus_scenecut` is a metadata-only filter on software
frames, so lavapipe says nothing about it.

### Format matrix

`ffmpeg-patches/test/vulkan-format-matrix.sh` (ADR-0147), unmodified, stops at
the first unexpected VUID. It stopped at `denoise-tile0-yuv420p` after 30 passing
rows. A copy that records a VUID failure and continues ran to the end in
1 min 44 s: 43 rows pass outright, and 10 rows (`denoise-tile0` and
`denoise-tile1` for yuv420p, yuv420p10le, p010le, yuv420p12le, p012le) fail only
on `09059`. The pixel comparisons in those 10 rows pass. Over the whole matrix
the layer logged 470 x `09064` (already allow-listed) and 52 x `09059` (not
allow-listed).

## Findings

1. **`09064` and `09059` come from FFmpeg, not from Pelorus.** Both are raised
   inside `vkCopyImageToMemoryEXT` and `vkCopyMemoryToImageEXT`, called from
   `libavutil/hwcontext_vulkan.c:4640` (FFmpeg `hwupload` and `hwdownload`). The
   copy passes `GENERAL`, which is the image's real layout; the layer still
   tracks `TRANSFER_DST_OPTIMAL` because it never applied the transition that
   FFmpeg's first compute barrier recorded. Stock `gblur_vulkan` and
   `scdet_vulkan` show the same pair. The host-copy route is not the trigger:
   Intel ANV also takes it and is clean. See
   [Root cause](#root-cause-of-the-lavapipe-layout-vuids).
2. **`pelorus_grain_estimate_vulkan` adds `00344` and `09600` from the same
   cause.** The filter's barrier sequence matches `pelorus_analyze_vulkan` call
   for call. Only `grain_estimate` reports these two because its shader indexes
   `input_images[0]` with a literal, and the layer checks descriptor layouts
   only for indices it can resolve. `analyze` indexes with a specialization
   constant; with a literal index it reports the same two VUIDs.
3. **Missing-extension behaviour has no filter-side hook; the lane guard owns it.**
   The filters declare no extension requirement of their own; FFmpeg picks
   device extensions and silently drops an unknown one requested with
   `device_extensions=` (checked with `VK_NV_nonexistent_ext`: exit 0, no
   message). In FFmpeg 9 even the extensions it prefers are optional:
   `libavutil/vulkan.c` uses `VK_KHR_push_descriptor` (`:2319`) and
   `VK_EXT_shader_object` (`:2403`) when the device has them and otherwise
   takes another path, and `hwcontext_vulkan.c` copies through
   `VK_EXT_host_image_copy` only when present. There is no
   `VK_EXT_descriptor_buffer` use in n9.0.2. A lane that loses one of them
   would stay green on a path no GPU runs. The shaders need
   `GL_KHR_shader_subgroup_arithmetic` (`pelorus_mc`) and
   `GL_EXT_shader_image_load_formatted` (nine shaders); those are the core
   subgroup operation and the core feature `shaderStorageImageReadWithoutFormat`,
   not extensions. The boundary is therefore enforced where it can name the
   requirement: `ffmpeg-patches/test/vulkan-lavapipe-guard.sh` reads
   `vulkaninfo`, requires the extensions in
   `ffmpeg-patches/test/vulkan-required-extensions.txt`, the feature and the
   subgroup operation, and prints each missing one by name. Proof: the real
   `vulkaninfo` text of lavapipe with the `VK_EXT_shader_object` line removed
   exits 1 with `required Vulkan device extension missing: VK_EXT_shader_object`;
   the unmodified text exits 0; the guard's self-test plants each missing item
   and a CI step proves the self-test fails when any single rule is switched off.
4. **The tester report could not express a lavapipe run (fixed in #253).**
   Before schema 2, `pelorus_tester_report.py run` with lavapipe forced recorded
   the device (`PHYSICAL_DEVICE_TYPE_CPU`, driver `llvmpipe`) and ended with
   verdict `pass`, exit 0, with nothing that said "software Vulkan". Since #253
   the report carries `execution_class` `software_vulkan` and `evidence_claim`
   `functional`, and the validator rejects a GPU claim on it. The stages that
   need a device still report `no_device` on lavapipe, because the stage runners
   pick hardware devices only. The lane therefore runs the filter and matrix
   scripts directly and uses the report as the class guard
   (`ffmpeg-patches/test/vulkan-lavapipe-report.sh`).

## Root cause of the lavapipe layout VUIDs

**Verdict:** FFmpeg defect, made visible by a gap in the validation layer. It is
fixed for this repository's stack by patch 0021 (#255,
[rebase notes](../rebase-notes.md)), which is last in the series and goes on the
first FFmpeg bump that carries an equivalent fix. No Pelorus filter code changed.

### Mechanism

1. lavapipe exposes one queue family. FFmpeg then creates frames with
   `VK_SHARING_MODE_EXCLUSIVE` and tracks each frame's queue family as that
   concrete index, not `VK_QUEUE_FAMILY_IGNORED` (n9.0.2
   `libavutil/hwcontext_vulkan.c:2722`, `:2748`). Devices with more than one
   family get `CONCURRENT` images and `IGNORED`.
2. `ff_vk_frame_barrier()` takes `srcQueueFamilyIndex` from that tracked value
   (`libavutil/vulkan.c:2121`). `hwcontext_vulkan.c` passes the same concrete
   family as the new one, but all 30 calls in `libavfilter/` and `libavcodec/`
   pass `VK_QUEUE_FAMILY_IGNORED`, among them
   `ff_vk_filter_process_simple()` (`libavfilter/vulkan_filter.c:290`, `:297`),
   and so does Pelorus's `record_estimator()`
   (`ffmpeg-patches/files/vf_pelorus_grain_estimate_vulkan.c:508`). The first
   barrier on each new `hwupload` frame is therefore `src=0`, `dst=IGNORED`,
   `TRANSFER_DST_OPTIMAL` to `GENERAL`. FFmpeg master (checked 2026-10-09) has
   the same code at all three places.
3. The Vulkan spec treats unequal queue-family indices as an ownership transfer
   and, for an `EXCLUSIVE` image, requires `dstQueueFamilyIndex` to be a valid,
   external or foreign family (`VUID-VkImageMemoryBarrier2-image-09118`).
   `IGNORED` is none of these, so the barrier is invalid.
4. Validation layer 1.4.363 does not report `09118`: it accepts `IGNORED` as a
   special family (`IsQueueFamilySpecial`, `layers/core_checks/cc_synchronization.cpp`).
   Because `src` equals the command pool's family, it then records the barrier
   as a release and defers the layout change to an acquire that never comes
   (`RecordTransitionImageLayout`, `layers/core_checks/cc_image_layout.cpp`).
   Its tracked layout stays `TRANSFER_DST_OPTIMAL`.
5. Every later use is reported against that stale layout: the dispatch in the
   same command buffer (`00344`), the next submits (`09600`), and the host
   copies, which pass the correct `GENERAL` (`09059`, `09064`). From the second
   frame on, FFmpeg's barrier is `GENERAL` to `GENERAL` with both families
   `IGNORED`; the layer records nothing for it, so the stale state never heals.

The validation message text reads the other way round from what one expects:
in "`srcImageLayout` is currently `TRANSFER_DST_OPTIMAL` but expected to be
`GENERAL`", the first layout is the layer's tracked one and the second is the
value FFmpeg passed (`ValidateHostCopyCurrentLayout`,
`layers/core_checks/cc_image_layout.cpp`).

### Evidence

`testsrc2` 320x180, 5 frames, `format=yuv420p,hwupload,<filter>,hwdownload`,
`VK_INSTANCE_LAYERS=VK_LAYER_KHRONOS_validation`, one ICD per run through
`VK_DRIVER_FILES`. FFmpeg n9.0.2 with the 20-patch stack of `e64962e`. Counts
are messages; 10 is the layer's per-message default cap.

| Device (queue families) | Host copy | `grain_estimate` | `analyze` | stock `gblur_vulkan` |
| --- | --- | --- | --- | --- |
| lavapipe, Mesa 26.2.2 (1) | yes | 00344 x1, 09600 x5, 09059 x10, 09064 x10 | 09059 x10, 09064 x10 | 09059 x3, 09064 x10 |
| lavapipe, FFmpeg barrier changed (1) | yes | none | none | none |
| Intel Arc A380, ANV Mesa 26.2.4 (3) | yes | none | none | none |
| AMD Raphael iGPU, RADV Mesa 26.2.4 (5) | no | none | none | none |
| RTX 4090, NVIDIA 615.71.09 (6) | no | allow-listed only | allow-listed only | allow-listed only |

- "FFmpeg barrier changed" is a scratch build in which `ff_vk_frame_barrier()`
  sets `srcQueueFamilyIndex` to `VK_QUEUE_FAMILY_IGNORED` when the caller passes
  `IGNORED` and the tracked family is a concrete index. That one change clears
  all four VUIDs for `grain_estimate`, `analyze`, `deband`, `gblur_vulkan` and
  `scdet_vulkan` on lavapipe. Reverting it brings all four back.
- Tracing `ff_vk_frame_barrier()` showed identical barriers for
  `grain_estimate` and `analyze`: `src=0 dst=IGNORED` on the first frame, then
  `IGNORED`/`IGNORED`. On ANV, RADV and NVIDIA the first barrier is already
  `IGNORED`/`IGNORED`.
- Rebuilding `analyze` with `input_images[0]` in place of
  `input_images[luma_plane]` makes it report the same `00344` x1 and
  `09600` x5 as `grain_estimate`.
- ANV takes the same host-copy route as lavapipe (`vulkan_transfer_host`) and
  reports nothing, so `VK_EXT_host_image_copy` is not the cause.

### Consequences

- Patch 0021 removes the defect from the stack, so no allow-list entry is
  needed: `09059` and `09064` left `vulkan-vuid-allowlist.txt`, and the lane
  runs with zero tolerated VUIDs. Adding an entry for `00344` or `09600` would
  hide genuine layout defects in Pelorus filters on the GPU box, because the
  allow-list is shared; none was added.
- Upstream fixes, both outside this repository: FFmpeg can emit
  `IGNORED`/`IGNORED` in `ff_vk_frame_barrier()` when the caller passes
  `IGNORED` (what 0021 does), or track `IGNORED` for single-family frames in
  `hwcontext_vulkan.c`. The validation layer can report `09118` for
  `dst=IGNORED` on an `EXCLUSIVE` image. Both belong on
  [#214](https://github.com/VMAFx/pelorus/issues/214).
- A Pelorus-only workaround (passing the frame's tracked family as the new
  family) would clear `grain_estimate`, but frames produced through FFmpeg's
  own helpers would keep the defect, and it would depart from the upstream
  filter idiom.

## Negative design: a lavapipe report cannot claim GPU evidence

Implemented in #253 (report schema 2). `execution_class` is derived by the
producer and re-derived by the validator from `devices[]`: `hardware` when at
least one device is not `PHYSICAL_DEVICE_TYPE_CPU`, `software_vulkan` when every
device is, `no_vulkan` when there is none. `evidence_claim` is `functional` or
`gpu`; the validator rejects `gpu` unless the class is `hardware`, and a
`needs_device` stage that passed on software Vulkan is `pass_software`, never
`pass`. The self-test plants each bad report and fails when its rule is switched
off. The lane adds a second check on the lane's own report
(`vulkan-lavapipe-report.sh`): class `software_vulkan`, claim `functional`, only
CPU devices.

## Cost and a CI lane

Measured on the workstation under load (load average 25 to 27 of 32 cores):

| Step | Wall time | Notes |
| --- | --- | --- |
| libpelorus build, install | under 30 s | included in the line below |
| 21-patch replay, FFmpeg configure, `make -j4 ffmpeg` | about 10 min | `--disable-debug`, no encoder SDKs |
| nine filters x 2 sizes plus scenecut, validation on | 10 s | one `ffmpeg` start each, load average 41 |
| format matrix, validation on | 31 to 40 s | zero VUIDs |

A 4-vCPU hosted runner builds more slowly under similar load. The lane is the
`lavapipe` job in `.github/workflows/ci.yml`. It builds the stack a second time
instead of extending `ffmpeg-stack`, because `build-and-run.sh` deletes its
binary: the new `KEEP_DIR` switch copies the linked `ffmpeg` and
`libpelorus.so*` out after every registration check passed. A separate job keeps
the required `ffmpeg-stack` check unchanged and gives the lane its own 45 minute
budget. Steps, in order:

1. `Stop on a draft pull request`, the Praetor hosted-gate shape (HISS-18);
   every later step carries `github.event.pull_request.draft != true`.
2. Checkout, build configuration, and packages: the `ffmpeg-stack` build set
   without the encoder SDKs, plus `mesa-vulkan-drivers`, `vulkan-tools` and
   `vulkan-validationlayers` (package names unverified on `ubuntu-26.04`).
3. Pinned FFmpeg source, then `build-and-run.sh` with `KEEP_DIR`.
4. The lavapipe ICD is picked from `/usr/share/vulkan/icd.d/lvp_icd*.json`
   (exactly one, else fail) and exported as `VK_DRIVER_FILES`, together with
   `PELORUS_VALIDATE=1`, `FFMPEG_BIN` and `LD_LIBRARY_PATH`.
5. Self-tests of the guard (and a loop proving the guard self-test fails when
   each single rule is switched off), the filter script and the report script.
6. `vulkan-lavapipe-guard.sh`: one device, `PHYSICAL_DEVICE_TYPE_CPU`, driver
   `llvmpipe`, every extension in `vulkan-required-extensions.txt`
   (`VK_EXT_shader_object`, `VK_KHR_push_descriptor`, `VK_EXT_host_image_copy`)
   named when missing, `shaderStorageImageReadWithoutFormat`, subgroup
   arithmetic, and the validation layer.
7. `vulkan-lavapipe-filters.sh`: nine filters at two sizes plus
   `pelorus_scenecut`; any VUID or non-zero exit fails, and the script first
   proves the validation layer was inserted into the device.
8. `vulkan-format-matrix.sh` (exit 77, the no-device skip, fails the step).
9. `vulkan-lavapipe-report.sh`: `software_vulkan` and `functional`, valid.

`scripts/check-build-config.py` pins the job shape (draft stop first, draft
guard on every step, at most 45 minutes, SHA-pinned actions, packages, the five
scripts, no `continue-on-error`, no `PELORUS_VALIDATE=0`) and its self-test
carries a rejected mutation for each rule. `ci.yml` has no path filter (the
Release workflow calls it), so a docs-only pull request runs the lane; adding a
path filter to the whole workflow is a separate decision.

## Open items

1. The first hosted run proves the package names, the ICD path and the 45 minute
   budget on `ubuntu-26.04`; this spike used Mesa 26.2.2 from a Flatpak runtime
   and the host validation layer 1.4.363.
2. The committed ruleset (`.github/rulesets/main.json`) lists the job as a ninth
   required check, because the audit refuses a ruleset that omits a
   pull-request job. It becomes required when the maintainer applies the ruleset
   with `praetorctl sync --remote`; the live classic protection (`core`,
   `ffmpeg-stack`, `docs`) is unchanged.
3. The tester stage runners still report `no_device` on lavapipe; wiring them
   for software Vulkan belongs with [#227](https://github.com/VMAFx/pelorus/issues/227).

## Reproduce

```bash
# lavapipe-only ICD file (path = your libvulkan_lvp.so)
printf '{"ICD":{"api_version":"1.4.354","library_path":"%s"},"file_format_version":"1.0.1"}\n' \
  /path/to/libvulkan_lvp.so > lvp.json
VK_DRIVER_FILES=$PWD/lvp.json vulkaninfo --summary    # expect deviceType CPU, llvmpipe

# patched FFmpeg, binary kept in $PWD/kept (ffmpeg and lib/)
FFMPEG_REPO=/path/to/ffmpeg KEEP_DIR=$PWD/kept ffmpeg-patches/test/build-and-run.sh
export LD_LIBRARY_PATH=$PWD/kept/lib FFMPEG_BIN=$PWD/kept/ffmpeg

# the lane, step by step
VK_DRIVER_FILES=$PWD/lvp.json ffmpeg-patches/test/vulkan-lavapipe-guard.sh
VK_DRIVER_FILES=$PWD/lvp.json ffmpeg-patches/test/vulkan-lavapipe-filters.sh
VK_DRIVER_FILES=$PWD/lvp.json ffmpeg-patches/test/vulkan-lavapipe-report.sh

# matrix and one filter
VK_DRIVER_FILES=$PWD/lvp.json PELORUS_VALIDATE=1 FFMPEG_BIN=/path/to/ffmpeg \
  ffmpeg-patches/test/vulkan-format-matrix.sh
VK_DRIVER_FILES=$PWD/lvp.json VK_INSTANCE_LAYERS=VK_LAYER_KHRONOS_validation \
  ffmpeg -init_hw_device vulkan=v -filter_hw_device v -f lavfi -i testsrc2=size=320x180 \
  -frames:v 5 -vf format=yuv420p,hwupload,pelorus_grain_estimate_vulkan,hwdownload,format=yuv420p -f null -

# root-cause comparison, one ICD per run (evidence table rows 1, 3, 4, 5)
for icd in $PWD/lvp.json /usr/share/vulkan/icd.d/{intel,radeon,nvidia}_icd.json; do
  echo "$icd queue families: $(VK_DRIVER_FILES=$icd vulkaninfo 2>/dev/null | grep -c 'queueProperties\[')"
  for f in pelorus_grain_estimate_vulkan pelorus_analyze_vulkan gblur_vulkan; do
    VK_DRIVER_FILES=$icd VK_INSTANCE_LAYERS=VK_LAYER_KHRONOS_validation \
      ffmpeg -hide_banner -loglevel warning -init_hw_device vulkan=v -filter_hw_device v \
      -f lavfi -i testsrc2=size=320x180 -frames:v 5 \
      -vf "format=yuv420p,hwupload,$f,hwdownload,format=yuv420p" -f null - 2>&1 |
      grep '^Validation Error' | grep -oE 'VUID-[[:alnum:]_.-]+' | sort | uniq -c
  done
done
```
