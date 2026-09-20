#!/usr/bin/env python3
"""Validate Pelorus's machine-maintained build dependency contract."""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "build-config.env"
EXPECTED_KEYS = {"FFMPEG_REMOTE", "FFMPEG_TAG", "FFMPEG_COMMIT"}
RENOVATE_MARKER = "# renovate: datasource=github-tags depName=FFmpeg/FFmpeg"
CONSUMERS = (
    ROOT / "ffmpeg-patches" / "generate.sh",
    ROOT / "ffmpeg-patches" / "test" / "build-and-run.sh",
)
RELEASE_TAG = re.compile(r"(?<![A-Za-z0-9_])n\d+\.\d+\.\d+(?![A-Za-z0-9_])")


def parse_assignments(text: str) -> tuple[dict[str, str], list[str]]:
    """Parse the deliberately tiny KEY=value format without executing it."""
    values: dict[str, str] = {}
    errors: list[str] = []

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Z][A-Z0-9_]*)=([^\s]+)", line)
        if match is None:
            errors.append(f"build-config.env:{line_number}: invalid assignment")
            continue
        key, value = match.groups()
        if key in values:
            errors.append(f"build-config.env:{line_number}: duplicate {key}")
            continue
        values[key] = value

    unknown = sorted(values.keys() - EXPECTED_KEYS)
    if unknown:
        errors.append(f"build-config.env: unexpected keys: {', '.join(unknown)}")
    missing = sorted(EXPECTED_KEYS - values.keys())
    if missing:
        errors.append(f"build-config.env: missing keys: {', '.join(missing)}")
    return values, errors


def validate_config(text: str) -> list[str]:
    """Validate both values and the contiguous layout Renovate extracts."""
    values, errors = parse_assignments(text)

    if RENOVATE_MARKER not in text.splitlines():
        errors.append("build-config.env: missing FFmpeg Renovate marker")
    if values.get("FFMPEG_REMOTE") != "https://github.com/FFmpeg/FFmpeg.git":
        errors.append("FFMPEG_REMOTE: expected the official FFmpeg GitHub remote")
    if not re.fullmatch(r"n\d+\.\d+\.\d+", values.get("FFMPEG_TAG", "")):
        errors.append("FFMPEG_TAG: expected nMAJOR.MINOR.PATCH")
    if not re.fullmatch(r"[0-9a-f]{40}", values.get("FFMPEG_COMMIT", "")):
        errors.append("FFMPEG_COMMIT: expected a lowercase 40-hex commit")

    if not errors:
        canonical = "\n".join(
            (
                RENOVATE_MARKER,
                f"FFMPEG_REMOTE={values['FFMPEG_REMOTE']}",
                f"FFMPEG_TAG={values['FFMPEG_TAG']}",
                f"FFMPEG_COMMIT={values['FFMPEG_COMMIT']}",
                "",
            )
        )
        if text != canonical:
            errors.append(
                "build-config.env: assignments must remain in the canonical "
                "contiguous Renovate block"
            )

    return errors


def validator_regressions() -> list[str]:
    """Exercise layouts that parse as shell assignments but break Renovate."""
    canonical = "\n".join(
        (
            RENOVATE_MARKER,
            "FFMPEG_REMOTE=https://github.com/FFmpeg/FFmpeg.git",
            "FFMPEG_TAG=n1.2.3",
            f"FFMPEG_COMMIT={'a' * 40}",
            "",
        )
    )
    cases = {
        "reordered": canonical.replace(
            "FFMPEG_REMOTE=https://github.com/FFmpeg/FFmpeg.git\nFFMPEG_TAG=n1.2.3",
            "FFMPEG_TAG=n1.2.3\nFFMPEG_REMOTE=https://github.com/FFmpeg/FFmpeg.git",
        ),
        "indented": canonical.replace("FFMPEG_TAG=", " FFMPEG_TAG="),
        "blank-separated": canonical.replace("FFMPEG_TAG=", "\nFFMPEG_TAG="),
    }
    failures = []
    if validate_config(canonical):
        failures.append("validator regression: canonical example was rejected")
    for name, text in cases.items():
        if not validate_config(text):
            failures.append(f"validator regression: {name} layout was accepted")
    return failures


