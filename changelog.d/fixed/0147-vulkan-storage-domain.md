- Corrected all arithmetic Vulkan filters to convert storage-image values into
  the logical sample domain before applying normalized thresholds, so planar
  10/12-bit, P010/P012, and 8-bit inputs use the same numeric contract; also
  preserve both semi-planar chroma components and unowned packed lanes during
  scalar transforms (ADR-0147).
