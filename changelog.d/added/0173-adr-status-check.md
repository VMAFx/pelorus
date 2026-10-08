- Added `scripts/adr/check-status.py`, a docs check that fails on a `Proposed`
  ADR without an `Implementation: pending (#N)` header line, so a merged but
  unflipped ADR cannot stay Proposed; it runs in `make docs-check` and the CI
  `docs` job.
