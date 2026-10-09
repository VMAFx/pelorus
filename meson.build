# Pelorus — GPU pre-encode pipeline (libpelorus core + FFmpeg patch-stack).
# Meson + Ninja, mirroring the vmafx sibling. See docs/development/build.md.
project(
  'pelorus',
  'c',
  version: '0.3.0',
  license: 'EUPL-1.2',
  default_options: [
    'c_std=c11',
    'warning_level=3',
    'werror=true',
    'default_library=shared',
  ],
  meson_version: '>=0.60.0',
)

fs = import('fs')
cc = meson.get_compiler('c')

# The version lives in two places: this project() call and pelorus.h's
# PELORUS_VERSION_* macros (the ABI-visible copy a consumer compiles against).
# Nothing used to assert they agreed, so they could silently diverge. Parse the
# header and fail configuration if they do.
_pelorus_h = fs.read('libpelorus/include/pelorus/pelorus.h')
_ver_str = _pelorus_h.split('#define PELORUS_VERSION_STR "')[1].split('"')[0]
if _ver_str != meson.project_version()
  error('version skew: meson.build says @0@ but pelorus.h PELORUS_VERSION_STR says @1@'
        .format(meson.project_version(), _ver_str))
endif

# A release candidate is `X.Y.Z-rc.N` (docs/development/release.md, ADR-0176).
# Meson rejects a suffix in a shared-library version, and pkg-config orders
# `0.4.0-rc.1` after `0.4.0`, so the shared object and the .pc file carry the
# numeric core and only project(), pelorus.h and the release tag carry the full
# string. For a final release the two are identical.
pelorus_core_version = meson.project_version().split('-')[0]

# Power-of-10 / CERT-leaning baseline: keep the warning surface wide. Project
# code is warning-clean to -Wall -Wextra; add a few high-signal extras.
add_project_arguments(
  cc.get_supported_arguments([
    '-Wshadow',
    '-Wpointer-arith',
    '-Wstrict-prototypes',
    '-Wmissing-prototypes',
    '-Wvla',
  ]),
  language: 'c',
)

subdir('libpelorus')

if get_option('tools')
  subdir('tools')
endif

# Since ADR-0143 each filter's GLSL binding declarations and its C
# FFVulkanDescriptorSetBinding array are two hand-maintained lists that must agree.
# Nothing in the compiler or the FFmpeg build checks that -- a swapped pair silently
# reads the wrong resource, which is corruption rather than a build failure. This is
# that check, and it is cheap enough to live in the fast gate.
python_prog = find_program('python3', required: true)
bash_prog = find_program('bash', required: true)
test('shader-binding-order',
     python_prog,
     args: [meson.project_source_root() / 'scripts' / 'check-shader-bindings.py'],
     workdir: meson.project_source_root(),
     suite: 'fast')

# The contract validation itself is platform-neutral text checking and runs
# everywhere. Its --self-test half drives throwaway Git repositories through the
# Linux FFmpeg-replay fixtures, which are not portable to a native Windows
# Python: MSYS2 Git brace-expands a `ref^{commit}` argument received from a
# non-MSYS parent, and Python text-mode writes give the `git am` fixture CRLF
# line endings that the replay strips, so its patch no longer applies. The
# Linux jobs run the full self-test on every PR, so the Windows leg skips only
# that half (ADR-0149).
build_config_args = [meson.project_source_root() / 'scripts' / 'check-build-config.py']
if build_machine.system() != 'windows'
  build_config_args += ['--self-test']
endif
test('build-config-sync',
     python_prog,
     args: build_config_args,
     workdir: meson.project_source_root(),
     suite: 'fast')

# ADR-0176: release tag shape and rc numbering, the candidate legs check and the
# tester image licence gate. Each --self-test plants a defect per rule and must
# see it rejected.
test('release-tag-rules',
     python_prog,
     args: ['-I', meson.project_source_root() / 'scripts' / 'release' / 'verify-release.py',
            '--self-test'],
     workdir: meson.project_source_root(),
     suite: 'fast')
