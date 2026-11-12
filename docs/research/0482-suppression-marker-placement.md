<!-- markdownlint-disable MD013 -->
# Where Semgrep honours a suppression marker

**Date:** 2026-11-02

**Decision:** [ADR-0241](../adr/0241-suppression-marker-binding.md).

**Scope:** Semgrep 1.182.1 (`semgrep --version`), local probe rules, `--metrics=off`, each source scanned once with markers honoured and once with `--disable-nosem`. A finding present in the second run and absent in the first is suppressed.

## Verdict

Semgrep binds a marker to the line where the finding starts, or the line before it. Which line that is depends on the rule: a call-level rule starts on the line of the call, while a taint rule whose sink is an argument starts on that argument's line. A static check cannot see a rule's granularity, so `scripts/check-suppression-markers.py` accepts any statement or expression start line and refuses lines that start no node; it also refuses formatter suppression comments (`fmt: skip/off/on`, `yapf: disable/enable`, `autopep8: off/on`; [ADR-0256](../adr/0256-mc-sad-gain-wall-time-threshold.md)).

## Measurements

| Placement | Rule kind | Suppressed |
| --- | --- | --- |
| Marker on the call line | call-level `subprocess.run(...)` | yes |
| Marker on the line before the call | call-level | yes |
| Bare `# nosemgrep` on the call line | call-level | yes, every rule on the line |
| Marker on an argument continuation | call-level | no |
| Marker on the closing bracket | call-level | no |
| Marker naming another rule id | call-level | no |
| Marker on the argument line | taint, sink focused on the first argument | yes |
| No marker on the argument line | taint, sink focused on the first argument | no |
| `# see the ADR # nosemgrep: <id> - <reason>` on the call line | call-level | yes |

The last row means a marker that follows prose in a comment, after its own `#`, is a live suppression. The check therefore validates it like any other marker (rule ids and reason) instead of skipping it. A marker word without its own `#` is refused.

## Limits

- An argument-line marker that names a call-level rule passes the check but suppresses nothing. Semgrep then still reports the finding, so the scan, not the check, catches it.
- Bandit `nosec` placement was not probed. Bandit is not installed in the build lanes.

## Evidence

Local, not tracked: `.workingdir/evidence/nosemgrep-binding/semgrep-placement-probe.log` (sha256 `40663c440dc8`) for the call-level rows, and `sourcery-probe.log` (sha256 `92500ee9dddc`) with `sourcery-probe-rules.yml` and `sourcery-probe-a.py` for the taint and prose rows.
