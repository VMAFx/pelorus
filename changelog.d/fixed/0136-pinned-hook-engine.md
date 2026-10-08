- Lefthook jobs and `make` now run the Praetor engine pinned at `PRAETOR_REF`
  (`scripts/praetor-engine.sh`, cached in `.workingdir/bin/`), not whichever
  `praetorctl` is first on `PATH`. A wrong-version engine, or no Go toolchain
  with an empty cache, fails closed; `PRAETORCTL=<path>` overrides
  ([#136](https://github.com/VMAFx/pelorus/issues/136)).