def consumer_validator_regressions() -> list[str]:
    """Exercise unsafe consumer patterns that previously escaped the gate."""
    failures: list[str] = []
    relative = "ffmpeg-patches/generate.sh"
    source = CONSUMERS[0].read_text(encoding="utf-8")
    tag_ref = '"refs/tags/${FFMPEG_TAG}^{commit}"'
    ambiguous_ref = '"${FFMPEG_TAG}^{commit}"'
    ambiguous = source.replace(tag_ref, ambiguous_ref)
    if not any(
        "fully-qualified FFmpeg tag ref" in error
        for error in validate_consumer_text(relative, ambiguous)
    ):
        failures.append(
            "consumer regression: a same-named branch can satisfy the tag check"
        )

    unsafe_cleanup = "\n".join(
        (
            source,
            'git -C "$FFMPEG_REPO" worktree remove --force "$WORKTREE"',
            "",
        )
    )
    if not any(
        "must not remove caller-selected WORKTREE" in error
        for error in validate_consumer_text(relative, unsafe_cleanup)
    ):
        failures.append(
            "consumer regression: force-removal of caller WORKTREE was accepted"
        )

    replay_relative = "ffmpeg-patches/test/build-and-run.sh"
    replay = CONSUMERS[1].read_text(encoding="utf-8")
    late_trap = replay.replace("trap cleanup EXIT\n", "", 1).replace(
        'mkdir "$LOG_DIR"\n', 'mkdir "$LOG_DIR"\ntrap cleanup EXIT\n', 1
    )
    if not any(
        "cleanup trap must precede fallible setup" in error
        for error in validate_consumer_text(replay_relative, late_trap)
    ):
        failures.append(
            "consumer regression: cleanup trap installed after mkdir was accepted"
        )
    return failures


def git_tag_ref_regression() -> list[str]:
    """Prove a branch cannot stand in for the configured release tag ref."""
    with tempfile.TemporaryDirectory(prefix="pelorus-tag-ref-") as temp_dir:
        commands = (
            ("git", "init", "-q", temp_dir),
            (
                "git",
                "-C",
                temp_dir,
                "-c",
                "user.name=Pelorus test",
                "-c",
                "user.email=test@pelorus.invalid",
                "commit",
                "--allow-empty",
                "-qm",
                "fixture",
            ),
            ("git", "-C", temp_dir, "branch", "n1.2.3"),
        )
        for command in commands:
            subprocess.run(command, check=True, capture_output=True, text=True)

        ambiguous = subprocess.run(
            (
                "git",
                "-C",
                temp_dir,
                "rev-parse",
                "--verify",
                "n1.2.3^{commit}",
            ),
            check=False,
            capture_output=True,
            text=True,
        )
        qualified = subprocess.run(
            (
                "git",
                "-C",
                temp_dir,
                "rev-parse",
                "--verify",
                "refs/tags/n1.2.3^{commit}",
            ),
            check=False,
            capture_output=True,
            text=True,
        )
    failures = []
    if ambiguous.returncode != 0:
        failures.append("git tag regression: ambiguous branch fixture did not resolve")
    if qualified.returncode == 0:
        failures.append("git tag regression: missing fully-qualified tag resolved")
    return failures


def validate_consumer_text(relative: str, text: str) -> list[str]:
    """Check that an FFmpeg consumer follows the shared, safe pin contract."""
    errors: list[str] = []
    required = {
        'source "$ROOT/build-config.env"': "must source root build-config.env",
        "FFMPEG_COMMIT": "must consume the immutable FFmpeg commit",
        '"refs/tags/${FFMPEG_TAG}^{commit}"': (
            "must resolve the fully-qualified FFmpeg tag ref"
        ),
        "mktemp -d": "must create a private run directory",
        'OWNED_WORKTREE=""': "must initialize explicit worktree ownership",
        "am --abort": "cleanup must abort an in-progress git am",
        "trap cleanup EXIT": "must install EXIT cleanup",
    }
    for token, message in required.items():
        if token not in text:
            errors.append(f"{relative}: {message}")

    if not re.search(r':\s*"\$\{FFMPEG_REPO:\?[^}]+\}"', text):
        errors.append(f"{relative}: FFMPEG_REPO must be explicitly required")
    if not re.search(
        r'worktree\s+add\s+--detach\s+"\$WORKTREE"\s+"\$FFMPEG_COMMIT"', text
    ):
        errors.append(f"{relative}: worktree base must be FFMPEG_COMMIT")
    worktree_add = text.find(
        'git -C "$FFMPEG_REPO" worktree add --detach "$WORKTREE" "$FFMPEG_COMMIT"'
    )
    ownership_assignment = text.find('OWNED_WORKTREE="$WORKTREE"')
    if ownership_assignment < 0 or ownership_assignment < worktree_add:
        errors.append(
            f"{relative}: worktree ownership must be recorded only after creation"
        )
    owned_guard = text.find('if [[ -n "$OWNED_WORKTREE" ]]; then')
    owned_removal = text.find('git -C "$FFMPEG_REPO" worktree remove "$OWNED_WORKTREE"')
    if owned_guard < 0 or owned_removal < owned_guard:
        errors.append(
            f"{relative}: cleanup must remove only an invocation-owned worktree"
        )
    if re.search(r"worktree\s+remove\s+--force\b", text) or re.search(
        r'worktree\s+remove(?:\s+--force)?\s+"\$WORKTREE"', text
    ):
        errors.append(
            f"{relative}: must not remove caller-selected WORKTREE with force or "
            "without ownership"
        )
    if not re.search(
        r'\[\[\s+-e\s+"\$WORKTREE"\s+\|\|\s+-L\s+"\$WORKTREE"\s+\]\]', text
    ):
        errors.append(f"{relative}: caller WORKTREE must be rejected when it exists")
    if RELEASE_TAG.search(text):
        errors.append(f"{relative}: contains a copied FFmpeg release tag")
    if "/home/kilian/" in text:
        errors.append(f"{relative}: contains a workstation-specific path")
    if re.search(r'FFMPEG_REPO="\$\{FFMPEG_REPO:-', text):
        errors.append(f"{relative}: gives FFMPEG_REPO an implicit default")
    if re.search(r'WORKTREE="\$\{WORKTREE:-/', text):
        errors.append(f"{relative}: gives WORKTREE a shared fixed-path default")
    mktemp_position = text.find("mktemp -d")
    trap_position = text.find("trap cleanup EXIT")
    fallible_positions = tuple(
        position
        for marker in ('mkdir "$LOG_DIR"', 'git -C "$FFMPEG_REPO" rev-parse')
        if (position := text.find(marker)) >= 0
    )
    if trap_position < mktemp_position:
        errors.append(
            f"{relative}: cleanup trap must follow successful scratch creation"
        )
    if fallible_positions and trap_position > min(fallible_positions):
        errors.append(
            f"{relative}: cleanup trap must precede fallible setup after mktemp"
        )

    return errors


