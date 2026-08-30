- **Benchmark corpus `bbb` re-pinned — this resets its baseline.** The pinned
  `download.blender.org` clip now 404s upstream and no mirror serves the same bytes, so
  `scripts/bench/fetch-corpus.sh` could not materialise it on a machine without a warm
  cache. Re-pinned to a Big Buck Bunny encode that is still served, keeping the same
  workload shape (640x360, yuv420p, 48 frames — an identical 16,588,800-byte decoded
  `.yuv`), so the harness runs exactly the same amount of work. The pixels differ, so
  **absolute `bbb` numbers recorded before 2026-08-30 are not comparable with ones
  recorded after**; `docs/development/bench-results.md` marks the boundary.
  `synth-banding` is lavfi-generated and unaffected.
