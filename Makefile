# Pelorus native + Praetor verification entry points.
# PRAETORCTL comes from the engine's make fragment: the engine pinned at
# PRAETOR_REF, resolved by .config/lefthook/engine.sh, not the PATH one (ADR-0187).
# PRAETORCTL=/path/to/engine on the command line or in the environment overrides it.
include .config/praetor/engine.mk
BUILD_DIR ?= build

.PHONY: all verify-all verify-native verify-offline configure build test format-check tidy \
	docs-check compile-context compile-context-verify audit hooks-install python-lint patches

all: build

verify-all: compile-context-verify audit verify-native

# verify-offline runs the native verification steps that require no network access
# (build, test, format-check, tidy, docs-check). python-lint is left out because
# it downloads and installs Ruff and zizmor from PyPI into a virtualenv.
verify-offline: build test format-check tidy docs-check

verify-native: verify-offline python-lint

configure:
	@if test -f "$(BUILD_DIR)/meson-private/coredata.dat"; then \
		meson setup "$(BUILD_DIR)" --reconfigure; \
	else \
		meson setup "$(BUILD_DIR)"; \
	fi

build: configure
	ninja -C "$(BUILD_DIR)"

# The credential scan runs even when the tests fail; the first failure status wins.
test: build
	@status=0; \
	python3 -I scripts/run-meson-test.py -C "$(BUILD_DIR)" --suite=fast --print-errorlogs || status=$$?; \
	python3 -I scripts/check-test-skips.py "$(BUILD_DIR)/meson-logs/testlog.json" || { rc=$$?; [ "$$status" -ne 0 ] || status=$$rc; }; \
	python3 -I scripts/check-testlog-credentials.py "$(BUILD_DIR)" || { rc=$$?; [ "$$status" -ne 0 ] || status=$$rc; }; \
	python3 -I scripts/check-tester-kit-install.py --run-kit "$(BUILD_DIR)" || { rc=$$?; [ "$$status" -ne 0 ] || status=$$rc; }; \
	exit $$status

format-check:
	clang-format --dry-run --Werror $$(find libpelorus tools -type f \( -name '*.c' -o -name '*.h' \))

# The clang_tidy lane of .standards.yaml: one list drives this run, the ci.yml
# core job and the audit's translation-unit coverage check (ADR-0168).
TIDY_FILES := .config/clang-tidy/lane-files.txt

tidy: build
	grep -Ev '^(#|$$)' $(TIDY_FILES) | xargs clang-tidy -p "$(BUILD_DIR)"

docs-check:
	python3 -I scripts/check-interop-abi-history.py --self-test
	python3 -I scripts/check-interop-abi-history.py
	python3 -I scripts/release/check-changelog-staleness.py --self-test
	python3 -I scripts/release/check-candidate-legs.py --self-test
	bash scripts/release/tag-release.sh --self-test
	bash scripts/release/check-publish-ref.sh --self-test
	bash scripts/release/extract-release-notes.sh --self-test
	python3 -I scripts/docs/render-counts.py --self-test
	python3 -I scripts/docs/render-counts.py
	bash scripts/release/write-patch-notice.sh --self-test
	python3 -I scripts/adr/check-status.py --self-test
	python3 -I scripts/adr/check-status.py
	python3 -I scripts/check-codacy-config.py --self-test
	python3 -I scripts/check-codacy-config.py
	python3 -I scripts/check-mirror-contract.py --self-test
	python3 -I scripts/check-mirror-contract.py
	python3 -I scripts/check-cochange.py --self-test
	python3 -I scripts/check-cochange.py
	python3 -I scripts/check-doc-recipes.py --self-test
	python3 -I scripts/check-doc-recipes.py
	python3 -I scripts/check-ci-test-wiring.py --self-test
	python3 -I scripts/check-ci-test-wiring.py
	python3 -I ffmpeg-patches/test/doc-recipes-smoke.py --self-test
	python3 -I ffmpeg-patches/test/doc-recipes-smoke.py --check-doc
	python3 -I scripts/check-filter-stance.py --self-test
	python3 -I scripts/check-filter-stance.py
	python3 -I scripts/check-filter-knob-contracts.py --self-test
	python3 -I scripts/check-filter-knob-contracts.py
	python3 -I tools/tester/pelorus_tester_report.py --self-test
	python3 -I scripts/windows/check-pe-imports.py --self-test
	python3 -I scripts/check-devcontainer-image.py --self-test
	bash ffmpeg-patches/test/fgs-sidedata-smoke.sh --self-test
	! bash ffmpeg-patches/test/fgs-sidedata-smoke.sh --self-test --disable wrapped >/dev/null 2>&1
	! bash ffmpeg-patches/test/fgs-sidedata-smoke.sh --self-test --disable kept >/dev/null 2>&1
	python3 -I scripts/hardware-reports/check-reports.py --self-test
	python3 -I scripts/hardware-reports/check-reports.py
	python3 -I scripts/hardware-reports/generate-index.py --self-test
	python3 -I scripts/hardware-reports/generate-index.py --check

# Outside $(BUILD_DIR): a directory there before `meson setup` blocks setup.
# Quoted in every recipe line; the self-test plants a path with a space.
PYTHON_LINT_ENV ?= .cache/python-lint

python-lint:
	@bash scripts/check-python-lint.sh --ensure "$(PYTHON_LINT_ENV)" >/dev/null
	PYTHON_LINT_ENV="$(PYTHON_LINT_ENV)" bash scripts/check-python-lint.sh --self-test
	PYTHON_LINT_ENV="$(PYTHON_LINT_ENV)" bash scripts/check-python-lint.sh

compile-context:
	$(PRAETORCTL) compile-context

compile-context-verify:
	$(PRAETORCTL) compile-context --verify

# Same ratchet as the hosted Standards gate (ADR-0145): touched files keep their
# baselined findings but may not gain one; the baseline may not grow against
# AUDIT_BASE unless the increase carries a recorded reason.
# AUDIT_FLAGS=--offline reads nothing from the forge; the pre-commit hook uses it.
AUDIT_BASE ?= origin/master
AUDIT_DEBT_REASON ?= ADR-0145 baseline ratchet; touched files keep baselined debt but may not add findings; zero-debt mode deferred
AUDIT_FLAGS ?=

audit:
	$(PRAETORCTL) audit $(AUDIT_FLAGS) --base "$(AUDIT_BASE)" --touched-debt-delta-reason "$(AUDIT_DEBT_REASON)"

# Regenerate the FFmpeg patch stack from ffmpeg-patches/files/ (mirrors generate.sh).
# FFMPEG_REPO is required and must name a local FFmpeg checkout.
patches:
	$(if $(FFMPEG_REPO),,$(error FFMPEG_REPO is required (must name a local FFmpeg checkout)))
	FFMPEG_REPO="$(FFMPEG_REPO)" bash ffmpeg-patches/generate.sh

# Explicit opt-in only. CI never installs hooks; adoption installs them only in
# a checkout whose lefthook.yml is an unedited Praetor rendering, which this one
# is not. Re-run after lefthook.yml changes (no_auto_install).
hooks-install:
	@command -v lefthook >/dev/null || { echo "error: lefthook not found" >&2; exit 1; }
	lefthook install

# BEGIN praetor documentation gate
.PHONY: docs-lint docs-figures
verify-all: docs-lint docs-figures
docs-lint:
	@node tools/markdownlint/verify.mjs
docs-figures:
	@node tools/figures/build.mjs check
	@node tools/figures/build.mjs sources
# END praetor documentation gate
