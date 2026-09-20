- Corrected all arithmetic Vulkan filters to convert storage-image values into
  the logical sample domain before applying normalized thresholds, so planar
  10/12-bit, P010/P012, and 8-bit inputs use the same numeric contract; also
  preserve both semi-planar chroma components and unowned packed lanes during
  scalar transforms, quantize shifted-format writes so P010/P012 padding bits
  remain zero, and bound grain-estimator reductions through DCI 8K
  ([ADR-0147](docs/adr/0147-vulkan-sample-domain-and-components.md)).
