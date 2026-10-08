<!-- markdownlint-disable MD013 -->
# ADR-0171: Pelorus is EUPL-1.2; the files that become part of FFmpeg stay LGPL-2.1-or-later

- **Status**: Accepted
- **Date**: 2026-10-08
- **Deciders**: Lusoris
- **Tags**: license, compliance, ffmpeg, interop, ci, docs
- **Supersedes**: [ADR-0105](0105-libpelorus-license.md)

## Context

[ADR-0105](0105-libpelorus-license.md) licensed `libpelorus` under
BSD-2-Clause-Patent for one reason: vmafx was BSD-2-Clause-Patent, and the
interop translation unit is vendored into vmafx verbatim, so a matching licence
avoided a licence seam. It kept FFmpeg's LGPL-2.1 header on the files under
`ffmpeg-patches/files/` that the patch stack adds to the FFmpeg tree.

That reason no longer holds. vmafx moved its own code to the EUPL-1.2
(vmafx ADR-1250, 2026-09-17) and its root `LICENSE` to the EUPL-1.2 text
(vmafx ADR-1699). It keeps its ten vendored Pelorus files on
BSD-2-Clause-Patent only because their terms are decided here (vmafx ADR-1250,
veto 4). Praetor, the governance engine Pelorus adopts, is EUPL-1.2 as well.
The maintainer directed on 2026-10-08 that Pelorus move to the EUPL-1.2 like
vmafx, before `v0.3.0`.

**Who holds the copyright.** A relicence needs every rights holder. Measured
on `origin/master` `420c19a` with `git log`:

- 104 commits. 100 are authored by `lusoris <lusoris@proton.me>` (91 under the
  name `lusoris`, 9 under `Kilian`, the same person and address).
- 4 are authored by `renovate[bot]`: `ce1a9a3` (the 6-line `renovate.json`
  onboarding), `a7c2b40` and `9724133` (`actions/checkout` version bumps, 4
  changed lines each) and `eb3b045`, the squash of pull request #59, in which
  the bot's own commit `4898eae` changed one line of `ci.yml` and the other two
  commits (`decf45a`, `6de5e1a`) are by `lusoris`.
- `Co-Authored-By` trailers name Claude models (94) and `renovate[bot]` (3).
  An AI tool holds no copyright in its output, and the trailer is an
  acknowledgement, not a rights claim (vmafx ADR-0861). A bot's version bump
  is mechanical.

No other person has contributed. Lusoris is the sole copyright holder of
everything Pelorus wrote, so the relicence is the holder's own decision and
needs nobody's consent.

**What is not Pelorus's own.** Pelorus is not a fork, so nothing is inherited.
It carries four kinds of other people's work:

- the patches change FFmpeg's own sources (`configure`, the Makefiles,
  `allfilters.c`, the NVENC, QSV, libaom, SVT-AV1 and Vulkan encoders,
  `hwcontext_vulkan.c`) and carry their context lines. At the pinned
  `n9.0.2` every one of them is LGPL-2.1-or-later (checked file by file at
  `946fcce0`; the Makefiles and `configure` carry no header and fall under
  FFmpeg's `LICENSE.md` default);
- `.claude/skills/superpowers/`, vendored unchanged from obra/superpowers
  (MIT, Jesse Vincent), with its `LICENSE`;
- Praetor's figure engine under `tools/figures/`: interfig (MIT, Vectorize
  AI) and a player bundle that includes React (MIT, Meta);
- the Praetor source archive in `.devcontainer/praetor-source.*.b64`, which
  holds Praetor's tree, the two items above included.

The FFmpeg-tree files (`vf_pelorus_*.c`, `h265_pelorus_fgs_bsf.c`, their
headers and the `vulkan/*.comp.glsl` shaders) are written by Lusoris. They
follow FFmpeg's Vulkan filter model (`vf_gblur_vulkan.c`,
`vf_nlmeans_vulkan.c`). Compared with seven of FFmpeg n9.0.2's Vulkan filters
and two of its bitstream filters, a file shares 4 to 57 of its 56 to 551
distinct lines longer than 25 characters, and the shared lines are FFmpeg's
API idiom (calls, barrier field names, `#include` lines), not copied logic.
Their headers name Lusoris alone.

## Decision

1. **Pelorus's own files are EUPL-1.2**, copyright `2026 Lusoris`:
   `libpelorus/` (sources, public headers, tests, reference shaders),
   `tools/`, `scripts/`, `docs/`, the build files, CI, the agent and editor
   configuration, the tests and scripts under `ffmpeg-patches/` that stay
   outside the FFmpeg tree, and the generated files (`CHANGELOG.md`, the
   agent context projections, `.standards-baseline.json`). The files Praetor
   writes carry the same terms, which are Praetor's own (EUPL-1.2, Lusoris).