test('candidate-legs',
     python_prog,
     args: ['-I', meson.project_source_root() / 'scripts' / 'release' / 'check-candidate-legs.py',
            '--self-test'],
     workdir: meson.project_source_root(),
     suite: 'fast')
test('tester-licence-gate',
     bash_prog,
     args: [meson.project_source_root() / 'tools' / 'tester' / 'check-ffmpeg-licence-self-test.sh'],
     workdir: meson.project_source_root(),
     suite: 'fast')
# ADR-0178 / #236: the artifact licence record is well formed and matches the
# repository pins, and its gate refuses every planted defect.
test('tester-licence-record',
     python_prog,
     args: ['-I', '-B', meson.project_source_root() / 'tools' / 'tester' / 'licensing.py', 'record'],
     workdir: meson.project_source_root(),
     suite: 'fast')
test('tester-licence-record-self-test',
     python_prog,
     args: ['-I', '-B', meson.project_source_root() / 'tools' / 'tester' / 'licensing.py', 'self-test'],
     workdir: meson.project_source_root(),
     suite: 'fast')

# Issue #220 / ADR-0174: telemetry field names are shared with VMAFx's stream
# metadata (VMAFx/vmafx#2271). The check runs the registry and interop.h rules;
# until VMAFx publishes its field list it exits 77, which Meson reports as SKIP
# with the reason printed, never as a pass. The --self-test plants a failure for
# every rule and must reject each.
test('telemetry-field-parity',
     python_prog,
     args: [meson.project_source_root() / 'scripts' / 'check-telemetry-field-parity.py'],
     workdir: meson.project_source_root(),
     suite: 'fast')
test('telemetry-field-parity-self-test',
     python_prog,
     args: [meson.project_source_root() / 'scripts' / 'check-telemetry-field-parity.py',
            '--self-test'],
     workdir: meson.project_source_root(),
     suite: 'fast')

# ADR-0147: FF_VK_REP_FLOAT exposes the storage container's UNORM domain, not
# necessarily the logical sample domain.  Keep conversion and component
# preservation consistent across the entire arithmetic-filter family.
test('vulkan-storage-domain',
     python_prog,
     args: [meson.project_source_root() / 'scripts' / 'check-vulkan-storage-domain.py'],
     workdir: meson.project_source_root(),
     suite: 'fast')

# ADR-0166: patch 0009's Vulkan-Video QP map needs the extension AND its feature
# enabled in hwcontext_vulkan, a driver-range dQP clamp, and the valid-usage
# repairs found on hardware. None of those fail a compile; --self-test proves
# the check rejects each defect.
test('vulkan-qpmap-contract',
     python_prog,
     args: [meson.project_source_root() / 'scripts' / 'check-vulkan-qpmap-contract.py',
            '--self-test'],
     workdir: meson.project_source_root(),
     suite: 'fast')

test('vulkan-validation-streams',
     bash_prog,
     args: [meson.project_source_root() / 'ffmpeg-patches' / 'test' /
            'vulkan-format-matrix-validation-self-test.sh'],
     workdir: meson.project_source_root(),
     suite: 'fast')

test('vulkan-sample-scale',
     python_prog,
     args: [meson.project_source_root() / 'scripts' / 'test-vulkan-sample-scale.py'],
     workdir: meson.project_source_root(),
     suite: 'fast')

# BUG-003/004/005/015: the FFmpeg-side consumers scan every appended
# SEI_UNREGISTERED blob (newest first), size-check older producers' sections per
# field, and derive the motion-grid pitch from the producer block edge. The real
# private header is compiled against a stub libavutil/frame.h (no FFmpeg tree).
pelorus_sidedata_test = executable(
  'pelorus_sidedata_test',
  files('ffmpeg-patches/test/pelorus_sidedata_test.c'),
  include_directories: include_directories('ffmpeg-patches/files', 'ffmpeg-patches/test/stub'),
  dependencies: libpelorus_dep,
)
test('ffmpeg-sidedata-consumers', pelorus_sidedata_test, suite: 'fast')

