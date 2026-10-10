<!-- markdownlint-disable MD013 -->
# Tester images (maintainer runbook)

Tester images are evidence for one commit on the default branch, never a release.
The rules are fixed by [ADR-0173](../adr/0173-tester-programme.md) and
[ADR-0176](../adr/0176-rc-process-and-tester-publish.md). Testers read
[the tester kit page](tester.md).

Status: `tools/tester/Containerfile` builds three kits
([ADR-0180](../adr/0180-tester-vendor-images.md)): `generic` (CPU), `nvidia`
(NVENC through the host driver, no NVIDIA file inside) and `intel` (Mesa ANV,
oneVPL, the Expat-licensed non-free media driver), each with a GPL-3.0-or-later FFmpeg, its
licence gates and a `-source` target. All three build locally; the NVIDIA kit
passes on an RTX 4090 and the Intel kit on an Arc A380 (`xe` kernel driver, no HuC), with `hevc_qsv` steering
through `intel-media-va-driver-non-free`
([research 0229](../research/0229-tester-vendor-images.md)). Nothing has been
dispatched end to end yet: the hosted build, the push and the SBOM attestation
verify line (predicate type `https://spdx.dev/Document/v2.3`) are unproven
until the first dispatch.

## Kits and targets

| Kit | Image target | `-source` target | Tag | Extra in the image |
| --- | --- | --- | --- | --- |
| `generic` | `final-generic` | `source-generic` | `tester-<YYYYMMDD>-<sha8>` | nothing |
| `nvidia` | `final-nvidia` | `source-nvidia` | `tester-nvidia-<YYYYMMDD>-<sha8>` | `libegl1` and `libxext6` (the host's Vulkan ICD needs both), `libdav1d7`; FFmpeg with `--enable-ffnvcodec --enable-nvenc --enable-libdav1d` against `nv-codec-headers` `n12.1.14.0`, whose notices ship and whose tree is in `-source` under `/source/kit/` |
| `intel` | `final-intel` | `source-intel` | `tester-intel-<YYYYMMDD>-<sha8>` | `mesa-vulkan-drivers`, `libvpl2`, `libmfx-gen1.2`, `libva2`, `libva-drm2`, `intel-media-va-driver-non-free` (Debian `non-free`; Expat, see below), `libdav1d7`; FFmpeg with `--enable-libvpl --enable-vaapi --enable-libdrm --disable-xlib --enable-libdav1d` |

Every `-source` tag is the image tag plus `-source`.

The Intel kit is the only package outside Debian `main`. `intel-media-va-driver-non-free`
is Expat-licensed (`debian/copyright` of `intel-media-driver-non-free`
25.2.3+ds1-1); Debian ships it in `non-free` because Intel's GPU kernels come
without source, not because of its licence ([ADR-0180](../adr/0180-tester-vendor-images.md)
decision 4a). Three places carry that one exception and nothing wider:

- `tools/tester/licensing.json`: the component `intel-media-driver` names
  `archive_component: non-free` and an `archive_reason`; `licensing.py check`
  admits exactly that component's packages from that area, and only with a
  redistributable licence. Another `non-free` or `contrib` package, or the
  driver in a kit that does not record it, fails.
- `tools/tester/Containerfile`: the `non-free` area is enabled only in
  `assembled-intel` and `debian-sources-intel` (the `-source` image fetches the
  driver's Debian source package with `deb-src` of `main non-free`).
- `scripts/check-build-config.py`: refuses the package name or the `non-free`
  area anywhere else, `contrib`, and any second non-free package.

Expat needs no source offer; the Debian source package is in `-source` anyway.
FFmpeg `--enable-nonfree` and the other nonfree components stay refused
(`check-ffmpeg-licence.sh`, `check-build-config.py`). The stages of one kit are
`build-<kit>` (FFmpeg through `tools/tester/build-ffmpeg.sh`), `assembled-<kit>`,
`licence-<kit>`, `final-<kit>`, `debian-sources-<kit>` and `source-<kit>`; the
`build` stage (toolchain, libpelorus, the patched FFmpeg tree) and the `base`
stage (runtime packages every kit needs) are shared.

Build one kit locally (FFmpeg compiles once per kit, a few minutes at `-j4`).
The image build applies the shared FFmpeg fix series before the Pelorus stack
([ADR-0185](../adr/0185-shared-ffmpeg-fix-series.md)), so fetch and verify its
release into the build context first; the Containerfile checks the tarball
against the sha256 pin again and fails without it. `.ffmpeg-series/` is
git-ignored and must not exist before the fetch:

```bash
. ./build-config.env
rm -rf .ffmpeg-series && scripts/fetch-ffmpeg-series.sh .ffmpeg-series
docker build -f tools/tester/Containerfile --target final-nvidia \
  --build-arg "FFMPEG_REMOTE=$FFMPEG_REMOTE" --build-arg "FFMPEG_COMMIT=$FFMPEG_COMMIT" \
  --build-arg "PELORUS_COMMIT=$(git rev-parse HEAD)" --build-arg MAKE_JOBS=4 \
  --tag pelorus-tester:nvidia .
```

## One-time setup (maintainer)

1. Make the package `ghcr.io/vmafx/pelorus` public after the first publish.
   GHCR creates a package on its first push, private, and its visibility
   cannot be changed through the API. The first dispatch therefore runs every
   step, then fails its last step, the anonymous pull of both digests. In the
   package settings, link it to `VMAFx/pelorus`, change its visibility to
   public, and run the workflow again.
2. Create the GitHub environment `tester-publish`: deployment branches limited
   to the default branch, the maintainer as required reviewer.
3. Apply the ruleset change with `praetorctl sync --remote` if it names the
   environment.

## Publish

```bash
gh workflow run tester-publish.yml -R VMAFx/pelorus --ref master
# optional input: --field ref=<commit, branch or tag reachable from master>
```

| Job | What it does |
| --- | --- |
| `validate` | resolves the ref; the commit and the workflow's own commit must be ancestors of `origin/master`, else the run fails |
| `publish` | waits for the environment approval, then builds, gates, pushes, signs, attests and verifies |

`publish` runs once per kit (`generic`, `nvidia`, `intel`) with
`fail-fast: false`, so a failing kit never cancels another kit between its push
and its signature; each kit's job waits for the `tester-publish` approval. Inside each, in
order: the kit's tags are named; the licence gate refuses planted flags; the
licence record is checked and its gate refuses planted defects; the kit's image
builds (the Containerfile runs the FFmpeg licence gate and the licence record
gate); both gates run again against the built image; the documented command runs
without a GPU and the report validates as that kit's (its `no_device` reasons
name `--gpus all` or `--device /dev/dri`); the `-source` image builds; both
images are pushed as `tester-<YYYYMMDD>-<sha8>[-source]` (generic) or
`tester-<kit>-<YYYYMMDD>-<sha8>[-source]`; an SPDX SBOM, SLSA provenance and a
keyless cosign signature are attached; signatures and attestations are verified.
Pull requests build each kit's image, run the same no-device report check and
push nothing; a draft pull request stops at the first step. The cost rules are
under [Cost](#cost).

## Cost

Hosted minutes are the limit, so the workflow runs only where it can change the
image and never publishes by itself.

| Trigger | Runs | Pushes |
| --- | --- | --- |
| pull request that changes `tools/tester/`, `ffmpeg-patches/`, `libpelorus/`, `meson.build`, `meson_options.txt`, `build-config.env`, `scripts/fetch-ffmpeg-series.sh`, `LICENSES/`, `REUSE.toml` or the workflow | `build`, one amd64 image build per kit (three) with both licence gates and the no-device report | nothing |
| pull request that changes only other paths (docs, ADRs, changelog fragments, scripts) | nothing | nothing |
| nightly schedule (02:17 UTC, this repository only) | `build` on the default branch | nothing |
| `workflow_dispatch` | `validate`, then `publish` after environment approval | the image and its `-source` image |

- `scripts/check-build-config.py` evaluates the pull request path filter
  against sample docs-only and image-input paths, so a filter that starts on a
  docs change (or misses an image input) fails the build-config check.
- The nightly cannot reach `publish`: `validate` and `publish` run on
  `workflow_dispatch` alone, the linter refuses `schedule` or `always()` in
  either, and `build` holds no registry login or secret.
- Timeouts: `build` 90 minutes, `validate` 10, `publish` 150, each per kit. A
  run past its timeout fails instead of hanging; the linter requires a numeric
  value no larger than the cap.
- Expected duration: the pull request build of #252 took about 9 minutes on a
  hosted runner (`.workingdir/STATE.md`, local); a cold local build with `-j4`
  took about four minutes ([research 0236](../research/0236-tester-artifact-licence-audit.md)).
  A cold local build of the NVIDIA kit took about four minutes with `-j4`
  ([research 0229](../research/0229-tester-vendor-images.md)). Record the first
  nightly and dispatch durations in `.workingdir/STATE.md` and lower the caps
  if they are far above.
- The image build copies only `meson.build`, `libpelorus/`, `ffmpeg-patches/`
  and the three gate scripts into the build stage, so a change to the tester
  program or the licence record reuses the cached FFmpeg layers where a cache
  exists.

## Licence record

[ADR-0178](../adr/0178-tester-artifact-licence-record.md). Every file in the
image belongs to a component in `tools/tester/licensing.json`, to a Debian
package, or to an ignore rule with a reason and an expiry date.

```bash
python3 -I -B tools/tester/licensing.py record      # the record is well formed and matches build-config.env
python3 -I -B tools/tester/licensing.py self-test   # each planted defect is refused
```

The Containerfile's `licence` stage runs `self-test`, `notices` and `check`
on the finished tree; the image carries
`/usr/share/licenses/pelorus-tester/THIRD_PARTY_NOTICES.txt` naming the
repository, the exact commit and the FFmpeg commit. To add a package or file to
the image, add it to the Containerfile and record it: a Debian package needs no
entry (dpkg ownership and its copyright file are checked), anything else needs a
component with its SPDX licence, source, redistributability and a licence text
in the registry. A licence marked `redistributable: false` in the registry fails
every component that uses it.

Kits ([ADR-0180](../adr/0180-tester-vendor-images.md)): the record lists the
kits, a component may name the kits it belongs to (no list: every kit), and
`licensing.py check` and `notices` take `--kit`. The record's `forbidden`
section names files (`libcuda.so*`, `libnvidia-*.so*`, `nvidia_icd.json`, ...)
and packages (`nvidia-*`, `libnvidia-*`, `libcuda*`, ...) that fail every kit
whoever owns them, a dpkg package or a component glob. `scripts/check-build-config.py` also refuses a vendor driver,
CUDA or non-free package in any `apt-get install` of the Containerfile, except
the one named above.

To see a failure, plant it in a copy of a kit's assembled stage and build that
kit's image against it; the `licence-<kit>` stage must fail:

```bash
# an NVIDIA library in the NVIDIA image
docker build -f tools/tester/Containerfile --target assembled-nvidia ... --tag planted-base .
printf 'FROM planted-base\nRUN touch /usr/lib/x86_64-linux-gnu/libnvidia-encode.so.1\n' \
  | docker build -t planted -
docker build -f tools/tester/Containerfile --target final-nvidia ... \
  --build-context assembled-nvidia=docker-image://planted .     # fails: forbidden file

# a second non-free package in the Intel image
docker build -f tools/tester/Containerfile --target assembled-intel ... --tag planted-base .
printf 'FROM planted-base\nRUN sed -i "s/^Components: main$/Components: main non-free/" /etc/apt/sources.list.d/debian.sources \\\n && apt-get update && apt-get install -y firmware-misc-nonfree\n' \
  | docker build -t planted -
docker build -f tools/tester/Containerfile --target final-intel ... \
  --build-context assembled-intel=docker-image://planted .      # fails: archive component non-free is not permitted
```

## Licence gate

Each kit's FFmpeg is built by `tools/tester/build-ffmpeg.sh`: the licence flags
come from the shared build stage, a kit adds feature flags only (the script
refuses a kit flag that touches the licence), and the script runs the gate's
self-test and then the gate on the installed binary.

`tools/tester/check-ffmpeg-licence.sh FFMPEG` fails unless `-version` shows
`--enable-gpl --enable-version3` and none of `--enable-nonfree`,
`--enable-cuda-nvcc`, `--enable-cuda-sdk`, `--enable-libfdk-aac`,
`--enable-decklink`, `--enable-libmpeghdec`, and `-L` reports GPL version 3 or
later without the word nonfree. `check-ffmpeg-licence-self-test.sh` plants each
flag in a stub binary and must see it refused. `scripts/check-build-config.py`
rejects the same flags in the workflow, the Containerfile and `build-ffmpeg.sh`.

## Verify an image

```bash
IMAGE=ghcr.io/vmafx/pelorus
DIGEST=$(docker buildx imagetools inspect "$IMAGE:tester-YYYYMMDD-SHA8" --format '{{json .Manifest}}' | jq -r .digest)
cosign verify \
  --certificate-identity-regexp '^https://github\.com/VMAFx/pelorus/\.github/workflows/tester-publish\.yml@' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  "$IMAGE@$DIGEST"
gh attestation verify "oci://$IMAGE@$DIGEST" -R VMAFx/pelorus \
  --signer-workflow VMAFx/pelorus/.github/workflows/tester-publish.yml
```

The `-source` image has the same two checks against its own digest. It holds
`/source/ffmpeg` (the patched tree as compiled, `series.txt`, the shared
FFmpeg fix series release `ffmpeg-patches-<tag>.tar.gz` that was applied
first, `ffmpeg-configure-line.txt`, `libpelorus-commit.txt`), `/source/kit` (the
NVIDIA kit's `nv-codec-headers` tree; empty for the other kits),
`/source/debian` (source packages at the versions the image installed) and
`/source/LICENSES`.

## Local check

```bash
bash tools/tester/check-ffmpeg-licence-self-test.sh
tools/tester/check-ffmpeg-licence.sh "$(command -v ffmpeg)"
python3 -I -B tools/tester/licensing.py record
python3 -I -B tools/tester/licensing.py self-test
python3 scripts/check-build-config.py --self-test
hadolint tools/tester/Containerfile
shellcheck tools/tester/build-ffmpeg.sh
```

The image build itself compiles FFmpeg (tens of minutes per architecture); build
with `--build-arg MAKE_JOBS=<cores / parallel builds>`.
