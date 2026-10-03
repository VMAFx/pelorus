#!/usr/bin/env python3
"""Validate Pelorus's machine-maintained build dependency contract."""

from __future__ import annotations

import json
import os
import re
import shlex
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
QSV_REPLAY = ROOT / "ffmpeg-patches" / "test" / "qsv-roi-regression.sh"
STATIC_AVFILTER_CONSUMER = (
    ROOT / "ffmpeg-patches" / "test" / "static-libavfilter-consumer.c"
)
SVTAV1_ROI_PATCH = ROOT / "ffmpeg-patches" / "files" / "svtav1-pelorus-roi.patch"
LIBPELORUS_FILTERS = (
    "pelorus_analyze_vulkan",
    "pelorus_deband_vulkan",
    "pelorus_denoise_vulkan",
    "pelorus_grain_estimate_vulkan",
    "pelorus_mc_vulkan",
    "pelorus_scenecut",
)
WORKFLOWS = (
    ROOT / ".github" / "workflows" / "ci.yml",
    ROOT / ".github" / "workflows" / "release.yml",
)
# ADR-0149: the native Windows fast-suite leg of ci.yml. setup-msys2 is pinned by
# full commit digest with the release comment Renovate maintains. Per ADR-0151
# the checker reads that pin from ci.yml instead of copying it: it validates the
# shape, so a Renovate digest bump changes ci.yml alone and stays green.
WINDOWS_JOB = "windows"
WINDOWS_RUNNER = "windows-2025"
SETUP_MSYS2_PIN = re.compile(
    r"^[ ]+(?:- )?uses: msys2/setup-msys2@[0-9a-f]{40} # v\d+\.\d+\.\d+$", re.MULTILINE
)
WINDOWS_PACKAGES = tuple(
    f"mingw-w64-ucrt-x86_64-{name}"
    for name in ("gcc", "meson", "ninja", "python", "glslang", "shaderc")
)
WINDOWS_COMMANDS = (
    "meson setup build && ninja -C build",
    "meson test -C build --suite=fast --print-errorlogs",
    "meson test -C build path-utf8 --verbose",
)
PIN_PROJECTIONS = (
    ROOT / "README.md",
    ROOT / "AGENTS.md",
    ROOT / "CLAUDE.md",
    ROOT / "ffmpeg-patches" / "README.md",
    ROOT / "ffmpeg-patches" / "series.txt",
)
PIN_DIGEST_PROJECTIONS = PIN_PROJECTIONS[1:]
OPERATIONAL_GUIDANCE = (
    ROOT / "docs" / "development" / "build.md",
    ROOT / ".claude" / "skills" / "add-vulkan-filter" / "SKILL.md",
    ROOT / ".claude" / "skills" / "ffmpeg-apply-patches" / "SKILL.md",
    ROOT / ".claude" / "skills" / "ffmpeg-build-patches" / "SKILL.md",
    ROOT / ".claude" / "agents" / "ffmpeg-patch-reviewer.md",
)
EXPLICIT_COMMAND_GUIDANCE = OPERATIONAL_GUIDANCE[:1] + OPERATIONAL_GUIDANCE[2:4]
LIBPELORUS_FLOOR_INPUTS = tuple(
    ROOT / "ffmpeg-patches" / f".commit-msg-{name}.txt"
    for name in ("deband", "analyze", "denoise", "grain_estimate", "mc")
)
SETUP_GO_COMMIT = "b7ad1dad31e06c5925ef5d2fc7ad053ef454303e"
# The Go toolchain that runs actionlint in ci.yml's docs job. Renovate's
# github-actions manager bumps the setup-go go-version input; the regex
# customManager in renovate.json (validated below) rewrites this literal with
# the same package identity, so both land in one renovate/go-<major>.x branch.
ACTIONLINT_GO_VERSION = "1.27.x"
ACTIONLINT_GO_STEP = "- name: Set up Go for actionlint"
RENOVATE_CONFIG = ROOT / "renovate.json"
CHECKER_RELATIVE = "scripts/check-build-config.py"
# Mirrors Renovate's known-action config for actions/setup-go (datasource,
# package, versioning, extractVersion), so the regex manager's dependency is
# the same `go` update the github-actions manager proposes.
GO_RENOVATE_TEMPLATES = {
    "depNameTemplate": "go",
    "packageNameTemplate": "actions/go-versions",
    "datasourceTemplate": "github-releases",
    "versioningTemplate": "npm",
    "extractVersionTemplate": r"^(?<version>\d+\.\d+\.\d+)(-\d+)?$",
}
RELEASE_TAG = re.compile(r"(?<![A-Za-z0-9_])n\d+\.\d+\.\d+(?![A-Za-z0-9_])")
FORMAT_PATCH_CONFIG = (
    "format.mboxrd=false",
    "format.pretty=medium",
    "format.encodeEmailHeaders=true",
    "diff.orderFile=/dev/null",
)
GIT_AM_CONFIG = (
    "user.name=Pelorus-Replay",
    "user.email=replay@pelorus.invalid",
    "commit.gpgSign=false",
    "core.hooksPath=/dev/null",
    "diff.orderFile=/dev/null",
)
GIT_AM_CONFIG_PATTERN = r"\s+".join(
    rf"-c\s+{re.escape(setting)}" for setting in GIT_AM_CONFIG
)
# Anchored (re.MULTILINE) to the start of a line whose first token is `git`, so the
# canonical command inside a comment or after another word does not satisfy it (#65).
GIT_AM_COMMAND_PATTERN = (
    r'(?m)^[ \t]*git\s+-C\s+"\$WORKTREE"\s+'
    + GIT_AM_CONFIG_PATTERN
    + r"\s+am\s+--3way\s+--no-gpg-sign\s+--no-verify\s"
)
# Environment the replay scripts export so global/system Git configuration and
# GIT_COMMITTER_* never reach `git am` or the worktree checkout (#65).
GIT_HERMETIC_ENV_LINES = (
    "export GIT_CONFIG_GLOBAL=/dev/null",
    "export GIT_CONFIG_NOSYSTEM=1",
    "unset GIT_COMMITTER_NAME GIT_COMMITTER_EMAIL GIT_COMMITTER_DATE",
)
FORMAT_PATCH_OPTIONS = (
    "--zero-commit",
    "--full-index",
    "--binary",
    "--default-prefix",
    "--find-renames=50%",
    "--diff-algorithm=myers",
    "--indent-heuristic",
    "--unified=3",
    "--inter-hunk-context=0",
    "--stat",
    "--stat-width=80",
    "--stat-name-width=60",
    "--stat-graph-width=20",
    "--no-ext-diff",
    "--no-textconv",
    "--no-signature",
    "--no-thread",
    "--no-cover-letter",
    "--numbered",
    "--suffix=.patch",
    "--subject-prefix=PATCH",
    "--no-signoff",
    "--no-base",
    "--no-attach",
    "--no-to",
    "--no-cc",
    "--no-add-header",
    "--no-from",
    "--no-force-in-body-from",
    "--no-notes",
    "--filename-max-length=64",
    "--start-number=1",
    "--quiet",
)


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


def read_tracked_text(path: Path) -> tuple[str, list[str]]:
    """Read a required tracked projection with a repository-relative diagnostic."""
    relative = path.relative_to(ROOT).as_posix()
    if not path.is_file():
        return "", [f"{relative}: missing"]
    try:
        return path.read_text(encoding="utf-8"), []
    except OSError as error:
        return "", [f"{relative}: could not read: {error}"]


def validate_pin_projection_text(
    relative: str,
    source: str,
    tag: str,
    commit: str,
    require_commit: bool,
) -> list[str]:
    """Validate one present-tense projection of the authoritative FFmpeg pin."""
    errors: list[str] = []
    if tag not in source:
        errors.append(f"{relative}: missing configured FFmpeg tag {tag}")
    copied = sorted(set(RELEASE_TAG.findall(source)) - {tag})
    if copied:
        errors.append(
            f"{relative}: contains stale/currently unsupported FFmpeg tags: "
            f"{', '.join(copied)}"
        )
    if require_commit and commit not in source:
        errors.append(f"{relative}: missing configured FFmpeg commit {commit}")
    return errors


def validate_guidance_text(
    relative: str, source: str, require_command: bool
) -> list[str]:
    """Reject retired command, shader, and configure models from live guidance."""
    errors: list[str] = []
    forbidden = (
        "/home/kilian/dev/ffmpeg",
        "BASE_TAG=",
        "n8.1.1",
        "n9.0.1",
        "spirv_library",
        "filter's inline GLSL",
        "mirrored inline",
    )
    for token in forbidden:
        if token in source:
            errors.append(f"{relative}: contains obsolete guidance {token}")
    if require_command and "FFMPEG_REPO=/absolute/path" not in source:
        errors.append(f"{relative}: missing explicit FFMPEG_REPO command")
    return errors


def validate_current_surfaces(values: dict[str, str]) -> list[str]:
    """Keep current documentation and agent guidance projected from authority."""
    errors: list[str] = []
    tag = values.get("FFMPEG_TAG", "")
    commit = values.get("FFMPEG_COMMIT", "")

    for path in PIN_PROJECTIONS:
        relative = path.relative_to(ROOT).as_posix()
        source, read_errors = read_tracked_text(path)
        errors.extend(read_errors)
        if read_errors:
            continue
        errors.extend(
            validate_pin_projection_text(
                relative,
                source,
                tag,
                commit,
                path in PIN_DIGEST_PROJECTIONS,
            )
        )

    for path in OPERATIONAL_GUIDANCE:
        relative = path.relative_to(ROOT).as_posix()
        source, read_errors = read_tracked_text(path)
        errors.extend(read_errors)
        if read_errors:
            continue
        errors.extend(
            validate_guidance_text(relative, source, path in EXPLICIT_COMMAND_GUIDANCE)
        )

    for path in LIBPELORUS_FLOOR_INPUTS:
        relative = path.relative_to(ROOT).as_posix()
        source, read_errors = read_tracked_text(path)
        errors.extend(read_errors)
        if read_errors:
            continue
        if "Requires libpelorus >= 0.2.0" not in source:
            errors.append(f"{relative}: libpelorus compatibility floor drifted")
        if "Requires libpelorus >= 0.1.0" in source:
            errors.append(f"{relative}: retains obsolete libpelorus floor")

    meson, meson_errors = read_tracked_text(ROOT / "meson.build")
    errors.extend(meson_errors)
    version_match = re.search(r"^\s*version:\s*'([^']+)'", meson, re.MULTILINE)
    if version_match is None:
        errors.append("meson.build: project version not found")
    else:
        release = f"v{version_match.group(1)}"
        for path in (ROOT / "README.md", ROOT / "CLAUDE.md"):
            relative = path.relative_to(ROOT).as_posix()
            source, read_errors = read_tracked_text(path)
            errors.extend(read_errors)
            if not read_errors and release not in source:
                errors.append(f"{relative}: current project release must be {release}")
    return errors


def run_fixture_commands(
    context: str, commands: tuple[tuple[str, ...], ...]
) -> list[str]:
    """Run fixture setup without leaking subprocess exceptions from the validator."""
    for command in commands:
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.SubprocessError) as error:
            return [f"{context}: could not run {shlex.join(command)}: {error}"]
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip().splitlines()
            suffix = f": {detail[-1]}" if detail else ""
            return [
                f"{context}: command failed ({result.returncode}): "
                f"{shlex.join(command)}{suffix}"
            ]
    return []


def fixture_commit(repo: str, message: str) -> tuple[str, ...]:
    """Build a synthetic commit command isolated from user signing and hooks."""
    return (
        "git",
        "-C",
        repo,
        "-c",
        "user.name=Pelorus test",
        "-c",
        "user.email=test@pelorus.invalid",
        "-c",
        "commit.gpgSign=false",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "diff.orderFile=/dev/null",
        "commit",
        "--no-gpg-sign",
        "--no-verify",
        "--allow-empty",
        "-qm",
        message,
    )


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


