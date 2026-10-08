<!-- markdownlint-disable MD013 -->
# ADR-0168: Re-pin the Praetor engine to `492a00f9`

- **Status**: Proposed
- **Date**: 2026-10-08
- **Deciders**: Lusoris
- **Tags**: governance, ci, standards, build
- **Supersedes**: the engine pin of [ADR-0154](0154-praetor-engine-repin.md);
  the rest of ADR-0154 stays Accepted

## Context

[ADR-0154](0154-praetor-engine-repin.md) pinned the Praetor engine at
`0af07a733e6534269b435cea185da4d1df7aba0c` and is Accepted, so a new pin needs
its own record. On 2026-10-08 upstream `main` was 31 squash commits further, at
`492a00f930e1a2df557ffebb76564cfa65637b77`. Nineteen of the changelog
fragments in that range are marked breaking. These changes fail Pelorus's
audit at the new pin:

- every tracked nested `AGENTS.md` is linted in the Caveman register by
  `compile-context --verify` and the audit (`f588ca7b3`, pull request 805).
  All three of Pelorus's nested files fail it;
- the audit fails a hosted native build lane without warnings as errors
  (`a6f83b048`, pull request 868). For Meson it reads only the `meson setup`
  command line, not `default_options`, so Pelorus's `werror=true` in
  `meson.build` does not count. Five lanes fail;
- the audit fails a tracked C translation unit that no declared clang-tidy
  lane reads and no dated exception names (`04cc813ff`, pull request 796,
  Praetor issue 778). Pelorus declared no lane, so all 23 tracked units fail;
- HISS-11 measures the declared SLSA level, cosign signing, and SBOM against
  the workflows (`0e6a00f5a`, pull request 813). `release.yml` reaches level 0
  with neither;
- the audit fails a `.paperclip/rules.md` that is not the engine's rendering
  of `.paperclip/harness.json` (`26ac9f960`, pull request 808);
- the locked documentation gate assets changed (`2cd1f4077`, pull request 866;
  the `katex` override in `4c7d0694b`). `praetor-docs.yml` now fails its first
  step on a draft pull request by design (`f19a91943`, pull request 826).

