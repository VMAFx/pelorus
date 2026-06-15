<!-- markdownlint-disable MD013 -->
# Agent guide — Pelorus

> Cross-tool context for Claude Code, Cursor, Aider, Codex, Continue, and other
> coding assistants. **Read this before suggesting changes.** Then read the
> per-subdirectory `AGENTS.md` for the area you're touching. Claude Code users:
> [CLAUDE.md](CLAUDE.md) extends this file.

## What this repo is

`Pelorus` is a GPU **pre-encode** pipeline: Vulkan compute filters + FFmpeg
filters that fix the psychovisual flaws of a hardware video encoder *before* it
sees the pixels, entirely in VRAM (zero-copy). The goal is to close the BD-rate
gap between fixed-function GPU encoders (NVENC/AMF/QSV) and slow CPU encoders
(x265/SVT-AV1) — debanding, temporal denoise, film-grain synthesis, and
optical-flow motion hints. These are **codec-agnostic**: deband/denoise/motion
help HEVC (`hevc_nvenc`/`hevc_qsv`/`hevc_vaapi`/`hevc_amf`, rivaling x265) and
AV1 equally; only film-grain is codec-specific (AV1 = AOM, HEVC/VVC = H.274).

Pelorus is the sibling of **vmafx** (the VMAF fork), hosted under the same
`vmafx` GitHub org. vmafx dropped its own Vulkan backend (its ADR-0726), so
Pelorus is the Vulkan home; vmafx stays the **quality oracle**. The two are
bidirectionally wired: Pelorus filters write a shared side-data blob that vmafx
reads for perceptually-weighted scoring, and Pelorus tunes its filter strength
against VMAF using vmafx's autotune loop. See
[docs/architecture/overview.md](docs/architecture/overview.md) and
[docs/principles.md](docs/principles.md).

## Hard rules

1. **Never break the `libpelorus` public ABI without a `Migration:` footer.**
   The `PelorusSideData` interop blob is append-only (interop.h R1/R2): add a
   field or section bit and bump `PELORUS_ABI_MINOR` — never reorder, resize, or
   remove. A breaking change is forbidden; mint a new section bit instead.
2. **All errors flow through `pel_result`.** No bare `return -1;` across a
   `libpelorus` API boundary. Every non-void return is checked or `(void)`-cast.
3. **No global mutable state / no static-init side effects.** Lifecycle is
   explicit. Banned C functions: `gets`, `strcpy`, `strcat`, `sprintf`,
   `strtok`, `atoi`, `atof`, `rand`, `system` (see docs/principles.md §1.2).
4. **Keep each filter's two shader implementations in lockstep.** The standalone
   reference shaders (`libpelorus/shaders/pelorus_<name>.comp`) and the FFmpeg
   filters' inline GLSL (`ffmpeg-patches/files/vf_pelorus_<name>_vulkan.c`)
   implement the same algorithm (deband, analyze, …); edit both together.
5. **Patch-stack sync.** A change to any `libpelorus` surface the FFmpeg patches
   consume updates `ffmpeg-patches/files/` + the regenerated patch in the same
   PR. Verify with a full series replay (`ffmpeg-patches/test/build-and-run.sh`),
   not per-patch `git apply --check`.
6. **Every PR leaves touched files lint-clean** to `-Wall -Wextra -Werror` +
   clang-tidy. A `// NOLINT` carries an inline citation.
7. **Every merged commit: 0 warnings · clang-tidy clean · `meson test
   --suite=fast` green · the deband shader compiles (glslang).**

See [docs/principles.md](docs/principles.md) for the full Power-of-10,
SEI-CERT-C, style, and Vulkan-usage contract, and [CONTRIBUTING.md](CONTRIBUTING.md)
for the per-PR deliverables (ADRs, per-surface docs, changelog fragments).

## Repository layout

```text
Pelorus/
├── meson.build  meson_options.txt   # build root (libpelorus + tests + shaders)
│
├── libpelorus/                       # the shared core (vendored by vmafx too)
│   ├── include/pelorus/
│   │   ├── pelorus.h                 #   umbrella: version, pel_result
│   │   ├── interop.h                 #   the Pelorus<->vmafx side-data ABI
│   │   └── deband.h                  #   smart-deband parameter contract
│   ├── src/                          #   interop.c (pack/parse), deband_params.c
│   ├── shaders/                      #   standalone reference .comp shaders
│   └── test/                         #   interop ABI conformance fixture
│
├── ffmpeg-patches/                   # vf_pelorus_* filters, stacked vs n8.1.1
│   ├── files/                        #   canonical filter sources (edit here)
│   ├── 0001-*.patch  series.txt      #   generated artifacts + apply order
│   ├── generate.sh                   #   regenerate patches from files/
│   └── test/build-and-run.sh         #   apply + build + smoke-test gate
│
├── docs/
│   ├── principles.md                 #   the coding + Vulkan-usage contract
│   ├── adr/                          #   Architecture Decision Records (Nygard)
│   ├── architecture/ api/ metrics/   #   per-surface docs (C4, interop, filters)
│   ├── usage/ backends/ development/  #   pipeline, Vulkan path, build/release
│   └── research/                     #   deep-dive digests
│
├── tools/                            # libpelorus CLI demonstrators (not installed)
│   └── pelorus_qp_report.c           #   x265 --csv -> PEL_SEC_QPREPORT (ADR-0122)
│
├── changelog.d/                      # Keep-a-Changelog fragments (rendered)
└── AGENTS.md  CLAUDE.md  CONTRIBUTING.md  README.md
```

Per-subdirectory `AGENTS.md` files give unit-level conventions + the
rebase-sensitive invariants.

## Common tasks

| Task | Command |
|---|---|
| Configure + build | `meson setup build && ninja -C build` |
| Fast test gate | `meson test -C build --suite=fast` |
| Install (for the FFmpeg patches) | `ninja -C build install` |
| Regenerate FFmpeg patches | `cd ffmpeg-patches && ./generate.sh` |
| Apply + build + smoke FFmpeg | `ffmpeg-patches/test/build-and-run.sh` |
| Lint | `clang-format --dry-run -Werror` + `clang-tidy` on touched files |
| Reserve an ADR number | `scripts/adr/next-free.sh --claim <slug>` |

## Pinned upstream references

| Component | Version |
|---|---|
| FFmpeg base for the patch stack | `n8.1.1` |
| FFmpeg Vulkan filter model | `libavfilter/vf_gblur_vulkan.c`, `vf_nlmeans_vulkan.c` |
| AV1 film-grain struct mirrored by interop §(d) | `libavutil/film_grain_params.h` |
| vmafx control plane (autotune) | `libvmaf_tune` filter, `vmafx-server` `/v1/score`, `vmaf-mcp` |

## When in doubt

Read [docs/principles.md](docs/principles.md), then the per-subdirectory
`AGENTS.md` for the area you're touching. Decisions live in
[docs/adr/](docs/adr/); the project plan + current status live in
`.workingdir/PLAN.md` + `.workingdir/STATE.md` (local, gitignored).
