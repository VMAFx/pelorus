<!-- markdownlint-disable MD013 -->
# ADR-0106: Control plane — tune Pelorus filter strength against VMAF via vmafx

- **Status**: Accepted
- **Date**: 2026-06-14
- **Deciders**: Lusoris
- **Tags**: interop, autotune, vmafx

## Context

A pre-encode filter's strength (deband threshold, denoise sigma, grain
intensity) trades quality against bitrate, and the optimum is content-dependent.
The honest objective is the *processed-then-encoded* VMAF, not a per-frame
metric. vmafx already ships the machinery to score and optimize: the
`libvmaf_tune` FFmpeg filter (in-graph VMAF + recommended-CRF feedback), the
`vmafx-server` `POST /v1/score`, the `vmaf-mcp` tools (`vmaf_score`,
`run_tune_per_shot`, `run_ladder`), and the `vmaf-tune` CLI (Optuna TPE
recommend / per-shot / ladder).

## Decision

We will **reuse vmafx's existing control plane** rather than build an
orchestrator. Pelorus exposes its strengths as FFmpeg `AVOption`s (settable per
run) and contributes the §3 perceptual-weighting inputs via the side-data ABI
([ADR-0103](0103-interop-sidedata-abi.md)). Two coupling modes: **in-graph**
(`libvmaf_tune` in the same filtergraph reads the score in one pass) and
**out-of-graph** (`vmafx-server`/MCP scores a finished encode, enabling a
distributed search). The search loop (TPE, bisect-to-target, per-shot) is
vmafx's; Pelorus is the tunable + the weighting source.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
|---|---|---|---|
| Reuse vmafx libvmaf_tune / server / MCP | No new orchestrator; battle-tested optimizer; in-graph one-pass option | Couples to vmafx's interfaces | **Chosen** |
| Build a Pelorus-native VMAF tuner | Self-contained | Reinvents vmafx; duplicate scoring + optimizer | Rejected |
| No autotune (manual presets only) | Simplest | Leaves the content-dependent optimum on the table | Rejected as the design target (presets still ship as defaults) |

## Consequences

- **Positive**: VMAF-in-the-loop tuning with zero new orchestration; per-shot
  strengths align with Pelorus's own scene-cut flags (motion section).
- **Negative**: the autotune workflow depends on a vmafx build/endpoint being
  available; versioned separately from the data-plane ABI.
- **Neutral / follow-ups**: a `vf_pelorus_*` ⇄ `libvmaf_tune` example graph in
  `docs/usage/`; a thin `pelorus-tune` wrapper that shells `vmaf-tune` is future
  work.

## References

- vmafx `ffmpeg-patches/0008-add-libvmaf_tune-filter.patch`,
  `api/openapi/vmafx-server-v1.yaml`, `mcp-server/vmaf-mcp`.
- [docs/api/interop-abi.md](../api/interop-abi.md) §control-plane.
- Source: `Q1.4` — "Shared ABI/side-data + autotune RPC".
