- **Breaking: Pelorus is now licensed under the EUPL-1.2**, a reciprocal
  licence, like its sibling vmafx. `libpelorus` was BSD-2-Clause-Patent up to
  `v0.2.2`; whoever distributes it from this release on, changed or not, keeps
  its notices, includes the licence and provides the source or a pointer to
  it. The sources the patch stack adds to FFmpeg and the patches stay
  LGPL-2.1-or-later. `REUSE.toml` records the copyright and licence of every
  file, `LICENSE` is the EUPL-1.2 text and `LICENSES/` holds every licence
  text in use; `reuse lint` runs in the pre-commit hooks and as the `REUSE
  lint` CI check, and the release archive of the patch stack now carries the
  LGPL-2.1-or-later and EUPL-1.2 texts
  ([ADR-0171](docs/adr/0171-eupl-relicense.md), [licensing](docs/licensing.md)).
