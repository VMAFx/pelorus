- Fixed the deband and borderfix Vulkan shaders ending the invocation at the
  first plane that does not contain the position: on a subsampled layout such
  as `yuva420p` the full-size alpha plane (after the half-size chroma planes)
  was never written. Each plane is now bounds-checked on its own (BUG-007,
  BUG-008).
- Fixed the deband `bayer8` dither matrix, which was not a Bayer matrix (the
  coordinate bits were consumed MSB-first); it is now the canonical recursive
  8x8 Bayer matrix in both the shipped shader and the reference (BUG-013).
- Made the borderfix clamp bounds order-safe when `left + right` (or
  `top + bottom`) reaches the plane size, in the shipped shader and the
  reference (BUG-025). New fast test `shader-plane-bounds`.