2. **Files that become part of FFmpeg stay LGPL-2.1-or-later**, the set
   ADR-0105 put there: `ffmpeg-patches/files/*.c`, `files/*.h` and
   `files/vulkan/**`. `ffmpeg-patches/test/qsv-roi-regression.c` joins them:
   `qsv-roi-regression.sh` copies it into FFmpeg's `libavcodec/tests/`, and it
   `#include`s `qsvenc.c` into one translation unit.
3. **The patches are LGPL-2.1-or-later with FFmpeg's copyright next to
   Lusoris's**: the numbered `ffmpeg-patches/0*.patch` and the hunk files
   `ffmpeg-patches/files/*.patch`, as vmafx annotates its own patches.
4. **Third-party files keep their terms**: the superpowers skills MIT (Jesse
   Vincent), with `NOTICE.md` beside them Pelorus's own; interfig MIT; the
   figure player bundle `EUPL-1.2 AND MIT`; the Praetor source archive
   `EUPL-1.2 AND MIT`.
5. **The files record it the REUSE way.** `REUSE.toml` (REUSE 3.3) labels
   every file: one whole-tree default, then the narrower tables, in the order
   `praetorctl audit` checks. It has no top-level key other than `version`:
   the pinned engine refuses any other (Praetor issue 896, fixed after the
   pin). The 33 files whose header stated BSD-2-Clause-Patent now carry
   `SPDX-License-Identifier` lines instead, in vmafx's form for its own files
   (`qsv-roi-regression.c` names `LGPL-2.1-or-later`, the rest `EUPL-1.2`).
   The FFmpeg-tree files keep their LGPL header unchanged, so the generated
   patches do not change. `LICENSE` is the EUPL-1.2 text, byte for byte vmafx's
   `LICENSE` and `LICENSES/EUPL-1.2.txt`; `LICENSES/` holds the three texts the
   tree uses (EUPL-1.2, LGPL-2.1-or-later, MIT), copied from vmafx. There is no
   `NOTICE`: Pelorus inherits no upstream licence text.
6. **Gates.** `reuse lint` runs in the pre-commit hooks (the `reuse-lint` job
   Praetor renders) and in CI (`.github/workflows/reuse.yml`, Praetor's
   rendering), and the committed ruleset requires its `REUSE lint` check.
   `praetorctl audit` checks the `REUSE.toml` annotation order and that the
   root holds one `LICENSE` with the declared licence's text.
7. **Package metadata follows the files.** The Meson `project()` licence is
   `EUPL-1.2`; the dev container base image's
   `org.opencontainers.image.licenses` label is `EUPL-1.2`; the release archive
   of the patch stack carries `LICENSES/LGPL-2.1-or-later.txt` and
   `LICENSES/EUPL-1.2.txt` (its `README.md` and `series.txt` are EUPL-1.2).
8. **Contributions are inbound = outbound**, under the licence of the file
   they touch, with a Developer Certificate of Origin sign-off on every commit
   and no contributor licence agreement, as in vmafx.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Stay BSD-2-Clause-Patent | No change; the library stays permissive for closed pipelines | Its only reason, matching vmafx, is gone; the maintainer wants reciprocity on Pelorus's work | Rejected by the maintainer |
| Dual `BSD-2-Clause-Patent OR EUPL-1.2` | Downstreams choose | A permissive branch lets a downstream defeat the reciprocity; vmafx dropped an MIT alternative from 123 files for that reason (vmafx ADR-1250) | Same outcome as staying permissive |
| EUPL-1.2 for everything, the FFmpeg-tree files included | One licence | Files added to FFmpeg's LGPL tree would carry a licence FFmpeg does not use; a combined FFmpeg would rest on the EUPL's compatibility clause file by file; nothing could go upstream | Rejected: those files belong to FFmpeg's licence |
| **EUPL-1.2 default, LGPL-2.1-or-later for the files that become part of FFmpeg** | Matches vmafx and Praetor; the EUPL's Article 5 compatibility appendix lists LGPL v. 2.1 and GPL v. 2 and v. 3, so `libpelorus` combines with an FFmpeg build; the FFmpeg-tree files stay as FFmpeg expects | Two licences in one tree | **Chosen** |
| Annotate through `REUSE.toml` alone and leave the BSD+Patent headers | No file churn | 33 files would state a licence the metadata contradicts | Rejected: a header and its metadata must agree |
| A 17-line EUPL prose header in the shape of the old one | vmafx's re-vendor script accepts the header layout unchanged | Not vmafx's form for its own files; prose with no SPDX tag; vmafx's sync script needs edits for the new terms anyway | vmafx's SPDX form chosen; its exceptions file asks for the SPDX line |