def validate_consumer(path: Path) -> list[str]:
    """Read and validate one operational FFmpeg consumer."""
    relative = path.relative_to(ROOT).as_posix()
    if not path.is_file():
        return [f"{relative}: missing"]
    return validate_consumer_text(relative, path.read_text(encoding="utf-8"))


def validate_consumers() -> list[str]:
    """Validate all operational consumers of the FFmpeg contract."""
    errors: list[str] = []
    for path in CONSUMERS:
        errors.extend(validate_consumer(path))

    generator = CONSUMERS[0].read_text(encoding="utf-8")
    if '"${FFMPEG_COMMIT}..HEAD"' not in generator:
        errors.append(
            "ffmpeg-patches/generate.sh: format-patch range must start at "
            "FFMPEG_COMMIT"
        )

    replay = CONSUMERS[1].read_text(encoding="utf-8")
    replay_required = {
        "--libdir=lib": "must install libpelorus into a private lib directory",
        "PKG_CONFIG_PATH": "must prefer the private libpelorus pkg-config file",
        "LD_LIBRARY_PATH": "must load the private libpelorus at runtime",
        "meson test": "must test the current libpelorus worktree",
        "meson install": "must install the current libpelorus worktree",
        "--enable-vulkan": "must enable Vulkan",
        "--disable-doc": "must disable FFmpeg documentation",
        "pelorus_fgs": "must verify the Pelorus FGS bitstream filter",
    }
    for token, message in replay_required.items():
        if token not in replay:
            errors.append(f"ffmpeg-patches/test/build-and-run.sh: {message}")
    for filter_name in (
        "pelorus_aa_vulkan",
        "pelorus_analyze_vulkan",
        "pelorus_borderfix_vulkan",
        "pelorus_deband_vulkan",
        "pelorus_deblock_vulkan",
        "pelorus_dehalo_vulkan",
        "pelorus_denoise_vulkan",
        "pelorus_grain_estimate_vulkan",
        "pelorus_mc_vulkan",
        "pelorus_scenecut",
    ):
        if filter_name not in replay:
            errors.append(
                "ffmpeg-patches/test/build-and-run.sh: missing registration "
                f"check for {filter_name}"
            )
    for forbidden in ("--enable-libshaderc", "--disable-programs"):
        if forbidden in replay:
            errors.append(
                "ffmpeg-patches/test/build-and-run.sh: forbidden configure flag "
                f"{forbidden}"
            )

    return errors


def main() -> int:
    if not CONFIG.is_file():
        print("build-config.env: missing", file=sys.stderr)
        return 1

    errors = validate_config(CONFIG.read_text(encoding="utf-8"))
    errors.extend(validate_consumers())
    if "--self-test" in sys.argv[1:]:
        errors.extend(validator_regressions())
        errors.extend(consumer_validator_regressions())
        errors.extend(git_tag_ref_regression())

    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
