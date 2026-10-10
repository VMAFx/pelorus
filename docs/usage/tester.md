<!-- markdownlint-disable MD013 -->
# Tester kit stages and their pass rules

The tester report program runs eight stages in a fixed order and records each as
`pass`, `pass_software`, `fail`, `not_run`, `no_device` or `incomplete`, with a
reason for everything except `pass`. How to run the kit and read the report:
[tester kit](../development/tester.md). Rules and licence terms:
[ADR-0173](../adr/0173-tester-programme.md). Stage runners:
[`tools/tester/pelorus_tester_stages.py`](../../tools/tester/pelorus_tester_stages.py).

A stage never substitutes another path for a missing one. A missing device,
encoder or validation layer is `not_run` or `no_device` with the reason named;
it is never reported as `pass`.

## Tester images

There is one image per kit, each with a `-source` companion that holds the
corresponding source ([ADR-0180](../adr/0180-tester-vendor-images.md)). The
report goes to the directory you mount on `/report`.

| Image tag | Kit | Encoders inside | Give the container |
| --- | --- | --- | --- |
| `ghcr.io/vmafx/pelorus:tester-<date>-<sha8>` | `generic` | Vulkan Video only | nothing: no GPU driver, every GPU stage is `no_device` |
| `ghcr.io/vmafx/pelorus:tester-nvidia-<date>-<sha8>` | `nvidia` | NVENC (`h264_nvenc`, `hevc_nvenc`, `av1_nvenc`), Vulkan Video | `--gpus all` |
| `ghcr.io/vmafx/pelorus:tester-intel-<date>-<sha8>` | `intel` | QSV (`h264_qsv`, `hevc_qsv`, `av1_qsv`), Vulkan Video | `--device /dev/dri` |

Without its device, a vendor image still exits 0 with a valid report: the GPU
stages are `no_device` and the reason names the option to add. The images set
`HOME=/tmp`, so a run with `--user` works and the fixture cache lives in
`/tmp/.cache/pelorus-tester` inside the container, gone with it.

### NVIDIA

```bash
mkdir -p report
docker run --rm --gpus all -v "$PWD/report:/report" ghcr.io/vmafx/pelorus:tester-nvidia-<date>-<sha8>
```

- Needs the NVIDIA driver 530.41.03 or newer on the host (the floor of the
  `nv-codec-headers` `n12.1.14.0` the image is built with) and the
  [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/).
  The image contains no NVIDIA file: the toolkit mounts `libcuda`,
  `libnvidia-encode` and the Vulkan ICD from the host.
- The image carries Debian's `libegl1` and `libxext6`: the host's Vulkan ICD
  needs both and does not load without them.
- The image sets `NVIDIA_DRIVER_CAPABILITIES=compute,utility,video,graphics`.
  `graphics` is what makes a toolkit without a CDI specification mount the
  Vulkan ICD; with a CDI specification (toolkit 1.17 and later, the default
  with recent Docker) the ICD is mounted either way.
- A report whose reason says the NVIDIA device nodes are present but no Vulkan
  driver was mounted means the toolkit gave the GPU without its graphics
  libraries; update the toolkit or regenerate its CDI specification
  (`nvidia-ctk cdi generate`). If the driver is mounted but fails to load, the
  probe is a `fail` with the loader's message, not `no_device`.
- AV1 NVENC needs an Ada (RTX 40) or newer GPU. On an older GPU `av1_nvenc`
  fails its first encode and its steering leg is `not_run` with the reason
  `no AV1 encode on this GPU or driver` and NVENC's own message.
- WSL2 with Docker Desktop: the image adds `/usr/lib/wsl/lib` to the library
  path, where Docker Desktop mounts the Windows driver's libraries. This path is
  not yet verified on WSL2 ([research 0229](../research/0229-tester-vendor-images.md)).

### Intel

