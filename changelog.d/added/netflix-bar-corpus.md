- **Benchmark corpus gains `netflix-bar`** — Netflix's Chimera *BarScene*, donated to the
  Xiph.Org derf collection. Real camera content with measurably more of what Pelorus
  targets than the clean-animation `bbb` pin: **2.4x the texture, 3.1x the variance and
  1.7x the banding** at the identical 640x360/48-frame workload. Also a materially more
  durable host than the single third party `bbb` now depends on. It is deliberately
  documented as a *complexity* entry, not a grain one: the pinned `.webm` is a VP9
  distribution copy whose encode removed the grain (`grain_sigma` 0.0124 vs `bbb`'s
  0.0131), and the ungraded `.y4m` is 29.6 GB. `docs/metrics/grain_estimate.md` now also
  documents that `grain_sigma` must be read together with `grain_flat` — a starved
  estimate reads as "clean", which would invert the `tune=auto` routing decision (ADR-0142).
