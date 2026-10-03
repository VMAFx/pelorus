- Re-pinned the Praetor governance engine from `25451d88` to `bf815ba5`. The
  engine now scans shell scripts, so the HISS baseline records 37 more findings
  in unchanged scripts (62 → 99) until a follow-up retires them. The locked
  documentation gate's npm dependencies now audit clean (previously eight
  advisories, six high)
  ([ADR-0154](docs/adr/0154-praetor-engine-repin-bf815ba5.md)).