```bash
mkdir -p report
docker run --rm --device /dev/dri -v "$PWD/report:/report" ghcr.io/vmafx/pelorus:tester-intel-<date>-<sha8>
```

- The image carries Mesa's Vulkan drivers (ANV for Intel), the oneVPL
  dispatcher and GPU runtime (`libvpl2`, `libmfx-gen1.2`) from Debian `main`
  and Debian's `intel-media-va-driver-non-free`. That package is Expat-licensed
  and redistributable; Debian lists it under `non-free` only because Intel's
  GPU kernels come without source. It is the only package the licence gate
  admits from outside `main`: the gate records it with that reason and fails
  the build on any other `non-free` or `contrib` package
  ([ADR-0180](../adr/0180-tester-vendor-images.md)).
- `--device /dev/dri` passes every GPU of the host. To test one GPU on a
  machine with several, pass only its render node, found under
  `/dev/dri/by-path/` (for example `--device /dev/dri/renderD129`).
- Running as another user (`--user "$(id -u):$(id -g)"`) also needs the group
  of the render node: `--group-add "$(stat -c %g /dev/dri/renderD128)"`. The
  report names that option when the render nodes are present but not readable.
- QSV finds the Intel GPU by vendor id among the render nodes (the image's
  FFmpeg is built with libdrm for that), so another vendor's render node first
  in the list does not matter.
- On an Arc A380 bound to the `xe` kernel driver, which loads no HuC
  firmware, only the non-free media driver lets `hevc_qsv` encode (the free
  `intel-media-va-driver` fails with `Invalid FrameType:0`). If the stage finds
  only the free driver installed (a run from a checkout on a host that has it,
  or an image built by hand), the `hevc_qsv` steering and side-data legs are
  `not_run` and the reason names the installed free driver and the missing
  `intel-media-va-driver-non-free`; the stage reads both from dpkg
  ([research 0229](../research/0229-tester-vendor-images.md)).
