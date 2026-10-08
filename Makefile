# Pelorus native + Praetor verification entry points.
PRAETORCTL ?= $(shell command -v standardsctl 2>/dev/null || command -v praetorctl 2>/dev/null || echo standardsctl)
BUILD_DIR ?= build

.PHONY: all verify-all verify-native configure build test format-check tidy \
	docs-check compile-context compile-context-verify audit hooks-install

all: build

verify-all: compile-context-verify audit verify-native

verify-native: build test format-check tidy docs-check

configure:
	@if test -f "$(BUILD_DIR)/meson-private/coredata.dat"; then \
		meson setup "$(BUILD_DIR)" --reconfigure; \
	else \
		meson setup "$(BUILD_DIR)"; \
	fi

build: configure
	ninja -C "$(BUILD_DIR)"

test: build
	meson test -C "$(BUILD_DIR)" --suite=fast --print-errorlogs

format-check:
	clang-format --dry-run --Werror $$(find libpelorus tools -type f \( -name '*.c' -o -name '*.h' \))

# The clang_tidy lane of .standards.yaml: one list drives this run, the ci.yml
# core job and the audit's translation-unit coverage check (ADR-0168).
TIDY_FILES := .config/clang-tidy/lane-files.txt

tidy: build
	grep -Ev '^(#|$$)' $(TIDY_FILES) | xargs clang-tidy -p "$(BUILD_DIR)"

docs-check:
	bash scripts/release/concat-changelog-fragments.sh --check

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
