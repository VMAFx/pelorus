<!-- markdownlint-disable MD013 -->
# ADR-0243: Run pinned Ruff and zizmor in the local gate

- **Status**: Accepted
- **Date**: 2026-11-04
- **Deciders**: Lusoris
- **Tags**: testing, static-analysis, ci, supply-chain

## Context

Codacy runs Ruff, Pylint and Bandit on every pull request, but the local gate (`make verify-all`) ran no Python linter. A Python finding therefore surfaced only after a push, and each fix round cost a full CI run: on #494 two Codacy rounds (Ruff `SIM103`, Pylint `C0123`) followed the first push ([#495](https://github.com/VMAFx/pelorus/issues/495)). Workflow files had no security audit beyond actionlint's syntax check and `scripts/check-codeql-workflow.py`, which reads `codeql.yml` only.

The gate scripts are a constraint: they run as `python3 -I` with the standard library alone, so a linter cannot be imported by them.

## Decision

Ruff and zizmor run as pinned, separate binaries next to the stdlib-only scripts.

- **Pins.** `.config/python-lint/requirements.txt` is the `uv pip compile --generate-hashes` lock and the only place the versions live (`ruff==0.17.3`, `zizmor==1.32.0`); `requirements.in` names the two packages without versions. The lock is installed with `pip --require-hashes --only-binary=:all:`. Renovate's `pip_requirements` manager bumps the lock, as it did for `.config/reuse/requirements.txt` in #484; one package rule groups both tools into one pull request.
- **Wrapper.** `scripts/check-python-lint.sh` reads the versions from that lock, refuses any other binary version, then runs `ruff check` over the tree and `zizmor --offline` over the repository's workflows, actions and Dependabot files. `--install DIR` creates the pinned virtual environment; `--self-test` plants an `S110`, `SIM103`, `E721` and bare-`noqa` source, a template-injection workflow and a wrong-version binary, and requires each to fail and the clean variants to pass.
- **Rule set.** `ruff.toml` selects the 86 Ruff patterns Codacy enables on this repository, plus `SIM103` and `E721` (the #494 recurrences) and `PGH004` (no blanket `noqa`). `S101`, `S603` and `S607` stay with Bandit: Ruff does not read `# nosec`, and the reviewed Bandit markers of [ADR-0241](0241-suppression-marker-binding.md) already answer those findings.
- **Wiring.** `make verify-native` (and so `make verify-all`) runs the self-test and the scan through target `python-lint`, installing the pinned tools into `$(BUILD_DIR)/python-lint` on first use. The CI `docs` job installs them into `$RUNNER_TEMP` and runs the same two commands.
- **Exceptions.** A Ruff finding the code answers on purpose carries `# noqa: <code> - <reason>`. zizmor's `self-repository` audit is ignored for the two reusable-workflow calls in `release.yml` (`.github/zizmor.yml`): the `uses: $/...` form it asks for is refused by the pinned actionlint 1.7.12 (upstream actionlint issue 711, open on 2026-11-04). Revisit when an actionlint release accepts that form.

`scripts/check-python-empty-except.py` stays. Ruff `S110` flags only bare and `except Exception` handlers by default, and with `check-typed-exception` it flags every `except ...: pass`, including the 16 handlers in the tree that explain themselves with a comment; neither matches CodeQL `py/empty-except`, which the script implements ([research digest 0495](../research/0495-ruff-zizmor-gate.md)).

## Alternatives considered

| Option | Result | Why not chosen |
| --- | --- | --- |
| Ruff and zizmor as pinned separate binaries (chosen) | Codacy's Ruff findings and workflow audits fail before push; scripts stay stdlib-only | Needs a network install once per build tree |
| Import linters or `jsonschema` inside the gate scripts | One process | Breaks the `python3 -I` stdlib-only contract every script and the VMAFx mirror rely on |
| Run Pylint and Bandit locally as well | Full Codacy parity | Pylint's style checks and Bandit's `subprocess` findings overlap Ruff and the reviewed markers; more pins for little new coverage |
| Flake8 with plugins | Same rule families | Several pinned plugins instead of one binary; Codacy already runs Ruff, so its rule ids match |
| Keep only Codacy | No new dependency | The cost #495 measured: one CI run per finding round |

## Consequences

- Eight findings in the tree are fixed with this change: five `B904` (exception chaining) and three justified `noqa` markers (`S105` argv token, `S311` seeded simulation, `S310` https-only download).
- Pylint `C0123` (`type(x) is int`) has no Ruff equivalent in 0.17.3; it stays a Codacy-only finding.
- A Ruff or zizmor upgrade arrives as a Renovate pull request that the self-test and the full scan judge before merge.
- The first `make verify-native` in a new build tree needs network access to PyPI.

## References

- [#495](https://github.com/VMAFx/pelorus/issues/495): Ruff in the local gate.
- [Research digest 0495](../research/0495-ruff-zizmor-gate.md).
- [ADR-0241](0241-suppression-marker-binding.md): reviewed `nosec` and `nosemgrep` markers.
- Fleet decision `pelorus-gate-python-deps`, 2026-11-04 (paraphrased): pin Ruff 0.17.3 and zizmor 1.32.0 as separate binaries with a Renovate rule, keep the scripts stdlib-only, and prove the gate with a planted case that fails it.
