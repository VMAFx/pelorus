- **Perceptual bit-allocation map — evaluated and rejected on measurement**
  ([ADR-0135](docs/adr/0135-analyze-perceptual-aq-map.md)). A per-tile perceptual-AQ map
  derived from the `analyze` statistics was built and measured against the encoder's own
  variance-AQ, and does not beat it. Recorded as a negative result rather than shipped;
  the same conclusion generalises in ADR-0142 (source-side rate control loses structurally
  to the encoder's RC) and is why ADR-0132's per-shot CRF steering was also rejected.
