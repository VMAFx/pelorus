<!-- markdownlint-disable MD013 -->
# Tester images (maintainer runbook)

Tester images are evidence for one commit on the default branch, never a release.
The rules are fixed by [ADR-0173](../adr/0173-tester-programme.md) and
[ADR-0176](../adr/0176-rc-process-and-tester-publish.md). Testers read
[the tester kit page](tester.md).

Status: `tools/tester/Containerfile` is a skeleton (CPU image, GPL-3.0-or-later
FFmpeg, licence record gate, `-source` target). The vendor images come with
[#229](https://github.com/VMAFx/pelorus/issues/229) and
[#230](https://github.com/VMAFx/pelorus/issues/230). Nothing has been dispatched
end to end yet: the image build, the first report run and the SBOM
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

Inside `publish`, in order: the licence gate refuses planted flags; the licence
record is checked and its gate refuses planted defects; the image builds (the
Containerfile runs the FFmpeg licence gate and the licence record gate); both
gates run again against the built image; the documented
command runs without a GPU and the report validates; the `-source` image builds;
both images are pushed as `tester-<YYYYMMDD>-<sha8>` and
`tester-<YYYYMMDD>-<sha8>-source`; an SPDX SBOM, SLSA provenance and a keyless
cosign signature are attached; signatures and attestations are verified.
Pull requests build the image and push nothing; a draft pull request stops at
the first step. The cost rules are under [Cost](#cost).

## Cost

Hosted minutes are the limit, so the workflow runs only where it can change the
image and never publishes by itself.

| Trigger | Runs | Pushes |
| --- | --- | --- |
| pull request that changes `tools/tester/`, `ffmpeg-patches/`, `libpelorus/`, `meson.build`, `meson_options.txt`, `build-config.env`, `LICENSES/`, `REUSE.toml` or the workflow | `build`, one amd64 image build with both licence gates | nothing |
| pull request that changes only other paths (docs, ADRs, changelog fragments, scripts) | nothing | nothing |
| nightly schedule (02:17 UTC, this repository only) | `build` on the default branch | nothing |
| `workflow_dispatch` | `validate`, then `publish` after environment approval | the image and its `-source` image |

- `scripts/check-build-config.py` evaluates the pull request path filter
  against sample docs-only and image-input paths, so a filter that starts on a
  docs change (or misses an image input) fails the build-config check.
- The nightly cannot reach `publish`: `validate` and `publish` run on
  `workflow_dispatch` alone, the linter refuses `schedule` or `always()` in
  either, and `build` holds no registry login or secret.
- Timeouts: `build` 90 minutes, `validate` 10, `publish` 150. A run past its
  timeout fails instead of hanging; the linter requires a numeric value no
  larger than the cap.
- Expected duration: the pull request build of #252 took about 9 minutes on a
  hosted runner (`.workingdir/STATE.md`, local); a cold local build with `-j4`
  took about four minutes ([research 0236](../research/0236-tester-artifact-licence-audit.md)).
  The caps leave room for the vendor images. Record the first nightly and
  dispatch durations in `.workingdir/STATE.md` and lower the caps if they are
  far above.
- The image build copies only `meson.build`, `libpelorus/` and `ffmpeg-patches/`
  into the build stage, so a change to the tester tools or the licence record
  reuses the cached FFmpeg layers where a cache exists.

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
every component that uses it. Vendor images (#229, #230) add their components
and the ADR that records their terms before they publish.

To see a failure, plant a file in a copy of the assembled stage and build
`--target runtime` with
`--build-context assembled=docker-image://<planted image>`.

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
python3 -I -B tools/tester/licensing.py record
python3 -I -B tools/tester/licensing.py self-test
python3 scripts/check-build-config.py --self-test
hadolint tools/tester/Containerfile
```

The image build itself compiles FFmpeg (tens of minutes per architecture); build
with `--build-arg MAKE_JOBS=<cores / parallel builds>`.
