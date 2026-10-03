- Fixed the denoise `meta=1` residual statistics and the tile=0/tile=1 identity
  (BUG-016, BUG-027). `noise_sigma_estimate` read 0 on clean content because the
  squared residual was truncated at a 1e3 fixed-point scale; each workgroup now
  reduces in shared memory and adds into 64-bit slices at scale 2^23 (bounds
  proven at DCI 8K by `scripts/test-denoise-accumulator-bounds.py`). On the RTX
  4090 `tile=1` differed from `tile=0` by one code value at 8/10/12-bit; the
  shader now pins every output-path operation (`precise`, hoisted divisors,
  explicit `fma`, spelled-out `mix`/`smoothstep`), and the format matrix asserts
  identity at 8/10/12-bit, semi-planar and `mc=1`. Output can move by one code
  value on a few pixels versus the previous release.
