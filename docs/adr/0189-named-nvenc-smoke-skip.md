<!-- markdownlint-disable MD013 MD060 -->
# ADR-0189: The patch-stack replay passes the NVENC smoke's skip only when the caller names its reason

- **Status**: Accepted
- **Date**: 2026-10-12
- **Deciders**: Lusoris
- **Tags**: testing, ci, ffmpeg-patches

## Context

`ffmpeg-patches/test/build-and-run.sh` ends with `nvenc-udu-sei-smoke.sh`, the GPU
proof of the NVENC SEI carrier (ADR-0181, ADR-0183). The smoke exits 77 with a
`SKIP:` line when the host cannot run it: `hevc_nvenc` not built, no NVIDIA driver,
no usable Vulkan device. The replay accepted exit 77 for any of those reasons.

On a GPU host that is meant to run the smoke, a missing driver or a lost device
also exits 77, so the replay reported green although the smoke never ran. Both CI
lanes that replay the stack skip by design, each for one known reason:

- `ffmpeg-stack`: `hevc_nvenc` is built, but the hosted runner has no NVIDIA
  driver (`Cannot load libcuda.so.1`).
- `lavapipe`: the lane installs no ffnvcodec (`hevc_nvenc is not built into`).

[ADR-0188](0188-named-test-skips.md) closed the same hole for Meson tests; this
smoke runs outside Meson.

## Decision

The replay passes the smoke's exit 77 only when the caller names the skip:
`NVENC_SMOKE_SKIP` holds fixed text that the smoke's last `SKIP:` line must contain.

- `ffmpeg-patches/test/named-skip.sh` judges the status: 0 passes; 77 passes only
  when the named text is set and found; an unnamed skip, a skip for another
  reason, a 77 without a `SKIP:` line and every other status fail.
- `build-and-run.sh` keeps the smoke output in its log directory and calls the
  judge. Unset, a skip fails the replay and the error names the variable.
- CI names the reason each lane expects (`ci.yml`, both replay steps).
- `named-skip-self-test` in the fast suite plants each case and requires the
  expected status.

## Alternatives considered

- **Fail on every skip.** Rejected: neither hosted lane has an NVIDIA GPU, and the
  replay's other checks (patch apply, link, registration) must still run there.
- **Name the skip as a yes/no switch.** Rejected: on the `ffmpeg-stack` lane a
  switch would also pass a skip for a missing Vulkan device or a broken build; only
  the reason separates the planned skip from those.
- **Reuse `check-test-skips.py`.** Rejected: it reads Meson's `testlog.json`; the
  smoke is a plain process inside a shell script, and one environment variable at
  the call site is the smaller change.

## Consequences

- Running `build-and-run.sh` on a machine without an NVIDIA driver needs
  `NVENC_SMOKE_SKIP="Cannot load libcuda.so.1"` (`docs/development/build.md`).
- A changed skip message in the smoke fails CI until `ci.yml` names it.

## References

- Gap audit 2026-10-12, row B3 (smoke exit 77 accepted for any reason); fleet order
  `fleet-green-while-broken-20261012`.
- Planted failure: a stub smoke that prints `SKIP: no usable Vulkan device` and
  exits 77 passes the replay block of `origin/master` (exit 0) and fails the new
  block (exit 1) unless `NVENC_SMOKE_SKIP` names that reason.