# ADR-0115: the grain estimator reduces fixed-point samples with uint32 atomics.
# Prove the maximum per-slice sum remains representable at DCI 8K, and that
# the rounded fixed-point path matches a float reference on one-code-value grain
# (ADR-0161). --self-test plants defects and requires each to be rejected.
test('grain-accumulator-bounds',
     python_prog,
     args: [meson.project_source_root() / 'scripts' / 'test-grain-accumulator-bounds.py',
            '--self-test'],
     workdir: meson.project_source_root(),
     suite: 'fast')

# BUG-022: the corpus fetch must fail closed (HTTP error, corrupt body, stalled
# server) and never leave a file at the cached-download path.
# The harness passes native-Windows temp paths (CORPUS, CORPUS_LOCK and an
# extensionless `#!/bin/sh` FFMPEG stub) from Windows Python to MSYS2 bash; on
# the windows-2025 leg even the positive control fails (CI run 37143360699),
# before any download logic runs. fetch-corpus.sh is the Linux bench harness
# and the Linux jobs run this test on every PR (same split as ADR-0149).
if build_machine.system() != 'windows'
  test('fetch-corpus-fail-closed',
       python_prog,
       args: [meson.project_source_root() / 'scripts' / 'test-fetch-corpus.py'],
       workdir: meson.project_source_root(),
       suite: 'fast')
endif

# The build-config self-test drives throwaway Git repositories. Started from a
# Git hook it inherits GIT_DIR/GIT_WORK_TREE/GIT_INDEX_FILE, and its fixtures then
# rewrote the developer's own .git/config and refs. Prove the self-test leaves an
# invoking repository untouched.
if build_machine.system() != 'windows'
  test('build-config-selftest-isolation',
       python_prog,
       args: [meson.project_source_root() / 'scripts' / 'test-selftest-git-isolation.py'],
       workdir: meson.project_source_root(),
       timeout: 600,
       suite: 'fast')
endif

# BUG-007/008/013/025: shader plane loops must `continue` (yuva420p alpha), bayer8 must be
# the canonical Bayer matrix, and borderfix clamp bounds must be order-safe.
test('shader-plane-bounds',
     python_prog,
     args: [meson.project_source_root() / 'scripts' / 'test-shader-plane-bounds.py'],
     workdir: meson.project_source_root(),
     suite: 'fast')

# BUG-028: deblock/aa plane loops must `continue` (yuva420p alpha stays written).
test('shader-plane-continue',
     python_prog,
     args: [meson.project_source_root() / 'scripts' / 'test-shader-plane-continue.py'],
     workdir: meson.project_source_root(),
     suite: 'fast')

# ADR-0155: pelorus_fgs defaults stay inside the SMPTE RDD 5 profile FFmpeg's H.274
# synthesizer implements, emit explicit cutoffs and per-component intervals, and
# reject options the CBS writer cannot serialize. --self-test plants each defect.
test('fgs-bsf-contract',
     python_prog,
     args: [meson.project_source_root() / 'scripts' / 'test-fgs-bsf-contract.py',
            '--self-test'],
     workdir: meson.project_source_root(),
     suite: 'fast')

# The NVENC hand diffs only compile inside an ffnvcodec-enabled FFmpeg build.
# Compile their pure value mappings here: AV1 film-grain chroma biases (BUG-019)
# and the per-codec ROI delta-QP span (BUG-020).
test('nvenc-pelorus-mapping',
     python_prog,
     args: [meson.project_source_root() / 'scripts' / 'test-nvenc-pelorus-mapping.py',
            '--cc'] + meson.get_compiler('c').cmd_array(),
     workdir: meson.project_source_root(),
     suite: 'fast')

# Per-frame external-ME-hint setup of patch 0008 (BUG-031): a frame without
# Pelorus motion data must still submit one zero-MV candidate per block, or the
# NVENC session fails the whole encode.
test('nvenc-me-hints',
     python_prog,
     args: [meson.project_source_root() / 'scripts' / 'test-nvenc-me-hints.py',
            '--cc'] + meson.get_compiler('c').cmd_array(),
     workdir: meson.project_source_root(),
     suite: 'fast')

