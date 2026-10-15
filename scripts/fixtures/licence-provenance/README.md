<!-- markdownlint-disable MD013 -->
# Licence Provenance Fixtures

Acceptance test cases for the Praetor licence provenance gate
([cordanaLLM/praetor#907](https://github.com/cordanaLLM/praetor/issues/907),
VMAFx/pelorus [#172](https://github.com/VMAFx/pelorus/issues/172),
[ADR-0195](../../docs/adr/0195-ai-tool-not-copyright-holder.md)).

## Purpose

These fixtures specify expected behaviour for the licence provenance gate
implemented upstream in the Praetor governance engine. The fixtures are stored
purely as **data** in `cases.json` rather than as live files in the repository
tree to prevent synthetic test cases with invalid headers or non-human holders
from failing repository-wide checks prior to gate adoption.

## Schema

`cases.json` contains a JSON array of test case objects. Each object provides:

- `id` (string): Unique identifier for the test case.
- `path` (string): Virtual file path in a simulated repository tree.
- `content` (string): Exact file contents to evaluate.
- `reuse_toml` (string): Simulated `REUSE.toml` contents for the test tree, or
  `"default"` indicating standard whole-tree EUPL-1.2 / 2026 Lusoris annotations.
- `commit_trailers` (array of strings, optional): Git commit message trailers
  associated with the change (e.g. `["Co-authored-by: ..."]`).
- `expect` (string): Expected verdict: `"pass"` or `"fail"`.
- `rule` (string): Name or identifier of the rule being exercised.
- `why` (string): Explanation of the scenario and the required outcome.

## Acceptance Cases

The dataset covers:

1. Files missing required licence headers where no `REUSE.toml` coverage exists (`fail`).
2. Licence mismatch between embedded file header (`SPDX-License-Identifier: MIT`)
   and `REUSE.toml` (`EUPL-1.2`) (`fail`).
3. Copyright holder mismatch between embedded file header (`2026 Lusoris`) and
   `REUSE.toml` (`fail`).
4. Rejection of AI tools or models named as copyright holders in
   `SPDX-FileCopyrightText` (`2026 Claude`) or `Copyright` notices (`GitHub Copilot`,
   `OpenAI Codex`) (`fail`).
5. Correct file headers matching `REUSE.toml` annotations (`pass`).
6. Files with no embedded header that are properly covered by `REUSE.toml`
   defaults (`pass`).
7. Commits carrying `Co-authored-by:` trailers with valid file headers (trailers
   must not be treated as copyright statements) (`pass`).
8. FFmpeg Vulkan host files with LGPL-2.1-or-later and FFmpeg developers
   co-notices matching a REUSE LGPL table (`pass`); the shipped shape, a header
   naming only Lusoris under an `override` annotation that adds the FFmpeg
   developers (`pass`); and an `override` annotation that lacks a holder the
   header names (`fail`).
9. FFmpeg Vulkan host files with conflicting EUPL-1.2 header against an LGPL
   table (`fail`).
10. Vendored files with third-party licences (e.g., MIT) inside declared
    `REUSE.toml` exception paths (`pass`) versus outside declared paths (`fail`).
