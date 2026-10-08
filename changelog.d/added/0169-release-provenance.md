- Releases now carry SLSA v1.0 build provenance, an SPDX SBOM of the patch
  archive, `SHA256SUMS`, and a keyless cosign signature of it
  (`SHA256SUMS.sigstore.json`). A reusable workflow, `release-build.yml`,
  builds, attests, and signs; the publishing job only uploads its files on a
  tag push. `docs/development/build.md` shows how to verify a release
  ([ADR-0169](docs/adr/0169-release-provenance-slsa3.md)).