## Consequences

- **Positive**: Pelorus, vmafx and Praetor share one licence; every file has
  machine-readable copyright and licence information, `reuse lint` and the
  audit keep it so; the interop files vmafx vendors no longer need a licence
  exception there.
- **Negative**: `libpelorus` is no longer permissive. Whoever distributes it,
  changed or not, keeps the notices, includes the licence and provides the
  source or a pointer to it (EUPL-1.2 Article 5); this is a breaking change for
  permissive consumers and goes into the `v0.3.0` release notes. Releases up to
  `v0.2.2` stay available under BSD-2-Clause-Patent, which cannot be withdrawn
  from them. Contributors accept the EUPL-1.2 for new work.
- **Neutral / follow-ups**:
  - The maintainer applies the ruleset with the new `REUSE lint` check with
    `praetorctl sync --remote` after merge ([build guide](../development/build.md#branch-ruleset)).
  - The EUPL-1.2's copyleft clause lets a licensee use a later EUPL version
    unless the work is "expressly distributed only under this version". Pelorus
    states `EUPL-1.2`, as vmafx does, and makes no "v. 1.2 only" statement; the
    maintainer decides whether to add one, together with vmafx.
  - A prebuilt Pelorus-enabled FFmpeg (1.0 scope) is built with
    `--enable-gpl --enable-version3`, never `--enable-nonfree`, and is
    distributed under GPL-3.0-or-later with its corresponding source, as vmafx
    does (vmafx ADR-1514). `libpelorus` joins that build through the EUPL's
    compatibility clause ([licensing guide](../licensing.md)).
  - vmafx#1455 plans `libgpudispatch` under the EUPL-1.2. Linking it from the
    LGPL-2.1-or-later filter files needs its own compatibility decision before
    Pelorus adopts it.
  - **Cross-repo, owned by vmafx** (not changed here), at its next re-vendor
    after this lands: the vendored files arrive EUPL-1.2, so
    - `REUSE.toml` (the `core/src/interop/pelorus*` table, line 328) changes
      from BSD-2-Clause-Patent to the vmafx default; that glob never covered
      the four headers under `core/include/libvmaf/pelorus/` or
      `core/test/test_pelorus_interop.c`;
    - the ten Pelorus entries of `.config/lint-exceptions.d/spdx.toml` (nine
      vendored files and the fixture) can go, as their reason text plans;
    - `docs/credits.yaml` (`id: pelorus`) names EUPL-1.2;
    - `scripts/sync-pelorus-interop.sh` assumes a 17-line licence header
      (`render_vendor`: `lines[17] != b"\n"` fails the render) and writes a
      BSD+Patent header into the fixture prefix; both need the new header;
    - vmafx ADR-1250's veto-4 row about the vendored mirror.

## References

- [ADR-0105](0105-libpelorus-license.md) (superseded), [ADR-0104](0104-ffmpeg-patch-stack.md),
  [ADR-0145](0145-praetor-governance-adoption.md), [ADR-0153](0153-praetor-full-adoption.md).
- vmafx ADR-0861 (AI tools in copyright lines), ADR-1250 (EUPL relicence),
  ADR-1474 (SPDX header form), ADR-1514 (FFmpeg build licence), ADR-1699
  (root licence files), `docs/licensing.md`, `REUSE.toml`.
- Praetor `docs/guides/licensing-gates.md` at `492a00f9` (annotation order,
  root licence, `reuse-lint` and `reuse.yml`); Praetor issue 896.
- EUPL-1.2 text, `LICENSES/EUPL-1.2.txt`: Article 5 (attribution, copyleft,
  compatibility and source clauses) and the Appendix of compatible licences.
- REUSE specification 3.3; `reuse lint` 6.2.0.
- Source: maintainer direction, 2026-10-08, relayed by the session coordinator:
  "relicense Pelorus to EUPL-1.2 like VMAFx/vmafx, ASAP, before v0.3.0. ADR-0105
  (libpelorus BSD-2-Clause-Patent; FFmpeg filter files LGPL-2.1) is superseded."
- Source: `Q-012` answer, 2026-10-08 (`.workingdir/QUESTIONS.md`): "licensing
  handled like vmafx (REUSE/notices/licence matrix)".
