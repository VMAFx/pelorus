<!-- markdownlint-disable MD013 MD060 -->
# Research 0236: licence audit of the tester image, the patch archive and `pelorus-dev`

Evidence for [ADR-0178](../adr/0178-tester-artifact-licence-record.md) and
issue [#236](https://github.com/VMAFx/pelorus/issues/236). Read 2026-10-09.
"Verified" means a command in this session returned it. The tester image row is
the image built from this branch; no image has been published yet.

## Summary

| Artifact | Verdict | Action |
| --- | --- | --- |
| Tester image (`tools/tester/Containerfile`, built from this branch) | Passes the new gate: 93 Debian packages, all from `main` with a copyright file; every other file owned by a component or an expiring ignore rule | Record and gate land with ADR-0178 |
| Patch archive `pelorus-ffmpeg-patches-v0.3.0.tar.gz` | Ships both licence texts, byte-identical to `LICENSES/`; ships no `NOTICE` | `scripts/release/write-patch-notice.sh` adds one from the next release |
| `ghcr.io/vmafx/pelorus-dev@sha256:33c934d3...` | Publicly pullable; Debian packages complete; three non-package tools ship without licence text and one is GPL without a source offer; the licence label is wrong | Follow-up issues (see below) |

## Method

1. Tester image: `docker build --target runtime` of the Containerfile, which
   runs the new `licence` stage. The gate's verdict is the build's exit status.
2. Patch archive: download the published v0.3.0 asset with `gh release
   download`, list it, compare the licence texts with `cmp`, read the licence
   header of every shipped source file.
3. `pelorus-dev`: pull the digest, query the registry anonymously, read the
   image configuration and history, list dpkg packages, their sections and
   copyright files, the apt sources, and the files outside dpkg. Read-only; the
   image was removed afterwards.

## Tester image

The record has 11 components (FFmpeg, the patches compiled into it, libpelorus,
the fixtures lock, the tester program, the licence texts, the notices, the
Debian source list, Vulkan loader and tools, the Python interpreter,
ca-certificates) and 7 ignore rules, each with a reason and the expiry
2027-04-09. Debian files are recorded by dpkg ownership. The gate refused every
planted defect in an image build whose `assembled` stage was replaced by a
modified copy (`docker build --build-context assembled=docker-image://...`):

| Planted in the assembled image | Build | Message |
| --- | --- | --- |
| `touch /opt/pelorus/lib/stray.so` | exit 1 | `unrecorded file: opt/pelorus/lib/stray.so` |
| a `.deb` with `Section: non-free/misc`, installed with `dpkg -i` | exit 1 | `archive component non-free is not permitted` |
| `rm /usr/share/doc/libvulkan1/copyright` | exit 1 | `libvulkan1/copyright is missing or empty` |
| a Debian library copied into `/opt/pelorus/lib` (`libz.so.1` as `libgomp.so.1`) | exit 1 | `unrecorded file: opt/pelorus/lib/libgomp.so.1` |

The unmodified image builds, passes the check again when run inside the built
image, and carries a 302-line notices file. The record-level defects (unknown
licence, non-redistributable licence, expired ignore rule, missing licence text,
stale notices, wrong SPDX header, fixture without attribution) are planted by
`licensing.py self-test`, which runs in every build.

Two facts from the audit are not licence findings:

- The first build installed `python3-minimal`, whose standard library lacks
  `shutil`, `http` and `urllib`; `pelorus_tester_stages.py` and
  `pelorus_tester_fixtures.py` import them, so `run --out /report` failed inside
  the image (`NameError: name 'os' is not defined` in `tempfile`). The
  Containerfile now installs `python3`, the record's `tester-python` component
  lists its packages, and the documented command runs in the image.
- The 15 to 30 minute FFmpeg build estimate of research 0172 is high. The
  pull request build of #252 took about 9 minutes on a hosted runner (maintainer
  state file, not in the tree). A cold local build with `-j4` on a loaded
  32-core host (load average 30 to 60) took 47 s for the toolchain install and
  151 s for FFmpeg's configure, `make` and `make install`.

## Patch archive

The archive lists `series.txt`, `README.md`, 18 numbered patches, 22 files under
`files/` and two licence texts: `LICENSES/LGPL-2.1-or-later.txt` and
`LICENSES/EUPL-1.2.txt`, both byte-identical to the repository's. The patches and
`files/` carry the FFmpeg LGPL header; `series.txt` and `README.md` fall under
the EUPL-1.2 default of `REUSE.toml`. The issue's suspicion that the archive
lacks licence texts is wrong; what it lacks is a statement of which text covers
which path, the release commit and the FFmpeg commit the stack applies to. The
SBOM and provenance exist (ADR-0169). The fix is a generated `NOTICE`
(`scripts/release/write-patch-notice.sh`) added to the archive and required by
`check-build-config.py`.

## `pelorus-dev`

Digest `sha256:33c934d3...`, `linux/amd64`, 2.2 GB, Ubuntu 26.04 base with the
dev-container features.

| Finding | Evidence | Risk |
| --- | --- | --- |
| Public | An anonymous token and manifest request to `ghcr.io` returned HTTP 200 | The image is redistribution, so its contents need the same record as the tester image |
| Label says `EUPL-1.2` | `org.opencontainers.image.licenses` | Wrong: the image holds GPL, MIT, Apache and Expat code; the label should not assert one licence |
| 353 dpkg packages, 0 without a copyright file | `dpkg-query` and `/usr/share/doc/<pkg>/copyright` | None |
| apt sources list `main universe restricted multiverse` | `/etc/apt/sources.list.d` | The lists are removed, so the archive component of an installed package cannot be read; no package name suggests a non-free one (no nvidia, cuda, x264, x265, fdk) |
| NVENC headers: `libffmpeg-nvenc-dev` 12.1.14.0 | `dpkg -S /usr/include/ffnvcodec/nvEncodeAPI.h`; copyright `License: Expat` | Low: these are the MIT nv-codec-headers, not NVIDIA's SDK |
| `git` 2.55.0 built from source in `/usr/local/bin` | shadows Debian's 2.53 in `/usr/bin`; GPL-2.0-only; no source offer in the image | Distributing GPL object code needs the source or an offer |
| `actionlint` 1.7.12, `lefthook` 2.1.14, `node` 24.21.0 installed outside dpkg | no licence text under `/usr/local` (the node doc directory holds only `gdbinit` and `lldb_commands.py`) | MIT notices are required with binary redistribution |
| No SBOM or licence record | none attached to the digest | Same gap the tester image closes |

## Follow-up issues (text for the maintainer)

1. `pelorus-dev`: ship licence texts and a source offer for the non-package
   tools. Install git from the distribution package or ship its source and a
   written offer; add MIT texts for actionlint, lefthook and node.
2. `pelorus-dev`: correct `org.opencontainers.image.licenses` and run the
   `licensing.py` gate with its own record, or make the package private.
3. Tester image: install `python3` (or drop the `shutil`, `tempfile` and
   `urllib` imports from the tester program) so that `run --out /report` works
   inside the image; update the `tester-python` component.
4. Record the measured hosted durations in `.workingdir/STATE.md` after the
   first PR, nightly and dispatch runs (#235).