def _consumer_generator_tag_and_cleanup_regressions(
    source: str, relative: str
) -> list[str]:
    failures: list[str] = []
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

    non_force_owned_cleanup = source.replace(
        'worktree remove --force "$OWNED_WORKTREE"',
        'worktree remove "$OWNED_WORKTREE"',
        1,
    )
    if non_force_owned_cleanup == source:
        failures.append(
            "consumer regression: owned cleanup mutation did not change fixture"
        )
    elif not any(
        "cleanup must force-remove invocation-owned failure residue" in error
        for error in validate_consumer_text(relative, non_force_owned_cleanup)
    ):
        failures.append("consumer regression: non-force owned cleanup was accepted")
    return failures


def _consumer_generator_path_regressions(source: str, relative: str) -> list[str]:
    failures: list[str] = []
    path_mutations = {
        "relative FFMPEG_REPO": source.replace(
            'FFMPEG_REPO="$(canonicalize_existing_dir "$FFMPEG_REPO")"',
            'FFMPEG_REPO="$FFMPEG_REPO"',
            1,
        ),
        "relative WORKTREE": source.replace(
            'WORKTREE="$(canonicalize_new_path "$WORKTREE")"',
            'WORKTREE="$WORKTREE"',
            1,
        ),
    }
    for name, unsafe_source in path_mutations.items():
        if unsafe_source == source:
            failures.append(
                f"consumer regression: {name} mutation did not change fixture"
            )
        elif not any(
            "must canonicalize caller paths before use" in error
            for error in validate_consumer_text(relative, unsafe_source)
        ):
            failures.append(f"consumer regression: {name} was accepted")
    return failures


def _consumer_generator_worktree_hook_regressions(
    source: str, relative: str
) -> list[str]:
    failures: list[str] = []
    hookful_worktree = source.replace(
        'git -C "$FFMPEG_REPO" -c core.hooksPath=/dev/null \\\n'
        '    worktree add --no-checkout --detach "$WORKTREE" "$FFMPEG_COMMIT"',
        'git -C "$FFMPEG_REPO" \\\n'
        '    worktree add --no-checkout --detach "$WORKTREE" "$FFMPEG_COMMIT"',
        1,
    )
    if hookful_worktree == source:
        failures.append(
            "consumer regression: hookful worktree mutation changed nothing"
        )
    elif not any(
        "worktree creation must disable Git hooks" in error
        for error in validate_consumer_text(relative, hookful_worktree)
    ):
        failures.append("consumer regression: hookful worktree creation was accepted")

    eager_checkout = source.replace("--no-checkout ", "", 1)
    if eager_checkout == source:
        failures.append("consumer regression: eager checkout mutation changed nothing")
    elif not any(
        "worktree creation must defer checkout" in error
        for error in validate_consumer_text(relative, eager_checkout)
    ):
        failures.append("consumer regression: eager worktree checkout was accepted")

    hookful_checkout = source.replace(
        'git -C "$WORKTREE" -c core.hooksPath=/dev/null \\\n'
        '    checkout --force --detach "$FFMPEG_COMMIT"',
        'git -C "$WORKTREE" \\\n' '    checkout --force --detach "$FFMPEG_COMMIT"',
        1,
    )
    if hookful_checkout == source:
        failures.append(
            "consumer regression: hookful checkout mutation changed nothing"
        )
    elif not any(
        "deferred checkout must disable Git hooks" in error
        for error in validate_consumer_text(relative, hookful_checkout)
    ):
        failures.append("consumer regression: hookful deferred checkout was accepted")
    return failures


def _consumer_generator_format_regressions(source: str) -> list[str]:
    failures: list[str] = []
    non_hermetic_format = source.replace("--no-signature", "", 1)
    if non_hermetic_format == source:
        failures.append("consumer regression: format mutation did not change fixture")
    elif not any(
        "format-patch invocation must neutralize Git configuration" in error
        for error in validate_generator_text(non_hermetic_format)
    ):
        failures.append("consumer regression: configured format signature was accepted")
    unsigned_commit = source.replace("commit_patch ", "git commit ", 1)
    if unsigned_commit == source:
        failures.append("consumer regression: commit mutation did not change fixture")
    elif not any(
        "synthetic commits must disable signing and hooks" in error
        for error in validate_generator_text(unsigned_commit)
    ):
        failures.append(
            "consumer regression: policy-dependent synthetic commit accepted"
        )
    return failures


def _consumer_replay_am_and_trap_regressions(
    replay: str, replay_relative: str
) -> list[str]:
    failures: list[str] = []
    parent_chdir = replay.replace("configure_ffmpeg() (", "configure_ffmpeg() {", 1)
    if not any(
        "configure must run in a subshell" in error
        for error in validate_replay_text(parent_chdir)
    ):
        failures.append("consumer regression: parent-shell configure cd was accepted")
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
    hookful_am = replay.replace("            -c core.hooksPath=/dev/null \\\n", "", 1)
    if hookful_am == replay:
        failures.append("consumer regression: hookful git-am mutation changed nothing")
    elif not any(
        "git am must neutralize Git configuration" in error
        for error in validate_replay_text(hookful_am)
    ):
        failures.append("consumer regression: hookful git am was accepted")
    return failures


def _consumer_replay_identity_regressions(replay: str) -> list[str]:
    failures: list[str] = []
    mutations = {
        "identityless git am": replay.replace(
            "            -c user.name=Pelorus-Replay \\\n"
            "            -c user.email=replay@pelorus.invalid \\\n",
            "",
            1,
        ),
        "signing git am": replay.replace(
            "am --3way --no-gpg-sign --no-verify", "am --3way --no-verify", 1
        ),
        "verifying git am": replay.replace(
            "am --3way --no-gpg-sign --no-verify", "am --3way --no-gpg-sign", 1
        ),
    }
    for name, mutated in mutations.items():
        if mutated == replay:
            failures.append(f"consumer regression: {name} mutation changed nothing")
        elif not any(
            "git am must neutralize Git configuration" in error
            for error in validate_replay_text(mutated)
        ):
            failures.append(f"consumer regression: {name} was accepted")
    return failures


def hermetic_and_anchor_mutations(source: str) -> dict[str, tuple[str, str]]:
    """Mutations of a replay script that the hermetic/anchored checks must refuse.

    Maps a case name to (mutated source, expected error fragment).
    """
    # Locate the multi-line command in the raw text (continuations intact).
    am_start = re.search(
        r'(?m)^[ \t]*git[ \t]+-C[ \t]+"\$WORKTREE"[ \t]*\\\n'
        r"(?:[ \t]*-c [^\n]*\\\n)+[ \t]*am [^\n]*$",
        source,
    )
    cases: dict[str, tuple[str, str]] = {}
    if am_start is not None:
        am_end = am_start.end()
        command = source[am_start.start() : am_end]
        # Negative: the canonical command survives only inside a comment.
        # A single comment line, as a maintainer would paste it, defeats an
        # unanchored search; the command itself is gone from the script.
        joined = " ".join(
            line.strip().removesuffix("\\").strip() for line in command.split("\n")
        )
        commented = f"# {joined}\n"
        cases["am only in a comment"] = (
            source[: am_start.start()] + commented + source[am_end + 1 :],
            "git am must neutralize Git configuration",
        )
        # Negative: the command follows another word, so it is not a command start.
        cases["am after another word"] = (
            source[: am_start.start()] + "echo " + source[am_start.start() :],
            "git am must neutralize Git configuration",
        )
    for line in GIT_HERMETIC_ENV_LINES:
        cases[f"missing {line.split()[1]}"] = (
            source.replace(line + "\n", "", 1),
            "must run git hermetically",
        )
        cases[f"commented {line.split()[1]}"] = (
            source.replace(line + "\n", "# " + line + "\n", 1),
            "must run git hermetically",
        )
    # Boundary: the exports exist but only after the first git am.
    exports = "".join(line + "\n" for line in GIT_HERMETIC_ENV_LINES)
    if exports in source:
        cases["exports after am"] = (
            source.replace(exports, "", 1) + "\n" + exports,
            "must precede the first git am",
        )
    return cases


def _hermetic_regressions(label: str, source: str, validate) -> list[str]:
    failures: list[str] = []
    cases = hermetic_and_anchor_mutations(source)
    if len(cases) < 2 + 2 * len(GIT_HERMETIC_ENV_LINES):
        failures.append(f"{label} regression: hermetic mutations were not generated")
    for name, (mutated, expected) in cases.items():
        if mutated == source:
            failures.append(f"{label} regression: {name} mutation changed nothing")
        elif not any(expected in error for error in validate(mutated)):
            failures.append(f"{label} regression: {name} was accepted")
    return failures


def _consumer_replay_sdk_and_query_regressions(replay: str) -> list[str]:
    failures: list[str] = []
    missing_optional_sdk = replay.replace(
        "configure_extra+=(--enable-libsvtav1)",
        "configure_extra+=(--encoder-sdk-removed)",
        1,
    )
    if missing_optional_sdk == replay:
        failures.append(
            "consumer regression: optional SDK mutation did not change fixture"
        )
    elif not any(
        "must compile SVT-AV1 consumers when available" in error
        for error in validate_replay_text(missing_optional_sdk)
    ):
        failures.append("consumer regression: missing optional SDK gate was accepted")

    missing_static_query = replay.replace(
        "pkg-config --static --libs libavfilter",
        "pkg-config --libs libavfilter",
        1,
    )
    if missing_static_query == replay:
        failures.append(
            "consumer regression: static pkg-config mutation did not change fixture"
        )
    elif not any(
        "must query libavfilter's static link closure" in error
        for error in validate_replay_text(missing_static_query)
    ):
        failures.append(
            "consumer regression: non-static libavfilter query was accepted"
        )
    return failures


def _consumer_generator_packaging_regressions(source: str) -> list[str]:
    failures: list[str] = []
    missing_filter_closure = source.replace(
        '_filter_extralibs="libpelorus_extralibs"',
        '_filter_extralibs="static-closure-removed"',
    )
    if missing_filter_closure == source:
        failures.append(
            "consumer regression: filter closure mutation did not change fixture"
        )
    elif not any(
        "must assign libpelorus_extralibs to consuming filters" in error
        for error in validate_generator_text(missing_filter_closure)
    ):
        failures.append("consumer regression: missing filter closure was accepted")

    missing_pkg_probe = source.replace(
        "&& require_pkg_config libpelorus", "&& static-probe-removed"
    )
    if missing_pkg_probe == source:
        failures.append(
            "consumer regression: guarded pkg-config mutation changed nothing"
        )
    elif not any(
        "must retain the guarded libpelorus pkg-config probe" in error
        for error in validate_generator_text(missing_pkg_probe)
    ):
        failures.append("consumer regression: missing libpelorus probe was accepted")

    global_extralibs = source + "\nadd_extralibs $libpelorus_extralibs\n"
    if not any(
        "must not add libpelorus to global executable extralibs" in error
        for error in validate_generator_text(global_extralibs)
    ):
        failures.append("consumer regression: global libpelorus link was accepted")

    unknown_filter_dep = source.replace(
        'pelorus_deband_vulkan_filter_deps="vulkan spirv_compiler"',
        'pelorus_deband_vulkan_filter_deps="vulkan spirv_compiler libpelorus"',
        1,
    )
    if unknown_filter_dep == source:
        failures.append(
            "consumer regression: unknown filter dep mutation changed nothing"
        )
    elif not any(
        "must not put libpelorus in filter dependencies" in error
        for error in validate_generator_text(unknown_filter_dep)
    ):
        failures.append("consumer regression: unknown libpelorus dep was accepted")
    return failures


def consumer_validator_regressions() -> list[str]:
    """Exercise unsafe consumer patterns that previously escaped the gate."""
    relative = "ffmpeg-patches/generate.sh"
    source = CONSUMERS[0].read_text(encoding="utf-8")
    replay_relative = "ffmpeg-patches/test/build-and-run.sh"
    replay = CONSUMERS[1].read_text(encoding="utf-8")

    failures: list[str] = []
    failures.extend(_consumer_generator_tag_and_cleanup_regressions(source, relative))
    failures.extend(_consumer_generator_path_regressions(source, relative))
    failures.extend(_consumer_generator_worktree_hook_regressions(source, relative))
    failures.extend(_consumer_generator_format_regressions(source))
    failures.extend(_consumer_generator_packaging_regressions(source))
    failures.extend(_consumer_replay_am_and_trap_regressions(replay, replay_relative))
    failures.extend(_consumer_replay_identity_regressions(replay))
    failures.extend(_consumer_replay_sdk_and_query_regressions(replay))
    failures.extend(_hermetic_regressions("replay", replay, validate_replay_text))
    return failures


