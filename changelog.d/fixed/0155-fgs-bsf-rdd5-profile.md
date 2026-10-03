- Fixed `pelorus_fgs` so its default H.274 film-grain SEI is actually
  synthesized. The old defaults (`model_id=1`, `log2_scale=8`) were ignored by
  FFmpeg's HEVC decoder and, with model 0, attenuated the grain below one code
  value; the defaults are now `model_id=0` and `log2_scale=2`. Model 0 now writes
  explicit cutoff frequencies through the new `cutoff_h`/`cutoff_v` options, and
  Cb/Cr get their own `intensity_low_c`/`intensity_high_c` interval instead of
  the luma one (BUG-001, BUG-012). An empty intensity interval, or a
  `scale_y`/`scale_c` beyond the H.274 range for the model and bit depth (for
  example 200 with `model_id=1` on 8-bit video), now fails at init with a clear
  error and a non-zero ffmpeg exit. Previously the first case inserted an SEI
  that matched no sample, and the second dropped every packet and still exited 0
  with an empty file (BUG-002, BUG-024). Existing command lines that set
  `model_id=1` or `log2_scale=8` keep working, but now log why FFmpeg will not
  show their grain ([ADR-0155](docs/adr/0155-fgs-bsf-rdd5-profile.md)).
