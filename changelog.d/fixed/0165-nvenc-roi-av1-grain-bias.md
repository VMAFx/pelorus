- Fixed two NVENC value mappings in the FFmpeg patch stack. `av1_nvenc
  -pelorus_film_grain 1` (patch 0011) now writes the AV1 chroma film-grain
  multipliers and offsets with their raw `+128`/`+256` biases. Before this fix
  the unbiased `AVFilmGrainAOMParams` values went straight into NVENC, which
  corrupted the chroma grain: a dav1d-exported `cb_offset` of −238 was coded
  as 274 instead of 18 (BUG-019). `av1_nvenc -pelorus_roi 1` (patch 0004) now
  scales a region's `qoffset` by the AV1 qindex span (255) instead of the
  H.264/HEVC QP span (51 at 8-bit), so an AV1 region receives the intended
  delta, saturating at the int8 map limit, instead of about a fifth of it at
  8-bit (BUG-020). H.264 and
  HEVC ROI output is unchanged. On an RTX 4090 the round trip now reproduces a
  libaom test vector's `cb_mult`/`cb_luma_mult`/`cb_offset` (247/192/18)
  exactly, and a full-frame AV1 delta of −40 matches a 40-lower constant qindex.
  A new fast-suite test, `nvenc-pelorus-mapping`, compiles both mappings from
  the hand-maintained diffs and checks them.