def static_consumer_validator_regressions() -> list[str]:
    """Prove the external smoke keeps a real Pelorus filter lookup."""
    source = STATIC_AVFILTER_CONSUMER.read_text(encoding="utf-8")
    mutated = source.replace(
        'avfilter_get_by_name("pelorus_scenecut")', "avfilter_version()", 1
    )
    if mutated == source:
        return ["static consumer regression: lookup mutation changed nothing"]
    if not any(
        "must resolve a Pelorus filter through libavfilter" in error
        for error in validate_static_consumer_text(mutated)
    ):
        return ["static consumer regression: missing filter lookup was accepted"]
    return []


def validate_svtav1_roi_patch_text(source: str) -> list[str]:
    """Keep the SVT-AV1 boolean assignment portable across supported SDKs."""
    if "enable_roi_map = 1;" not in source or "enable_roi_map = true;" in source:
        return [
            "ffmpeg-patches/files/svtav1-pelorus-roi.patch: enable_roi_map must "
            "use an SDK-neutral integer boolean"
        ]
    return []


def svtav1_roi_patch_validator_regression() -> list[str]:
    """Prove the Ubuntu 26.04 SVT-AV1 2.x boolean spelling is required."""
    source = SVTAV1_ROI_PATCH.read_text(encoding="utf-8")
    mutated = source.replace("enable_roi_map = 1;", "enable_roi_map = true;", 1)
    if mutated == source:
        return ["SVT-AV1 regression: boolean mutation changed nothing"]
    if not validate_svtav1_roi_patch_text(mutated):
        return ["SVT-AV1 regression: SDK-dependent true token was accepted"]
    return []


def qsv_validator_regressions() -> list[str]:
    """Prove the focused QSV gate cannot bypass the shared pin or Git policy."""
    source = QSV_REPLAY.read_text(encoding="utf-8")
    cases = {
        "copied tag": (
            source + "\n# n1.2.3\n",
            "contains copied FFmpeg tag",
        ),
        "missing pin authority": (
            source.replace('source "$ROOT/build-config.env"', "", 1),
            "must source root build-config.env",
        ),
        "hookful worktree": (
            source.replace(
                'git -C "$FFMPEG_REPO" -c core.hooksPath=/dev/null \\\n'
                "    worktree add --no-checkout --detach",
                'git -C "$FFMPEG_REPO" \\\n' "    worktree add --no-checkout --detach",
                1,
            ),
            "worktree creation must be pinned and hook-neutral",
        ),
        "implicit checkout": (
            source.replace("--no-checkout ", "", 1),
            "worktree creation must be pinned and hook-neutral",
        ),
        "implicit committer": (
            source.replace("        -c user.name=Pelorus-Replay \\\n", "", 1),
            "git am must neutralize Git configuration",
        ),
    }
    failures: list[str] = []
    for name, (mutated, expected) in cases.items():
        if mutated == source:
            failures.append(f"QSV regression: {name} mutation changed nothing")
            continue
        errors = validate_qsv_replay_text(mutated)
        if not any(expected in error for error in errors):
            failures.append(f"QSV regression: {name} was accepted")
    failures.extend(_hermetic_regressions("QSV", source, validate_qsv_replay_text))
    return failures


def surface_validator_regressions() -> list[str]:
    """Prove current guidance cannot retain a copied pin or retired model."""
    values, parse_errors = parse_assignments(CONFIG.read_text(encoding="utf-8"))
    if parse_errors:
        return ["surface regression: canonical build-config.env did not parse"]
    tag = values["FFMPEG_TAG"]
    commit = values["FFMPEG_COMMIT"]
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    stale_tag = readme.replace(tag, "n1.2.3")
    if not any(
        "missing configured FFmpeg tag" in error
        for error in validate_pin_projection_text(
            "README.md", stale_tag, tag, commit, False
        )
    ):
        return ["surface regression: stale README pin was accepted"]

    skill = OPERATIONAL_GUIDANCE[2].read_text(encoding="utf-8")
    retired = skill + '\n`*_filter_deps="vulkan spirv_library"`\n'
    if not any(
        "contains obsolete guidance spirv_library" in error
        for error in validate_guidance_text(
            OPERATIONAL_GUIDANCE[2].relative_to(ROOT).as_posix(), retired, True
        )
    ):
        return ["surface regression: retired shader dependency was accepted"]
    return []


def fixture_subprocess_regression() -> list[str]:
    """Require fixture setup failures to become validator diagnostics."""
    helper = globals().get("run_fixture_commands")
    if not callable(helper):
        return ["fixture regression: subprocess diagnostic helper is missing"]
    errors = helper(
        "deliberate fixture failure",
        (("pelorus-command-that-must-not-exist",),),
    )
    if len(errors) != 1 or "deliberate fixture failure" not in errors[0]:
        return ["fixture regression: subprocess failure was not a clear diagnostic"]
    return []


def git_fixture_policy_regression() -> list[str]:
    """Prove signing and hook policy cannot break synthetic fixture commits."""
    with tempfile.TemporaryDirectory(prefix="pelorus-hostile-hooks-") as temp_dir:
        hook = Path(temp_dir) / "pre-commit"
        hook.write_text("#!/bin/sh\nexit 91\n", encoding="utf-8")
        hook.chmod(0o755)
        hostile = {
            "GIT_CONFIG_COUNT": "4",
            "GIT_CONFIG_KEY_0": "commit.gpgSign",
            "GIT_CONFIG_VALUE_0": "true",
            "GIT_CONFIG_KEY_1": "gpg.program",
            "GIT_CONFIG_VALUE_1": "/bin/false",
            "GIT_CONFIG_KEY_2": "core.hooksPath",
            "GIT_CONFIG_VALUE_2": temp_dir,
            "GIT_CONFIG_KEY_3": "diff.orderFile",
            "GIT_CONFIG_VALUE_3": "/definitely/missing/order-file",
        }
        saved = {key: os.environ.get(key) for key in hostile}
        os.environ.update(hostile)
        try:
            errors = git_tag_ref_regression()
        except (OSError, subprocess.SubprocessError):
            return ["fixture regression: hostile Git policy escaped as an exception"]
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
    if errors:
        return ["fixture regression: hostile Git policy blocked synthetic commit"]
    return []


def git_tag_ref_regression() -> list[str]:
    """Prove a branch cannot stand in for the configured release tag ref."""
    with tempfile.TemporaryDirectory(prefix="pelorus-tag-ref-") as temp_dir:
        errors = run_fixture_commands(
            "git tag fixture init", (("git", "init", "-q", temp_dir),)
        )
        if errors:
            return errors
        (Path(temp_dir) / "tracked.txt").write_text("fixture\n", encoding="utf-8")
        (Path(temp_dir) / "second.txt").write_text("fixture\n", encoding="utf-8")
        errors = run_fixture_commands(
            "git tag fixture setup",
            (
                ("git", "-C", temp_dir, "add", "tracked.txt", "second.txt"),
                fixture_commit(temp_dir, "fixture"),
                ("git", "-C", temp_dir, "branch", "n1.2.3"),
            ),
        )
        if errors:
            return errors

        try:
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
        except (OSError, subprocess.SubprocessError) as error:
            return [f"git tag fixture probe failed: {error}"]
    failures = []
    if ambiguous.returncode != 0:
        failures.append("git tag regression: ambiguous branch fixture did not resolve")
    if qualified.returncode == 0:
        failures.append("git tag regression: missing fully-qualified tag resolved")
    return failures


def _probe_dirty_worktree_removal(repo: Path, worktree: Path) -> (
    tuple[
        subprocess.CompletedProcess,
        bool,
        subprocess.CompletedProcess,
        subprocess.CompletedProcess,
        bool,
    ]
    | list[str]
):
    (worktree / "failure-residue").write_text("untracked\n", encoding="utf-8")
    try:
        non_force = subprocess.run(
            ("git", "-C", str(repo), "worktree", "remove", str(worktree)),
            check=False,
            capture_output=True,
            text=True,
        )
        still_present = worktree.exists()
        forced = subprocess.run(
            (
                "git",
                "-C",
                str(repo),
                "worktree",
                "remove",
                "--force",
                str(worktree),
            ),
            check=False,
            capture_output=True,
            text=True,
        )
        removed = not worktree.exists()
        registered = subprocess.run(
            ("git", "-C", str(repo), "worktree", "list", "--porcelain"),
            check=False,
            capture_output=True,
            text=True,
        )
        return non_force, still_present, forced, registered, removed
    except (OSError, subprocess.SubprocessError) as error:
        return [f"worktree cleanup fixture probe failed: {error}"]


def git_dirty_worktree_cleanup_regression() -> list[str]:
    """Prove owned forced cleanup removes untracked failure residue."""
    with tempfile.TemporaryDirectory(prefix="pelorus-worktree-cleanup-") as temp_dir:
        repo = Path(temp_dir) / "repo"
        worktree = Path(temp_dir) / "owned-worktree"
        commands = (
            ("git", "init", "-q", str(repo)),
            fixture_commit(str(repo), "fixture"),
            (
                "git",
                "-C",
                str(repo),
                "-c",
                "core.hooksPath=/dev/null",
                "worktree",
                "add",
                "--detach",
                str(worktree),
                "HEAD",
            ),
        )
        errors = run_fixture_commands("worktree cleanup fixture setup", commands)
        if errors:
            return errors

        probe = _probe_dirty_worktree_removal(repo, worktree)
        if isinstance(probe, list):
            return probe
        non_force, still_present, forced, registered, removed = probe

    failures = []
    if non_force.returncode == 0 or not still_present:
        failures.append("worktree cleanup regression: non-force removed dirty fixture")
    if (
        forced.returncode != 0
        or registered.returncode != 0
        or not removed
        or str(worktree) in registered.stdout
    ):
        failures.append("worktree cleanup regression: owned force cleanup failed")
    return failures


def _git_force_remove_worktree(
    repo: Path, worktree: Path
) -> subprocess.CompletedProcess:
    return subprocess.run(
        (
            "git",
            "-C",
            str(repo),
            "-c",
            "core.hooksPath=/dev/null",
            "worktree",
            "remove",
            "--force",
            str(worktree),
        ),
        check=False,
        capture_output=True,
        text=True,
    )


def _git_worktree_list(repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ("git", "-C", str(repo), "worktree", "list", "--porcelain"),
        check=False,
        capture_output=True,
        text=True,
    )


def _probe_ordinary_worktree_hook(repo: Path, ordinary: Path, hooks: Path) -> tuple[
    subprocess.CompletedProcess,
    subprocess.CompletedProcess,
    subprocess.CompletedProcess,
]:
    ordinary_add = subprocess.run(
        (
            "git",
            "-C",
            str(repo),
            "-c",
            f"core.hooksPath={hooks}",
            "worktree",
            "add",
            "--detach",
            str(ordinary),
            "HEAD",
        ),
        check=False,
        capture_output=True,
        text=True,
    )
    ordinary_list = _git_worktree_list(repo)
    ordinary_cleanup = _git_force_remove_worktree(repo, ordinary)
    return ordinary_add, ordinary_list, ordinary_cleanup


def _probe_hardened_worktree_hook(repo: Path, hardened: Path) -> tuple[
    subprocess.CompletedProcess,
    subprocess.CompletedProcess,
    subprocess.CompletedProcess,
    subprocess.CompletedProcess,
]:
    hardened_add = subprocess.run(
        (
            "git",
            "-C",
            str(repo),
            "-c",
            "core.hooksPath=/dev/null",
            "worktree",
            "add",
            "--no-checkout",
            "--detach",
            str(hardened),
            "HEAD",
        ),
        check=False,
        capture_output=True,
        text=True,
    )
    hardened_checkout = subprocess.run(
        (
            "git",
            "-C",
            str(hardened),
            "-c",
            "core.hooksPath=/dev/null",
            "checkout",
            "--force",
            "--detach",
            "HEAD",
        ),
        check=False,
        capture_output=True,
        text=True,
    )
    hardened_cleanup = _git_force_remove_worktree(repo, hardened)
    final_list = _git_worktree_list(repo)
    return hardened_add, hardened_checkout, hardened_cleanup, final_list


