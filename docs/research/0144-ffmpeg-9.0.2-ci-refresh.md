<!-- markdownlint-disable MD013 -->
# Research digest 0144 — FFmpeg n9.0.2 and Ubuntu 26.04 CI refresh

Evidence for
[ADR-0144](../adr/0144-ffmpeg-pin-and-ci-runner-policy.md). Sources were checked
on 2026-09-20. This digest separates facts available locally from acceptance
that can only be obtained from a GitHub-hosted run.

## Upstream FFmpeg identity

FFmpeg publishes the 9.0.2 release archive in its official release index. The
official Git repository resolves the annotated tag as follows:

```text
ce8f11b9fac4cd7ccfb41ca7c01bc17f5cda8cc3  refs/tags/n9.0.2
946fcce07b6dcd0331c8cc609192aeff5e1924f8  refs/tags/n9.0.2^{}
```

The second object is the commit the build must consume. Keeping both the tag
and peeled commit preserves readable release intent while detecting a moved or
incorrect local tag.

Source: <https://ffmpeg.org/releases/> and
`git ls-remote https://github.com/FFmpeg/FFmpeg.git refs/tags/n9.0.2 refs/tags/n9.0.2^{}`.

## Vulkan packages

LunarG's package page states that it stopped updating Ubuntu packages to newer
SDK releases after May 2025 and directs users who need newer SDKs to tarballs.
That makes the Noble apt feed a historical input, not a sustainable CI source.

Ubuntu Resolute publishes the required build-time packages directly:

| Package | Resolute evidence | Purpose |
|---|---|---|
| `libvulkan-dev` | Vulkan loader/header development package (1.4.341 at review time) | FFmpeg Vulkan detection and compilation |
| `glslc` | shaderc command-line compiler (2026.1 at review time) | FFmpeg 9 build-time GLSL to SPIR-V rule |
| `glslang-tools` | Khronos GLSL/SPIR-V tools (16.2 at review time) | Pelorus reference-shader fast tests |

Sources: <https://vulkan.lunarg.com/content/view/packages-home.dhtml>,
<https://packages.ubuntu.com/resolute/libvulkan-dev>,
<https://packages.ubuntu.com/resolute/glslc>, and
<https://packages.ubuntu.com/resolute/glslang-tools>.

## Hosted runner status

GitHub lists `ubuntu-26.04` as an available x64 runner label while
`ubuntu-latest` still resolves to Ubuntu 24.04. Its runner-image policy says
beta images update weekly and do not fall under the Actions customer SLA. The
workflow must therefore pin the label rather than rely on `ubuntu-latest`, log
the image and package versions, and pass on the hosted runner before the
migration is called operationally restored.

A local container or Resolute host can validate commands and packages. It
cannot validate GitHub image rollout, capacity, preinstalled software, or the
actual `ImageVersion`. A manual non-publishing release dispatch is needed
because the release workflow otherwise runs only after a tag, which is too
late to discover runner incompatibility.

Source: <https://github.com/actions/runner-images/blob/main/README.md>.

## Machine maintenance

Renovate's regex manager accepts named `currentValue` and `currentDigest`
captures. One match spanning adjacent `FFMPEG_TAG` and `FFMPEG_COMMIT` lines
lets the update proposal carry the readable release and its immutable object
together. Regex matching is whole-file RE2 syntax, so the manager must not rely
on lookahead or backreferences.

The supported validator invocation comes from Renovate's distribution:

```bash
npx --yes --package renovate@44.103.6 -- \
  renovate-config-validator --strict
```

Using the default `renovate.json` discovery avoids the validator's special
global-config treatment for an explicitly supplied filename.

Sources: <https://docs.renovatebot.com/modules/manager/regex/> and
<https://docs.renovatebot.com/config-validation/>.

## Acceptance matrix

| Claim | Required evidence |
|---|---|
| n9.0.2 patch compatibility | deterministic 18-patch generation and full replay at the pinned commit, compiling available oneVPL, libaom, SVT-AV1, and NVENC consumers and checking their Pelorus AVOptions |
| correct libpelorus linkage | private install prefix reported by `pkg-config`; linked `ffmpeg`; all Pelorus filters and BSF register; installed static `libavfilter.pc` exposes `-lpelorus` and links/runs an external consumer |
| workflow structure | build-config checker plus actionlint v1.7.12 |
| sanitizer preservation | fast suite under ASan+UBSan with fatal alignment failures |
| Ubuntu 26.04 operational acceptance | all PR jobs and the manual non-publishing release gate green on GitHub-hosted `ubuntu-26.04`, with image/package versions retained |

No local result substitutes for the last row.
