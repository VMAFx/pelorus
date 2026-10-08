- Rendered the dev container bundle on the published toolchain base,
  `ghcr.io/vmafx/pelorus-dev@sha256:33c934d3...` (built from `10e032a`, SLSA
  provenance verified), so `postCreateCommand` finds Meson, Ninja, clang and
  Node.js. The pin is a digest; the renderer rejects a tag-only reference.
  `docs/development/build.md` lists the refresh steps for when the Containerfile
  changes ([ADR-0153](docs/adr/0153-praetor-full-adoption.md), #132).
