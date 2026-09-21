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

tidy: build
	clang-tidy -p "$(BUILD_DIR)" libpelorus/src/*.c

docs-check:
	bash scripts/release/concat-changelog-fragments.sh --check

compile-context:
	$(PRAETORCTL) compile-context

compile-context-verify:
	$(PRAETORCTL) compile-context --verify

audit:
	$(PRAETORCTL) audit

# Explicit opt-in only. Adoption and CI never install or replace shared hooks.
hooks-install:
	@command -v lefthook >/dev/null || { echo "error: lefthook not found" >&2; exit 1; }
	lefthook install