# SVT-AV1 keeps the last ROI event as a sticky pointer (BUG-030); the event
# builder of patch 0013 must replace it with a neutral event when ROI data ends.
test('svtav1-roi-sticky',
     python_prog,
     args: [meson.project_source_root() / 'scripts' / 'test-svtav1-roi-sticky.py',
            '--cc'] + meson.get_compiler('c').cmd_array(),
     workdir: meson.project_source_root(),
     suite: 'fast')

# vf_pelorus_mc_vulkan keeps its host-side MV arithmetic in a plain-C header
# (ffmpeg-patches/files/pelorus_mc_stats.h): the Q2 -> integer-pel predictor
# boundary (BUG-009) and the O(n) p95 select, proven bit-identical to the old
# O(n^2) scan (BUG-014). It has no libav* dependency, so it is tested here.
if get_option('tests')
  mc_stats_test = executable(
    'pelorus_mc_stats_test',
    files('ffmpeg-patches/test/mc_stats_test.c'),
    include_directories: include_directories('ffmpeg-patches/files'),
    dependencies: cc.find_library('m', required: false),
  )
  test('mc-stats', mc_stats_test, suite: 'fast')
endif

# Issue #219 / ADR-0177: vf_pelorus_analyze_vulkan's per-cell maps
# (ffmpeg-patches/files/pelorus_analyze_maps.h): the cell grid and its bound, the
# per-cell banding score on a synthetic banded ramp against a flat noisy frame,
# and the map packing round trip through libpelorus's reader.
if get_option('tests')
  analyze_maps_test = executable(
    'pelorus_analyze_maps_test',
    files('ffmpeg-patches/test/analyze_maps_test.c'),
    include_directories: include_directories('ffmpeg-patches/files'),
    dependencies: [libpelorus_dep, cc.find_library('m', required: false)],
  )
  test('analyze-maps', analyze_maps_test, suite: 'fast')
  # The pre-ABI-1.4 packing path (pel_blob_pack + copy), forced on 1.4 headers.
  analyze_maps_legacy_test = executable(
    'pelorus_analyze_maps_legacy_test',
    files('ffmpeg-patches/test/analyze_maps_test.c'),
    c_args: ['-DPEL_AN_FORCE_LEGACY_PACK'],
    include_directories: include_directories('ffmpeg-patches/files'),
    dependencies: [libpelorus_dep, cc.find_library('m', required: false)],
  )
  test('analyze-maps-legacy-pack', analyze_maps_legacy_test, suite: 'fast')
endif

# The hardware rows of vulkan-format-matrix.sh decode the analyze maps with
# analyze-maps-check.py; --self-test plants one defect per reader rule and
# requires each to be rejected.
test('analyze-maps-check-self-test',
     python_prog,
     args: [meson.project_source_root() / 'ffmpeg-patches' / 'test' / 'analyze-maps-check.py',
            '--self-test'],
     workdir: meson.project_source_root(),
     suite: 'fast')

# BUG-016: the denoise meta=1 residual sums are per-workgroup uint32 partials
# added into 64-bit slices. Prove the DCI 8K bounds and the fixed-point
# resolution that keeps noise_sigma_estimate non-zero on clean content.
test('denoise-accumulator-bounds',
     python_prog,
     args: [meson.project_source_root() / 'scripts' / 'test-denoise-accumulator-bounds.py'],
     workdir: meson.project_source_root(),
     suite: 'fast')

# ADR-0163: the dehalo shader must not rewrite line-art. Checks the corrected gate
# and pull expressions in the shipped shader and the libpelorus reference, then runs
# a CPU model on a 1-px stroke and a ringed line. The pre-fix shader fails it.
test('dehalo-line-art',
     python_prog,
     args: [meson.project_source_root() / 'scripts' / 'test-dehalo-line-art.py'],
     workdir: meson.project_source_root(),
     suite: 'fast')
