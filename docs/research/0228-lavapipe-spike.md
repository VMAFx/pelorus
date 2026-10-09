<!-- markdownlint-disable MD013 MD060 -->
# Mesa lavapipe as the hosted-CI Vulkan device for the Pelorus filters

**Date:** 2026-10-09

**Decision:** none yet; input to [ADR-0173](../adr/0173-tester-programme.md) and
[#228](https://github.com/VMAFx/pelorus/issues/228)

**Scope:** Mesa 26.2.2 lavapipe (LLVM 21.1.8), Vulkan validation layer 1.4.363 from
the host, FFmpeg `n9.0.2-18-g91f2c4ce94` (pin `946fcce0` plus the 18-patch stack),
libpelorus 0.3.0, x86-64 workstation.

## Verdict: partial go

The real shaders run on lavapipe. All ten filters finish with exit 0, and the
53-row Vulkan format matrix completes with every output comparison passing.
The "validation clean" bar is not met. lavapipe has a single queue family, and
on such a device FFmpeg records each frame's first image barrier as a
queue-family ownership release that is never acquired. The validation layer
then keeps a stale layout for the image and reports `09059` and `09064` on
every filter, plus `00344` and `09600` on `pelorus_grain_estimate_vulkan`. All
four come from that one FFmpeg barrier, not from Pelorus code (see
[Root cause](#root-cause-of-the-lavapipe-layout-vuids)). Hosted CI can therefore
run lavapipe as a functional gate today with validation off, and as a
validation gate once "Open before a lane" item 1 is settled.

## Baseline per filter

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
3. **Missing-extension behaviour is not testable as written.** The filters declare
   no extension requirement of their own; FFmpeg picks device extensions and
   silently drops an unknown one requested with `device_extensions=` (checked
   with `VK_NV_nonexistent_ext`: exit 0, no message). The shaders need
   `GL_KHR_shader_subgroup_basic/_arithmetic` (`pelorus_mc`) and
   `GL_EXT_shader_image_load_formatted` (nine shaders); a device lacking them
   would fail at pipeline creation with a driver error, not a named
   requirement. Acceptance item "missing required extension fails with its name"
   needs a probe step in the lane (see below), not a change in the filters.
4. **The tester report cannot express a lavapipe run today.** Running
   `tools/tester/pelorus_tester_report.py run` with lavapipe forced records the
   device (`PHYSICAL_DEVICE_TYPE_CPU`, driver `llvmpipe`), marks `format_matrix`,
   `steering_smoke` and `zero_copy_chain` as `no_device`, and ends with verdict
   `pass`, exit 0. Nothing in the report says "software Vulkan", so a consumer
   who skips the device list cannot tell it from a GPU pass, and the
   functional rows that did run on lavapipe are not recorded at all.

## Root cause of the lavapipe layout VUIDs

**Verdict:** FFmpeg defect, made visible by a gap in the validation layer. No
Pelorus change fixes it at the source, and none is made here.

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

- The `09064` allow-list entry (#214) covers this defect on lavapipe. `09059` is
  its upload twin, and `00344` and `09600` are the same defect seen through a
  descriptor.
- `00344` and `09600` are generic layout VUIDs. An allow-list entry for them
  also hides genuine layout defects in Pelorus filters on the GPU box, because
  the allow-list is shared. A lavapipe-only allow-list, or keeping
  `grain_estimate` out of a validating lavapipe lane, avoids that.
- Upstream fixes, both outside this repository: FFmpeg can emit
  `IGNORED`/`IGNORED` in `ff_vk_frame_barrier()` when the caller passes
  `IGNORED` (what the scratch build did), or track `IGNORED` for
  single-family frames in `hwcontext_vulkan.c`. The validation layer can
  report `09118` for `dst=IGNORED` on an `EXCLUSIVE` image. Both belong on
  [#214](https://github.com/VMAFx/pelorus/issues/214).
- A Pelorus-only workaround (passing the frame's tracked family as the new
  family) would clear `grain_estimate`, but frames produced through FFmpeg's
  own helpers would keep the defect, and it would depart from the upstream
  filter idiom.

## Negative design: a lavapipe report cannot claim GPU evidence

Not implemented here; the change touches the schema, the producer, the
validator and the self-test planted cases, and belongs with
[#226](https://github.com/VMAFx/pelorus/issues/226).

- Add required field `execution_class`, derived by the producer and re-derived
  by the validator from `devices[]`: `hardware` when at least one device has a
  `device_type` other than `PHYSICAL_DEVICE_TYPE_CPU`, `software_vulkan` when
  every device is CPU type (lavapipe, SwiftShader), `no_vulkan` when there are
  none. The validator refuses a report whose `execution_class` does not follow
  from its own `devices[]`.
- Add required field `evidence_claim`, one of `functional` or `gpu`. The
  validator rejects `gpu` unless `execution_class` is `hardware`, and rejects any
  `needs_device` stage with status `pass` unless `execution_class` is
  `hardware` or the stage carries a new status value `pass_software` (a
  functional pass on `software_vulkan`, never counted toward a GPU claim).
- Planted bad cases for the self-test, each of which must be rejected, and the
  self-test must fail when its rule is switched off: a lavapipe device list with
  `evidence_claim: gpu`; a lavapipe device list with `execution_class: hardware`;
  a `needs_device` stage `pass` on `software_vulkan`.
- `report.schema.json` stays at version 1 only if both fields are optional with
  a default of `no_vulkan` and `functional`; otherwise bump to 2 before first
  publication, which costs nothing yet because no report is published.

## Cost and a CI lane

Measured on the workstation under load (load average 25 to 27 of 32 cores):

| Step | Wall time | Notes |
| --- | --- | --- |
| libpelorus build, install | under 30 s | included in the line below |
| 18-patch replay, FFmpeg configure, `make -j4 ffmpeg` | 4 min 50 s | `--disable-debug`, no encoder SDKs |
| format matrix, validation on | 1 min 44 s to 2 min 14 s | |
| per-filter smoke, 10 filters | about 3 s | one `ffmpeg` start each |

A 4-vCPU hosted runner builds more slowly than that under similar load, so plan
8 to 12 minutes for build plus replay, plus 2 to 3 minutes for the matrix.
`ci.yml` already has `ffmpeg-stack` (60 minute timeout, `ubuntu-26.04`) that
does the replay and link, but `build-and-run.sh` deletes its scratch directory
and the binary on exit. A lavapipe step therefore needs either a keep-binary
switch in `build-and-run.sh` or a second build, so this is a script change, not
only a workflow change. This spike did not use the script for that reason: it
ran the same steps by hand (meson install, `git am` of `series.txt`, `./configure
--enable-vulkan --disable-doc --disable-debug`, `make ffmpeg`).

Lane needs:

- Packages on top of `ffmpeg-stack`: `mesa-vulkan-drivers`, `vulkan-tools`
  (for `vulkaninfo`), `vulkan-validationlayers`.
- Environment: `VK_DRIVER_FILES` set to the lavapipe ICD JSON
  (`/usr/share/vulkan/icd.d/lvp_icd.json` on Debian-family images; the path is
  not verified on `ubuntu-26.04`), `PELORUS_VALIDATE=1`, `FFMPEG_BIN`.
- A first step that runs `vulkaninfo --summary` and fails unless the device is
  `llvmpipe`, so the lane cannot silently run on another driver, and a second
  step that checks the named device extensions (`VK_EXT_shader_object`,
  `VK_EXT_descriptor_buffer`, `VK_KHR_push_descriptor`) so a missing one is
  reported by name.
- Timeout: 30 minutes if the binary is reused from `ffmpeg-stack`, 45 if it is
  rebuilt.

## Open before a lane

1. Decide how a validating lavapipe lane treats `09059`, `00344` and `09600`,
   all from the FFmpeg barrier described under
   [Root cause](#root-cause-of-the-lavapipe-layout-vuids). Options: add them to
   the allow-list with the `09064` citation, use a lavapipe-only allow-list,
   or run the lane with validation off and keep validation as a GPU-box step.
   The shared allow-list would also hide `00344` and `09600` on the GPU box,
   where they would point at a real Pelorus defect.
2. Settled: `00344` and `09600` on `grain_estimate` are the FFmpeg barrier
   defect, not a Pelorus ordering defect (finding 2).
3. Confirm Mesa and the validation layer versions on `ubuntu-26.04`; this spike
   used Mesa 26.2.2 from a Flatpak runtime, because no lavapipe was installed on
   the workstation and no container image was pulled.
4. Land the report fields above before any lavapipe result is published.

## Reproduce

```bash
# lavapipe-only ICD file (path = your libvulkan_lvp.so)
printf '{"ICD":{"api_version":"1.4.354","library_path":"%s"},"file_format_version":"1.0.1"}\n' \
  /path/to/libvulkan_lvp.so > lvp.json
VK_DRIVER_FILES=$PWD/lvp.json vulkaninfo --summary    # expect deviceType CPU, llvmpipe

# patched FFmpeg, same steps as ffmpeg-patches/test/build-and-run.sh, binary kept
meson setup pb . --prefix=$PWD/prefix --libdir=lib && meson install -C pb
PKG_CONFIG_PATH=$PWD/prefix/lib/pkgconfig  # then git am series.txt, configure, make ffmpeg

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