def git_worktree_hook_regression() -> list[str]:
    """Prove checkout hooks cannot mutate or strand invocation worktrees."""
    with tempfile.TemporaryDirectory(prefix="pelorus-worktree-hooks-") as temp_dir:
        root = Path(temp_dir)
        repo = root / "repo"
        ordinary = root / "ordinary"
        hardened = root / "hardened"
        hooks = root / "hooks"
        hooks.mkdir()
        hook = hooks / "post-checkout"
        hook.write_text("#!/bin/sh\nexit 91\n", encoding="utf-8")
        hook.chmod(0o755)

        errors = run_fixture_commands(
            "worktree hook fixture setup",
            (
                ("git", "init", "-q", str(repo)),
                fixture_commit(str(repo), "fixture"),
                (
                    "git",
                    "-C",
                    str(repo),
                    "config",
                    "core.hooksPath",
                    str(hooks),
                ),
            ),
        )
        if errors:
            return errors

        ordinary_add, ordinary_list, ordinary_cleanup = _probe_ordinary_worktree_hook(
            repo, ordinary, hooks
        )
        hardened_add, hardened_checkout, hardened_cleanup, final_list = (
            _probe_hardened_worktree_hook(repo, hardened)
        )

    failures = []
    if ordinary_add.returncode == 0 or str(ordinary) not in ordinary_list.stdout:
        failures.append(
            "worktree hook regression: hostile post-checkout did not strand the "
            "ordinary worktree fixture"
        )
    if ordinary_cleanup.returncode != 0:
        failures.append("worktree hook regression: ordinary residue cleanup failed")
    if hardened_add.returncode != 0 or hardened_checkout.returncode != 0:
        failures.append("worktree hook regression: hook-neutralized checkout failed")
    if (
        hardened_cleanup.returncode != 0
        or final_list.returncode != 0
        or str(ordinary) in final_list.stdout
        or str(hardened) in final_list.stdout
    ):
        failures.append("worktree hook regression: invocation worktree leaked")
    return failures


def _setup_am_hook_repo_and_patch(
    root: Path, repo: Path
) -> tuple[Path | None, list[str]]:
    errors = run_fixture_commands(
        "git am hook fixture setup",
        (("git", "init", "-q", str(repo)),),
    )
    if errors:
        return None, errors
    sample = repo / "sample.txt"
    sample.write_text("base\n", encoding="utf-8")
    errors = run_fixture_commands(
        "git am hook fixture commits",
        (
            ("git", "-C", str(repo), "add", "sample.txt"),
            fixture_commit(str(repo), "base"),
            ("git", "-C", str(repo), "branch", "base"),
        ),
    )
    if errors:
        return None, errors
    sample.write_text("base\npatched\n", encoding="utf-8")
    errors = run_fixture_commands(
        "git am hook fixture patch",
        (
            ("git", "-C", str(repo), "add", "sample.txt"),
            fixture_commit(str(repo), "patch"),
        ),
    )
    if errors:
        return None, errors
    patch_result = subprocess.run(
        ("git", "-C", str(repo), "format-patch", "-1", "--stdout"),
        check=False,
        capture_output=True,
        text=True,
    )
    if patch_result.returncode != 0:
        return None, ["git am hook regression: could not create fixture patch"]
    patch = root / "fixture.patch"
    patch.write_text(patch_result.stdout, encoding="utf-8")
    return patch, []


def _setup_am_hook_worktrees(repo: Path, ordinary: Path, hardened: Path) -> list[str]:
    return run_fixture_commands(
        "git am hook fixture worktrees",
        (
            (
                "git",
                "-C",
                str(repo),
                "-c",
                "core.hooksPath=/dev/null",
                "worktree",
                "add",
                "--detach",
                str(ordinary),
                "base",
            ),
            (
                "git",
                "-C",
                str(repo),
                "-c",
                "core.hooksPath=/dev/null",
                "worktree",
                "add",
                "--detach",
                str(hardened),
                "base",
            ),
        ),
    )


def _setup_am_hook_policy(repo: Path, hooks: Path) -> list[str]:
    for name in ("applypatch-msg", "pre-applypatch", "post-applypatch"):
        hook = hooks / name
        hook.write_text("#!/bin/sh\nexit 91\n", encoding="utf-8")
        hook.chmod(0o755)
    return run_fixture_commands(
        "git am hook fixture policy",
        (
            (
                "git",
                "-C",
                str(repo),
                "config",
                "core.hooksPath",
                str(hooks),
            ),
            (
                "git",
                "-C",
                str(repo),
                "config",
                "commit.gpgSign",
                "true",
            ),
            (
                "git",
                "-C",
                str(repo),
                "config",
                "gpg.program",
                "/bin/false",
            ),
            (
                "git",
                "-C",
                str(repo),
                "config",
                "diff.orderFile",
                "/definitely/missing/order-file",
            ),
        ),
    )


