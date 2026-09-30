<!-- markdownlint-disable MD013 MD060 -->
# Renovate's `actions/setup-go` identity and `managerFilePatterns` matching

**Date:** 2026-09-30

**Decision:** [ADR-0151](../adr/0151-renovate-mirrors-checker-toolchain-pins.md)

**Scope:** Renovate 44.x (renovatebot/renovate `main` at
`70e3d9d3ca5b140e4e97e255026a10457ab1d2b8`, latest release 44.123.0; the
repo-level section at `13aad8030aa865b3a0bcf7067eb61c3c558f4ded`);
`renovate.json` and `scripts/check-build-config.py` on PR #59 and its
follow-up

## Finding

Renovate's github-actions manager does not treat `go-version` as a plain
string. `actions/setup-go` has an entry in the manager's known-action table
(`lib/modules/manager/github-actions/known-actions/github-releases.ts`):

| Field | Value |
|---|---|
| `depName` | `go` |
| `datasource` | `github-releases` |
| `packageName` | `actions/go-versions` |
| `versioning` | `npm` |
| `extractVersion` | `actionsVersionsExtractVersion` = `^(?<version>\d+\.\d+\.\d+)(-\d+)?$` (`known-actions/utils.ts`) |
| input | `go-version` |

`WillAbides/setup-go-faster` shares that identity. The branch
`renovate/go-1.x` comes from the dependency name and the new major version.
A regex `customManager` with the same `depNameTemplate`,
`packageNameTemplate`, `datasourceTemplate`, `versioningTemplate` and
`extractVersionTemplate` therefore yields the same dependency. The update to it
goes into the same branch as the workflow edit. A manager that differs in any of
these fields, for example the `golang-version` datasource or `semver`
versioning, yields a separate update. That update lands in a different branch,
or in none.

The actions-versions tags carry a build suffix (`1.27.1-33583469715`).
`extractVersion` strips it, and `npm` versioning treats `1.27.x` as a range that
the next minor replaces with `1.28.x`.

## How Renovate selects files

`lib/workers/repository/extract/file-match.ts` (`getMatchingFiles`) runs
`matchRegexOrGlob` once per `managerFilePatterns` entry and takes the union.
`lib/util/string-match.ts` defines one entry:

- the literal `*` matches every file;
- an entry matching `^!?/` and `/i?$` is a regex (`i` flag when it ends in
  `i`, negated when it starts with `!`). If the regex does not compile under
  Renovate's RE2 wrapper, the entry falls through to the glob path;
- anything else is a minimatch 10.2.6 glob with `{ dot: true, nocase: true }`.
  In such a glob, `*` stays within one path segment and a whole `**` segment
  spans directories. `{a,b}` expands, a leading `!` negates, and a leading `#`
  never matches.

The checker first shipped a `startswith("/") and endswith("/")` test with an
`fnmatch` fallback. That test miscounted `/re/i` and case-insensitive globs,
and a Python-only regex error raised a traceback. It now implements the rules
above. Glob syntax outside the modelled subset (character classes, extglobs,
escapes, `{x..y}` ranges, braces that span `/`, `.`/`..` segments) raises a
checker error. It is never approximated.

## Repo-level settings that reach one site

A post-merge review of PR #59 found that the checker validated only
`customManagers`. Other `renovate.json` settings could still split the bump
while `build-config-sync` stayed green. Renovate `main` at
`13aad8030aa865b3a0bcf7067eb61c3c558f4ded` handles them as follows:

