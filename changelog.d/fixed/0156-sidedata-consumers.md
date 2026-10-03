- Fixed the FFmpeg-side Pelorus consumers (`pelorus_analyze`, `pelorus_denoise`
  motion compensation, `pelorus_scenecut`) to scan every appended
  `AV_FRAME_DATA_SEI_UNREGISTERED` blob newest first instead of only the first
  entry, so sections from a later producer in a chain are no longer invisible
  (BUG-005); to check the readable section size before every field read, so a
  shorter section from an older producer is no longer read past its end
  (BUG-003, BUG-004); and to map the motion-vector grid onto pixels with the
  producer's block edge instead of `ceil(size / cells)`, which disagreed with
  `pelorus_mc` for block sizes such as 31 (BUG-015). On small frames where
  several block sizes fit the grid, the `pelorus_mc` default (16) is assumed with
  a one-time warning; a grid that fits nothing, or whose ambiguity excludes the
  default, disables motion compensation for that frame with a one-time warning. See [the interop guide](docs/api/interop-abi.md).
