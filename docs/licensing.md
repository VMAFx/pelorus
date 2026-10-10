<!-- markdownlint-disable MD013 -->
# Licensing

Pelorus is licensed under the **EUPL-1.2**. The files that the patch stack adds
to FFmpeg stay under FFmpeg's **LGPL-2.1-or-later**, and a few vendored files
keep their own terms ([ADR-0171](adr/0171-eupl-relicense.md)). Lusoris is the
copyright holder of everything Pelorus wrote.

[`REUSE.toml`](../REUSE.toml) records the copyright and licence of every file
in the repository, following the [REUSE specification 3.3](https://reuse.software/spec-3.3/).
A file's own `SPDX-License-Identifier` line, where it has one, says the same.

| Files | Licence | Rights holder |
| --- | --- | --- |
| Everything not listed below: `libpelorus/`, `tools/`, `scripts/`, `docs/`, the build files, CI, the tests and scripts under `ffmpeg-patches/` | EUPL-1.2 ([`LICENSE`](../LICENSE)) | 2026 Lusoris |
| Sources the patch stack adds to FFmpeg: `ffmpeg-patches/files/*.c`, `files/*.h`, `files/vulkan/*.comp.glsl`, and `ffmpeg-patches/test/qsv-roi-regression.c`, which is built inside FFmpeg's `libavcodec/tests/` | LGPL-2.1-or-later ([`LICENSES/LGPL-2.1-or-later.txt`](../LICENSES/LGPL-2.1-or-later.txt)) | 2026 Lusoris |
| Of those, the nine Vulkan filter host files `ffmpeg-patches/files/vf_pelorus_*_vulkan.c`, which follow FFmpeg's model filters `vf_gblur_vulkan.c`, `vf_nlmeans_vulkan.c` and `vf_scdet_vulkan.c` | LGPL-2.1-or-later | 2000-2026 the FFmpeg developers, 2026 Lusoris |
| The patches: `ffmpeg-patches/0*.patch` and `ffmpeg-patches/files/*.patch` | LGPL-2.1-or-later | 2000-2026 the FFmpeg developers, 2026 Lusoris |
| `.claude/skills/superpowers/`, vendored from [obra/superpowers](https://github.com/obra/superpowers) (its `NOTICE.md` is Pelorus's, EUPL-1.2) | MIT ([`LICENSE`](../.claude/skills/superpowers/LICENSE)) | 2025 Jesse Vincent |
| `tools/figures/third_party/interfig/upstream/`, Praetor's vendored interfig | MIT | 2025 Vectorize AI, Inc. |
| `tools/figures/dist/`, the figure player (Praetor's loader with interfig and React) | EUPL-1.2 AND MIT ([`THIRD-PARTY-LICENSES.txt`](../tools/figures/dist/THIRD-PARTY-LICENSES.txt)) | Lusoris, Vectorize AI, Meta Platforms |
| `.devcontainer/praetor-source.*.b64`, the Praetor source the dev container builds | EUPL-1.2 AND MIT | Lusoris, Vectorize AI, Meta Platforms |

[`LICENSES/`](../LICENSES/) holds the text of each licence named here. The root
[`LICENSE`](../LICENSE) is the EUPL-1.2 text, the same bytes as
`LICENSES/EUPL-1.2.txt`.

Releases up to `v0.2.2` were published under BSD-2-Clause-Patent for
`libpelorus` ([ADR-0105](adr/0105-libpelorus-license.md)). Those copies keep
those terms; the EUPL-1.2 applies from `v0.3.0`.

## Checking the licence of a file

`reuse lint` checks that every file has copyright and licence information and
that `LICENSES/` holds exactly the texts the tree uses. It runs in the
`reuse-lint` pre-commit job, in the `REUSE lint` CI check
(`.github/workflows/reuse.yml`), and by hand:

```bash
python3 -m venv /tmp/reuse && /tmp/reuse/bin/pip install 'reuse>=6,<7'
/tmp/reuse/bin/reuse lint
```

It ends with `Congratulations! Your project is compliant with version 3.3 of
the REUSE Specification :-)` and exit status 0. To see what applies to one
file, read its entry in the SPDX document `reuse spdx` writes:

```bash
/tmp/reuse/bin/reuse spdx | grep -A8 'FileName: ./libpelorus/src/interop.c'
```

`praetorctl audit` adds two checks: every `REUSE.toml` table takes effect
(REUSE applies only the last table that matches a file, so the whole-tree
default comes first), and the root holds one licence file with the declared
licence's text:

```text
[PASS] REUSE.toml annotation order: no path of its 9 annotations is matched whole by the annotations after it.
[PASS] root licence: LICENSE holds the text of LICENSES/EUPL-1.2.txt; no other root file named like a licence.
```

## Using libpelorus in another product

> This section repeats what the licence texts say and how the European
> Commission reads the EUPL. It is not legal advice; for a decision about your
> product, ask a lawyer.

`libpelorus` and its public headers (`pelorus/interop.h`, `pelorus.h`,
`deband.h`, `denoise.h`) are EUPL-1.2. There is no dual or commercial licence.

| What you do | What the EUPL-1.2 asks |
| --- | --- |
| Distribute `libpelorus` or `pelorus_qp_report`, changed or not, inside your product | Keep every copyright notice and the licence notice, and include a copy of the licence (Article 5, "Attribution right"). Provide the source, or name a repository where it is "easily and freely available", for as long as you distribute (Article 5, "Provision of Source Code"). This holds for an unchanged copy too |
| Link your program against `libpelorus`, statically or dynamically | The licence leaves what counts as a derivative work to the applicable copyright law (Article 1, "Derivative Works"). The Commission reads static and dynamic linking as creating no condition on the other program. There is no case law on it |
| Change `libpelorus` and distribute the result | Distribute it under the EUPL-1.2, mark it as modified with the date, and provide its source (Article 5). Combined with a work under a licence in the EUPL's appendix (GPL v. 2 and v. 3, LGPL v. 2.1 and v. 3, MPL v. 2 and others), you may distribute the combination under that licence instead ("Compatibility clause") |
| Run it on a server for others | "Distribution or Communication" includes "providing access to its essential functionalities" (Article 1), so the source obligation applies to that service too |
| Patents | The licensor grants use of its patents "to the extent necessary to make use of the rights granted" (Article 2) |

vmafx vendors ten `libpelorus` interop files byte for byte
([ADR-0103](adr/0103-interop-sidedata-abi.md)). Copies vendored from `v0.3.0`
on are EUPL-1.2, which is also vmafx's licence for its own code.

## The FFmpeg boundary

The patch stack turns Pelorus into part of an FFmpeg build. Three kinds of
code meet there:

- FFmpeg's own sources, LGPL-2.1-or-later (some parts GPL when FFmpeg is
  configured with `--enable-gpl`), with the shared FFmpeg fix series applied
  first: patches to FFmpeg's own files, LGPL-2.1-or-later, published by
  [VMAFx/ffmpeg-patches](https://github.com/VMAFx/ffmpeg-patches) and not part
  of this repository or its release archive
  ([ADR-0185](adr/0185-shared-ffmpeg-fix-series.md));
- the Pelorus files the stack adds to FFmpeg, LGPL-2.1-or-later like the tree
  they join;
- `libpelorus`, EUPL-1.2, which the filters `pelorus_analyze_vulkan`,
  `pelorus_deband_vulkan`, `pelorus_denoise_vulkan`,
  `pelorus_grain_estimate_vulkan`, `pelorus_mc_vulkan` and `pelorus_scenecut`
  link through `pkg-config libpelorus` and whose `pelorus/interop.h` they
  include.

An FFmpeg binary built this way combines EUPL-1.2 and LGPL or GPL code. The
EUPL's compatibility clause (Article 5) lets the combination be distributed
under LGPL v. 2.1 or GPL v. 2 or v. 3, which its appendix lists; where the
obligations conflict, the compatible licence's prevail. Whoever distributes
such a binary gives its recipients the corresponding source of all three
parts.

Pelorus publishes no FFmpeg binary today: a release carries the patch stack
(`pelorus-ffmpeg-patches-<tag>.tar.gz`) with `LICENSES/LGPL-2.1-or-later.txt`,
`LICENSES/EUPL-1.2.txt` and a `NOTICE` that maps paths to licences and names the
release commit, the FFmpeg base commit and the shared fix series release the
stack needs first (`scripts/release/write-patch-notice.sh`). A Pelorus-enabled FFmpeg the project publishes
later follows vmafx's rule ([ADR-0171](adr/0171-eupl-relicense.md)):

- configured with `--enable-gpl --enable-version3`, with x264 and x265 among
  its libraries; FFmpeg's `configure` reports the result as "GPL version 3 or
  later";
- never configured with `--enable-nonfree`, so never with the libraries
  `configure` lists as nonfree, among them `cuda_nvcc`, `cuda_sdk`,
  `libfdk_aac` and `decklink`: such a build is "nonfree and unredistributable"
  (the Pelorus filters are Vulkan and need none of them);
- distributed under GPL-3.0-or-later, with a `-source` image next to it that
  holds the corresponding source of FFmpeg, its libraries and `libpelorus`.

The `libpelorus` part stays EUPL-1.2 on its own; the compatibility clause is
what lets it join the GPL-3.0-or-later build.

The tester image ([ADR-0173](adr/0173-tester-programme.md)) records the licence
of every file it ships in `tools/tester/licensing.json`; the image build fails on
a file, package or licence that record does not cover and writes
`/usr/share/licenses/pelorus-tester/THIRD_PARTY_NOTICES.txt` into the image
([ADR-0178](adr/0178-tester-artifact-licence-record.md),
[the runbook](development/tester-image.md#licence-record)).
The public `pelorus-dev` image does the same with `.devcontainer/base/licensing.json`
and `/usr/share/licenses/pelorus-dev/THIRD_PARTY_NOTICES.txt`
([ADR-0179](adr/0179-dev-image-licence-record.md),
[the build guide](development/build.md#licence-record-and-notices)).

## Contributing

A contribution falls under the licence of the file it changes, and a new file
under the EUPL-1.2 unless it joins the FFmpeg tree. Every commit carries a
Developer Certificate of Origin sign-off; there is no contributor licence
agreement ([CONTRIBUTING.md](../CONTRIBUTING.md#license)).

## Sources

Fetched or read on 2026-10-08:

- EUPL-1.2, the repository copy [`LICENSES/EUPL-1.2.txt`](../LICENSES/EUPL-1.2.txt),
  the European Commission's text: Article 1 (definitions), Article 2 (rights
  and patents), Article 5 (attribution, copyleft, compatibility and source
  clauses) and the Appendix of compatible licences.
- The Commission's EUPL FAQ,
  <https://interoperable-europe.ec.europa.eu/collection/eupl/faqs>, on linking:
  "The EUPL is not viral", and "static and dynamic linking can be implemented
  with other programs without barriers or conditions".
- REUSE specification 3.3, <https://reuse.software/spec-3.3/>; `reuse` 6.2.0.
- Praetor `docs/guides/licensing-gates.md` at the pinned engine `492a00f9`.