| Setting | Where Renovate applies it | How it splits the Go bump |
|---|---|---|
| `includePaths` | `file-match.ts` `getIncludedFiles`, before `managerFilePatterns`: keep a file if it equals an entry or matches `minimatch(entry, { dot: true })` (case-sensitive, no regex form) | leaves out `ci.yml` or the checker |
| `ignorePaths` | `file-match.ts` `filterIgnoredFiles`: drop a file if `file.includes(entry)` or the same minimatch matches | `scripts/**`, `.github/workflows` or even `.py` drops one site |
| `enabledManagers` | `modules/manager/index.ts` `getEnabledManagersList`: a manager runs if the list is empty or names `m` or `custom.m` | `["github-actions"]` drops the regex manager |
| top-level `github-actions` / `regex` block | `config/index.ts` `getManagerConfig` merges `config[manager]` into that manager only | `{"enabled": false}`, a `groupName`, nested `packageRules` |
| `packageRules` | `util/package-rules/*`: every matcher present must pass (AND); name matchers use `matchRegexOrGlobList` | `matchManagers`, `matchFileNames`, `matchDepTypes` (`uses-with` against none) and `matchCategories` (`ci` against `custom`) see different inputs at the two occurrences. `matchRegistryUrls` and `matchJsonata` are not modelled and count as site matchers |
| regex manager options | `modules/manager/custom/regex` | `currentValueTemplate`, `autoReplaceStringTemplate`, `matchStringsStrategy`, `depTypeTemplate` change the checker occurrence only |

`matchRegexOrGlobList` returns false for an empty list. Otherwise some positive
entry must match (when there is one) and every `!` entry must hold. So
`"matchDepNames": ["!go"]` rules a rule out of `go`. The two occurrences share
`depName` `go`, `packageName` `actions/go-versions`, datasource
`github-releases`, the current and new values, repository and branch. They
differ in manager (`github-actions` against `custom.regex`), `packageFile`,
`depType` and categories.

Options considered for the finding:

| Option | Pros | Cons | Chosen |
|---|---|---|---|
| Soften the docs to what the checker enforces | No new code | Leaves the gaps; the next bump finds them | No |
| Model each setting above and reject the ones that reach one site (conservative on anything unmodelled) | Closes every gap visible in `renovate.json`; site-neutral rules still pass | More Renovate semantics to keep in step | **Yes** |
| Forbid `includePaths`, `ignorePaths`, `enabledManagers` and site matchers outright | Simplest code | Rejects harmless configs, such as the `:ignoreModulesAndTests` globs | No |
| Expand `extends` presets too | Full coverage | Needs Renovate's preset resolver and network | No: the gap is documented instead |

## Verification

- For the repo-level checks, a second harness transcribed
  `matchRegexOrGlobList`, `getIncludedFiles` and `filterIgnoredFiles` over
  real `minimatch@10.2.6`. It compared 39 name-matcher cases, 15
  `includePaths` lists and 20 `ignorePaths` lists (the `:ignoreModulesAndTests`
  globs among them) with the checker's model. It also compared which sites
  `validate_go_site_parity` flags. All 109 comparisons agreed.
- A harness ran real `minimatch@10.2.6` plus Renovate's `string-match.ts` logic
  (JS `RegExp` standing in for RE2) and compared the results with
  `renovate_file_pattern_matches`. It used 40 patterns against 8 repo paths:
  all 320 pairs agreed, and the one deliberately unmodelled pattern
  (`{**/*.py,x}`) was rejected.
- The implementer's run for PR #59 found that
  `renovate-config-validator` (renovate 44.123.0) accepts `renovate.json`. A
  `renovate --platform=local --dry-run=lookup` extraction produced the same `go`
  / `actions/go-versions` / `github-releases` / `npm` dependency at `1.27.x`
  from both `ci.yml` and the checker. The lookup stage did not run because no
  token was available.
- A local simulation of a 1.28 bump fails the checker when only `ci.yml`
  changes, and passes when both sites change.

## Open questions

- Only Renovate's next Go minor (1.28) can show on the hosted app that both
  sites land in one PR. The dry run proves the extraction, not the grouping.
- If Renovate changes the setup-go known-action config, the checker's
  `GO_RENOVATE_TEMPLATES` must follow. The checker has no way to detect that
  change.
- Presets named in `extends` are not expanded. A grep of the preset sources
  that `config:recommended` extends, at the commit above, found no rule that
  both matches on the site and can reach `go`. Its github-actions rules are
  limited by package name, by datasource, or by depType `action`/`workflow`,
  and the `:ignoreModulesAndTests` globs miss both files. This was a spot
  check, not an expansion. A new or changed preset could still split the
  bump, and the next Go bump would show that as a red `build-config-sync`.