One change removes a known failure. The audit used to require a
`.git/hooks/pre-commit` file even though the manifest declines `git-hooks`
([Praetor issue 175](https://github.com/cordanaLLM/praetor/issues/175)). Since
`06bae6671` (pull request 802) it reports the decline as a pass, so
Pelorus's documented local
workaround (`make -k verify-all`, "report local exit 1") is obsolete.

The measurements behind each item are in
[research digest 0168](../research/0168-praetor-repin-492a00f.md).

## Decision

Pin Praetor `492a00f930e1a2df557ffebb76564cfa65637b77` in
`.github/workflows/standards-gate.yml` and in every document that quotes the
current pin. Use ADR-0154's upgrade path: run `adopt --force
--lock-source-root=<praetor at the pin>`, then reconcile its output.

- Accept the refreshed documentation-gate assets, `praetor-docs.yml`, and the
  three register skills that adopt installs under `.agents/skills/` and
  projects to `.claude/skills/`.
- Keep the Pelorus `AGENTS.md` harness and `.zed/settings.json`, as
  ADR-0154 did. Delete the issue-175 workaround from `AGENTS.md`,
  `CONTRIBUTING.md`, `README.md`, and `docs/development/build.md`.
- Rewrite `ffmpeg-patches/AGENTS.md`, `libpelorus/AGENTS.md`, and
  `tools/AGENTS.md` in the Caveman register without dropping any rule, path,
  flag, or number.
- Pass `--werror` to every hosted `meson setup` (`ci.yml` `core` x2,
  `sanitizers`, `windows`; the release build, now in `release-build.yml` per
  [ADR-0169](0169-release-provenance-slsa3.md)). `scripts/check-build-config.py`
  pins the Windows job's `meson setup --werror build && ninja -C build`, and
  its self-test rejects the form without `--werror`.
- Declare one clang-tidy lane, `libpelorus`, as a tracked `files` list,
  `.config/clang-tidy/lane-files.txt`. It names every C unit the Meson build
  compiles: `libpelorus/src` (5), `libpelorus/test` (2), `tools` (1), and the
  two `ffmpeg-patches/test` programs Meson builds against a stub header
  (`mc_stats_test.c`, `pelorus_sidedata_test.c`). `make tidy` and the
  `ci.yml` `core` job run clang-tidy over exactly that list, so the lane the
  audit judges is the lane that runs. Before this ADR the run read only
  `libpelorus/src`.
- Give each of the other 13 C units under `ffmpeg-patches/` (11 filter and
  bitstream-filter sources in `files/`, `test/qsv-roi-regression.c`, and
  `test/static-libavfilter-consumer.c`) its own `clang-tidy-coverage`
  exception in `.standards.yaml`: one rule, one path, a reason, and expiry
  2027-01-06 (90 days). They build only inside an FFmpeg tree. The follow-up,
  VMAFx/pelorus#94, adds an FFmpeg-tree clang-tidy lane to the replay build.
- Implement HISS-11 instead of declaring a gap: a reusable release build that
  builds, attests SLSA provenance, writes an SBOM, and signs with cosign
  ([ADR-0169](0169-release-provenance-slsa3.md)).
- Keep Pelorus's operator-owned `.paperclip/harness.json` and replace
  `.paperclip/rules.md` with the engine's rendering of it (80-column wrapped
  list items, the engine's headings). The engine-regenerated pair would name
  `main` instead of `master`, drop Pelorus's invariants, and change the
  `register.sources` pin from 12 to 13 strings. The audit now reports the
  harness as operator-owned and `rules.md` as its rendering. Because the
  harness stores its push command as `agit_push_format`, the rendered heading
  reads "AGit Push Protocol" over a plain GitHub push.
- Keep the baseline. On `master` after pull request 76, it records 51
  findings, and `baseline --verify` passes at the new engine without a
  re-record.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Re-pin now and fix every Pelorus-side finding in the same change | The gate runs current checks; the issue-175 workaround goes | Larger change: release workflow, lane list, 13 exceptions | **Chosen** |
| Stay on `0af07a73` | No work now | Keeps issue 175's local failure; the gap to upstream keeps growing | Debt compounds, as ADR-0154 found |
| Rely on `werror=true` in `meson.build` `default_options` | No workflow edits | The gate does not read `default_options` (engine `docs/guides/build-warnings.md`, "What the gate does not read"), so five lanes still fail | Fails the gate |
| Declare a dated `HISS-10` exception per workflow | No workflow edits | An exception for a lane that already builds with warnings as errors, renewed every 90 days | Records a gap that does not exist |
| clang-tidy lane as `compile_database: build/compile_commands.json`, plus a `praetorctl ci tidy-coverage` step in `ci.yml` | The lane follows Meson automatically | The audit skips the check in every checkout without `build/`, which includes the hosted `Standards` job and the Git hooks; the fail-closed step needs a second engine install and pin in `ci.yml` | Fails closed in one CI step only |
| `files` lane that lists units without driving the run (dry-run form) | No `Makefile` or `ci.yml` change | The list and the real run can drift apart, so the lane can claim units clang-tidy never reads | Lane would overstate what runs |
| Exceptions for `libpelorus/test`, `tools`, and the two Meson-built `ffmpeg-patches` tests instead of linting them | No new lint scope | They are Meson units with a compile database entry and pass the profile today; an exception would hide them for 90 days at a time | No reason to exclude them |
| One `glob` exception for `ffmpeg-patches/**` | One entry | Also excuses any future unit there, including Meson-built ones; the project rule is one file, one rule, one reason, one expiry per exception | Too broad |
| An FFmpeg-tree clang-tidy lane now | No exceptions | Needs the configured FFmpeg tree of the replay build and its own compile database; a separate change | Follow-up VMAFx/pelorus#94 |
| Take the engine-regenerated Paperclip pair | No hand rendering | Names `main` on a `master` repository (Praetor issue 71), replaces the Pelorus invariants with generic text, and moves the `register.sources` pin | Loses Pelorus rules |

## Consequences

- **Positive**: the hosted gate runs the current engine. Every hosted Meson lane
  is checked for warnings as errors, and clang-tidy now reads the test and tool
  units as well as the library. A new Meson unit fails the audit until it is
  added to the lane list. The local audit no longer fails on the declined
  hooks.
- **Negative**: 13 dated exceptions expire on 2027-01-06 and fail the audit
  then unless VMAFx/pelorus#94 lands or they are renewed in a reviewed change.
  `rules.md` must be re-rendered by hand whenever `harness.json` changes.
  `praetor-docs.yml` fails on draft pull requests by design. HISS-18 reports six
  warnings for `ci.yml` and `standards-gate.yml` jobs that run on drafts.
  Exceptions expire after at most 90 days, and the engine has no renewal
  tooling yet (Praetor issue 798).
- **Neutral / follow-ups**: `ffmpeg-patches/test/build-and-run.sh` runs its own
  `meson setup` inside a script, which the HISS-10 gate does not read; it still
  builds with warnings as errors through `default_options`. clang-tidy 23.1.1
  reports six `bugprone-signed-bitwise` warnings across the lane (three in
  `libpelorus/src`) and one `readability-function-size` in
  `pelorus_sidedata_test.c`, none of them in `WarningsAsErrors`, so the run
  exits 0.
  `praetorctl paperclip harness` hardcodes `main` (reported on Praetor issue
  71).

## References

- [ADR-0145](0145-praetor-governance-adoption.md),
  [ADR-0154](0154-praetor-engine-repin.md), and
  [research digest 0168](../research/0168-praetor-repin-492a00f.md).
- Praetor `0af07a73..492a00f9` on `github.com/cordanaLLM/praetor`; engine guides
  `docs/guides/build-warnings.md`, `docs/guides/clang-tidy-coverage.md`,
  `docs/guides/releasing.md`, and `docs/guides/workflow-triggers.md` at the pin.
- Source: user direction 2026-10-08, paraphrased: re-pin the Praetor engine
  to the current upstream `main` tip.
