<!-- markdownlint-disable MD013 -->
# Architecture Decision Records

Nygard-format ADRs for Pelorus. One file per decision,
`NNNN-kebab-case.md`, following [0000-template.md](0000-template.md). Reserve a
number atomically with `scripts/adr/next-free.sh --claim <slug>` before creating
the file. Land the ADR **before** the implementing commit; cite the user request
(`req`) or popup answer (`Q<r>.<q>`) in `## References`. Decisions are immutable
once Accepted — supersede with a new ADR rather than editing the body.

Numbering: project decisions start at `0001`; rules adopted from the vmafx
sibling keep vmafx's number (e.g. 0100, 0108) for an easy cross-walk.

| ADR | Title | Status |
|---|---|---|
| [0001](0001-project-genesis.md) | Pelorus genesis — Vulkan GPU pre-encode pipeline under the vmafx org | Accepted |
| [0100](0100-doc-substance-rule.md) | Every user-discoverable surface ships docs in the same PR | Accepted |
| [0102](0102-flagship-smart-deband.md) | Smart deband (f3kdb) is the flagship filter | Accepted |
| [0103](0103-interop-sidedata-abi.md) | Pelorus⇄vmafx interop via a UUID-keyed AVFrame side-data ABI | Accepted |
| [0104](0104-ffmpeg-patch-stack.md) | Delivery — libpelorus core + an FFmpeg patch stack | Accepted |
| [0105](0105-libpelorus-license.md) | libpelorus is BSD-2-Clause-Patent; FFmpeg files are LGPL-2.1 | Accepted |
| [0106](0106-autotune-control-plane.md) | Control plane — tune filter strength against VMAF via vmafx | Accepted |
| [0108](0108-deep-dive-deliverables-rule.md) | Fork-local PRs ship the deep-dive deliverables | Accepted |
