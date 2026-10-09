<!-- markdownlint-disable MD013 -->
# Tester images (maintainer runbook)

Tester images are evidence for one commit on the default branch, never a release.
The rules are fixed by [ADR-0173](../adr/0173-tester-programme.md) and
[ADR-0176](../adr/0176-rc-process-and-tester-publish.md). Testers read
[the tester kit page](tester.md).

Status: `tools/tester/Containerfile` is a skeleton (CPU image, GPL-3.0-or-later
FFmpeg, `-source` target). The vendor images come with
[#229](https://github.com/VMAFx/pelorus/issues/229) and
[#230](https://github.com/VMAFx/pelorus/issues/230), the licence record gate with
[#236](https://github.com/VMAFx/pelorus/issues/236). Nothing has been dispatched
or built end to end yet: the image build, the first report run and the SBOM
attestation verify line (predicate type `https://spdx.dev/Document/v2.3`) are
unproven until the first dispatch.

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

Inside `publish`, in order: the licence gate refuses planted flags; the image builds (the Containerfile runs the licence gate
on the new `ffmpeg`); the gate runs again against the built image; the documented
command runs without a GPU and the report validates; the `-source` image builds;
both images are pushed as `tester-<YYYYMMDD>-<sha8>` and
`tester-<YYYYMMDD>-<sha8>-source`; an SPDX SBOM, SLSA provenance and a keyless
cosign signature are attached; signatures and attestations are verified.
Pull requests that touch `tools/tester/`, `ffmpeg-patches/`, `build-config.env`
or the workflow build the image and push nothing; a draft pull request stops at
the first step.

## Licence gate

`tools/tester/check-ffmpeg-licence.sh FFMPEG` fails unless `-version` shows
`--enable-gpl --enable-version3` and none of `--enable-nonfree`,
`--enable-cuda-nvcc`, `--enable-cuda-sdk`, `--enable-libfdk-aac`,
`--enable-decklink`, `--enable-libmpeghdec`, and `-L` reports GPL version 3 or
later without the word nonfree. `check-ffmpeg-licence-self-test.sh` plants each
flag in a stub binary and must see it refused. `scripts/check-build-config.py`
rejects the same flags in the workflow and the Containerfile text.

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
`/source/ffmpeg` (the patched tree as compiled, `series.txt`,
`ffmpeg-configure-line.txt`, `libpelorus-commit.txt`), `/source/debian` (source
packages at the versions the image installed) and `/source/LICENSES`.

## Local check

```bash
bash tools/tester/check-ffmpeg-licence-self-test.sh
tools/tester/check-ffmpeg-licence.sh "$(command -v ffmpeg)"
python3 scripts/check-build-config.py --self-test
hadolint tools/tester/Containerfile
```

The image build itself compiles FFmpeg (tens of minutes per architecture); build
with `--build-arg MAKE_JOBS=<cores / parallel builds>`.
