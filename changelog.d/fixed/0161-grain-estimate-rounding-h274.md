- Fixed `vf_pelorus_grain_estimate_vulkan`'s lag-1 correlation, which was
  pinned at −1.0 for every input, so the AV1 parameters always carried
  `ar_coeffs_y[0] = −64` (BUG-010). The lag-1 sum used a bias of 1.0 at a scale
  of 2000 and truncated each add, and that bias swamped the product of real
  grain residuals. Every accumulator add is now rounded to the nearest unit,
  and the lag-1 product is stored with a bias of `0.08²` at a scale of 150000.
  On Arc, NVIDIA and RADV the lag-1 coefficient is now within 0.004 of a float
  reference (white grain gives about −0.167 and `ar_coeffs_y[0]` about −11), and
  the per-band RMS within 1% (it read up to 5% low for light grain). AV1
  estimates and `lavfi.pelorus.grain_sigma` therefore change slightly for
  every input. The `grain-accumulator-bounds` fast test now also checks the
  rounding and the precision against a float reference, and `--self-test`
  plants nine defects
  ([ADR-0161](docs/adr/0161-grain-estimate-rounding-and-h274-mapping.md)).
- Fixed the estimator's H.274 output, which FFmpeg could not synthesize
  (BUG-029). `PEL_SEC_FILMGRAIN` now carries `h274_model_id` 0 and
  `h274_log2_scale` 2 (the SMPTE RDD 5 profile and the `pelorus_fgs` defaults)
  instead of 1 and 8. With `model=h274`, the filter also emits
  `lavfi.pelorus.h274_model_id`, `h274_log2_scale`, `h274_scale_y`,
  `h274_cutoff_h` and `h274_cutoff_v`. Their scale and cutoff come from a table
  calibrated against FFmpeg's `libavcodec/h274.c`
  (`scripts/gen-h274-grain-calibration.py`). Through `pelorus_fgs` with
  explicit cutoffs and FFmpeg's HEVC decoder, white grain of one to three code
  values comes back within 4% of its source standard deviation. The new
  `lavfi.pelorus.grain_lag1` key exposes the measured residual correlation
  ([ADR-0161](docs/adr/0161-grain-estimate-rounding-and-h274-mapping.md)).