def _run_ordinary_am_probe(
    ordinary: Path, patch: Path, hooks: Path, env: dict[str, str]
) -> subprocess.CompletedProcess:
    ordinary_am = subprocess.run(
        (
            "git",
            "-C",
            str(ordinary),
            "-c",
            "user.name=Pelorus-Replay",
            "-c",
            "user.email=replay@pelorus.invalid",
            "-c",
            "commit.gpgSign=false",
            "-c",
            f"core.hooksPath={hooks}",
            "-c",
            "diff.orderFile=/dev/null",
            "am",
            "--3way",
            str(patch),
        ),
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    subprocess.run(
        (
            "git",
            "-C",
            str(ordinary),
            "-c",
            "core.hooksPath=/dev/null",
            "am",
            "--abort",
        ),
        check=False,
        capture_output=True,
        text=True,
    )
    return ordinary_am


def _run_hardened_am_probe(
    hardened: Path, patch: Path, env: dict[str, str]
) -> tuple[subprocess.CompletedProcess, str]:
    hardened_am = subprocess.run(
        (
            "git",
            "-C",
            str(hardened),
            "-c",
            "user.name=Pelorus-Replay",
            "-c",
            "user.email=replay@pelorus.invalid",
            "-c",
            "commit.gpgSign=false",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "diff.orderFile=/dev/null",
            "am",
            "--3way",
            str(patch),
        ),
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    patched = (hardened / "sample.txt").read_text(encoding="utf-8")
    return hardened_am, patched


def git_am_hook_regression() -> list[str]:
    """Prove applypatch hooks cannot block or mutate deterministic replay."""
    with tempfile.TemporaryDirectory(prefix="pelorus-am-hooks-") as temp_dir:
        root = Path(temp_dir)
        repo = root / "repo"
        ordinary = root / "ordinary"
        hardened = root / "hardened"
        hooks = root / "hooks"
        hooks.mkdir()

        patch, patch_errors = _setup_am_hook_repo_and_patch(root, repo)
        if patch_errors or patch is None:
            return patch_errors
        worktree_errors = _setup_am_hook_worktrees(repo, ordinary, hardened)
        if worktree_errors:
            return worktree_errors
        policy_errors = _setup_am_hook_policy(repo, hooks)
        if policy_errors:
            return policy_errors

        clean_git_env = os.environ.copy()
        clean_git_env["GIT_CONFIG_GLOBAL"] = "/dev/null"
        clean_git_env["GIT_CONFIG_NOSYSTEM"] = "1"
        ordinary_am = _run_ordinary_am_probe(ordinary, patch, hooks, clean_git_env)
        hardened_am, patched = _run_hardened_am_probe(hardened, patch, clean_git_env)

    failures = []
    if ordinary_am.returncode == 0:
        failures.append("git am hook regression: hostile applypatch hook did not run")
    if hardened_am.returncode != 0 or patched != "base\npatched\n":
        failures.append("git am hook regression: hook-neutralized replay failed")
    return failures


def _setup_smudge_cleanup_fixture(repo: Path, worktree: Path) -> list[str]:
    errors = run_fixture_commands(
        "smudge cleanup fixture setup",
        (("git", "init", "-q", str(repo)),),
    )
    if errors:
        return errors
    (repo / ".gitattributes").write_text(
        "payload.txt filter=pelorus-fail\n", encoding="utf-8"
    )
    (repo / "payload.txt").write_text("fixture\n", encoding="utf-8")
    return run_fixture_commands(
        "smudge cleanup fixture commit",
        (
            ("git", "-C", str(repo), "add", ".gitattributes", "payload.txt"),
            fixture_commit(str(repo), "fixture"),
            (
                "git",
                "-C",
                str(repo),
                "config",
                "filter.pelorus-fail.smudge",
                "/bin/false",
            ),
            (
                "git",
                "-C",
                str(repo),
                "config",
                "filter.pelorus-fail.required",
                "true",
            ),
            (
                "git",
                "-C",
                str(repo),
                "-c",
                "core.hooksPath=/dev/null",
                "worktree",
                "add",
                "--no-checkout",
                "--detach",
                str(worktree),
                "HEAD",
            ),
        ),
    )


def _probe_smudge_cleanup(repo: Path, worktree: Path) -> tuple[
    subprocess.CompletedProcess,
    subprocess.CompletedProcess,
    subprocess.CompletedProcess,
]:
    checkout = subprocess.run(
        (
            "git",
            "-C",
            str(worktree),
            "-c",
            "core.hooksPath=/dev/null",
            "checkout",
            "--force",
            "--detach",
            "HEAD",
        ),
        check=False,
        capture_output=True,
        text=True,
    )
    cleanup = _git_force_remove_worktree(repo, worktree)
    registered = _git_worktree_list(repo)
    return checkout, cleanup, registered


def git_smudge_cleanup_regression() -> list[str]:
    """Prove a required-filter checkout failure leaves no owned worktree."""
    with tempfile.TemporaryDirectory(prefix="pelorus-smudge-cleanup-") as temp_dir:
        root = Path(temp_dir)
        repo = root / "repo"
        worktree = root / "owned-worktree"
        errors = _setup_smudge_cleanup_fixture(repo, worktree)
        if errors:
            return errors
        checkout, cleanup, registered = _probe_smudge_cleanup(repo, worktree)

    failures = []
    if checkout.returncode == 0:
        failures.append("smudge cleanup regression: required filter did not fail")
    if (
        cleanup.returncode != 0
        or registered.returncode != 0
        or worktree.exists()
        or str(worktree) in registered.stdout
    ):
        failures.append("smudge cleanup regression: owned worktree leaked")
    return failures


HOSTILE_FORMAT_SETTINGS = (
    ("format.signature", "HOSTILE"),
    ("format.noprefix", "true"),
    ("format.suffix", ".evil"),
    ("format.thread", "deep"),
    ("format.coverLetter", "true"),
    ("format.numbered", "false"),
    ("format.subjectPrefix", "HOSTILE"),
    ("format.signOff", "true"),
    ("format.attach", "true"),
    ("format.to", "hostile@example.invalid"),
    ("format.cc", "hostile-cc@example.invalid"),
    ("format.headers", "X-Hostile: yes"),
    ("format.useAutoBase", "true"),
    ("format.from", "hostile@example.invalid"),
    ("format.forceInBodyFrom", "true"),
    ("format.filenameMaxLength", "20"),
    ("format.mboxrd", "true"),
    ("format.pretty", "oneline"),
    ("format.encodeEmailHeaders", "false"),
    ("diff.noprefix", "true"),
    ("diff.mnemonicPrefix", "true"),
    ("diff.srcPrefix", "old/"),
    ("diff.dstPrefix", "new/"),
    ("diff.renames", "false"),
    ("diff.algorithm", "patience"),
    ("diff.indentHeuristic", "false"),
    ("diff.context", "0"),
    ("diff.interHunkContext", "99"),
    ("diff.orderFile", "/definitely/missing/order-file"),
    ("diff.external", "/bin/false"),
)


def _setup_format_fixture_patches(
    repo: Path, sample: Path, base_lines: list[str], helper
) -> list[str]:
    changed_lines = base_lines.copy()
    changed_lines[3] = "line-04 changed\n"
    changed_lines[34] = "line-35 changed\n"
    sample.write_text("".join(changed_lines), encoding="utf-8")
    errors = helper(
        "format fixture first patch",
        (
            ("git", "-C", str(repo), "add", "sample.txt"),
            fixture_commit(str(repo), "one\n\nFrom config-sensitive body"),
        ),
    )
    if errors:
        return errors
    renamed = repo / "renamed.txt"
    sample.rename(renamed)
    renamed.write_text("".join(changed_lines) + "tail\n", encoding="utf-8")
    return helper(
        "format fixture second patch",
        (
            ("git", "-C", str(repo), "add", "-A"),
            fixture_commit(str(repo), "two"),
            (
                "git",
                "-C",
                str(repo),
                "-c",
                "core.hooksPath=/dev/null",
                "notes",
                "add",
                "-m",
                "configured note",
                "HEAD",
            ),
        ),
    )


def _setup_format_fixture_history(repo: Path, helper) -> list[str]:
    setup = (
        ("git", "init", "-q", str(repo)),
        ("git", "-C", str(repo), "config", "user.name", "Pelorus test"),
        (
            "git",
            "-C",
            str(repo),
            "config",
            "user.email",
            "test@pelorus.invalid",
        ),
    )
    errors = helper("format fixture setup", setup)
    if errors:
        return errors
    sample = repo / "sample.txt"
    base_lines = [f"line-{number:02d}\n" for number in range(1, 41)]
    sample.write_text("".join(base_lines), encoding="utf-8")
    errors = helper(
        "format fixture base",
        (
            ("git", "-C", str(repo), "add", "sample.txt"),
            fixture_commit(str(repo), "base"),
            ("git", "-C", str(repo), "branch", "base"),
        ),
    )
    if errors:
        return errors
    return _setup_format_fixture_patches(repo, sample, base_lines, helper)


def _run_format_patch_comparison(
    repo: Path, clean_output: Path, hostile_output: Path, helper
) -> list[str]:
    format_command = (
        "git",
        "-C",
        str(repo),
        *(item for value in FORMAT_PATCH_CONFIG for item in ("-c", value)),
        "format-patch",
        *FORMAT_PATCH_OPTIONS,
    )
    errors = helper(
        "clean format-patch",
        (format_command + ("--output-directory", str(clean_output), "base..HEAD"),),
    )
    if errors:
        return errors

    errors = helper(
        "hostile format configuration",
        tuple(
            ("git", "-C", str(repo), "config", key, value)
            for key, value in HOSTILE_FORMAT_SETTINGS
        ),
    )
    if errors:
        return errors
    errors = helper(
        "hostile format-patch",
        (format_command + ("--output-directory", str(hostile_output), "base..HEAD"),),
    )
    if errors:
        return errors

    clean = {path.name: path.read_bytes() for path in sorted(clean_output.iterdir())}
    hostile = {
        path.name: path.read_bytes() for path in sorted(hostile_output.iterdir())
    }
    if clean != hostile:
        return ["format regression: hostile Git configuration changed patch bytes"]
    if len(clean) != 2 or not all(name.endswith(".patch") for name in clean):
        return ["format regression: fixture did not produce two patch artifacts"]
    return []


def git_format_config_regression() -> list[str]:
    """Prove hostile repository format settings cannot change patch bytes."""
    helper = globals().get("run_fixture_commands")
    if not callable(helper):
        return ["format regression: subprocess diagnostic helper is missing"]
    with tempfile.TemporaryDirectory(prefix="pelorus-format-patch-") as temp_dir:
        repo = Path(temp_dir) / "repo"
        clean_output = Path(temp_dir) / "clean"
        hostile_output = Path(temp_dir) / "hostile"
        repo.mkdir()
        clean_output.mkdir()
        hostile_output.mkdir()

        errors = _setup_format_fixture_history(repo, helper)
        if errors:
            return errors

        return _run_format_patch_comparison(repo, clean_output, hostile_output, helper)


def _validate_consumer_required_tokens(relative: str, text: str) -> list[str]:
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
        'FFMPEG_REPO="$(canonicalize_existing_dir "$FFMPEG_REPO")"': (
            "must canonicalize caller paths before use"
        ),
        'WORKTREE="$(canonicalize_new_path "$WORKTREE")"': (
            "must canonicalize caller paths before use"
        ),
    }
    for token, message in required.items():
        if token not in text:
            errors.append(f"{relative}: {message}")

    if not re.search(r':\s*"\$\{FFMPEG_REPO:\?[^}]+\}"', text):
        errors.append(f"{relative}: FFMPEG_REPO must be explicitly required")
    return errors


def _validate_consumer_worktree_lifecycle(
    relative: str, text: str, shell_text: str
) -> list[str]:
    errors: list[str] = []
    worktree_pattern = re.compile(
        r'git\s+-C\s+"\$FFMPEG_REPO"\s+-c\s+core\.hooksPath=/dev/null\s+'
        r"worktree\s+add\s+--no-checkout\s+--detach\s+"
        r'"\$WORKTREE"\s+"\$FFMPEG_COMMIT"'
    )
    worktree_match = worktree_pattern.search(shell_text)
    if worktree_match is None:
        if not re.search(
            r'worktree\s+add\s+.*"\$WORKTREE"\s+"\$FFMPEG_COMMIT"', shell_text
        ):
            errors.append(f"{relative}: worktree base must be FFMPEG_COMMIT")
        if "--no-checkout" not in text:
            errors.append(f"{relative}: worktree creation must defer checkout")
        if not re.search(
            r'git\s+-C\s+"\$FFMPEG_REPO"\s+-c\s+'
            r"core\.hooksPath=/dev/null\s+worktree\s+add",
            shell_text,
        ):
            errors.append(f"{relative}: worktree creation must disable Git hooks")
    checkout_pattern = re.compile(
        r'git\s+-C\s+"\$WORKTREE"\s+-c\s+core\.hooksPath=/dev/null\s+'
        r'checkout\s+--force\s+--detach\s+"\$FFMPEG_COMMIT"'
    )
    checkout_match = checkout_pattern.search(shell_text)
    if checkout_match is None:
        errors.append(f"{relative}: deferred checkout must disable Git hooks")
    worktree_add = -1 if worktree_match is None else worktree_match.end()
    ownership_assignment = shell_text.find('OWNED_WORKTREE="$WORKTREE"')
    checkout_start = -1 if checkout_match is None else checkout_match.start()
    if ownership_assignment < worktree_add or checkout_start < ownership_assignment:
        errors.append(
            f"{relative}: worktree ownership must be recorded between registration "
            "and checkout"
        )
    if not re.search(
        r'git\s+-C\s+"\$OWNED_WORKTREE"\s+-c\s+core\.hooksPath=/dev/null\s+'
        r"am\s+--abort",
        shell_text,
    ):
        errors.append(f"{relative}: cleanup git am must disable Git hooks")
    return errors


def _validate_consumer_cleanup_and_paths(relative: str, text: str) -> list[str]:
    errors: list[str] = []
    owned_guard = text.find('if [[ -n "$OWNED_WORKTREE" ]]; then')
    safe_removal = 'worktree remove --force "$OWNED_WORKTREE"'
    owned_removal = text.find(f'git -C "$FFMPEG_REPO" {safe_removal}')
    if owned_guard < 0 or owned_removal < owned_guard:
        errors.append(
            f"{relative}: cleanup must force-remove invocation-owned failure residue"
        )
    removal_lines = (line for line in text.splitlines() if "worktree remove" in line)
    if any(safe_removal not in line for line in removal_lines):
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


def validate_consumer_text(relative: str, text: str) -> list[str]:
    """Check that an FFmpeg consumer follows the shared, safe pin contract."""
    shell_text = text.replace("\\\n", " ")
    errors: list[str] = []
    errors.extend(_validate_consumer_required_tokens(relative, text))
    errors.extend(_validate_consumer_worktree_lifecycle(relative, text, shell_text))
    errors.extend(_validate_consumer_cleanup_and_paths(relative, text))
    return errors


def validate_consumer(path: Path) -> list[str]:
    """Read and validate one operational FFmpeg consumer."""
    relative = path.relative_to(ROOT).as_posix()
    if not path.is_file():
        return [f"{relative}: missing"]
    return validate_consumer_text(relative, path.read_text(encoding="utf-8"))


def validate_generator_text(generator: str) -> list[str]:
    """Validate requirements specific to patch generation."""
    errors: list[str] = []
    if '"${FFMPEG_COMMIT}..HEAD"' not in generator:
        errors.append(
            "ffmpeg-patches/generate.sh: format-patch range must start at "
            "FFMPEG_COMMIT"
        )
    hermetic_tokens = (
        tuple(f"-c {setting}" for setting in FORMAT_PATCH_CONFIG) + FORMAT_PATCH_OPTIONS
    )
    missing = [token for token in hermetic_tokens if token not in generator]
    if missing:
        errors.append(
            "ffmpeg-patches/generate.sh: format-patch invocation must neutralize "
            f"Git configuration (missing {', '.join(missing)})"
        )
    commit_tokens = (
        "commit_patch()",
        "commit.gpgSign=false",
        "core.hooksPath=/dev/null",
        "diff.orderFile=/dev/null",
        "commit --no-gpg-sign --no-verify",
    )
    if any(token not in generator for token in commit_tokens) or re.search(
        r"^\s*git\s+commit\b", generator, re.MULTILINE
    ):
        errors.append(
            "ffmpeg-patches/generate.sh: synthetic commits must disable signing "
            "and hooks"
        )
    if "add_extralibs $libpelorus_extralibs" in generator:
        errors.append(
            "ffmpeg-patches/generate.sh: must not add libpelorus to global "
            "executable extralibs"
        )
    if re.search(r"_filter_deps=[^\n]*\blibpelorus\b", generator):
        errors.append(
            "ffmpeg-patches/generate.sh: must not put libpelorus in filter "
            "dependencies"
        )
    if '_filter_extralibs="libpelorus_extralibs"' not in generator:
        errors.append(
            "ffmpeg-patches/generate.sh: must assign libpelorus_extralibs to "
            "consuming filters"
        )
    guarded_probe = "f'enabled {name}_filter && require_pkg_config libpelorus '"
    if guarded_probe not in generator:
        errors.append(
            "ffmpeg-patches/generate.sh: must retain the guarded libpelorus "
            "pkg-config probe"
        )
    for filter_name in LIBPELORUS_FILTERS:
        if f'libpelorus_link("{filter_name}")' not in generator:
            errors.append(
                "ffmpeg-patches/generate.sh: missing static link closure for "
                f"{filter_name}_filter"
            )
    return errors


def _validate_replay_tokens_and_filters(replay: str) -> list[str]:
    errors: list[str] = []
    replay_required = {
        "--libdir=lib": "must install libpelorus into a private lib directory",
        "PKG_CONFIG_PATH": "must prefer the private libpelorus pkg-config file",
        "LD_LIBRARY_PATH": "must load the private libpelorus at runtime",
        "meson test": "must test the current libpelorus worktree",
        "meson install": "must install the current libpelorus worktree",
        "--enable-vulkan": "must enable Vulkan",
        "--enable-libaom": "must compile libaom consumers when available",
        "--enable-libsvtav1": "must compile SVT-AV1 consumers when available",
        "--disable-doc": "must disable FFmpeg documentation",
        "pelorus_fgs": "must verify the Pelorus FGS bitstream filter",
        "libaom-av1": "must verify the libaom Pelorus option",
        "libsvtav1": "must verify the SVT-AV1 Pelorus option",
        "h264_qsv": "must verify the QSV Pelorus option",
        "av1_nvenc": "must verify the NVENC Pelorus options",
        "h264_vulkan": "must verify the Vulkan encoder Pelorus option",
        "pelorus_me_hints": "must verify NVENC motion-hint registration",
        "pelorus_film_grain": "must verify NVENC film-grain registration",
        "static-libavfilter-consumer.c": (
            "must compile the external static libavfilter consumer"
        ),
        "pkg-config --static --libs libavfilter": (
            "must query libavfilter's static link closure"
        ),
        "-lpelorus": "must assert libavfilter's static closure contains -lpelorus",
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
    return errors


def _validate_replay_structure(replay: str, shell_replay: str) -> list[str]:
    errors: list[str] = []
    for forbidden in ("--enable-libshaderc", "--disable-programs"):
        if forbidden in replay:
            errors.append(
                "ffmpeg-patches/test/build-and-run.sh: forbidden configure flag "
                f"{forbidden}"
            )
    if not re.search(
        GIT_AM_COMMAND_PATTERN,
        shell_replay,
    ):
        errors.append(
            "ffmpeg-patches/test/build-and-run.sh: git am must neutralize Git configuration"
        )
    if "configure_ffmpeg() (" not in replay or "exec ./configure" not in replay:
        errors.append(
            "ffmpeg-patches/test/build-and-run.sh: configure must run in a subshell"
        )
    for module in ("vpl", "aom", "SvtAv1Enc", "ffnvcodec"):
        if f"pkg-config --exists {module}" not in replay:
            errors.append(
                "ffmpeg-patches/test/build-and-run.sh: missing optional SDK probe "
                f"{module}"
            )
    return errors


def validate_hermetic_git_env(relative: str, shell_text: str) -> list[str]:
    """Require the hermetic Git environment on active lines before the first am."""
    errors: list[str] = []
    am_match = re.search(GIT_AM_COMMAND_PATTERN, shell_text)
    for line in GIT_HERMETIC_ENV_LINES:
        match = re.search(rf"(?m)^[ \t]*{re.escape(line)}[ \t]*$", shell_text)
        if match is None:
            errors.append(
                f"{relative}: must run git hermetically; missing active line: {line}"
            )
        elif am_match is not None and match.start() > am_match.start():
            errors.append(f"{relative}: `{line}` must precede the first git am")
    return errors


def validate_replay_text(replay: str) -> list[str]:
    """Validate requirements specific to full stack replay."""
    shell_replay = replay.replace("\\\n", " ")
    errors: list[str] = []
    errors.extend(_validate_replay_tokens_and_filters(replay))
    errors.extend(_validate_replay_structure(replay, shell_replay))
    errors.extend(
        validate_hermetic_git_env("ffmpeg-patches/test/build-and-run.sh", shell_replay)
    )
    return errors


def validate_static_consumer_text(source: str) -> list[str]:
    """Validate the minimal out-of-tree libavfilter link fixture."""
    errors: list[str] = []
    required = {
        "#include <libavfilter/avfilter.h>": "must include libavfilter's public API",
        "int main(void)": "must provide a standalone entry point",
        'avfilter_get_by_name("pelorus_scenecut")': (
            "must resolve a Pelorus filter through libavfilter"
        ),
    }
    for token, message in required.items():
        if token not in source:
            errors.append(
                f"ffmpeg-patches/test/static-libavfilter-consumer.c: {message}"
            )
    return errors


def _validate_qsv_replay_tokens(relative: str, replay: str) -> list[str]:
    errors: list[str] = []
    required = {
        'source "$ROOT/build-config.env"': "must source root build-config.env",
        ': "${FFMPEG_REPO:?': "must explicitly require FFMPEG_REPO",
        'FFMPEG_REPO="$(canonicalize_existing_dir "$FFMPEG_REPO")"': (
            "must canonicalize FFMPEG_REPO"
        ),
        '"refs/tags/${FFMPEG_TAG}^{commit}"': (
            "must resolve the fully-qualified FFmpeg tag ref"
        ),
        '"$FFMPEG_COMMIT"': "must consume the immutable FFmpeg commit",
        "mktemp -d": "must create a private run directory",
        'OWNED_WORKTREE=""': "must initialize worktree ownership",
        "trap cleanup EXIT": "must install EXIT cleanup",
        "am --abort": "cleanup must abort an in-progress git am",
        "clang-asan-ubsan": "must run the sanitizer configuration",
        "-DQSV_HAVE_MBQP=0": "must compile the MBQP-absent branch",
    }
    for token, message in required.items():
        if token not in replay:
            errors.append(f"{relative}: {message}")
    return errors


def validate_qsv_replay_text(replay: str) -> list[str]:
    """Validate the focused QSV gate's immutable pin and owned-worktree policy."""
    relative = "ffmpeg-patches/test/qsv-roi-regression.sh"
    errors: list[str] = []
    shell_replay = replay.replace("\\\n", " ")
    errors.extend(_validate_qsv_replay_tokens(relative, replay))

    worktree = re.search(
        r'git\s+-C\s+"\$FFMPEG_REPO"\s+-c\s+core\.hooksPath=/dev/null\s+'
        r"worktree\s+add\s+--no-checkout\s+--detach\s+"
        r'"\$WORKTREE"\s+"\$FFMPEG_COMMIT"',
        shell_replay,
    )
    checkout = re.search(
        r'git\s+-C\s+"\$WORKTREE"\s+-c\s+core\.hooksPath=/dev/null\s+'
        r'checkout\s+--force\s+--detach\s+"\$FFMPEG_COMMIT"',
        shell_replay,
    )
    if worktree is None:
        errors.append(f"{relative}: worktree creation must be pinned and hook-neutral")
    if checkout is None:
        errors.append(f"{relative}: checkout must be pinned and hook-neutral")
    ownership = shell_replay.find('OWNED_WORKTREE="$WORKTREE"')
    if (
        worktree is None
        or checkout is None
        or ownership < worktree.end()
        or checkout.start() < ownership
    ):
        errors.append(
            f"{relative}: ownership must be recorded between registration and checkout"
        )
    if 'worktree remove --force "$OWNED_WORKTREE"' not in shell_replay:
        errors.append(f"{relative}: cleanup must remove only the owned worktree")
    if not re.search(
        GIT_AM_COMMAND_PATTERN,
        shell_replay,
    ):
        errors.append(f"{relative}: git am must neutralize Git configuration")
    errors.extend(validate_hermetic_git_env(relative, shell_replay))
    for token in ("BASE_TAG=", "/home/kilian/", "n8.1.1", "n9.0.1"):
        if token in replay:
            errors.append(f"{relative}: contains obsolete pin input {token}")
    copied_tags = RELEASE_TAG.findall(replay)
    if copied_tags:
        errors.append(f"{relative}: contains copied FFmpeg tag {copied_tags[0]}")
    return errors


def workflow_job_blocks(text: str) -> dict[str, str]:
    """Extract top-level workflow job blocks without executing YAML."""
    lines = text.splitlines(keepends=True)
    try:
        jobs_line = next(
            index for index, line in enumerate(lines) if line.rstrip() == "jobs:"
        )
    except StopIteration:
        return {}

    starts: list[tuple[str, int]] = []
    for index in range(jobs_line + 1, len(lines)):
        line = lines[index]
        if line.strip() and not line.startswith((" ", "\t")):
            break
        match = re.fullmatch(r"  ([A-Za-z0-9_-]+):\s*(?:#.*)?\n?", line)
        if match:
            starts.append((match.group(1), index))

    blocks: dict[str, str] = {}
    for position, (name, start) in enumerate(starts):
        end = starts[position + 1][1] if position + 1 < len(starts) else len(lines)
        blocks[name] = "".join(lines[start:end])
    return blocks


def msys2_install_list(block: str) -> list[str]:
    """Return the package names of the setup-msys2 folded `install: >-` list."""
    match = re.search(
        r"^(?P<indent>[ ]+)install:\s*>-[ ]*\n(?P<body>(?:(?P=indent)[ ]+\S.*\n?)+)",
        block,
        re.MULTILINE,
    )
    return match.group("body").split() if match else []


def validate_windows_job(relative: str, block: str | None) -> list[str]:
    """Validate the native Windows fast-suite leg as strictly as the Linux jobs."""
    if block is None:
        return [f"{relative}: missing Windows job {WINDOWS_JOB}"]
    prefix = f"{relative}: Windows job"
    errors: list[str] = []
    autocrlf = block.find("git config --global core.autocrlf false")
    checkout = block.find("uses: actions/checkout@")
    if autocrlf < 0 or autocrlf > checkout:
        errors.append(f"{prefix} must disable core.autocrlf before checkout")
    pin = SETUP_MSYS2_PIN.search(block)
    if block.count("msys2/setup-msys2@") != 1 or pin is None:
        errors.append(
            f"{prefix} must pin msys2/setup-msys2 once by full commit digest "
            "with its `# vX.Y.Z` release comment"
        )
    elif pin.start() > block.find("name: Load build configuration"):
        errors.append(f"{prefix} must install MSYS2 before its first msys2 step")
    for token in ("shell: msys2 {0}", "msystem: UCRT64", "update: false"):
        if token not in block:
            errors.append(f"{prefix} must set {token}")
    packages = msys2_install_list(block)
    for package in sorted(set(WINDOWS_PACKAGES) - set(packages)):
        errors.append(f"{prefix} must install MSYS2 package {package}")
    for package in sorted(set(packages) - set(WINDOWS_PACKAGES)):
        errors.append(f"{prefix} installs unexpected MSYS2 package {package}")
    for token in WINDOWS_COMMANDS + (
        "ImageOS",
        "ImageVersion",
        "pacman -Q " + " ".join(WINDOWS_PACKAGES),
        "gcc --version",
        "glslc --version",
        "glslangValidator --version",
    ):
        if token not in block:
            errors.append(f"{prefix} is missing {token}")
    return errors


def _validate_workflow_structure_and_jobs(
    relative: str, text: str, jobs: dict[str, str]
) -> list[str]:
    errors: list[str] = []
    forbidden = (
        "ubuntu-latest",
        "windows-latest",
        "packages.lunarg.com",
        "vulkan-sdk",
        "/home/kilian/",
    )
    for token in forbidden:
        if token in text:
            errors.append(f"{relative}: forbidden workflow token {token}")
    if RELEASE_TAG.search(text):
        errors.append(f"{relative}: contains a copied FFmpeg release tag")

    load_tokens = (
        "name: Load build configuration",
        "build-config.env",
        "FFMPEG_REMOTE",
        "FFMPEG_TAG",
        "FFMPEG_COMMIT",
        "GITHUB_ENV",
    )
    is_ci = Path(relative).name == "ci.yml"
    for name, block in jobs.items():
        runner = WINDOWS_RUNNER if is_ci and name == WINDOWS_JOB else "ubuntu-26.04"
        runners = re.findall(r"^\s+runs-on:\s*([^\s#]+)", block, re.MULTILINE)
        if runners != [runner]:
            errors.append(f"{relative}: job {name} must run exactly on {runner}")
        checkout = block.find("uses: actions/checkout@")
        load = block.find("name: Load build configuration")
        if checkout < 0 or load < checkout:
            errors.append(
                f"{relative}: job {name} must load build-config.env after checkout"
            )
        for token in load_tokens:
            if token not in block:
                errors.append(
                    f"{relative}: job {name} build-config step is missing {token}"
                )
    for token in (
        "ImageOS",
        "ImageVersion",
        "/etc/os-release",
        "glslc --version",
        "glslangValidator --version",
    ):
        if token not in text:
            errors.append(f"{relative}: environment receipt is missing {token}")
    return errors


def _validate_workflow_native_packages(
    relative: str, jobs: dict[str, str]
) -> list[str]:
    errors: list[str] = []
    build_jobs = {
        "ci.yml": ("core", "ffmpeg-stack", "sanitizers"),
        "release.yml": ("release",),
    }.get(Path(relative).name, ())
    for name in build_jobs:
        block = jobs.get(name)
        if block is None:
            errors.append(f"{relative}: missing build/test job {name}")
            continue
        install_start = block.find("sudo apt-get install")
        install_end = block.find("\n      - name:", install_start)
        install = (
            block[install_start:]
            if install_end < 0
            else block[install_start:install_end]
        )
        for package in ("glslc", "glslang-tools"):
            if not re.search(
                rf"(?<![A-Za-z0-9_-]){re.escape(package)}(?![A-Za-z0-9_-])",
                install,
            ):
                errors.append(
                    f"{relative}: job {name} must install native package {package}"
                )
    return errors


def _validate_workflow_specialized_jobs(
    relative: str, text: str, jobs: dict[str, str]
) -> list[str]:
    errors: list[str] = []
    rel_name = Path(relative).name
    if rel_name == "ci.yml":
        ffmpeg = jobs.get("ffmpeg-stack", "")
        for token in (
            "libvulkan-dev",
            "libvpl-dev",
            "libaom-dev",
            "libsvtav1enc-dev",
            "libffmpeg-nvenc-dev",
            "refs/tags/${FFMPEG_TAG}^{commit}",
            '"$FFMPEG_COMMIT"',
            "ffmpeg-patches/generate.sh",
            "ffmpeg-patches/test/build-and-run.sh",
        ):
            if token not in ffmpeg:
                errors.append(f"{relative}: FFmpeg job is missing {token}")
        docs = jobs.get("docs", "")
        for token in (
            ACTIONLINT_GO_STEP,
            f"actions/setup-go@{SETUP_GO_COMMIT}",
            f"go-version: '{ACTIONLINT_GO_VERSION}'",
            "github.com/rhysd/actionlint/cmd/actionlint@v1.7.12",
        ):
            if token not in docs:
                errors.append(f"{relative}: docs job is missing {token}")
        go_versions = re.findall(r"go-version:\s*(\S+)", docs)
        if go_versions != [f"'{ACTIONLINT_GO_VERSION}'"]:
            errors.append(
                f"{relative}: docs job must set exactly one go-version, "
                f"'{ACTIONLINT_GO_VERSION}' (found {go_versions})"
            )
        errors.extend(validate_windows_job(relative, jobs.get(WINDOWS_JOB)))
    elif rel_name == "release.yml":
        if "workflow_dispatch:" not in text:
            errors.append(f"{relative}: release gate needs workflow_dispatch")
        publish = jobs.get("release", "")
        guard = (
            "if: github.event_name == 'push' && "
            "startsWith(github.ref, 'refs/tags/v')"
        )
        if guard not in publish:
            errors.append(
                f"{relative}: publish step must be tag-push-only for manual safety"
            )
        for token in (
            "GITHUB_REF_NAME//\\//-",
            "ARTIFACT_LABEL",
        ):
            if token not in publish:
                errors.append(f"{relative}: manual package naming is missing {token}")
    return errors


def validate_workflow_text(relative: str, text: str) -> list[str]:
    """Validate the pinned runner, toolchain, and shared FFmpeg workflow contract."""
    jobs = workflow_job_blocks(text)
    if not jobs:
        return [f"{relative}: no jobs found"]
    return (
        _validate_workflow_structure_and_jobs(relative, text, jobs)
        + _validate_workflow_native_packages(relative, jobs)
        + _validate_workflow_specialized_jobs(relative, text, jobs)
    )


def validate_workflows() -> list[str]:
    """Validate all hosted workflow consumers of the build contract."""
    errors: list[str] = []
    for path in WORKFLOWS:
        relative = path.relative_to(ROOT).as_posix()
        if not path.is_file():
            errors.append(f"{relative}: missing")
            continue
        errors.extend(
            validate_workflow_text(relative, path.read_text(encoding="utf-8"))
        )
    return errors


def replace_in_job(source: str, job: str, old: str, new: str) -> str:
    """Mutate the first `old` inside one workflow job block only."""
    block = workflow_job_blocks(source).get(job, "")
    if old not in block:
        return source
    return source.replace(block, block.replace(old, new, 1), 1)


def windows_workflow_cases(source: str) -> dict[str, tuple[str, str]]:
    """Mutations of the ADR-0149 Windows job that the validator must reject."""
    windows = workflow_job_blocks(source).get(WINDOWS_JOB, "")

    def mutate(old: str, new: str) -> str:
        return replace_in_job(source, WINDOWS_JOB, old, new)

    return {
        "floating Windows runner": (
            mutate(WINDOWS_RUNNER, "windows-latest"),
            "forbidden workflow token windows-latest",
        ),
        "Windows job on another image": (
            mutate(WINDOWS_RUNNER, "windows-2022"),
            f"job {WINDOWS_JOB} must run exactly on {WINDOWS_RUNNER}",
        ),
        "missing Windows job": (
            source.replace(windows, "", 1) if windows else source,
            f"missing Windows job {WINDOWS_JOB}",
        ),
        "unpinned setup-msys2": (
            re.sub(r"setup-msys2@[0-9a-f]{40}", "setup-msys2@v2", source, count=1),
            "must pin msys2/setup-msys2 once",
        ),
        "abbreviated setup-msys2 digest": (
            re.sub(r"(setup-msys2@[0-9a-f]{12})[0-9a-f]{28}", r"\1", source, count=1),
            "must pin msys2/setup-msys2 once",
        ),
        "setup-msys2 without release comment": (
            re.sub(r"(setup-msys2@[0-9a-f]{40}) # v\S+", r"\1", source, count=1),
            "must pin msys2/setup-msys2 once",
        ),
        "non-UCRT64 environment": (
            mutate("msystem: UCRT64", "msystem: MINGW64"),
            "must set msystem: UCRT64",
        ),
        "missing MSYS2 package": (
            mutate("mingw-w64-ucrt-x86_64-glslang", "mingw-w64-x86_64-glslang"),
            "must install MSYS2 package mingw-w64-ucrt-x86_64-glslang",
        ),
        "extra MSYS2 package": (
            mutate("mingw-w64-ucrt-x86_64-gcc", "mingw-w64-ucrt-x86_64-gcc git"),
            "installs unexpected MSYS2 package git",
        ),
        "CRLF checkout": (
            mutate("core.autocrlf false", "core.autocrlf true"),
            "must disable core.autocrlf before checkout",
        ),
        "Windows fast suite dropped": (
            mutate("--suite=fast", "--suite=slow"),
            "is missing meson test -C build --suite=fast --print-errorlogs",
        ),
    }


def windows_pin_bump_regression(source: str, relative: str) -> list[str]:
    """A Renovate-style setup-msys2 bump edits ci.yml alone and must stay valid."""
    bumped = re.sub(
        r"setup-msys2@[0-9a-f]{40} # v\S+",
        "setup-msys2@" + "0123456789abcdef" * 2 + "01234567 # v99.0.0",
        source,
        count=1,
    )
    if bumped == source:
        return ["workflow regression: setup-msys2 bump mutation changed nothing"]
    errors = validate_workflow_text(relative, bumped)
    if validate_workflow_text(relative, source) != errors:
        return [f"workflow regression: setup-msys2 bump was rejected: {errors}"]
    return []


def workflow_validator_regressions() -> list[str]:
    """Prove runner and native-toolchain regressions are rejected."""
    failures: list[str] = []
    ci_path = WORKFLOWS[0]
    source = ci_path.read_text(encoding="utf-8")
    cases = {
        **windows_workflow_cases(source),
        "floating runner": (
            source.replace("ubuntu-26.04", "ubuntu-latest", 1),
            "forbidden workflow token ubuntu-latest",
        ),
        "retired LunarG source": (
            source + "\n# https://packages.lunarg.com/retired\n",
            "forbidden workflow token packages.lunarg.com",
        ),
        "missing native glslc": (
            source.replace("glslc", "shader-compiler-removed", 1),
            "must install native package glslc",
        ),
        "missing optional encoder SDK": (
            source.replace("libsvtav1enc-dev", "encoder-sdk-removed", 1),
            "FFmpeg job is missing libsvtav1enc-dev",
        ),
        "stale actionlint Go toolchain": (
            source.replace(
                f"go-version: '{ACTIONLINT_GO_VERSION}'", "go-version: '1.0.x'", 1
            ),
            f"docs job is missing go-version: '{ACTIONLINT_GO_VERSION}'",
        ),
        "second Go toolchain in docs job": (
            source.replace(
                f"go-version: '{ACTIONLINT_GO_VERSION}'",
                f"go-version: '{ACTIONLINT_GO_VERSION}'\n          go-version: '1.0.x'",
                1,
            ),
            "docs job must set exactly one go-version",
        ),
        "versioned Go step name": (
            source.replace(
                ACTIONLINT_GO_STEP, "- name: Set up Go 1.0 for actionlint", 1
            ),
            f"docs job is missing {ACTIONLINT_GO_STEP}",
        ),
    }
    relative = ci_path.relative_to(ROOT).as_posix()
    failures.extend(windows_pin_bump_regression(source, relative))
    for name, (mutated, expected) in cases.items():
        if mutated == source:
            failures.append(f"workflow regression: {name} mutation changed nothing")
            continue
        errors = validate_workflow_text(relative, mutated)
        if not any(expected in error for error in errors):
            failures.append(f"workflow regression: {name} was accepted")
    return failures


class RenovatePatternError(ValueError):
    """A managerFilePatterns entry this checker cannot evaluate like Renovate."""


def renovate_glob_segment_regex(segment: str, glob: str) -> str:
    """Translate one path segment of a Renovate glob (see below)."""
    out: list[str] = []
    braces: list[bool] = []  # per open `{`: seen a top-level `,` yet
    index = 0
    while index < len(segment):
        char = segment[index]
        pair = segment[index : index + 2]
        if char in "[]\\" or (char in "@!+*?" and pair[1:] == "("):
            raise RenovatePatternError(
                f"managerFilePatterns {glob!r}: glob syntax {pair!r} is not "
                "evaluated by this checker; use a /regex/ entry"
            )
        if char == "*":
            while segment[index + 1 : index + 2] == "*":
                index += 1
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        elif char == "{":
            braces.append(False)
            out.append("(?:")
        elif char == "," and braces:
            braces[-1] = True
            out.append("|")
        elif char == "}" and braces:
            if not braces.pop():
                raise RenovatePatternError(
                    f"managerFilePatterns {glob!r}: only {{a,b}} brace "
                    "alternation is evaluated by this checker"
                )
            out.append(")")
        else:
            out.append(re.escape(char))
        index += 1
    if braces:
        raise RenovatePatternError(
            f"managerFilePatterns {glob!r}: brace group spans a `/` or is unclosed"
        )
    return "".join(out)


def renovate_glob_regex(glob: str) -> str:
    """Translate the minimatch subset this checker models into a regex.

    Renovate matches non-regex managerFilePatterns with minimatch
    (`dot: true, nocase: true`): `*` and `?` stay inside one path segment, a
    whole `**` segment spans any number of segments, and `{a,b}` alternates.
    Anything else (classes, extglobs, escapes, ranges) raises instead of
    being approximated.
    """
    segments = glob.split("/")
    parts: list[str] = []
    for index, segment in enumerate(segments):
        last = index == len(segments) - 1
        if segment == "**":
            parts.append(".*" if last else "(?:[^/]*/)*")
            continue
        if segment in (".", ".."):
            raise RenovatePatternError(
                f"managerFilePatterns {glob!r}: relative segments are not "
                "evaluated by this checker"
            )
        parts.append(renovate_glob_segment_regex(segment, glob) + ("" if last else "/"))
    return "".join(parts)


def renovate_file_pattern_matches(pattern: object, relative: str) -> bool:
    """Apply one managerFilePatterns entry like Renovate's matchRegexOrGlob.

    Mirrors lib/util/string-match.ts: `*` matches every file; `/re/` and
    `/re/i`, optionally `!`-negated, are regexes; anything else is a minimatch
    glob (`dot`, `nocase`, leading `!` negates, leading `#` never matches).
    Raises RenovatePatternError rather than guess at an entry it cannot model.
    """
    if not isinstance(pattern, str):
        return False
    if pattern == "*":
        return True
    if re.match(r"!?/", pattern) and re.search(r"/i?$", pattern):
        body = re.sub(r"/i?$", "", re.sub(r"^!?/", "", pattern, count=1), count=1)
        flags = re.IGNORECASE if pattern.endswith("i") else 0
        try:
            compiled = re.compile(python_regex(body), flags)
        except re.error as exc:
            raise RenovatePatternError(
                f"managerFilePatterns {pattern!r}: regex not evaluable here: {exc}"
            ) from exc
        return (compiled.search(relative) is not None) != pattern.startswith("!")
    if pattern.startswith("#"):
        return False
    glob = pattern.lstrip("!")
    negated = (len(pattern) - len(glob)) % 2 == 1
    matched = re.fullmatch(renovate_glob_regex(glob), relative, re.IGNORECASE)
    return (matched is not None) != negated


def renovate_pattern_regressions() -> list[str]:
    """Pin renovate_file_pattern_matches to Renovate's matching semantics."""
    failures: list[str] = []
    cases = {
        "/^scripts/check-build-config\\.py$/": True,
        "/CHECK-BUILD-CONFIG\\.PY$/i": True,
        "/CHECK-BUILD-CONFIG\\.PY$/": False,
        "!/^renovate\\.json$/": True,
        "!/check-build-config/": False,
        "/(?<name>check)-build/": True,
        "*": True,
        "scripts/*.py": True,
        "*.py": False,
        "**/*.py": True,
        "scripts/**": True,
        "**/scripts/**/check-build-config.py": True,
        "SCRIPTS/{check-build-config,other}.PY": True,
        "scripts/{other,x}.py": False,
        "scripts/check-build-config.p?": True,
        "!scripts/**": False,
        "!!scripts/*.py": True,
        "#scripts/*.py": False,
    }
    for pattern, expected in cases.items():
        try:
            actual = renovate_file_pattern_matches(pattern, CHECKER_RELATIVE)
        except RenovatePatternError as exc:
            failures.append(f"renovate pattern regression: {pattern!r} raised {exc}")
            continue
        if actual != expected:
            failures.append(
                f"renovate pattern regression: {pattern!r} gave {actual}, "
                f"Renovate gives {expected}"
            )
    for pattern in (
        "/(?<name/",
        "scripts/[a-c]*.py",
        "scripts/@(check)*.py",
        "scripts/{1..3}.py",
        "scripts\\/check-build-config.py",
        "{scripts/x,y}.py",
        "./scripts/*.py",
    ):
        try:
            renovate_file_pattern_matches(pattern, CHECKER_RELATIVE)
        except RenovatePatternError:
            continue
        failures.append(
            f"renovate pattern regression: unmodelled {pattern!r} was evaluated"
        )
    return failures


def python_regex(renovate_regex: str) -> str:
    """Translate JS/RE2 named groups `(?<name>` to Python's `(?P<name>`."""
    return re.sub(r"\(\?<(?=[A-Za-z_])", "(?P<", renovate_regex)


def validate_renovate_text(text: str, checker_source: str) -> list[str]:
    """Require one regex manager that bumps ACTIONLINT_GO_VERSION as `go`."""
    try:
        config = json.loads(text)
    except json.JSONDecodeError as exc:
        return [f"renovate.json: invalid JSON: {exc}"]
    managers = config.get("customManagers") if isinstance(config, dict) else None
    try:
        covering = [
            manager
            for manager in (managers if isinstance(managers, list) else [])
            if isinstance(manager, dict)
            and manager.get("customType") == "regex"
            and isinstance(manager.get("managerFilePatterns"), list)
            and any(
                renovate_file_pattern_matches(pattern, CHECKER_RELATIVE)
                for pattern in manager["managerFilePatterns"]
            )
        ]
    except RenovatePatternError as exc:
        return [f"renovate.json: {exc}"]
    if len(covering) != 1:
        return [
            f"renovate.json: expected exactly one regex customManager for "
            f"{CHECKER_RELATIVE} (found {len(covering)})"
        ]
    manager = covering[0]
    errors = [
        f"renovate.json: {CHECKER_RELATIVE} manager needs {key} {value!r}"
        for key, value in GO_RENOVATE_TEMPLATES.items()
        if manager.get(key) != value
    ]
    literal = f'ACTIONLINT_GO_VERSION = "{ACTIONLINT_GO_VERSION}"'
    match_strings = manager.get("matchStrings")
    if not isinstance(match_strings, list) or len(match_strings) != 1:
        errors.append(
            f"renovate.json: {CHECKER_RELATIVE} manager needs one matchString"
        )
        return errors
    try:
        matches = list(re.finditer(python_regex(str(match_strings[0])), checker_source))
    except re.error as exc:
        return errors + [f"renovate.json: {CHECKER_RELATIVE} matchString: {exc}"]
    if (
        len(matches) != 1
        or matches[0].group(0) != literal
        or matches[0].groupdict().get("currentValue") != ACTIONLINT_GO_VERSION
    ):
        errors.append(
            f"renovate.json: {CHECKER_RELATIVE} matchString must capture exactly "
            f"{literal} as currentValue (found {len(matches)} matches)"
        )
    return errors


def validate_renovate() -> list[str]:
    """Validate the checked-in Renovate config against this checker."""
    if not RENOVATE_CONFIG.is_file():
        return ["renovate.json: missing"]
    checker = Path(__file__).resolve()
    return validate_renovate_text(
        RENOVATE_CONFIG.read_text(encoding="utf-8"),
        checker.read_text(encoding="utf-8"),
    )


def without_checker_manager(value: dict) -> dict:
    value["customManagers"] = [
        manager
        for manager in value["customManagers"]
        if not any(
            renovate_file_pattern_matches(pattern, CHECKER_RELATIVE)
            for pattern in manager.get("managerFilePatterns", [])
        )
    ]
    return value

def mutate_checker_manager(key: str, replacement: object):
    def mutate(value: dict) -> dict:
        for manager in value["customManagers"]:
            if any(
                renovate_file_pattern_matches(pattern, CHECKER_RELATIVE)
                for pattern in manager.get("managerFilePatterns", [])
            ):
                manager[key] = replacement
        return value

    return mutate

def with_extra_manager(patterns: list[str]):
    def mutate(value: dict) -> dict:
        value["customManagers"].append(
            {
                "customType": "regex",
                "managerFilePatterns": patterns,
                "matchStrings": ['GO = "(?<currentValue>[^"]+)"'],
                "datasourceTemplate": "github-releases",
            }
        )
        return value

    return mutate


def _renovate_mutation_cases() -> dict:
    """Mutations of the Renovate config that the validator must refuse (ADR-0152)."""
    return {
        "second manager via /regex/i": (
            with_extra_manager(["/SCRIPTS/CHECK-BUILD-CONFIG\\.PY$/i"]),
            "expected exactly one regex customManager",
        ),
        "second manager via glob": (
            with_extra_manager(["**/*.py"]),
            "expected exactly one regex customManager",
        ),
        "unevaluable file pattern": (
            with_extra_manager(["scripts/[a-z]*.py"]),
            "is not evaluated by this checker",
        ),
        "missing Go manager": (
            without_checker_manager,
            "expected exactly one regex customManager",
        ),
        "golang-version datasource": (
            mutate_checker_manager("datasourceTemplate", "golang-version"),
            "needs datasourceTemplate 'github-releases'",
        ),
        "different depName": (
            mutate_checker_manager("depNameTemplate", "golang"),
            "needs depNameTemplate 'go'",
        ),
        "semver versioning": (
            mutate_checker_manager("versioningTemplate", "semver"),
            "needs versioningTemplate 'npm'",
        ),
        "matchString misses literal": (
            mutate_checker_manager(
                "matchStrings", ['GO_VERSION = "(?<currentValue>\\d+\\.\\d+)"']
            ),
            "matchString must capture exactly",
        ),
    }


def renovate_validator_regressions() -> list[str]:
    """Prove that removing or breaking the mirroring Go manager fails (ADR-0152)."""
    failures: list[str] = []
    source = RENOVATE_CONFIG.read_text(encoding="utf-8")
    checker = Path(__file__).resolve().read_text(encoding="utf-8")
    config = json.loads(source)

    cases = _renovate_mutation_cases()
    for name, (mutate, expected) in cases.items():
        try:
            mutated = json.dumps(mutate(json.loads(json.dumps(config))))
        except RenovatePatternError as exc:
            failures.append(f"renovate regression: {name} not run: {exc}")
            continue
        if json.loads(mutated) == config:
            failures.append(f"renovate regression: {name} mutation changed nothing")
            continue
        errors = validate_renovate_text(mutated, checker)
        if not any(expected in error for error in errors):
            failures.append(f"renovate regression: {name} was accepted")
    stale = checker.replace(
        f'ACTIONLINT_GO_VERSION = "{ACTIONLINT_GO_VERSION}"',
        'ACTIONLINT_GO_VERSION = "go1.0"',
        1,
    )
    if stale == checker or not validate_renovate_text(source, stale):
        failures.append("renovate regression: unmatched checker literal was accepted")
    return failures


def validate_consumers() -> list[str]:
    """Validate all operational consumers of the FFmpeg contract."""
    errors: list[str] = []
    for path in CONSUMERS:
        errors.extend(validate_consumer(path))

    generator = CONSUMERS[0].read_text(encoding="utf-8")
    errors.extend(validate_generator_text(generator))
    replay = CONSUMERS[1].read_text(encoding="utf-8")
    errors.extend(validate_replay_text(replay))
    if not QSV_REPLAY.is_file():
        errors.append("ffmpeg-patches/test/qsv-roi-regression.sh: missing")
    else:
        errors.extend(validate_qsv_replay_text(QSV_REPLAY.read_text(encoding="utf-8")))
    if not STATIC_AVFILTER_CONSUMER.is_file():
        errors.append("ffmpeg-patches/test/static-libavfilter-consumer.c: missing")
    else:
        errors.extend(
            validate_static_consumer_text(
                STATIC_AVFILTER_CONSUMER.read_text(encoding="utf-8")
            )
        )
    if not SVTAV1_ROI_PATCH.is_file():
        errors.append("ffmpeg-patches/files/svtav1-pelorus-roi.patch: missing")
    else:
        errors.extend(
            validate_svtav1_roi_patch_text(SVTAV1_ROI_PATCH.read_text(encoding="utf-8"))
        )
    errors.extend(validate_workflows())
    return errors


def isolate_from_invoking_repository() -> list[str]:
    """Drop the repository-locating Git variables before any fixture runs.

    Git exports GIT_DIR, GIT_WORK_TREE, GIT_INDEX_FILE and related variables to
    hooks. A self-test started from a hook (the pre-push `make verify-all`)
    would otherwise run every fixture `git init`, `git config` and `git commit`
    against the invoking repository instead of its throwaway one, rewriting
    the developer's .git/config (core.bare, core.hooksPath, user.*, gpg.program)
    and branch refs. `git rev-parse --local-env-vars` is Git's own list of
    these variables, the same list its sample hooks unset.
    """
    listed = subprocess.run(
        ("git", "rev-parse", "--local-env-vars"),
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if listed.returncode != 0:
        return [
            "self-test isolation: git rev-parse --local-env-vars failed: "
            + listed.stderr.strip()
        ]
    for name in listed.stdout.split():
        os.environ.pop(name, None)
    return []


def main() -> int:
    if not CONFIG.is_file():
        print("build-config.env: missing", file=sys.stderr)
        return 1

    config_text = CONFIG.read_text(encoding="utf-8")
    values, parse_errors = parse_assignments(config_text)
    errors = validate_config(config_text)
    errors.extend(validate_consumers())
    errors.extend(validate_renovate())
    if not parse_errors:
        errors.extend(validate_current_surfaces(values))
    if "--self-test" in sys.argv[1:]:
        errors.extend(isolate_from_invoking_repository())
        errors.extend(validator_regressions())
        errors.extend(consumer_validator_regressions())
        errors.extend(static_consumer_validator_regressions())
        errors.extend(svtav1_roi_patch_validator_regression())
        errors.extend(qsv_validator_regressions())
        errors.extend(surface_validator_regressions())
        errors.extend(fixture_subprocess_regression())
        errors.extend(git_fixture_policy_regression())
        errors.extend(git_tag_ref_regression())
        errors.extend(git_dirty_worktree_cleanup_regression())
        errors.extend(git_worktree_hook_regression())
        errors.extend(git_am_hook_regression())
        errors.extend(git_smudge_cleanup_regression())
        errors.extend(git_format_config_regression())
        errors.extend(workflow_validator_regressions())
        errors.extend(renovate_pattern_regressions())
        errors.extend(renovate_validator_regressions())

    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
