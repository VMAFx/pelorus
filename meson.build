# Pelorus — GPU pre-encode pipeline (libpelorus core + FFmpeg patch-stack).
# Meson + Ninja, mirroring the vmafx sibling. See docs/development/build.md.
project(
  'pelorus',
  'c',
  version: '0.4.0',
  license: 'EUPL-1.2',
  default_options: [
    'c_std=c11',
    'warning_level=3',
    'werror=true',
    'default_library=shared',
  ],
  meson_version: '>=0.57.0',
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

# ADR-0220: CodeQL workflow structure, language matrix, pinned actions and permissions.
test('codeql-workflow',
     python_prog,
     args: ['-I', meson.project_source_root() / 'scripts' / 'check-codeql-workflow.py', '--self-test'],
     workdir: meson.project_source_root(),
     suite: 'fast')

# Issue #502: interop ABI minor bump guard and consistency check across sites.
test('bump-abi-check',
     python_prog,
     args: ['-I', meson.project_source_root() / 'scripts' / 'bump-abi.py', '--check'],
     workdir: meson.project_source_root(),
     timeout: 60,
     suite: 'fast')
test('bump-abi-self-test',
     python_prog,
     args: ['-I', meson.project_source_root() / 'scripts' / 'bump-abi.py', '--self-test'],
     workdir: meson.project_source_root(),
     timeout: 60,
     suite: 'fast')

# Issue #502: release version synchronization across build definitions, headers and docs.
test('prepare-release-check',
     python_prog,
     args: ['-I', meson.project_source_root() / 'scripts' / 'prepare-release.py', '--check'],
     workdir: meson.project_source_root(),
     timeout: 60,
     suite: 'fast')
test('prepare-release-self-test',
     python_prog,
     args: ['-I', meson.project_source_root() / 'scripts' / 'prepare-release.py', '--self-test'],
     workdir: meson.project_source_root(),
     timeout: 60,
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
test('release-notes',
     python_prog,
     args: ['-I', meson.project_source_root() / 'scripts' / 'release' / 'release_notes.py',
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
# metadata (VMAFx/vmafx#2271). The check runs the registry and interop.h rules
# and compares the registry with the pinned VMAFx field list in
# libpelorus/test/fixtures/. Without that file it exits 77, which Meson reports
# as SKIP and scripts/check-test-skips.py refuses (no declared skip). The
# --self-test plants a failure for every rule and must reject each.
test('telemetry-field-parity',
     python_prog,
     args: [meson.project_source_root() / 'scripts' / 'check-telemetry-field-parity.py'],
     workdir: meson.project_source_root(),
     suite: 'fast')
# ADR-0188: `meson test` passes a test that exits 77 whatever the reason.
# scripts/check-test-skips.py fails every skip that .config/test-skips.json does
# not list with its exact reason; make test and each CI fast-suite run call it on
# testlog.json. --self-test plants an unlisted skip, a listed test skipping for
# another reason, and malformed inputs, and requires each to be rejected.
test('test-skips-self-test',
     python_prog,
     args: [meson.project_source_root() / 'scripts' / 'check-test-skips.py',
            '--self-test'],
     workdir: meson.project_source_root(),
     suite: 'fast')
# A skip that the host never takes still has to be listed: a new test that
# exits 77 without its tool passes where the tool is installed and first fails
# on a CI lane without it. scripts/check-skip-listings.py reads intro-tests.json
# and requires a test-skips.json entry for every literal SKIP line that an exit
# 77 follows; --self-test plants an unlisted and a wrong-reason skip.
test('skip-listings',
     python_prog,
     args: ['-I', meson.project_source_root() / 'scripts' / 'check-skip-listings.py',
            meson.project_build_root() / 'meson-info' / 'intro-tests.json'],
     workdir: meson.project_source_root(),
     suite: 'fast')
test('skip-listings-self-test',
     python_prog,
     args: ['-I', meson.project_source_root() / 'scripts' / 'check-skip-listings.py',
            '--self-test'],
     workdir: meson.project_source_root(),
     suite: 'fast')
# bench-results v0.19: guard the ~5 % vf_pelorus_mc speedup by refusing a block_sad
# without its subgroup reduction or with a barrier-tree loop.
test('mc-sad-reduction',
     python_prog,
     args: ['-I', meson.project_source_root() / 'scripts' / 'check-mc-sad-reduction.py'],
     workdir: meson.project_source_root(),
     # glslangValidator and spirv-dis run in sequence, 30 s deadline each.
     timeout: 90,
     suite: 'fast')
test('mc-sad-reduction-self-test',
     python_prog,
     args: ['-I', meson.project_source_root() / 'scripts' / 'check-mc-sad-reduction.py',
            '--self-test'],
     workdir: meson.project_source_root(),
     suite: 'fast')
# Fleet exception category rule: every .standards.yaml exception entry must name
# its category (A = stated in entry, B <= 3d, C <= 10d, D <= 120d, engine cap 90d).
# scripts/check-exception-categories.py --self-test judges the live file against
# today and runs in-memory mutations on an embedded fixture, verifying each defect is rejected.
test('exception-categories',
     python_prog,
     args: ['-I', meson.project_source_root() / 'scripts' / 'check-exception-categories.py',
            '--self-test'],
     workdir: meson.project_source_root(),
     suite: 'fast')
test('standards-exceptions',
     python_prog,
     args: ['-I', meson.project_source_root() / 'scripts' / 'standards_exceptions.py',
            '--self-test'],
     workdir: meson.project_source_root(),
     suite: 'fast')
# ADR-0241: every nosemgrep / nosec marker in tracked Python sits on the start line
# of a statement or expression and names its rule ids plus a reason. --self-test
# plants each refused shape (misplaced line, bare marker, missing reason, ...) and
# then scans the tree.
test('suppression-markers-self-test',
     python_prog,
     args: ['-I', meson.project_source_root() / 'scripts' / 'check-suppression-markers.py',
            '--self-test-only'],
     workdir: meson.project_source_root(),
     suite: 'fast')
# Planted cases must skip by name when git is missing; this runs them without git on Linux too.
test('suppression-markers-self-test-nogit',
     python_prog,
     args: ['-I', meson.project_source_root() / 'scripts' / 'check-suppression-markers.py',
            '--self-test-only'],
     workdir: meson.project_source_root(),
     suite: 'fast',
     env: {'PATH': meson.current_build_dir() / 'nogit-path'})
# The tree scan lists files with git, which the MSYS2 UCRT64 leg does not put
# on PATH (CI run 41408892182: FileNotFoundError for git). The planted cases
# above run there; the Linux jobs scan the tree on every PR.
if build_machine.system() != 'windows'
  test('suppression-markers',
       python_prog,
       args: ['-I', meson.project_source_root() / 'scripts' / 'check-suppression-markers.py',
              '--self-test'],
       workdir: meson.project_source_root(),
       suite: 'fast')
endif
# Unmarked subprocess sites otherwise surface only in Codacy on the pull request;
# block growth before push. --self-test-only runs the planted cases on all platforms;
# the Linux jobs run --check against scripts/subprocess-markers-baseline.json.
test('subprocess-markers-self-test',
     python_prog,
     args: ['-I', meson.project_source_root() / 'scripts' / 'check-subprocess-markers.py',
            '--self-test-only'],
     workdir: meson.project_source_root(),
     suite: 'fast')
# Planted cases must skip by name when git is missing; this runs them without git on Linux too.
test('subprocess-markers-self-test-nogit',
     python_prog,
     args: ['-I', meson.project_source_root() / 'scripts' / 'check-subprocess-markers.py',
            '--self-test-only'],
     workdir: meson.project_source_root(),
     suite: 'fast',
     env: {'PATH': meson.current_build_dir() / 'nogit-path'})
if build_machine.system() != 'windows'
  test('subprocess-markers-check',
       python_prog,
       args: ['-I', meson.project_source_root() / 'scripts' / 'check-subprocess-markers.py',
              '--check'],
       workdir: meson.project_source_root(),
       suite: 'fast')
endif
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

# ADR-0222 (#109): a storage buffer the shader reads must be device-local, or a
# discrete GPU reads it over PCIe during the dispatch. --self-test plants a
# host-only storage buffer per source shape and requires a refusal.
test('vulkan-buffer-domain',
     python_prog,
     args: ['-I', meson.project_source_root() / 'scripts' / 'check-vulkan-buffer-domain.py',
            '--self-test'],
     workdir: meson.project_source_root(),
     suite: 'fast')

# ADR-0166: patch 0009b's Vulkan-Video QP map needs the extension AND its feature
# enabled in hwcontext_vulkan (patch 0009a), a driver-range dQP clamp, and the valid-usage
# repairs found on hardware. None of those fail a compile; --self-test proves
# the check rejects each defect.
test('vulkan-qpmap-contract',
     python_prog,
     args: ['-I', meson.project_source_root() / 'scripts' / 'check-vulkan-qpmap-contract.py',
            '--self-test'],
     workdir: meson.project_source_root(),
     suite: 'fast')

# #278 / ADR-0182: patch 0009b takes the map format the driver advertises (RADV:
# R32_SINT) and fills it by a path the encode queue family can record (RADV's
# family has VIDEO_ENCODE only: host-mapped LINEAR image). The entry classifier
# and the host raster run in a C harness; --self-test plants a defect per rule.
# 12 harness builds x 150 s deadline + margin (scripts/patch_harness.py).
test('vulkan-qpmap-fill',
     python_prog,
     args: ['-I', meson.project_source_root() / 'scripts' / 'test-vulkan-qpmap-fill.py',
            '--self-test', '--cc'] + meson.get_compiler('c').cmd_array(),
     workdir: meson.project_source_root(),
     timeout: 1830,
     suite: 'fast')

test('vulkan-validation-streams',
     bash_prog,
     args: [meson.project_source_root() / 'ffmpeg-patches' / 'test' /
            'vulkan-format-matrix-validation-self-test.sh'],
     workdir: meson.project_source_root(),
     suite: 'fast')

test('vulkan-sample-scale',
     python_prog,
     args: [meson.project_source_root() / 'scripts' / 'test-vulkan-sample-scale.py',
            '--cc'] + meson.get_compiler('c').cmd_array(),
     workdir: meson.project_source_root(),
     timeout: 180,
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
test('gen-grain-gate-curve-self-test',
     python_prog,
     args: ['-I', meson.project_source_root() / 'scripts' / 'gen-grain-gate-curve.py',
            '--self-test'],
     workdir: meson.project_source_root(),
     suite: 'fast')
test('gen-grain-gate-curve-check',
     python_prog,
     args: ['-I', meson.project_source_root() / 'scripts' / 'gen-grain-gate-curve.py',
            '--check'],
     workdir: meson.project_source_root(),
     suite: 'numpy')
test('gen-av1-grain-calibration-self-test',
     python_prog,
     args: ['-I', meson.project_source_root() / 'scripts' / 'gen-av1-grain-calibration.py',
            '--self-test'],
     workdir: meson.project_source_root(),
     suite: 'fast')
test('gen-h274-grain-calibration-self-test',
     python_prog,
     args: ['-I', meson.project_source_root() / 'scripts' / 'gen-h274-grain-calibration.py',
            '--self-test'],
     workdir: meson.project_source_root(),
     suite: 'fast')

# ADR-0111: bd_rate.py must enforce a monotonic guard on RD curves (strictly
# increasing bitrate and score, >= 2 points) and fail closed on invalid or
# disjoint inputs instead of returning an undefined metric.
test('bd-rate',
     python_prog,
     args: ['-I', meson.project_source_root() / 'scripts' / 'test-bd-rate.py'],
     workdir: meson.project_source_root(),
     suite: 'fast')

# Issue #300 / fixed/0300-synth-source-pixfmt: synth_source() must write raw
# clips in the requested pixfmt so 10-bit runs do not ingest 8-bit bytes.
test('synth-source-pixfmt',
     python_prog,
     args: ['-I', meson.project_source_root() / 'scripts' / 'test-synth-source-pixfmt.py'],
     workdir: meson.project_source_root(),
     timeout: 150,
     suite: 'fast')
test('synth-source-pixfmt-self-test',
     python_prog,
     args: ['-I', meson.project_source_root() / 'scripts' / 'test-synth-source-pixfmt.py',
            '--self-test'],
     workdir: meson.project_source_root(),
     timeout: 150,
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
  # ADR-0185: the offline checks of scripts/fetch-ffmpeg-series.sh (sha256 pin,
  # FFmpeg base, listed patches, archive members) refuse planted defects. Its
  # network checks (cosign, attestation) have their planted defects in the
  # script's --self-test, which the ffmpeg-stack CI job runs. Linux bash and
  # GNU tar, like the patch-stack scripts it serves.
  test('fetch-ffmpeg-series-fail-closed',
       python_prog,
       args: [meson.project_source_root() / 'scripts' / 'test-fetch-ffmpeg-series.py'],
       workdir: meson.project_source_root(),
       suite: 'fast')
  # CURL_MAX_TIME, CURL_STALL_TIME and CURL_RETRY_WINDOW: each of the three
  # download scripts refuses a value that is not a positive decimal integer
  # before any curl call. Not on Windows, for the native temp paths the
  # fetch-corpus-fail-closed note above describes.
  test('curl-env-validation',
       python_prog,
       args: [meson.project_source_root() / 'scripts' / 'test-curl-env-validation.py'],
       workdir: meson.project_source_root(),
       suite: 'fast')
endif

# The build-config self-test drives throwaway Git repositories. Started from a
# Git hook it inherits GIT_DIR/GIT_WORK_TREE/GIT_INDEX_FILE, and its fixtures then
# rewrote the developer's own .git/config and refs. Prove each self-test that
# builds fixture repositories leaves an invoking repository untouched.
if build_machine.system() != 'windows'
  test('build-config-selftest-isolation',
       python_prog,
       args: [meson.project_source_root() / 'scripts' / 'test-selftest-git-isolation.py'],
       workdir: meson.project_source_root(),
       timeout: 600,
       suite: 'fast')
  test('tag-release-selftest-isolation',
       python_prog,
       args: [meson.project_source_root() / 'scripts' / 'test-selftest-git-isolation.py',
              'bash', meson.project_source_root() / 'scripts' / 'release' / 'tag-release.sh',
              '--self-test'],
       workdir: meson.project_source_root(),
       timeout: 600,
       suite: 'fast')
  test('check-publish-ref-selftest-isolation',
       python_prog,
       args: [meson.project_source_root() / 'scripts' / 'test-selftest-git-isolation.py',
              'bash', meson.project_source_root() / 'scripts' / 'release' / 'check-publish-ref.sh',
              '--self-test'],
       workdir: meson.project_source_root(),
       timeout: 600,
       suite: 'fast')
  test('interop-constants-guard-selftest-isolation',
       python_prog,
       args: [meson.project_source_root() / 'scripts' / 'test-selftest-git-isolation.py',
              python_prog.full_path(), '-I',
              meson.project_source_root() / 'scripts' / 'check-interop-constants.py',
              '--self-test'],
       workdir: meson.project_source_root(),
       timeout: 600,
       suite: 'fast')
  test('check-cochange-selftest-isolation',
       python_prog,
       args: [meson.project_source_root() / 'scripts' / 'test-selftest-git-isolation.py',
              python_prog.full_path(), '-I',
              meson.project_source_root() / 'scripts' / 'check-cochange.py',
              '--self-test'],
       workdir: meson.project_source_root(),
       timeout: 600,
       suite: 'fast')
  test('dco-self-test',
       python_prog,
       args: ['-I', meson.project_source_root() / 'scripts' / 'check-dco.py',
              '--self-test'],
       workdir: meson.project_source_root(),
       timeout: 60,
       suite: 'fast')
  test('dco-selftest-isolation',
       python_prog,
       args: [meson.project_source_root() / 'scripts' / 'test-selftest-git-isolation.py',
              python_prog.full_path(), '-I',
              meson.project_source_root() / 'scripts' / 'check-dco.py',
              '--self-test'],
       workdir: meson.project_source_root(),
       timeout: 600,
       suite: 'fast')
  # ADR-0219: the ADR allocator discovers numbers across all local worktrees and
  # branch tips without network. The test drives throwaway Git repositories and
  # worktrees; MSYS2 path translation and POSIX bash fixtures stay Linux-first
  # (bash + git worktree fixtures; ADR-0149 split).
  test('adr-next-free',
       python_prog,
       args: ['-I', meson.project_source_root() / 'scripts' / 'test-adr-next-free.py'],
       workdir: meson.project_source_root(),
       timeout: 60,
       suite: 'fast')
  # ADR-0246: credential pattern scrub and testlog scanner self-test.
  test('meson-testlog-credentials',
       python_prog,
       args: ['-I', meson.project_source_root() / 'scripts' / 'test-meson-testlog-credentials.py'],
       workdir: meson.project_source_root(),
       timeout: 60,
       suite: 'fast')
  # Tester-kit packaging check self-test (fixture arguments must install cleanly).
  test('check-tester-kit-install',
       python_prog,
       args: ['-I', meson.project_source_root() / 'scripts' / 'test-check-tester-kit-install.py'],
       workdir: meson.project_source_root(),
       timeout: 60,
       suite: 'fast')
endif

# ADR-0246: run-meson-test.py waits for Meson as a child on Windows. Its
# deadline stops Meson with every test process it started, and a failed tree
# stop is named on stderr. Live processes on every platform, the Windows legs
# included (taskkill /T, SYNCHRONIZE-handle probe). Cases wait at most
# 2 s + 0.5 s + 0.1 s deadlines, 30 s per stop and 10 s per pid file.
test('run-meson-deadline',
     python_prog,
     args: ['-I', meson.project_source_root() / 'scripts' / 'test-run-meson-deadline.py'],
     workdir: meson.project_source_root(),
     timeout: 180,
     suite: 'fast')

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
# 1 harness build x 150 s deadline + margin (scripts/patch_harness.py).
test('nvenc-pelorus-mapping',
     python_prog,
     args: [meson.project_source_root() / 'scripts' / 'test-nvenc-pelorus-mapping.py',
            '--cc'] + meson.get_compiler('c').cmd_array(),
     workdir: meson.project_source_root(),
     timeout: 180,
     suite: 'fast')

# The shared C harness of the patch tests reports pass, fail and compile error
# apart and ends a stalled compiler or harness at its deadline (HISS-02).
# 3 cases at the default deadlines (2 x 150 s + 120 s) + 2 at 1 s + margin.
test('patch-harness',
     python_prog,
     args: ['-I', meson.project_source_root() / 'scripts' / 'test-patch-harness.py',
            '--cc'] + meson.get_compiler('c').cmd_array(),
     workdir: meson.project_source_root(),
     timeout: 480,
     suite: 'fast')

# Every harness test above declares a Meson timeout above its harness deadlines;
# the self-test plants a missing and a short timeout.
test('harness-test-timeouts',
     python_prog,
     args: ['-I', meson.project_source_root() / 'scripts' / 'check-harness-test-timeouts.py'],
     workdir: meson.project_source_root(),
     suite: 'fast')

test('harness-test-timeouts-self-test',
     python_prog,
     args: ['-I', meson.project_source_root() / 'scripts' / 'check-harness-test-timeouts.py',
            '--self-test'],
     workdir: meson.project_source_root(),
     suite: 'fast')

# Every Python except clause with bare pass must carry an explanatory comment;
# the self-test exercises bare passes, comments, and strings with hash symbols.
# The tree scan lists files with git, which the MSYS2 UCRT64 leg does not put
# on PATH (CI run 41645525072: FileNotFoundError for git). The self-test
# runs there; the Linux jobs scan the tree on every PR.
if build_machine.system() != 'windows'
  test('python-empty-except',
       python_prog,
       args: ['-I', meson.project_source_root() / 'scripts' / 'check-python-empty-except.py'],
       workdir: meson.project_source_root(),
       suite: 'fast')
endif

test('python-empty-except-self-test',
     python_prog,
     args: ['-I', meson.project_source_root() / 'scripts' / 'check-python-empty-except.py',
            '--self-test'],
     workdir: meson.project_source_root(),
     suite: 'fast')

# Snapshot cleanup must not chmod a symlink target (PR 538 review). Creating
# the planted symlink needs a privilege Windows CI does not grant.
if build_machine.system() != 'windows'
  test('writable-retry-symlink',
       python_prog,
       args: ['-I', meson.project_source_root() / 'scripts' / 'test-writable-retry.py'],
       workdir: meson.project_source_root(),
       suite: 'fast')
endif

# Git-tree scans must skip Windows CI, where git is not on PATH.
# This source-only check and its planted cases run on every platform.
test('meson-windows-git',
     python_prog,
     args: ['-I', meson.project_source_root() / 'scripts' / 'check-meson-windows-git.py'],
     workdir: meson.project_source_root(),
     timeout: 60,
     suite: 'fast')

test('meson-windows-git-self-test',
     python_prog,
     args: ['-I', meson.project_source_root() / 'scripts' / 'check-meson-windows-git.py',
            '--self-test'],
     workdir: meson.project_source_root(),
     timeout: 60,
     suite: 'fast')

# Every curl download needs retry >= 5, speed-time, retry-max-time > max-time,
# so hangs and transient 504s are retried (CI runs 42044634889, 42464703696).
if build_machine.system() != 'windows'
  test('curl-retry',
       python_prog,
       args: ['-I', meson.project_source_root() / 'scripts' / 'check-curl-retry.py'],
       workdir: meson.project_source_root(),
       timeout: 60,
       suite: 'fast')
endif

test('curl-retry-self-test',
     python_prog,
     args: ['-I', meson.project_source_root() / 'scripts' / 'check-curl-retry.py',
            '--self-test'],
     workdir: meson.project_source_root(),
     timeout: 60,
     suite: 'fast')

# Every ffmpeg run whose showinfo log is parsed needs -nostats, or the progress
# line splits side-data lines (CI run 42494841487). The tree scan lists files
# with git; see python-empty-except above for why it skips Windows.
if build_machine.system() != 'windows'
  test('showinfo-nostats',
       python_prog,
       args: ['-I', meson.project_source_root() / 'scripts' / 'check-showinfo-nostats.py'],
       workdir: meson.project_source_root(),
       timeout: 60,
       suite: 'fast')
endif

test('showinfo-nostats-self-test',
     python_prog,
     args: ['-I', meson.project_source_root() / 'scripts' / 'check-showinfo-nostats.py',
            '--self-test'],
     workdir: meson.project_source_root(),
     timeout: 60,
     suite: 'fast')

# Planted soak defects, including a progress-line split (vulkan-lavapipe-filters.sh).
if build_machine.system() != 'windows'
  test('vulkan-lavapipe-filters-self-test',
       bash_prog,
       args: [meson.project_source_root() / 'ffmpeg-patches' / 'test' / 'vulkan-lavapipe-filters.sh',
              '--self-test'],
       workdir: meson.project_source_root(),
       timeout: 120,
       suite: 'fast')
endif

# A try block that guards Path.read_text() against OSError must also catch
# UnicodeDecodeError, or a file with invalid bytes escapes as a traceback.
# The tree scan lists files with git; see python-empty-except above for why
# it skips the Windows leg.
if build_machine.system() != 'windows'
  test('python-decode-errors',
       python_prog,
       args: ['-I', meson.project_source_root() / 'scripts' / 'check-python-decode-errors.py'],
       workdir: meson.project_source_root(),
       suite: 'fast')
endif

test('python-decode-errors-self-test',
     python_prog,
     args: ['-I', meson.project_source_root() / 'scripts' / 'check-python-decode-errors.py',
            '--self-test'],
     workdir: meson.project_source_root(),
     suite: 'fast')

# Every tracked Python script must parse CLI arguments with stdlib argparse;
# no hand-indexed argv. The self-test exercises AST shapes and planted bad cases.
# The tree scan lists files with git, which the MSYS2 UCRT64 leg does not put
# on PATH (CI run 41645525072: FileNotFoundError for git). The self-test
# runs there; the Linux jobs scan the tree on every PR.
if build_machine.system() != 'windows'
  test('python-argv',
       python_prog,
       args: ['-I', meson.project_source_root() / 'scripts' / 'check-python-argv.py'],
       workdir: meson.project_source_root(),
       suite: 'fast')
endif

test('python-argv-self-test',
     python_prog,
     args: ['-I', meson.project_source_root() / 'scripts' / 'check-python-argv.py',
            '--self-test'],
     workdir: meson.project_source_root(),
     suite: 'fast')

# Every tracked file outside docs/ and an explicit allow-list must avoid hardcoded
# upstream repository and container registry name literals (#499).
# The tree scan lists files with git, which Windows CI does not guarantee on PATH.
if build_machine.system() != 'windows'
  test('repo-name-literals',
       python_prog,
       args: ['-I', meson.project_source_root() / 'scripts' / 'check-repo-name-literals.py'],
       workdir: meson.project_source_root(),
       suite: 'fast')
endif

test('repo-name-literals-self-test',
     python_prog,
     args: ['-I', meson.project_source_root() / 'scripts' / 'check-repo-name-literals.py',
            '--self-test'],
     workdir: meson.project_source_root(),
     suite: 'fast')

# Per-frame external-ME-hint setup of patch 0008 (BUG-031): a frame without
# Pelorus motion data must still submit one zero-MV candidate per block, or the
# NVENC session fails the whole encode.
# 2 harness builds x 150 s deadline + margin (scripts/patch_harness.py).
test('nvenc-me-hints',
     python_prog,
     args: [meson.project_source_root() / 'scripts' / 'test-nvenc-me-hints.py',
            '--cc'] + meson.get_compiler('c').cmd_array(),
     workdir: meson.project_source_root(),
     timeout: 330,
     suite: 'fast')

# SVT-AV1 keeps the last ROI event as a sticky pointer (BUG-030); the event
# builder of patch 0013 must replace it with a neutral event when ROI data ends.
# 1 harness build x 150 s deadline + margin (scripts/patch_harness.py).
test('svtav1-roi-sticky',
     python_prog,
     args: [meson.project_source_root() / 'scripts' / 'test-svtav1-roi-sticky.py',
            '--cc'] + meson.get_compiler('c').cmd_array(),
     workdir: meson.project_source_root(),
     timeout: 180,
     suite: 'fast')

# vf_pelorus_mc_vulkan keeps its host-side MV arithmetic in a plain-C header
# (ffmpeg-patches/files/pelorus_mc_stats.h): the Q2 -> integer-pel predictor
# boundary (BUG-009) and the O(n) p95 select, proven bit-identical to the old
# O(n^2) scan (BUG-014). It has no libav* dependency, so it is tested here.
if get_option('tests')
  mc_stats_test = executable(
    'pelorus_mc_stats_test',
    files('ffmpeg-patches/test/mc_stats_test.c'),
    include_directories: include_directories('ffmpeg-patches/files', 'libpelorus/test'),
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
    include_directories: include_directories('ffmpeg-patches/files', 'libpelorus/test'),
    dependencies: [libpelorus_dep, cc.find_library('m', required: false)],
  )
  test('analyze-maps', analyze_maps_test, suite: 'fast')
  # The pre-ABI-1.4 packing path (pel_blob_pack + copy), forced on 1.4 headers.
  analyze_maps_legacy_test = executable(
    'pelorus_analyze_maps_legacy_test',
    files('ffmpeg-patches/test/analyze_maps_test.c'),
    c_args: ['-DPEL_AN_FORCE_LEGACY_PACK'],
    include_directories: include_directories('ffmpeg-patches/files', 'libpelorus/test'),
    dependencies: [libpelorus_dep, cc.find_library('m', required: false)],
  )
  test('analyze-maps-legacy-pack', analyze_maps_legacy_test, suite: 'fast')
endif

bench_record_script = meson.project_source_root() / 'scripts' / 'bench' / 'check-record.py'
test('bench-record',
     python_prog,
     args: ['-I', bench_record_script, '--self-test'],
     suite: 'fast')

bench_make_record_script = meson.project_source_root() / 'scripts' / 'bench' / 'make-record.py'
test('bench-make-record',
     python_prog,
     args: ['-I', bench_make_record_script, '--self-test'],
     suite: 'fast')

json_shape_script = meson.project_source_root() / 'tools' / 'tester' / 'pelorus_json_shape.py'
test('json-shape',
     python_prog,
     args: ['-I', json_shape_script, '--self-test'],
     suite: 'fast')

# Issue #500: interop constant parser self-test and repository guard check.
interop_consts_script = meson.project_source_root() / 'tools' / 'tester' / 'pelorus_interop_consts.py'
test('interop-consts',
     python_prog,
     args: ['-I', interop_consts_script, '--self-test'],
     suite: 'fast')

interop_guard_script = meson.project_source_root() / 'scripts' / 'check-interop-constants.py'
# The tree scan lists files with git, which the MSYS2 UCRT64 leg does not put
# on PATH (CI run 41408892182: FileNotFoundError for git). The Linux jobs scan
# the tree on every PR.
if build_machine.system() != 'windows'
  test('interop-constants-guard',
       python_prog,
       args: ['-I', interop_guard_script],
       workdir: meson.project_source_root(),
       suite: 'fast')
  test('interop-constants-guard-self-test',
       python_prog,
       args: ['-I', interop_guard_script, '--self-test'],
       workdir: meson.project_source_root(),
       suite: 'fast')
endif

# Issue #267 / ADR-0181: hevc_nvenc's udu_sei path keeps each picture's
# parameter sets and SEI within NVENC's 1024-byte limit
# (ffmpeg-patches/files/pelorus_sei_fit.h; the QSV budget of issue #286 is
# pelorus_sei_fit_qsv.h): a Pelorus blob that does not fit
# is carried as its scalar sections, byte-identical to the maps=0 blob, and
# anything else that does not fit is dropped, never cut short.
#
# The fit rules are Pelorus's own (ADR-0236): FFmpeg 96b7f8281a merged a
# 512-byte payload check of its own, and nothing is read from the shared
# FFmpeg fix series. Each sei-fit-planted-<rule> test builds the same test
# against a copy of pelorus_sei_fit.h with one rule changed
# (scripts/plant-header.py); its checks must fail, and the planted build
# exits 0 only then, so the tester kit runs it like any fast test.
plant_header = meson.project_source_root() / 'scripts' / 'plant-header.py'
test('plant-header',
     python_prog,
     args: ['-I', plant_header, '--self-test'],
     suite: 'fast')
if get_option('tests')
  sei_fit_test = executable(
    'pelorus_sei_fit_test',
    files('ffmpeg-patches/test/sei_fit_test.c'),
    include_directories: include_directories('ffmpeg-patches/files'),
    dependencies: [libpelorus_dep, cc.find_library('m', required: false)],
  )
  test('sei-fit', sei_fit_test, suite: 'fast')
  foreach rule : ['reserve', 'payload', 'epb', 'truncated', 'charge', 'wrap']
    planted_h = custom_target(
      'pelorus_sei_fit_' + rule + '.h',
      input: 'ffmpeg-patches/files/pelorus_sei_fit.h',
      output: 'pelorus_sei_fit_' + rule + '.h',
      command: [python_prog, '-I', plant_header, '@INPUT@', '@OUTPUT@', rule],
    )
    planted_c_args = ['-DPEL_SEI_FIT_PLANTED="pelorus_sei_fit_' + rule + '.h"']
    if rule == 'wrap'
      # The planted header removes the saturating add on purpose; GCC -O3 proves
      # the wrap statically (array-bounds, stringop-overflow); the runtime check
      # is what the twin tests.
      planted_c_args += cc.get_supported_arguments([
        '-Wno-array-bounds',
        '-Wno-stringop-overflow',
      ])
    endif
    planted_exe = executable(
      'pelorus_sei_fit_planted_' + rule + '_test',
      files('ffmpeg-patches/test/sei_fit_test.c'), planted_h,
      c_args: planted_c_args,
      include_directories: [include_directories('.'),
                            include_directories('ffmpeg-patches/files')],
      dependencies: [libpelorus_dep, cc.find_library('m', required: false)],
    )
    test('sei-fit-planted-' + rule, planted_exe, suite: 'fast')
  endforeach
endif

# Issue #82 / ADR-0210: classification of direct and x265-wrapped user data
# unregistered SEI payloads for pelorus_fgs (ffmpeg-patches/files/pelorus_udu_wrap.h).
if get_option('tests')
  udu_wrap_test = executable(
    'pelorus_udu_wrap_test',
    files('ffmpeg-patches/test/udu_wrap_test.c'),
    include_directories: include_directories('ffmpeg-patches/files', 'libpelorus/test'),
    dependencies: [libpelorus_dep, cc.find_library('m', required: false)],
  )
  test(
    'udu-wrap',
    udu_wrap_test,
    args: [meson.project_source_root() / 'ffmpeg-patches' / 'test' / 'fixtures' / 'udu-wrap-vectors.txt'],
    suite: 'fast',
  )
endif

# Issue #500: interop constant mirror equality between libpelorus interop.h
# and FFmpeg-side pelorus_sei_fit.h.
if get_option('tests')
  interop_constants_test = executable(
    'pelorus_interop_constants_test',
    files('ffmpeg-patches/test/interop_constants_test.c'),
    include_directories: include_directories('ffmpeg-patches/files'),
    dependencies: libpelorus_dep,
  )
  test('interop-constants', interop_constants_test, suite: 'fast')
endif

# Issue #103 / ADR-0184: the DRM format modifier rule of the tiling=drm output
# pools (ffmpeg-patches/files/pelorus_drm_modifier.h): storage-capable modifiers
# without auxiliary planes, intersected with drm_modifiers, and the logged
# DRM_FORMAT_MOD_LINEAR substitution when the intersection is empty.
if get_option('tests')
  drm_modifier_test = executable(
    'pelorus_drm_modifier_test',
    files('ffmpeg-patches/test/drm_modifier_test.c'),
    include_directories: include_directories('ffmpeg-patches/files'),
  )
  test('drm-modifier', drm_modifier_test, suite: 'fast')
endif

# build-and-run.sh passes the NVENC smoke's skip (exit 77) only when the caller
# names its reason (ADR-0189); --self-test plants an unnamed skip, a skip for
# another reason and a 77 without a SKIP line.
test('named-skip-self-test',
     bash_prog,
     args: [meson.project_source_root() / 'ffmpeg-patches' / 'test' / 'named-skip.sh',
            '--self-test'],
     workdir: meson.project_source_root(),
     suite: 'fast')

# Issue #160: plant an overlapping hunk and require the cumulative shared +
# vmafx + Pelorus series checker to reject both apply orders with patch-pair,
# file and hunk diagnostics. The fixture is synthetic and needs no FFmpeg tree,
# but it drives git, which the MSYS2 UCRT64 leg does not put on PATH (CI run
# 38782391790: "git: command not found"). The checker applies patches to an
# FFmpeg worktree on Linux build hosts, and the Linux jobs run both tests on
# every PR (same split as ADR-0149).
if build_machine.system() != 'windows'
  test('series-coexistence-self-test',
       bash_prog,
       args: [meson.project_source_root() / 'ffmpeg-patches' / 'test' / 'series-coexistence.sh',
              '--self-test'],
       workdir: meson.project_source_root(),
       suite: 'fast')
  test('series-coexistence-selftest-isolation',
       python_prog,
       args: [meson.project_source_root() / 'scripts' / 'test-selftest-git-isolation.py',
              'bash', meson.project_source_root() / 'ffmpeg-patches' / 'test' / 'series-coexistence.sh',
              '--self-test'],
       workdir: meson.project_source_root(),
       timeout: 600,
       suite: 'fast')
endif

# The hardware smoke for the same pools: --self-test plants defects into its
# checks; the GPU run needs FFMPEG_BIN and a VAAPI-capable RENDER_NODE and
# otherwise exits 77 (skip) with the reason. It belongs in the fast suite only
# because it skips by default: nothing sets FFMPEG_BIN in CI or `make
# verify-native`, so the suite never needs a GPU; export FFMPEG_BIN and
# RENDER_NODE to run it on a workstation.
test('vulkan-drm-map-self-test',
     bash_prog,
     args: [meson.project_source_root() / 'ffmpeg-patches' / 'test' / 'vulkan-drm-map-smoke.sh',
            '--self-test'],
     workdir: meson.project_source_root(),
     suite: 'fast')
if build_machine.system() != 'windows'
  test('vulkan-drm-map',
       bash_prog,
       args: [meson.project_source_root() / 'ffmpeg-patches' / 'test' / 'vulkan-drm-map-smoke.sh'],
       workdir: meson.project_source_root(),
       timeout: 600,
       suite: 'fast')
endif

# Documented zero-copy recipes run verbatim against hardware devices (ADR-0240):
# --check-doc enforces section and marker coverage, --self-test plants defects
# into all assertions, and the smoke skips by default when FFMPEG_BIN is unset.
test('doc-recipes-self-test',
     python_prog,
     args: [meson.project_source_root() / 'ffmpeg-patches' / 'test' / 'doc-recipes-smoke.py',
            '--self-test'],
     workdir: meson.project_source_root(),
     suite: 'fast')
test('doc-recipes-check-doc',
     python_prog,
     args: [meson.project_source_root() / 'ffmpeg-patches' / 'test' / 'doc-recipes-smoke.py',
            '--check-doc'],
     workdir: meson.project_source_root(),
     suite: 'fast')
if build_machine.system() != 'windows'
  test('doc-recipes',
       python_prog,
       args: [meson.project_source_root() / 'ffmpeg-patches' / 'test' / 'doc-recipes-smoke.py'],
       workdir: meson.project_source_root(),
       timeout: 600,
       suite: 'fast')
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

# ADR-0239: planted-defect recall for the float-identity Semgrep rule.
# Refuses == and != between computed floating-point values in test code.
# Three sequential Semgrep scans at SEMGREP_TIMEOUT_S (180 s) each stay under
# the 600 s Meson bound, so a hung scan is named by the script, not killed by
# Meson's 30 s default (a local run takes about 4 s).
# This harness calls git, which the MSYS2 UCRT64 leg does not put on PATH
# (CI runs 41645525072 and 42050155443). Source-only self-tests run there;
# the Linux jobs exercise git-dependent harnesses on every PR.
if build_machine.system() != 'windows'
  test('semgrep-float-identity',
       python_prog,
       args: [meson.project_source_root() / 'scripts' / 'test-semgrep-float-identity.py'],
       workdir: meson.project_source_root(),
       timeout: 600,
       suite: 'fast')
endif

# Smoke skip reasons name the first error line, not a Mesa warning: the
# rule pelorus-smoke-skip-reason-head refuses head -n 1 reasons and the
# test runs pel_first_error from each smoke on planted log fixtures.
test('semgrep-smoke-skip-reason',
     python_prog,
     args: [meson.project_source_root() / 'scripts' / 'test-semgrep-smoke-skip-reason.py'],
     workdir: meson.project_source_root(),
     timeout: 600,
     suite: 'fast')

# Executables from shutil.which reach subprocess only after a resolve,
# is_file and X_OK check: pelorus-validate-which-executable and its
# planted cases; three Semgrep scans at 180 s each stay under 600 s.
# This harness calls git, which the MSYS2 UCRT64 leg does not put on PATH
# (CI runs 41645525072 and 42050155443). Source-only self-tests run there;
# the Linux jobs exercise git-dependent harnesses on every PR.
if build_machine.system() != 'windows'
  test('semgrep-which-executable',
       python_prog,
       args: [meson.project_source_root() / 'scripts' / 'test-semgrep-which-executable.py'],
       workdir: meson.project_source_root(),
       timeout: 600,
       suite: 'fast')
endif

# File permissions set by os.chmod and Path.chmod grant owner access only
# (0o600/0o400 for data, 0o500 for stubs); pelorus-chmod-owner-only defends
# against CodeQL and Opengrep mode flags; three Semgrep scans stay under 600 s.
# This harness calls git, which the MSYS2 UCRT64 leg does not put on PATH
# (CI runs 41645525072 and 42050155443). Source-only self-tests run there;
# the Linux jobs exercise git-dependent harnesses on every PR.
if build_machine.system() != 'windows'
  test('semgrep-chmod-mode',
       python_prog,
       args: [meson.project_source_root() / 'scripts' / 'test-semgrep-chmod-mode.py'],
       workdir: meson.project_source_root(),
       timeout: 600,
       suite: 'fast')
  test('semgrep-wall-clock',
       python_prog,
       args: [meson.project_source_root() / 'scripts' / 'test-semgrep-wall-clock.py'],
       workdir: meson.project_source_root(),
       timeout: 600,
       suite: 'fast')
  # A command-line argument reaches printf, sprintf or vsprintf only through
  # a named local: pelorus-c-printf-argv refuses the call shapes Codacy's
  # Opengrep insecure-use-printf-fn reports (pull request 578).
  test('semgrep-printf-argv',
       python_prog,
       args: ['-I', meson.project_source_root() / 'scripts' / 'test-semgrep-printf-argv.py'],
       workdir: meson.project_source_root(),
       timeout: 600,
       suite: 'fast')
  # The partial output of a caught subprocess.TimeoutExpired is bytes on
  # POSIX even with text=True: pelorus-py-timeout-partial-output requires
  # _partial_text() around it (pull request 578).
  test('semgrep-timeout-output',
       python_prog,
       args: ['-I', meson.project_source_root() / 'scripts' / 'test-semgrep-timeout-output.py'],
       workdir: meson.project_source_root(),
       timeout: 600,
       suite: 'fast')
  # The praetor HISS-02 scan passes a subprocess, urlopen or requests call
  # with a ** unpack as undecidable: pelorus-py-io-unpack-deadline requires
  # timeout= on such a call, or on the dict first (pull request 579).
  test('semgrep-io-unpack-deadline',
       python_prog,
       args: ['-I', meson.project_source_root() / 'scripts' / 'test-semgrep-io-unpack-deadline.py'],
       workdir: meson.project_source_root(),
       timeout: 600,
       suite: 'fast')
  # Meson below 1.1 ignores the newer options file name and refuses the
  # project's -D options: pelorus-meson-options-file keeps meson_options.txt
  # while the meson_version floor stays below 1.1 (pull request 578).
  test('semgrep-meson-options',
       python_prog,
       args: ['-I', meson.project_source_root() / 'scripts' / 'test-semgrep-meson-options.py'],
       workdir: meson.project_source_root(),
       timeout: 600,
       suite: 'fast')
endif

# Shared Semgrep test harness enforcement: test scripts must import
# semgrep_harness and not re-implement its members (ADR-0239/issue #538).
test('semgrep-harness-use-self-test',
     python_prog,
     args: ['-I', meson.project_source_root() / 'scripts' / 'check-semgrep-harness.py', '--self-test'],
     workdir: meson.project_source_root(),
     timeout: 60,
     suite: 'fast')
# The tree scan lists files with git, which the MSYS2 UCRT64 leg does not put
# on PATH (CI runs 41645525072 and 42050155443). The self-test runs there;
# the Linux jobs scan the tree on every PR.
if build_machine.system() != 'windows'
  test('semgrep-harness-use',
       python_prog,
       args: ['-I', meson.project_source_root() / 'scripts' / 'check-semgrep-harness.py'],
       workdir: meson.project_source_root(),
       timeout: 60,
       suite: 'fast')
endif

# clang-tidy lane-files.txt generator and synchronization check (ADR-0168).
# The lane is the Linux build with tests and tools on (the ci.yml core job);
# Windows and builds with either option off compile a different unit set, so
# the drift check runs only in that configuration.
gen_tidy_lane_files = meson.project_source_root() / 'scripts' / 'gen-tidy-lane-files.py'
test('gen-tidy-lane-files-self-test',
     python_prog,
     args: ['-I', gen_tidy_lane_files, '--self-test'],
     workdir: meson.project_source_root(),
     timeout: 60,
     suite: 'fast')
if host_machine.system() == 'linux' and get_option('tests') and get_option('tools')
  test('tidy-lane-files-check',
       python_prog,
       args: ['-I', gen_tidy_lane_files, '--check', '--build-dir', meson.project_build_root()],
       workdir: meson.project_source_root(),
       timeout: 60,
       suite: 'fast')
endif

# ffmpeg-patches/generate.sh rebase-notes heading line logic. The test drives
# git in a throwaway repository, and the MSYS2 UCRT64 leg does not put git on
# PATH (see series-coexistence above), so both tests run on the other hosts.
if build_machine.system() != 'windows'
  test('generate-regen-line',
       bash_prog,
       args: [meson.project_source_root() / 'scripts' / 'test-generate-regen-line.sh'],
       workdir: meson.project_source_root(),
       timeout: 60,
       suite: 'fast')
  test('generate-regen-line-selftest-isolation',
       python_prog,
       args: [meson.project_source_root() / 'scripts' / 'test-selftest-git-isolation.py',
              'bash', meson.project_source_root() / 'scripts' / 'test-generate-regen-line.sh'],
       workdir: meson.project_source_root(),
       timeout: 120,
       suite: 'fast')
endif