- Without HuC the GPU's own bitrate control (CBR, VBR, ICQ) fails on DG2
  (Arc A-series) with every media driver. The steering and side-data legs use
  constant QP. The [bitrate-control legs](#qsv-bitrate-control-legs) show what
  works there: CBR through oneVPL's software bitrate control (`-extbrc 1`)
  passes on `h264_qsv` and `hevc_qsv`, and the hardware CBR legs are `not_run`
  with the reason "hardware bitrate control needs HuC firmware, and the xe
  kernel driver loads none on DG2 (Arc A-series)". Each hardware CBR leg
  there waits until oneVPL gives up: under a second for `h264_qsv`, 10 to 25
  seconds for `hevc_qsv` and `av1_qsv`
  ([research 0180](../research/0180-qsv-bitrate-control-dg2-xe.md)).
- `av1_qsv` encodes on Arc and newer GPUs, but the patch stack adds
  `-pelorus_roi` to `h264_qsv` and `hevc_qsv` only, so the AV1 QSV steering leg
  is `not_run` and says so. On a GPU without AV1 encode the same leg reads
  `no AV1 encode on this GPU or driver`.
- WSL2: Intel GPUs in WSL2 use `/dev/dxg` and the Windows driver's libraries
  under `/usr/lib/wsl/lib`, which this image does not use; the Linux render
  node path above is the supported one ([research 0229](../research/0229-tester-vendor-images.md)).

## Execution class and evidence claim

Every report states what kind of device it ran on and what it may claim
([research 0228](../research/0228-lavapipe-spike.md), #228). The producer
derives both fields and the validator re-derives them; a mismatch is refused.

| Field | Values | Rule |
| --- | --- | --- |
| `execution_class` | `hardware`, `software_vulkan`, `no_vulkan` | `hardware` when a listed device is not `PHYSICAL_DEVICE_TYPE_CPU`; `software_vulkan` when every device is CPU type (lavapipe, SwiftShader); `no_vulkan` when the probe found none |
| `evidence_claim` | `functional`, `gpu` | `gpu` only with `execution_class` `hardware`, and only for a run whose verdict is `pass` with at least one GPU stage passed; otherwise `functional` |

A GPU stage that passes on software Vulkan is recorded as `pass_software`, never
`pass`, and counts toward no GPU claim. The validator rejects a GPU stage with
status `pass` unless `execution_class` is `hardware`, and `pass_software` on any
other class. The stage runners still report `no_device` on a software-only host,
so `pass_software` appears only when a run executes a GPU stage there on purpose.

### Report schema version 2

`report.schema.json` is at version 2. Version 2 adds the two fields above, the
status `pass_software` and `tool.sha256` (the digest the
[report intake](../development/tester.md#3-file-it) checks). No report had been
published under version 1, so the version was bumped instead of keeping the
fields optional with defaults. A later change that removes a field or changes
its meaning bumps the version again; an added field stays in the same version
only when it is optional with a default. Tool 0.4.5 adds the optional leg field
`rate_control` (`cbr_extbrc`, `cbr_hw`) under that rule; tools 0.4.6 to 0.4.9
change no field; a leg without it is
the stage's own leg.

## Stages

| # | Stage | Pass rule | Not run / no device |
| --- | --- | --- | --- |
| 1 | `probe` | `vulkaninfo --summary` exits 0 | no hardware device: `no_device` for every stage marked "GPU" below |
| 2 | `libpelorus_suite` | not part of this kit version | `not_run` |
| 3 | `registration` | not part of this kit version | `not_run` |
| 4 | `format_matrix` (GPU) | [`vulkan-format-matrix.sh`](../../ffmpeg-patches/test/vulkan-format-matrix.sh) exits 0 with the validation layer on; every Vulkan diagnostic is on the [allow-list](#validation-gate) | layer absent: `not_run`; script exit 77: `no_device`; no `ffmpeg`: `not_run` |
| 5 | `steering_smoke` (GPU) | for each usable encoder, 8- and 16-frame encodes both decode to 8 and 16 frames, and the steered bitstream differs from the unsteered one at both lengths; for each QSV encoder, the [bitrate-control legs](#qsv-bitrate-control-legs) decode to every frame within 15 % of the target bitrate or are a named `not_run` | encoder not built, not usable on this host, or without `-pelorus_roi`: a `not_run` leg with the reason; none usable: `not_run` (a bitrate-control leg cannot pass the stage alone) |
| 6 | `sidedata_roundtrip` (GPU) | `pelorus_analyze_vulkan` + `pelorus_deband_vulkan`, then each usable carrier with `-udu_sei 1` (`hevc_nvenc`, `h264_nvenc`, `hevc_qsv`, `h264_qsv`, `hevc_vulkan`, `h264_vulkan`): every coded picture carries a well-formed `PelorusSideData` blob with the banding and variance sections and distinct `frame_pts` echoes, the decoder returns the blob as frame side data on every picture, and in both places each blob equals the one the analyze filter attached, byte for byte (or, on `hevc_nvenc`, `hevc_qsv` and `h264_qsv` only, its [maps-stripped form](#stage-6-side-data-round-trip)); `hevc_nvenc` and `h264_nvenc` carry it as the [zero-free carrier](#stage-6-side-data-round-trip), every other carrier as the plain blob; five cases per carrier | carrier not built, or its encode fails and the same encode without the side data fails too: a `not_run` leg with the encoder's error; none usable: `not_run`; AV1 has no carrier. A carrier whose encode fails only with the side data (baseline encodes) is a `fail` leg, never `not_run` ([ADR-0173](../adr/0173-tester-programme.md)) |
| 7 | `zero_copy_chain` (GPU) | the `-loglevel debug` graph holds no `hwdownload`, `hwupload` or `scale` beyond the allowed edges (below) and contains the Pelorus filters | no device with Vulkan Video decode and encode: `not_run` (the software-encoder leg still runs and can fail) |
| 8 | `bench` (GPU, opt-in) | non-gating: `scripts/bench/run-bench.py` writes `result.json` for a 4-point CQ ladder; a failure is recorded but never changes the verdict | needs `--bench`, a `vmaf` binary, `hevc_nvenc` or `av1_nvenc`: otherwise `not_run` |

### Stage 5: steering smoke

The steering input is `pelorus_analyze_vulkan=roi=1`, which attaches
region-of-interest side data; `-pelorus_roi 1` makes the encoder honour it. The
encoders tried are `h264_vulkan`, `hevc_vulkan`, `av1_vulkan`, `h264_nvenc`,
`hevc_nvenc`, `av1_nvenc` (`-rc constqp`), `h264_qsv`, `hevc_qsv`, `av1_qsv`
(`-q:v 30`, constant QP), `libsvtav1` and `libaom-av1`. Vulkan encoders are
tried on each hardware device in turn and the first device that encodes is
used; NVENC and QSV encoders take the frames back from the first hardware
Vulkan device and pick their own GPU.

Each encoder is one leg of the stage (`legs` in the report), `pass`, `fail` or
`not_run` with a reason. Before steering, the stage encodes 8 frames without
it. An encoder that fails that first encode is a `not_run` leg; for an AV1
encoder that fails as unsupported (NVENC before Ada, QSV before Arc) the reason
is `no AV1 encode on this GPU or driver` followed by the encoder's own message.
An encoder that encodes but has no `-pelorus_roi` option (`av1_qsv`) is a
`not_run` leg that names the option.

Per encoder the stage encodes the clip five times (unsteered 8 frames twice,
steered 8, unsteered 16, steered 16) and decodes four of them. The unsteered
encodes run `pelorus_analyze_vulkan=roi=0`, so no region-of-interest side data
reaches the encoder: FFmpeg's stock QSV path applies ROI rectangles whether or
not `-pelorus_roi` is set (it is how `h264_qsv` is steered), so a control that
carried them would be steered too.

- an unsteered stream that does not decode is an encoder or driver defect, not a
  steering result: the leg is `not_run` with the reason `baseline output does
  not decode (encoder/driver defect)`, its name and the decoder error line. A
  steered stream that does not decode while the baseline does is a `fail`;
- the two unsteered 8-frame streams must be identical; if not, the encoder is
  not deterministic and the leg is `not_run` as inconclusive;
- a steered stream identical to its unsteered twin is a `fail`, unless the
  encoder itself logged that it ignores the data (`continuing without ROI bias`,
  or the Vulkan driver clamping negative QP deltas to 0). Then the leg is
  `not_run` and the reason carries that log line.

The stage is `fail` when any leg fails, `pass` when at least one steering leg
passes and none fails, and `not_run` when no steering leg was usable; the
validator refuses a stage that passes with a failed leg or without a passing
steering leg. A hardware encoder whose driver cannot honour the map therefore
shows as a named `not_run` leg, not as a green result.

#### QSV bitrate-control legs

For each QSV encoder in the FFmpeg build (`h264_qsv`, `hevc_qsv`, `av1_qsv` in
the Intel image) the stage adds two legs, marked in the report with
`rate_control` ([ADR-0180](../adr/0180-tester-vendor-images.md) decision 8):

| `rate_control` | Encode |
| --- | --- |
| `cbr_extbrc` | CBR with `-extbrc 1`: oneVPL picks each frame's QP and drives the driver in constant QP, so no HuC firmware is needed |
| `cbr_hw` | CBR with the GPU's own bitrate control |

Both encode the `synth-motion` fixture eight times over (400 frames, 16 s)
through `pelorus_deband_vulkan`, with `-b:v 1000k -maxrate 1000k -bufsize
1000k`, into an elementary stream. A leg passes when the stream decodes to all
400 frames and its bitrate is within 15 % of 1000 kb/s; a `cbr_extbrc` encode
must also log `ExtBRC: ON`. On the Arc A380 under `xe` the image measured
+1.8 % (`h264_qsv`) and +1.0 % (`hevc_qsv`).

| Outcome | Leg |
| --- | --- |
| bitrate more than 15 % off, a frame missing, or `ExtBRC` not on | `fail` |
| `av1_qsv` `cbr_extbrc` | `not_run` without an encode: oneVPL applies `-extbrc` to AV1 only through its look-ahead tools, which `av1_qsv` does not enable by default |
| the encode fails and a constant-QP encode of the same input fails too | `not_run`: the encoder is not usable on this host (with the free media driver named, as in the steering legs) |
| `cbr_extbrc` fails while constant QP encodes | `fail` |
| `cbr_hw` fails with `Invalid FrameType:0`, `GPU Hang (-21)` or `device failed (-17)` while constant QP encodes, and an Intel render node is a DG2 (PCI id `0x56xx`) bound to `xe` | `not_run`: "hardware bitrate control needs HuC firmware, and the xe kernel driver loads none on DG2 (Arc A-series)" |
| the same failure, but `/sys/class/drm` names no Intel render node | `not_run` that says the cause is not confirmed |
| `cbr_hw` fails with `Selected ratecontrol mode is unsupported` | `not_run`: the media driver offers no hardware bitrate control |
| any other `cbr_hw` failure, or the no-HuC failure on another GPU or kernel driver | `fail` |

### Stage 7: zero-copy chain

The stage runs two legs and reads the filter graph that FFmpeg prints at
`-loglevel debug` (`Filter '...' formats:` blocks and `auto-inserting filter`
lines):

| Leg | Chain | Allowed transfers |
| --- | --- | --- |
| native | Vulkan decode, `pelorus_deband_vulkan`, `pelorus_denoise_vulkan`, `pelorus_mc_vulkan`, Vulkan encode | none |
| software encoder | raw source, `hwupload`, the same three filters, `hwdownload`, `libsvtav1` or `libaom-av1` | one `hwupload`, one `hwdownload` |

Any `scale` (including `auto_scale_N`), any further `hwupload` or `hwdownload`,
an empty graph, or a graph without the three Pelorus filters fails the leg. The
background and hop rules are in [research 0172](../research/0172-zero-copy-audit.md).
When the native leg cannot run (no device with both Vulkan Video decode and
encode), the stage is `not_run` and its reason states the result of the
software-encoder leg.

## Environment

| Variable | Effect |
| --- | --- |
| `FFMPEG_BIN` | patched FFmpeg binary (default `ffmpeg` on `PATH`) |
| `VULKAN_DEVICE` | FFmpeg Vulkan device index; default is the hardware devices in `vulkaninfo` order |
| `PELORUS_VALIDATE` | `auto` (default), `1` or `0`. `format_matrix` needs `VK_LAYER_KHRONOS_validation`: with `auto` and the layer absent it is `not_run`; with `0` it runs without validation and says so. `1` with the layer absent is a `fail` in every GPU stage (fail closed); with the layer present every GPU stage runs with it on and [gates its messages](#validation-gate) |
| `PELORUS_VUID_ALLOWLIST` | path of the VUID allow-list (default `ffmpeg-patches/test/vulkan-vuid-allowlist.txt`) |
| `LIBVA_DRIVER_NAME` | passed through to FFmpeg. A run from a checkout on a host that sets it for another GPU (for example `nvidia`) must set `iHD`: otherwise QSV fails with `Error creating a MFX session: -9` and every QSV leg is `not_run`. The images do not set it; libva then picks the driver of the render node |
| `PELORUS_TESTER_CACHE` | fixture cache directory (default `$XDG_CACHE_HOME/pelorus-tester`) |
| `PELORUS_FORMAT_MATRIX` | path of the format matrix script, for images that install it elsewhere |
| `VMAF_BIN` | `vmaf` binary for the bench stage |

### Stage 6: side-data round trip

Each carrier runs five cases, and its leg fails when any case fails:

| Case | Input | `pelorus_analyze_vulkan` options |
| --- | --- | --- |
| `gradient-default` | `synth-banding` | defaults |
| `flat-180p-maps1-cell32` | `synth-flat-180p` | `maps=1:cell=32` |
| `flat-1080p-maps1-cell32` | `synth-flat-1080p` | `maps=1:cell=32` |
| `flat-180p-maps0`, `flat-1080p-maps0` | the two flat clips | `maps=0` |

Flat content gives a blob that is mostly zero bytes. On an RTX 4090 with driver
615.78.08, NVENC writes a SEI payload truncated when it needs more emulation
prevention bytes than `ceil(P / 3) + 3` for a `P`-byte payload, which those
zero runs reach, so the decoder drops the blob
([issue #284](https://github.com/VMAFx/pelorus/issues/284)); a gradient never
shows this. The `maps=0` cases are the small blob that always survives.

The reference for each case comes from the same chain run without an encoder,
with `showinfo` printing the blob as a hex dump into the log. The tester runs
that graph with `-nostats`: the progress line is written by another thread and
can land in the middle of a long hex dump, which cut a 12 408-byte reference
down to a few kilobytes and failed a healthy carrier
([issue #290](https://github.com/VMAFx/pelorus/issues/290)). Every reference
blob is also checked against the `total_size` in its own header and against the
picture count. A reference that fails the check is read once more; a second
failure ends the stage as `incomplete` (exit code 2, a harness error) and never
as a carrier `fail`. A real carrier `fail` on another leg still wins.

Since interop ABI 1.5, `hevc_nvenc` and `h264_nvenc` (patch 0022) write every
Pelorus blob in its zero-free carrier form: the UUID
`3f9b37b8-fd9a-4621-920e-9b78b55cf9b5`, then the COBS-encoded blob, with no zero
byte ([ADR-0183](../adr/0183-sidedata-zero-free-carrier.md)). The runner reads
both forms in the stream and at the decode tap. It decodes a carrier as strictly
as `pel_blob_unwrap()`: a zero byte, a block that runs past the end, an empty
final block after a full one or an image shorter than the header fails the
leg as a malformed carrier. An NVENC leg must carry every Pelorus payload as a
carrier, and every other carrier must write the plain blob; the wrong form fails
the leg and names how many payloads had it. The decoded carrier is then compared
like a plain blob.

The expected blob is not a constant. The runner repeats the encode's filter
chain with `showinfo` in place of the encoder and reads each frame's blob at
the encoder's input. It then compares, per picture:

- the blobs parsed out of the encoded stream (pictures ordered by their
  `frame_pts` echo, because a stream lists them in decode order), and
- the blobs the decoder returns as frame side data (`showinfo` of a decode).

A picture passes when its blob equals the written blob byte for byte. The only
exceptions are `hevc_nvenc`, which writes a blob that does not fit its
1024-byte header budget without the per-cell maps (patch 0022,
[ADR-0181](../adr/0181-hevc-nvenc-sei-header-budget.md)), and `hevc_qsv` and
`h264_qsv`, which do the same for the SEI space the QSV runtime reserves per
picture (patch 0023, [research 0286](../research/0286-qsv-udu-sei-budget.md)): the sections up to
the last one, every map offset and size zero, `total_size` the new end. The
tester builds that form exactly as `pelorus_sei_fit.h` does and accepts nothing
else; the pass reason says how many pictures were stripped. Any other
difference fails the leg with the carrier, the picture index, both lengths and
the first differing offset, as does a picture count or a per-picture blob count
that differs from what was written.

## Validation gate

With `PELORUS_VALIDATE=1` every GPU stage fails on a Vulkan validation message
(`VUID-...`) that is not on the allow-list. There is no blanket ignore. The list
is one file, [`vulkan-vuid-allowlist.txt`](../../ffmpeg-patches/test/vulkan-vuid-allowlist.txt),
read by `vulkan-format-matrix.sh` and by the stage runners:

```text
<VUID without the "VUID-" prefix> | <reference> | <expiry YYYY-MM-DD>
```

- one line per VUID, never a pattern;
- the reference is an issue (`#214`) or a filed upstream report, and the owner of
  the defect, not Pelorus; every current entry reproduces with a plain FFmpeg
  command and no Pelorus filter or patch;
- an entry without a reference, with a bad expiry or past its expiry stops
  matching, so the message fails again;
- a stage that passes through a listed VUID names the entry (VUID, reference,
  expiry) in its reason.

## Fixtures

Stages 5 to 8 decode a pinned clip. The tester runs offline, so fixtures are
pinned by SHA-256 in [`tools/tester/fixtures.lock.json`](../../tools/tester/fixtures.lock.json)
with a licence record per file, and are written into the cache once.

```bash
python3 -I tools/tester/pelorus_tester_fixtures.py fetch    # once, needs network for bbb
python3 -I tools/tester/pelorus_tester_fixtures.py verify   # offline; exits 1 on any problem
python3 -I tools/tester/pelorus_tester_fixtures.py notices  # attribution text for THIRD_PARTY_NOTICES
```

| Fixture | Source | Licence |
| --- | --- | --- |
| `synth-banding` | recorded `lavfi` recipe (`gradients` with `seed=1`), 640x360, 24 frames; rendered into the cache on first use, no network | EUPL-1.2 |
| `synth-flat-180p`, `synth-flat-1080p` | recorded `lavfi` recipe (`color=c=gray`), 320x180 and 1920x1080, 4 frames at 25 fps, for the flat side-data cases; rendered into the cache on first use, no network | EUPL-1.2 |
| `synth-motion` | recorded `lavfi` recipe (`testsrc2`), 640x360, 50 frames at 25 fps, for the QSV bitrate-control legs; rendered into the cache on first use, no network | EUPL-1.2 |
| `bbb` | Big Buck Bunny excerpt, 640x360, 48 frames, downloaded once and extracted | CC BY 3.0, see below |

`verify` fails when a file is missing from the cache, when its hash differs from
the pin (one flipped byte is enough), and when an entry lacks a licence record:
SPDX id, holder and source, plus attribution text naming the holder for CC BY
licences. The stages use `synth-banding`, the side-data round trip also the
two `synth-flat-*` clips, and the QSV bitrate-control legs `synth-motion`; a stage that needs a fixture that is not in the cache reports
`not_run` rather than downloading.

`netflix-bar` is not in the lock. Its licence is unconfirmed
([#225](https://github.com/VMAFx/pelorus/issues/225)); it ships only after that
is settled.

### Attribution

Big Buck Bunny, (c) copyright 2008, Blender Foundation,
[www.bigbuckbunny.org](https://www.bigbuckbunny.org), licensed under
[Creative Commons Attribution 3.0](https://creativecommons.org/licenses/by/3.0/).
Changes: a 2 second excerpt of a third-party 640x360 H.264 transcode, rescaled
and decoded to raw YUV 4:2:0. Any artifact that ships the `bbb` fixture carries
this text in its notices file; `pelorus_tester_fixtures.py notices` prints it.

## Planted failures

`python3 -I tools/tester/pelorus_tester_report.py --self-test` runs one planted
bad case per rule below, and `--self-test --disable <rule>` must exit 1 for each.

| Rule | Planted case |
| --- | --- |
| `zc_hwdownload`, `zc_hwupload` | a graph with an extra `hwdownload` or `hwupload`; a second `hwdownload` in the software-encoder leg |
| `zc_scale` | an `auto-inserting filter 'auto_scale_1'` line |
| `zc_graph_nonempty` | an empty graph; a graph without the Pelorus filters |
| `vuid_unlisted`, `vuid_gate_every_stage` | an unknown VUID in any GPU stage must fail it |
| `vuid_expiry`, `vuid_reference` | an expired entry, and an entry without a valid reference, must not match |
| `steering_baseline_defect` | an unsteered baseline that does not decode: named `not_run` leg; a steered-only failure stays a `fail` |
| `validate_fail_closed` | `PELORUS_VALIDATE=1`, layer absent: must be `fail` |
| `validation_absent_not_run` | `auto`, layer absent: must be `not_run` |
| `encoder_absent_not_run` | no usable encoder: must be `not_run`, not `pass` |
| `steering_effect` | steered bitstream equal to the unsteered one |
| `steering_selfreport` | no effect, but the encoder logged that it ignores the data: must be a named `not_run` leg |
| `steering_decode` | a steered stream that decodes to 15 of 16 frames |
| `steering_control` | two unsteered encodes that differ |
| `sd_present` | a coded picture without a Pelorus blob |
| `sd_structure` | wrong total size or ABI major in the blob |
| `sd_pts` | identical `frame_pts` echoes on all pictures |
| `sd_decode_tap` | decoder output without the Pelorus UUID |
| `sd_integrity_count` | a dropped blob, a missing picture, an extra blob |
| `sd_integrity_bytes` | a truncated blob, one flipped byte, a maps-stripped blob from a carrier that does not strip |
| `sd_integrity_stripped` | a maps-stripped blob with `total_size` kept, a flipped scalar or map fields left in place |
| `sd_strip_carriers` | a maps-stripped blob accepted from any carrier but `hevc_nvenc`, `hevc_qsv` and `h264_qsv` (the carrier set that may strip) |
| `sd_carrier_form` | a plain blob on an NVENC leg; a zero-free carrier on any other leg |
| `sd_carrier_strict` | a carrier that ends with an empty block after a full one (non-canonical) |
| `sd_reference_length` | a reference blob shorter than the `total_size` in its own header, a missing or doubled reference blob |
| `sd_reference_not_carrier_fail` | a reference that stays unusable after the one retry, reported as a `fail` instead of an `incomplete` stage |
| `bench_nongating` | a failing bench stage: verdict must stay `pass` |
| `execution_class_derived` | a lavapipe device list with `execution_class: hardware`; a report with no device and `software_vulkan` |
| `claim_gpu_needs_hardware` | a lavapipe device list with `evidence_claim: gpu` |
| `software_pass_status` | a GPU stage with status `pass` on `software_vulkan` |
| `pass_software_class` | a `pass_software` stage on a `hardware` report |
| `no_device_option` | an `nvidia` or `intel` report whose `no_device` reason names none of the kit's options (`--gpus all` or `NVIDIA_DRIVER_CAPABILITIES`; `--device /dev/dri` or `--group-add`) |
| `legs_consistent` | a stage that passes with a failed leg, without a passing leg, with only a bitrate-control leg passing, or with a `not_run` leg that has no reason |
| `brc_bitrate` | a bitrate-control stream 16 % above or below the target; an `-extbrc 1` encode at 1.4 Mb/s |
| `brc_decode` | a bitrate-control stream that decodes to 399 of 400 frames |
| `brc_extbrc_engaged` | an `-extbrc 1` encode that logs `ExtBRC: OFF`, or no ExtBRC state |
| `brc_no_huc` | hardware CBR failing with `Invalid FrameType:0`, `GPU Hang (-21)` or `device failed (-17)` on a DG2 under `xe`: must be the named `not_run`, and stays a `fail` on another GPU |
| `brc_hw_refused` | hardware CBR refused with `Selected ratecontrol mode is unsupported`: must be a named `not_run` |
| `fixture_hash` | one flipped byte in a fixture |
| `fixture_licence` | a fixture without a licence record |
| `fixture_attribution` | a CC BY fixture without attribution text |
