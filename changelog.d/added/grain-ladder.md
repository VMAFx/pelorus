- **Grain ladder** ([docs/development/grain-ladder.md](docs/development/grain-ladder.md)) —
  profiles `--grain N` against 86 real titles (56 films + 30 TV series, stratified by
  decade) so the value can be read as "about the grain of X", and marks where the
  measurement stops being trustworthy. Findings: real-content `grain_sigma` runs
  0.0011–0.0265 (median 0.0067); **grain collapses in the 2020s** (film 0.0022, TV 0.0028,
  vs ~0.008 for the 1960s–80s); **TV is grainier than film from the 1990s on** (1990s TV
  0.0105 vs film 0.0064); and **downscaling to the 640x360 corpus halves the interquartile
  range**, so benchmarking there under-detects grain differences. Recommends `netflix-bar`
  as the grain base with `--grain <= 16` — `bbb` starts at 33% flat coverage and its axis
  goes non-monotonic past 16 (sigma peaks at 0.0244 then falls). Also records that
  `grain_sigma` measures high-frequency residual in flat areas, of which codec noise is a
  contributor alongside grain, so cross-source absolute comparison needs comparable encodes.
