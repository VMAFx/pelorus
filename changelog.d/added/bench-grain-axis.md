- **`run-bench.py --grain N`** — a controlled grain axis. Overlays seeded noise on a
  **real** source and scores both arms against the clean original (implying
  `--clean-reference`), so a denoise/deband arm is not penalised for removing the
  impairment under test. Verified monotonic and reproducible: grain 0→20 moves
  `grain_sigma` 0.0124→0.0182 with `grain_flat` 0.600→0.171, and the same seed yields
  byte-identical output. This exists because there is no grainy clip to pin — measured,
  real Blu-ray scans come in at `grain_sigma` 0.0047–0.0101 (lower than clean animation's
  0.0131), the public 4K sets are distribution re-encodes with the grain compressed out,
  and any downscale to the corpus resolution attenuates what survives. The pre-existing
  `--synth noise` is not a substitute: it noises a flat grey plate, which starves the
  grain estimator. ADR-0142 is the consumer.
