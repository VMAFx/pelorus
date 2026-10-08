<!-- markdownlint-disable MD013 -->
# Tester kit stages and their pass rules

The tester report program runs eight stages in a fixed order and records each as
`pass`, `fail`, `not_run`, `no_device` or `incomplete`, with a reason for
everything except `pass`. How to run the kit and read the report:
[tester kit](../development/tester.md). Rules and licence terms:
[ADR-0173](../adr/0173-tester-programme.md). Stage runners:
[`tools/tester/pelorus_tester_stages.py`](../../tools/tester/pelorus_tester_stages.py).

A stage never substitutes another path for a missing one. A missing device,
encoder or validation layer is `not_run` or `no_device` with the reason named;
it is never reported as `pass`.

## Stages

| # | Stage | Pass rule | Not run / no device |
| --- | --- | --- | --- |
| 1 | `probe` | `vulkaninfo --summary` exits 0 | no hardware device: `no_device` for every stage marked "GPU" below |
| 2 | `libpelorus_suite` | not part of this kit version | `not_run` |
| 3 | `registration` | not part of this kit version | `not_run` |
| 4 | `format_matrix` (GPU) | [`vulkan-format-matrix.sh`](../../ffmpeg-patches/test/vulkan-format-matrix.sh) exits 0 with the validation layer on; every Vulkan diagnostic is on the script's upstream allow-list | layer absent: `not_run`; script exit 77: `no_device`; no `ffmpeg`: `not_run` |
| 5 | `steering_smoke` (GPU) | for each usable encoder, 8- and 16-frame encodes both decode to 8 and 16 frames, and the steered bitstream differs from the unsteered one at both lengths | encoder not built or not usable on this host: skipped and named; none usable: `not_run` |
| 6 | `sidedata_roundtrip` (GPU) | `pelorus_analyze_vulkan` + `pelorus_deband_vulkan`, then `hevc_nvenc` or `h264_nvenc` with `-udu_sei 1`: every coded picture carries a well-formed `PelorusSideData` blob with the banding and variance sections, distinct `frame_pts` echoes, and the decoder returns the blob as frame side data on every picture | neither NVENC encoder usable: `not_run` |
| 7 | `zero_copy_chain` (GPU) | the `-loglevel debug` graph holds no `hwdownload`, `hwupload` or `scale` beyond the allowed edges (below) and contains the Pelorus filters | no device with Vulkan Video decode and encode: `not_run` (the software-encoder leg still runs and can fail) |
| 8 | `bench` (GPU, opt-in) | non-gating: `scripts/bench/run-bench.py` writes `result.json` for a 4-point CQ ladder; a failure is recorded but never changes the verdict | needs `--bench`, a `vmaf` binary, `hevc_nvenc` or `av1_nvenc`: otherwise `not_run` |

### Stage 5: steering smoke

The steering input is `pelorus_analyze_vulkan=roi=1`, which attaches
region-of-interest side data; `-pelorus_roi 1` makes the encoder honour it. The
encoders tried are `h264_vulkan`, `hevc_vulkan`, `av1_vulkan`, `hevc_nvenc`,
`av1_nvenc`, `libsvtav1` and `libaom-av1`. Vulkan encoders are tried on each
hardware device in turn and the first device that encodes is used.

Per encoder the stage encodes the clip five times (unsteered 8 frames twice,
steered 8, unsteered 16, steered 16) and decodes four of them:

- the two unsteered 8-frame streams must be identical; if not, the encoder is
  not deterministic and the encoder is skipped as inconclusive;
- a steered stream identical to its unsteered twin is a `fail`, unless the
  encoder itself logged that it ignores the data (`continuing without ROI bias`,
  or the Vulkan driver clamping negative QP deltas to 0). Then the encoder is
  skipped and the reason carries that log line.

The stage is `fail` when any encoder fails, `pass` when at least one passes and
none fails, and `not_run` when none was usable. A hardware encoder whose driver
cannot honour the map therefore shows as a named skip, not as a green result.

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
| `PELORUS_VALIDATE` | `auto` (default), `1` or `0`. `format_matrix` needs `VK_LAYER_KHRONOS_validation`: with `auto` and the layer absent it is `not_run`; with `0` it runs without validation and says so. `1` with the layer absent is a `fail` in every GPU stage (fail closed). With `1`, the other GPU stages run with the layer on and list new diagnostics in their reason without failing: those come from Vulkan Video code the project does not own, and only `format_matrix` gates them |
| `PELORUS_TESTER_CACHE` | fixture cache directory (default `$XDG_CACHE_HOME/pelorus-tester`) |
| `PELORUS_FORMAT_MATRIX` | path of the format matrix script, for images that install it elsewhere |
| `VMAF_BIN` | `vmaf` binary for the bench stage |

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
| `bbb` | Big Buck Bunny excerpt, 640x360, 48 frames, downloaded once and extracted | CC BY 3.0, see below |

`verify` fails when a file is missing from the cache, when its hash differs from
the pin (one flipped byte is enough), and when an entry lacks a licence record:
SPDX id, holder and source, plus attribution text naming the holder for CC BY
licences. The stages use `synth-banding`; a stage that needs a fixture that is
not in the cache reports `not_run` rather than downloading.

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
| `validate_fail_closed` | `PELORUS_VALIDATE=1`, layer absent: must be `fail` |
| `validation_absent_not_run` | `auto`, layer absent: must be `not_run` |
| `encoder_absent_not_run` | no usable encoder: must be `not_run`, not `pass` |
| `steering_effect` | steered bitstream equal to the unsteered one |
| `steering_selfreport` | no effect, but the encoder logged that it ignores the data: must be a named skip |
| `steering_decode` | a steered stream that decodes to 15 of 16 frames |
| `steering_control` | two unsteered encodes that differ |
| `sd_present` | a coded picture without a Pelorus blob |
| `sd_structure` | wrong total size or ABI major in the blob |
| `sd_pts` | identical `frame_pts` echoes on all pictures |
| `sd_decode_tap` | decoder output without the Pelorus UUID |
| `bench_nongating` | a failing bench stage: verdict must stay `pass` |
| `fixture_hash` | one flipped byte in a fixture |
| `fixture_licence` | a fixture without a licence record |
| `fixture_attribution` | a CC BY fixture without attribution text |
