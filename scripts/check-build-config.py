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
    ROOT / ".github" / "workflows" / "release-build.yml",
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
# HISS-10 (Praetor 492a00f9 audit): every hosted Meson lane passes --werror on
# the meson setup command line; the gate does not read default_options.
WINDOWS_COMMANDS = (
    "meson setup --werror build && ninja -C build",
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
# ADR-0170: the docs job pins actions/setup-go and actionlint by shape, never by
# value. Renovate's github-actions manager bumps the setup-go digest and its
# `# vX.Y.Z` comment, and the regex manager for ci.yml bumps the actionlint tag,
# in ci.yml alone; a checker-held copy of either would turn every such bump red.
SETUP_GO_PIN = re.compile(
    r"^[ ]+(?:- )?uses: actions/setup-go@[0-9a-f]{40} # v\d+\.\d+\.\d+$", re.MULTILINE
)
ACTIONLINT_MODULE = "github.com/rhysd/actionlint"
ACTIONLINT_RUN = re.compile(
    r"^[ ]+run: go run github\.com/rhysd/actionlint/cmd/actionlint@(?P<tag>v\d+\.\d+\.\d+)$",
    re.MULTILINE,
)
CI_RELATIVE = ".github/workflows/ci.yml"
ACTIONLINT_RENOVATE_TEMPLATES = {
    "depNameTemplate": ACTIONLINT_MODULE,
    "datasourceTemplate": "go",
}
# The Go toolchain that runs actionlint in ci.yml's docs job. Renovate's
# github-actions manager bumps the setup-go go-version input; the regex
# customManager in renovate.json (validated below) rewrites this literal with
# the same package identity, so both land in one renovate/go-<major>.x branch.
ACTIONLINT_GO_VERSION = "1.27.x"
ACTIONLINT_GO_STEP = "- name: Set up Go for actionlint"
# #228: the hosted lavapipe lane (software Vulkan). It is functional evidence
# only. The Praetor hosted-gate draft step comes first (HISS-18), every later
# step carries the draft guard, and the lane keeps a bounded budget.
LAVAPIPE_JOB = "lavapipe"
LAVAPIPE_MAX_TIMEOUT_MINUTES = 45
LAVAPIPE_DRAFT_STEP = "- name: Stop on a draft pull request"
LAVAPIPE_DRAFT_GUARD = "if: github.event.pull_request.draft != true"
LAVAPIPE_PACKAGES = ("mesa-vulkan-drivers", "vulkan-tools", "vulkan-validationlayers")
LAVAPIPE_TOKENS = (
    "if: github.event.pull_request.draft == true",
    "KEEP_DIR=",
    "ffmpeg-patches/test/build-and-run.sh",
    "VK_DRIVER_FILES=",
    "echo \"PELORUS_VALIDATE=1\"",
    "ffmpeg-patches/test/vulkan-lavapipe-guard.sh --self-test --disable",
    "ffmpeg-patches/test/vulkan-lavapipe-filters.sh --self-test",
    "ffmpeg-patches/test/vulkan-lavapipe-report.sh --self-test",
    "ffmpeg-patches/test/vulkan-lavapipe-guard.sh\n",
    "ffmpeg-patches/test/vulkan-lavapipe-filters.sh\n",
    "ffmpeg-patches/test/vulkan-format-matrix.sh\n",
    "ffmpeg-patches/test/vulkan-lavapipe-report.sh\n",
)
LAVAPIPE_FORBIDDEN = ("continue-on-error", "PELORUS_VALIDATE=0", "|| true")
USES_LINE = re.compile(r"^\s+(?:- )?uses:\s*(\S+)", re.MULTILINE)
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
    failures.extend(_consumer_replay_keep_regressions(replay))
    failures.extend(_hermetic_regressions("replay", replay, validate_replay_text))
    return failures


def _consumer_replay_keep_regressions(replay: str) -> list[str]:
    mutated = replay.replace("KEEP_DIR", "KEEP_REMOVED")
    if mutated == replay:
        return ["consumer regression: KEEP_DIR mutation did not change fixture"]
    if not any(
        "must keep the linked binary for the lavapipe lane" in error
        for error in validate_replay_text(mutated)
    ):
        return ["consumer regression: replay without KEEP_DIR was accepted"]
    return []


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
        "KEEP_DIR": "must keep the linked binary for the lavapipe lane",
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


def validate_lavapipe_job(relative: str, block: str | None) -> list[str]:
    """Validate the lavapipe lane: draft stop first, bounded, pinned, guarded."""
    if block is None:
        return [f"{relative}: missing lavapipe job {LAVAPIPE_JOB}"]
    prefix = f"{relative}: lavapipe job"
    errors: list[str] = []
    steps = re.findall(r"^      - (?:name|uses):", block, re.MULTILINE)
    first = block.find("\n      - ")
    if first < 0 or not block[first + 1 :].lstrip().startswith(LAVAPIPE_DRAFT_STEP):
        errors.append(f"{prefix} must start with the draft stop step")
    if block.count(LAVAPIPE_DRAFT_GUARD) != len(steps) - 1:
        errors.append(f"{prefix} must guard every step after the draft stop with draft != true")
    timeout = re.search(r"^    timeout-minutes:\s*(\d+)\s*$", block, re.MULTILINE)
    if timeout is None or int(timeout.group(1)) > LAVAPIPE_MAX_TIMEOUT_MINUTES:
        errors.append(
            f"{prefix} must set timeout-minutes of at most {LAVAPIPE_MAX_TIMEOUT_MINUTES}"
        )
    for action in USES_LINE.findall(block):
        if not re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", action):
            errors.append(f"{prefix} must pin {action} by full commit digest")
    install_start = block.find("sudo apt-get install")
    install_end = block.find("\n      - name:", install_start)
    install = block[install_start:install_end] if install_start >= 0 else ""
    for package in LAVAPIPE_PACKAGES:
        if not re.search(rf"(?<![A-Za-z0-9_-]){re.escape(package)}(?![A-Za-z0-9_-])", install):
            errors.append(f"{prefix} must install {package}")
    for token in LAVAPIPE_TOKENS:
        if token not in block:
            errors.append(f"{prefix} is missing {token.strip()}")
    for token in LAVAPIPE_FORBIDDEN:
        if token in block:
            errors.append(f"{prefix} must not contain {token}")
    return errors


CI_CALL_USES = "uses: ./.github/workflows/ci.yml"
TAG_GUARD = "if: github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')"
# ADR-0169: release.yml calls the reusable release build, which builds, attests
# SLSA provenance, writes the SBOM and signs SHA256SUMS in one job; the publish
# job only downloads those files and creates the release on a tag push.
RELEASE_BUILD_CALL_USES = "uses: ./.github/workflows/release-build.yml"
RELEASE_BUILD_JOB = "build"
PUBLISH_JOB = "publish"
RELEASE_FILE_STEM = "pelorus-ffmpeg-patches-${artifact_label}"
RELEASE_ASSETS = (
    f'"{RELEASE_FILE_STEM}.tar.gz"',
    f'"{RELEASE_FILE_STEM}.spdx.json"',
    f'"{RELEASE_FILE_STEM}.provenance.sigstore.json"',
    "SHA256SUMS \\",
    "SHA256SUMS.sigstore.json",
)
# In file order: an attestation counts as SLSA Build Level 3 only when a build
# step of its own job runs before it (Praetor internal/forge MeasureProvenance).
RELEASE_BUILD_ORDER = (
    "run: meson setup --werror build",
    "run: make build BUILD_DIR=build",
    "name: Release tag shape and rc numbering",
    "name: Tag matches the project version",
    "name: Candidate legs are green on this commit",
    "name: Package the FFmpeg patch stack",
    "uses: anchore/sbom-action@",
    "uses: actions/attest-build-provenance@",
    "run: cosign sign-blob --yes --bundle SHA256SUMS.sigstore.json SHA256SUMS",
    "cosign verify-blob",
    "uses: actions/upload-artifact@",
)
RELEASE_BUILD_TOKENS = (
    "actions: read",
    "prerelease: ${{ steps.tag.outputs.prerelease }}",
    "id-token: write",
    "attestations: write",
    "upload-artifact: false",
    "upload-release-assets: false",
    "format: spdx-json",
    "--certificate-identity-regexp "
    "'^https://github\\.com/VMAFx/pelorus/\\.github/workflows/release-build\\.yml@'",
    "--certificate-oidc-issuer https://token.actions.githubusercontent.com",
    "GITHUB_REF_NAME//\\//-",
    "ARTIFACT_LABEL",
)
# Any of these before the attestation would make it attest files the job did
# not build (Level 2); the reusable release build uses none of them.
RELEASE_BUILD_IMPORTS = ("actions/download-artifact", "actions/cache", "gh run download")


def is_job_call(block: str) -> bool:
    """True when a job calls a reusable workflow of this repository."""
    return re.search(r"^    uses: \./\.github/workflows/", block, re.MULTILINE) is not None


def workflow_triggers(text: str) -> list[str]:
    """The event names of the top-level `on:` mapping, in file order."""
    match = re.search(r"^on:[ ]*\n(?P<body>(?:(?:[ ]+.*|[ ]*)\n)+)", text, re.MULTILINE)
    if match is None:
        return []
    return re.findall(r"^  ([A-Za-z_]+):", match.group("body"), re.MULTILINE)


def is_reusable_ci_call(block: str) -> bool:
    """True when a job's whole body is the job-level call of ci.yml."""
    body = [
        line.strip()
        for line in block.splitlines()[1:]
        if line.strip() and not line.strip().startswith("#")
    ]
    return body == [CI_CALL_USES]


def job_needs(block: str) -> set[str]:
    """Names in a job's `needs:` (scalar, flow list or block list)."""
    inline = re.search(
        r"^    needs:[ ]*(?P<value>[^\s#].*?)\s*(?:#.*)?$", block, re.MULTILINE
    )
    if inline:
        return set(re.findall(r"[A-Za-z0-9_-]+", inline.group("value")))
    listed = re.search(
        r"^    needs:[ ]*(?:#.*)?\n(?P<body>(?:      -[ ]+\S.*\n?)+)",
        block,
        re.MULTILINE,
    )
    if listed:
        return set(
            re.findall(
                r"^      -[ ]+([A-Za-z0-9_-]+)", listed.group("body"), re.MULTILINE
            )
        )
    return set()


def _validate_release_gate(relative: str, jobs: dict[str, str]) -> list[str]:
    """A14/A15 + ADR-0169: CI call -> release build call -> tag-only publish."""
    errors: list[str] = []
    calls = [name for name, block in jobs.items() if is_reusable_ci_call(block)]
    if not calls:
        errors.append(f"{relative}: missing job that calls ./.github/workflows/ci.yml")
    build = jobs.get(RELEASE_BUILD_JOB, "")
    if RELEASE_BUILD_CALL_USES not in build:
        errors.append(
            f"{relative}: job {RELEASE_BUILD_JOB} must call "
            "./.github/workflows/release-build.yml"
        )
    if calls and not set(calls) & job_needs(build):
        errors.append(
            f"{relative}: release build job must list the ci call "
            f"({', '.join(calls)}) in needs"
        )
    publish = jobs.get(PUBLISH_JOB, "")
    if RELEASE_BUILD_JOB not in job_needs(publish):
        errors.append(f"{relative}: publish job must need the release build")
    if f"    {TAG_GUARD}\n" not in publish:
        errors.append(f"{relative}: publish job must run only on a v* tag push")
    for token in (
        "uses: actions/download-artifact@",
        "needs.build.outputs.artifact-name",
        "gh release create",
        "GITHUB_REF_NAME//\\//-",
        "PRERELEASE: ${{ needs.build.outputs.prerelease }}",
        "true) kind=(--prerelease --latest=false) ;;",
        "false) kind=() ;;",
        '"${kind[@]}"',
        "*) echo \"::error::release build did not classify",
        "gh release view",
        "--json isPrerelease",
    ) + RELEASE_ASSETS:
        if token not in publish:
            errors.append(f"{relative}: publish job is missing {token}")
    if "uses: actions/checkout@" in publish:
        errors.append(f"{relative}: publish job must publish the attested files only")
    writers = [n for n, b in jobs.items() if "contents: write" in b]
    if writers != [PUBLISH_JOB]:
        errors.append(f"{relative}: only the publish job may hold contents: write")
    return errors


def _validate_tag_version_step(relative: str, block: str) -> list[str]:
    """A15: a tag push fails unless the tag equals the meson.build version."""
    step = block.find("name: Tag matches the project version")
    if step < 0:
        return [f"{relative}: release build is missing the tag==version step"]
    end = block.find("\n      - name:", step)
    body = block[step:] if end < 0 else block[step:end]
    return [
        f"{relative}: tag==version step is missing {token}"
        for token in (
            TAG_GUARD,
            "GITHUB_REF_NAME#v",
            "meson introspect --projectinfo build",
            "::error::",
            "exit 1",
            "python3 -I scripts/release/verify-release.py version",
            "--meson-version",
            "libpelorus/include/pelorus/pelorus.h",
        )
        if token not in body
    ]


def _step_body(block: str, name: str) -> str | None:
    """The text of one named step, up to the next step; None when absent."""
    start = block.find(f"name: {name}")
    if start < 0:
        return None
    end = block.find("\n      - ", start)
    return block[start:] if end < 0 else block[start:end]


def _validate_rc_steps(relative: str, block: str) -> list[str]:
    """ADR-0176: tag shape, rc numbering and candidate legs before any build output."""
    errors: list[str] = []
    shape = _step_body(block, "Release tag shape and rc numbering")
    if shape is None:
        return [f"{relative}: release build is missing the tag shape and rc numbering step"]
    for token in (
        "id: tag",
        TAG_GUARD.removeprefix("if: "),
        "git ls-remote --tags --refs origin",
        "test -s",
        "python3 -I scripts/release/verify-release.py tag",
        "--tags-file",
        "set -o pipefail",
        "GITHUB_OUTPUT",
    ):
        if token not in shape:
            errors.append(f"{relative}: tag shape step is missing {token}")
    legs = _step_body(block, "Candidate legs are green on this commit")
    if legs is None:
        return errors + [f"{relative}: release build is missing the candidate legs step"]
    for token in (
        "steps.tag.outputs.prerelease == 'true'",
        TAG_GUARD.removeprefix("if: ").split(" && ")[0],
        "python3 -I scripts/release/check-candidate-legs.py",
        '--sha "$GITHUB_SHA"',
        "dist/CANDIDATE_LEGS.json",
        "GH_TOKEN: ${{ github.token }}",
    ):
        if token not in legs:
            errors.append(f"{relative}: candidate legs step is missing {token}")
    return errors


def _validate_release_notes_step(relative: str, block: str) -> list[str]:
    """Release notes come from the "## [<version>]" section, never [Unreleased]."""
    step = block.find("name: Extract release notes")
    if step < 0:
        return [f"{relative}: release build is missing the release notes step"]
    end = block.find("\n      - name:", step)
    body = block[step:] if end < 0 else block[step:end]
    errors = [
        f"{relative}: release notes step is missing {token}"
        for token in (
            "meson introspect --projectinfo build",
            "scripts/release/extract-release-notes.sh",
            '"$project_version"',
        )
        if token not in body
    ]
    if "UNRELEASED" in body or "Unreleased" in body:
        errors.append(f"{relative}: release notes step must not read [Unreleased]")
    return errors


def _validate_patch_notice(relative: str, block: str) -> list[str]:
    """#236: the patch archive carries a NOTICE that names the repository and the commit."""
    notice = _step_body(block, "Notice for the patch archive")
    package = _step_body(block, "Package the FFmpeg patch stack")
    if notice is None or package is None:
        return [f"{relative}: release build is missing the patch archive notice step"]
    errors = [
        f"{relative}: patch archive notice step is missing {token}"
        for token in ("bash scripts/release/write-patch-notice.sh", '"$GITHUB_REF_NAME" "$GITHUB_SHA"')
        if token not in notice
    ]
    if block.find("name: Notice for the patch archive") > block.find("name: Package the FFmpeg patch stack"):
        errors.append(f"{relative}: the patch archive notice must be written before the archive")
    if 'NOTICE' not in package or "LICENSES/LGPL-2.1-or-later.txt" not in package or "LICENSES/EUPL-1.2.txt" not in package:
        errors.append(f"{relative}: the patch archive must ship NOTICE and both licence texts")
    return errors


def _validate_release_build(relative: str, text: str, jobs: dict[str, str]) -> list[str]:
    """ADR-0169: build, SBOM, Level 3 attestation and cosign in one called job."""
    errors: list[str] = []
    if workflow_triggers(text) != ["workflow_call"]:
        errors.append(f"{relative}: release-build.yml must be triggered by workflow_call only")
    block = jobs.get(RELEASE_BUILD_JOB)
    if block is None:
        return errors + [f"{relative}: missing job {RELEASE_BUILD_JOB}"]
    errors.extend(_validate_rc_steps(relative, block))
    errors.extend(_validate_tag_version_step(relative, block))
    errors.extend(_validate_release_notes_step(relative, block))
    errors.extend(_validate_patch_notice(relative, block))
    for token in RELEASE_BUILD_TOKENS:
        if token not in block:
            errors.append(f"{relative}: release build is missing {token}")
    for token in RELEASE_BUILD_IMPORTS:
        if token in text:
            errors.append(f"{relative}: release build must not use {token}")
    positions = [block.find(token) for token in RELEASE_BUILD_ORDER]
    for token, position in zip(RELEASE_BUILD_ORDER, positions):
        if position < 0:
            errors.append(f"{relative}: release build is missing {token}")
    found = [position for position in positions if position >= 0]
    if found != sorted(found):
        errors.append(
            f"{relative}: release build steps are out of order (expected: "
            + " -> ".join(RELEASE_BUILD_ORDER)
            + ")"
        )
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
    is_release = Path(relative).name == "release.yml"
    for name, block in jobs.items():
        if is_job_call(block):
            continue  # a reusable-workflow call has no runner or steps of its own
        runner = WINDOWS_RUNNER if is_ci and name == WINDOWS_JOB else "ubuntu-26.04"
        runners = re.findall(r"^\s+runs-on:\s*([^\s#]+)", block, re.MULTILINE)
        if runners != [runner]:
            errors.append(f"{relative}: job {name} must run exactly on {runner}")
        if is_release and name == PUBLISH_JOB:
            continue  # publishes the release build's files; no checkout, no build
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
        if token not in text and not is_release:
            errors.append(f"{relative}: environment receipt is missing {token}")
    return errors


def _validate_workflow_native_packages(
    relative: str, jobs: dict[str, str]
) -> list[str]:
    errors: list[str] = []
    build_jobs = {
        "ci.yml": ("core", "ffmpeg-stack", LAVAPIPE_JOB, "sanitizers"),
        "release-build.yml": (RELEASE_BUILD_JOB,),
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
        if not re.search(r"^  workflow_call:", text, re.MULTILINE):
            errors.append(f"{relative}: ci.yml must declare workflow_call for release")
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
            f"go-version: '{ACTIONLINT_GO_VERSION}'",
        ):
            if token not in docs:
                errors.append(f"{relative}: docs job is missing {token}")
        if docs.count("actions/setup-go@") != 1 or SETUP_GO_PIN.search(docs) is None:
            errors.append(
                f"{relative}: docs job must pin actions/setup-go once by full commit "
                "digest with its `# vX.Y.Z` release comment"
            )
        if docs.count("actionlint/cmd/actionlint@") != 1 or ACTIONLINT_RUN.search(docs) is None:
            errors.append(
                f"{relative}: docs job must run actionlint once as "
                "`go run github.com/rhysd/actionlint/cmd/actionlint@vX.Y.Z` "
                "(exact release tag)"
            )
        go_versions = re.findall(r"go-version:\s*(\S+)", docs)
        if go_versions != [f"'{ACTIONLINT_GO_VERSION}'"]:
            errors.append(
                f"{relative}: docs job must set exactly one go-version, "
                f"'{ACTIONLINT_GO_VERSION}' (found {go_versions})"
            )
        errors.extend(validate_windows_job(relative, jobs.get(WINDOWS_JOB)))
        errors.extend(validate_lavapipe_job(relative, jobs.get(LAVAPIPE_JOB)))
    elif rel_name == "release.yml":
        if "workflow_dispatch:" not in text:
            errors.append(f"{relative}: release gate needs workflow_dispatch")
        errors.extend(_validate_release_gate(relative, jobs))
    elif rel_name == "release-build.yml":
        errors.extend(_validate_release_build(relative, text, jobs))
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
        "Windows build without warnings as errors": (
            mutate(
                "meson setup --werror build && ninja -C build",
                "meson setup build && ninja -C build",
            ),
            f"is missing {WINDOWS_COMMANDS[0]}",
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


def _rejected(relative: str, original: str, cases: dict[str, tuple[str, str]]) -> list[str]:
    """Each mutation must change the file and be rejected with its expected error."""
    failures: list[str] = []
    for name, (mutated, expected) in cases.items():
        if mutated == original:
            failures.append(f"workflow regression: {name} mutation changed nothing")
        elif not any(expected in e for e in validate_workflow_text(relative, mutated)):
            failures.append(f"workflow regression: {name} was accepted")
    if validate_workflow_text(relative, original):
        failures.append(f"workflow regression: current {relative} is rejected")
    return failures


def release_gate_regressions(source: str, relative: str) -> list[str]:
    """Prove the A14/A15 + ADR-0169 release gate and ci.yml workflow_call."""
    release_path = WORKFLOWS[1]
    release = release_path.read_text(encoding="utf-8")
    release_rel = release_path.relative_to(ROOT).as_posix()
    guard_line = f"    {TAG_GUARD}\n"
    failures = _rejected(release_rel, release, {
        "release build without needs: ci": (
            release.replace("    needs: ci\n", "", 1),
            "release build job must list the ci call",
        ),
        "release build needs lists only another job": (
            release.replace("    needs: ci\n", "    needs: [other]\n", 1),
            "release build job must list the ci call",
        ),
        "release without ci call job": (
            release.replace(CI_CALL_USES, "uses: ./.github/workflows/other.yml", 1),
            "missing job that calls ./.github/workflows/ci.yml",
        ),
        "publish on every run": (
            release.replace(guard_line, "", 1),
            "publish job must run only on a v* tag push",
        ),
        "publish before the release build": (
            release.replace("    needs: build\n", "    needs: ci\n", 1),
            "publish job must need the release build",
        ),
        "publish without the SHA256SUMS signature": (
            release.replace("            SHA256SUMS.sigstore.json \\\n", "", 1),
            "publish job is missing SHA256SUMS.sigstore.json",
        ),
        "publish rebuilds from a checkout": (
            release.replace(
                "    steps:\n",
                "    steps:\n      - uses: actions/checkout@" + "0" * 40 + "\n",
                1,
            ),
            "publish job must publish the attested files only",
        ),
        "release build may write contents": (
            release.replace("      id-token: write\n", "      contents: write\n", 1),
            "only the publish job may hold contents: write",
        ),
    })
    # Boundary: needs as flow and block lists with other entries stay accepted.
    for label, needs in (
        ("flow", "    needs: [other, ci]\n"),
        ("block", "    needs:\n      - other\n      - ci\n"),
    ):
        boundary = release.replace("    needs: ci\n", needs, 1)
        if boundary == release or validate_workflow_text(release_rel, boundary):
            failures.append(f"workflow regression: needs {label} list was rejected")
    no_call = source.replace("  workflow_call:\n", "", 1)
    if no_call == source or not any(
        "must declare workflow_call" in e
        for e in validate_workflow_text(relative, no_call)
    ):
        failures.append("workflow regression: ci.yml without workflow_call was accepted")
    return failures + release_build_regressions()


def _patch_notice_cases(text: str) -> dict[str, tuple[str, str]]:
    """#236: mutations of the patch archive NOTICE step the validator must reject."""
    return {
        "no patch archive notice step": (
            text.replace("name: Notice for the patch archive", "name: Other", 1),
            "release build is missing the patch archive notice step",
        ),
        "patch notice not generated": (
            text.replace("bash scripts/release/write-patch-notice.sh", "true", 1),
            "patch archive notice step is missing bash scripts/release/write-patch-notice.sh",
        ),
        "patch notice without the commit": (
            text.replace('"$GITHUB_REF_NAME" "$GITHUB_SHA"', '"$GITHUB_REF_NAME" HEAD', 1),
            'patch archive notice step is missing "$GITHUB_REF_NAME" "$GITHUB_SHA"',
        ),
        "patch notice left out of the archive": (
            text.replace(' \\\n            -C "$RUNNER_TEMP/patch-notice" NOTICE', "", 1),
            "the patch archive must ship NOTICE and both licence texts",
        ),
        "licence text left out of the archive": (
            text.replace("LICENSES/EUPL-1.2.txt ", "", 1),
            "the patch archive must ship NOTICE and both licence texts",
        ),
    }


def release_build_regressions() -> list[str]:
    """Prove the ADR-0169 reusable release build contract is enforced."""
    path = WORKFLOWS[2]
    text = path.read_text(encoding="utf-8")
    attest = "      - name: Attest build provenance (SLSA v1.0)\n"
    download = (
        "      - uses: actions/download-artifact@" + "0" * 40 + "\n"
        "        with:\n          name: x\n"
    )
    return _rejected(path.relative_to(ROOT).as_posix(), text, {
        "release build also runs on push": (
            text.replace("  workflow_call:\n", "  push:\n  workflow_call:\n", 1),
            "must be triggered by workflow_call only",
        ),
        "artefact downloaded before the attestation": (
            text.replace(attest, download + attest, 1),
            "release build must not use actions/download-artifact",
        ),
        "attestation without a build step": (
            text.replace("run: make build BUILD_DIR=build", "run: ninja -C build", 1),
            "release build is missing run: make build BUILD_DIR=build",
        ),
        "signature before the attestation": (
            text.replace(attest, "      - run: cosign sign-blob --yes --bundle "
                         "SHA256SUMS.sigstore.json SHA256SUMS\n" + attest, 1),
            "release build steps are out of order",
        ),
        "SBOM uploaded to the release by the action": (
            text.replace("upload-release-assets: false", "upload-release-assets: true", 1),
            "release build is missing upload-release-assets: false",
        ),
        "signature verified without the signer identity": (
            text.replace("--certificate-identity-regexp", "--certificate-identity-ignored", 1),
            "release build is missing --certificate-identity-regexp",
        ),
        "release build without tag==version step": (
            text.replace("name: Tag matches the project version", "name: Other", 1),
            "release build is missing the tag==version step",
        ),
        "release notes read the Unreleased block": (
            text.replace(
                'bash scripts/release/extract-release-notes.sh "$project_version" CHANGELOG.md',
                "awk '/<!-- BEGIN UNRELEASED/{f=1;next} /<!-- END UNRELEASED/{f=0} f' CHANGELOG.md",
                1,
            ),
            "release notes step is missing scripts/release/extract-release-notes.sh",
        ),
        "release notes without the version section read": (
            text.replace('"$project_version" CHANGELOG.md', "CHANGELOG.md", 1),
            'release notes step is missing "$project_version"',
        ),
        **_patch_notice_cases(text),
        "tag step without error annotation": (
            text.replace("::error::", "", 1),
            "tag==version step is missing ::error::",
        ),
    })


# --- ADR-0176: tester-publish.yml, its Containerfile and the candidate legs ---

TESTER_PUBLISH = ROOT / ".github" / "workflows" / "tester-publish.yml"
TESTER_CONTAINERFILE = ROOT / "tools" / "tester" / "Containerfile"
TESTER_BUILD_SCRIPT = ROOT / "tools" / "tester" / "build-ffmpeg.sh"
# ADR-0180: one image per kit; every job runs once per kit.
TESTER_KITS = ("generic", "nvidia", "intel")
TESTER_KIT_MATRIX = "        kit: [generic, nvidia, intel]\n"
# No apt-installed package and no source component may bring a vendor driver
# or a non-free archive component into any kit, with one exception that names
# one package and the stages that handle it (ADR-0180 decision 4a): Debian's
# intel-media-va-driver-non-free is Expat and sits in non-free only because its
# GPU kernels come without source. The licence record gate admits that package
# alone from outside main; here the Containerfile may name it, and enable the
# non-free area, only where the Intel kit installs it and fetches its source.
TESTER_APT_FORBIDDEN = re.compile(r"nvidia|cuda|non-?free|contrib", re.IGNORECASE)
TESTER_NONFREE_PACKAGE = "intel-media-va-driver-non-free"
TESTER_NONFREE_AREA_SED = "s/^Components: main$/Components: main non-free/"
TESTER_NONFREE_PACKAGE_STAGE = "assembled-intel"
TESTER_NONFREE_AREA_STAGES = ("assembled-intel", "debian-sources-intel")
# What each vendor kit's stages must carry (ADR-0180).
TESTER_KIT_TOKENS = {
    "build-nvidia": ("ARG NV_CODEC_HEADERS_COMMIT=", "--enable-ffnvcodec --enable-nvenc", "--enable-libdav1d"),
    # The host's Vulkan ICD (libGLX_nvidia.so.0) links libXext.so.6 and opens
    # the GLVND libEGL.so.1 (research 0229).
    "assembled-nvidia": ("libegl1", "libxext6"),
    "final-nvidia": ("NVIDIA_DRIVER_CAPABILITIES=compute,utility,video,graphics",),
    # libdrm: QSV picks the Intel render node by vendor id (research 0229).
    "build-intel": ("--enable-libvpl --enable-vaapi --enable-libdrm", "--enable-libdav1d"),
    "assembled-intel": (TESTER_NONFREE_PACKAGE, TESTER_NONFREE_AREA_SED, "libvpl2", "libmfx-gen1.2", "mesa-vulkan-drivers"),
    "debian-sources-intel": (TESTER_NONFREE_AREA_SED,),
}
CANDIDATE_LEGS = ROOT / "scripts" / "release" / "candidate-legs.json"
LICENCE_FLAGS_ARG = 'ARG FFMPEG_LICENCE_FLAGS="--enable-gpl --enable-version3"'
# ADR-0173 decision 5: a tester image never ships these, in any file we author.
BANNED_FFMPEG_FLAGS = (
    "--enable-nonfree",
    "--enable-cuda-nvcc",
    "--enable-cuda-sdk",
    "--enable-libfdk-aac",
    "--enable-libfdk_aac",
    "--enable-decklink",
    "--enable-libmpeghdec",
)
ACTION_USES = re.compile(r"^\s*(?:- )?uses: (\S+)(.*)$", re.MULTILINE)
PINNED_USE = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")
TESTER_PUBLISH_ORDER = (
    "name: Name the image tags of this kit",
    "name: Licence gate refuses a planted flag",
    "name: Licence record is valid and its gate refuses planted defects",
    "name: Build the tester image",
    "name: Licence gate on the built image",
    "name: Licence record gate on the built image",
    "name: Run the documented command without a GPU",
    "name: Build the -source companion",
    "name: Log in to ghcr.io",
    "name: Push both images",
    "uses: anchore/sbom-action@",
    "uses: actions/attest-build-provenance@",
    "uses: actions/attest@",
    "uses: sigstore/cosign-installer@",
    "cosign sign --yes",
    "cosign verify",
    "gh attestation verify",
    "name: The package is public",
)
TESTER_PUBLISH_TOKENS = (
    "environment: tester-publish",
    "id-token: write",
    "attestations: write",
    "packages: write",
    "push-to-registry: true",
    "format: spdx-json",
    "upload-artifact: false",
    "upload-release-assets: false",
    "sbom-path:",
    "--signer-workflow VMAFx/pelorus/.github/workflows/tester-publish.yml",
    "--certificate-identity-regexp "
    "'^https://github\\.com/VMAFx/pelorus/\\.github/workflows/tester-publish\\.yml@'",
    "--certificate-oidc-issuer https://token.actions.githubusercontent.com",
    '--target "final-${KIT}"',
    '--target "source-${KIT}"',
    '--tag "${IMAGE}:${TAG}-source"',
    "bash tools/tester/check-ffmpeg-licence.sh",
    "bash tools/tester/check-ffmpeg-licence-self-test.sh",
    "python3 -I -B tools/tester/licensing.py record",
    "python3 -I -B tools/tester/licensing.py self-test",
    '/opt/pelorus/tester/licensing.py check --kit "${KIT}" --root /',
    'pelorus_tester_report.py validate "${RUNNER_TEMP}/report/report.json" --kit "${KIT}"',
    # ADR-0180 tags: the generic kit keeps tester-<date>-<sha8>.
    'tag="tester-${STAMP}"',
    'tag="tester-${KIT}-${STAMP}"',
    "      fail-fast: false\n",
    TESTER_KIT_MATRIX,
    "      KIT: ${{ matrix.kit }}\n",
)
# The pull-request and nightly build job builds every kit and checks the
# report each one writes without a device (ADR-0180).
TESTER_BUILD_TOKENS = (
    TESTER_KIT_MATRIX,
    "      fail-fast: false\n",
    "      KIT: ${{ matrix.kit }}\n",
    '--target "final-${KIT}"',
    'pelorus_tester_report.py validate "${RUNNER_TEMP}/report/report.json" --kit "${KIT}"',
)
# ADR-0178 (#235): hosted-minute bounds. A job's timeout is a number no larger
# than its cap, so a stuck build fails instead of running for six hours.
TESTER_TIMEOUT_CAPS = {"build": 90, "validate": 10, "publish": 150}
TESTER_TIMEOUT_DEFAULT_CAP = 150
TESTER_TRIGGERS = ["pull_request", "schedule", "workflow_dispatch"]
TESTER_BUILD_IF = (
    "    if: github.event_name == 'pull_request' || "
    "(github.event_name == 'schedule' && github.repository == 'VMAFx/pelorus')\n"
)
# A pull request that changes only these must not start the image build; a
# change to any of the next set must.
TESTER_PATHS_QUIET = (
    "docs/development/tester-image.md",
    "docs/adr/0178-tester-artifact-licence-record.md",
    "docs/research/0236-tester-artifact-licence-audit.md",
    "CHANGELOG.md",
    "changelog.d/added/x.md",
    "README.md",
    "scripts/check-build-config.py",
    ".claude/skills/build/SKILL.md",
)
TESTER_PATHS_LOUD = (
    "tools/tester/Containerfile",
    "tools/tester/licensing.json",
    "tools/tester/licensing.py",
    "ffmpeg-patches/0001-x.patch",
    "libpelorus/src/pelorus.c",
    "build-config.env",
    "LICENSES/EUPL-1.2.txt",
    ".github/workflows/tester-publish.yml",
)


def _path_glob(pattern: str) -> re.Pattern[str]:
    """GitHub path filter glob: `**` crosses directories, `*` and `?` do not."""
    out: list[str] = []
    index = 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            out.append("(?:.*/)?")
            index += 3
        elif pattern.startswith("**", index):
            out.append(".*")
            index += 2
        elif pattern[index] == "*":
            out.append("[^/]*")
            index += 1
        elif pattern[index] == "?":
            out.append("[^/]")
            index += 1
        else:
            out.append(re.escape(pattern[index]))
            index += 1
    return re.compile("".join(out) + r"\Z")


def tester_pull_request_paths(text: str) -> list[str]:
    """The `paths:` list of the pull_request trigger, empty when there is none."""
    match = re.search(
        r"^  pull_request:\n(?:    (?!paths:).*\n)*    paths:\n((?:      - .*\n)+)", text, re.MULTILINE
    )
    if match is None:
        return []
    return [p.strip().strip("'\"") for p in re.findall(r"^      - (.*)$", match.group(1), re.MULTILINE)]


def _tester_paths(relative: str, text: str) -> list[str]:
    """A docs-only pull request must not start the image build; an image input must."""
    paths = tester_pull_request_paths(text)
    if not paths:
        return [f"{relative}: pull_request needs a paths filter (hosted-minute cost, #235)"]
    globs = [_path_glob(p) for p in paths]
    errors = [
        f"{relative}: pull_request paths match a docs-only change ({sample}); the image build would start"
        for sample in TESTER_PATHS_QUIET
        if any(g.match(sample) for g in globs)
    ]
    errors.extend(
        f"{relative}: pull_request paths miss an image input ({sample})"
        for sample in TESTER_PATHS_LOUD
        if not any(g.match(sample) for g in globs)
    )
    return errors


def _tester_timeouts(relative: str, jobs: dict[str, str]) -> list[str]:
    errors: list[str] = []
    for name, block in jobs.items():
        match = re.search(r"^    timeout-minutes:[ ]*(\S+)[ ]*$", block, re.MULTILINE)
        cap = TESTER_TIMEOUT_CAPS.get(name, TESTER_TIMEOUT_DEFAULT_CAP)
        if match is None or not match.group(1).isdigit():
            errors.append(f"{relative}: job {name} needs a numeric timeout-minutes")
        elif not 0 < int(match.group(1)) <= cap:
            errors.append(f"{relative}: job {name} timeout-minutes {match.group(1)} exceeds its cap {cap}")
    return errors


def _tester_nightly(relative: str, text: str, jobs: dict[str, str]) -> list[str]:
    """The nightly schedule builds and never publishes (#235): only `build` can run on it."""
    errors: list[str] = []
    if len(re.findall(r"^    - cron:", text, re.MULTILINE)) != 1:
        errors.append(f"{relative}: schedule needs exactly one cron entry")
    for name, block in jobs.items():
        mentions = "schedule" in block or "always()" in block
        if name != "build" and mentions:
            errors.append(f"{relative}: job {name} must not run on schedule or use always()")
    for name in ("validate", "publish"):
        if "github.event_name != 'workflow_dispatch'" in jobs.get(name, ""):
            errors.append(f"{relative}: job {name} must not negate workflow_dispatch")
    return errors


def _tester_pins(relative: str, text: str) -> list[str]:
    """Every action is pinned by full digest with its `# vX.Y.Z` release comment."""
    errors: list[str] = []
    for match in ACTION_USES.finditer(text):
        target, rest = match.group(1), match.group(2)
        if target.startswith("./"):
            continue
        if PINNED_USE.fullmatch(target) is None or not re.fullmatch(
            r"\s+# v\d+\.\d+\.\d+\s*", rest
        ):
            errors.append(
                f"{relative}: action {target} must be pinned by full commit digest "
                "with a `# vX.Y.Z` release comment"
            )
    return errors


def _tester_job_hygiene(relative: str, jobs: dict[str, str]) -> list[str]:
    """Runner, timeout, checkout credentials and publish-only privileges per job."""
    errors: list[str] = []
    for name, block in jobs.items():
        if re.findall(r"^\s+runs-on:\s*([^\s#]+)", block, re.MULTILINE) != ["ubuntu-26.04"]:
            errors.append(f"{relative}: job {name} must run exactly on ubuntu-26.04")
        if "timeout-minutes:" not in block:
            errors.append(f"{relative}: job {name} needs timeout-minutes")
        for checkout in re.finditer(
            r"uses: actions/checkout@[^\n]*\n(?P<with>(?:        .*\n)*)", block
        ):
            if "persist-credentials: false" not in checkout.group("with"):
                errors.append(
                    f"{relative}: job {name} checkout must set persist-credentials: false"
                )
        if name == "publish":
            continue
        for token in ("docker push", "docker login", "secrets.", "packages: write", "id-token: write"):
            if token in block:
                errors.append(f"{relative}: job {name} must not use {token}; only publish publishes")
    return errors


def _tester_jobs(relative: str, jobs: dict[str, str]) -> list[str]:
    errors = [f"{relative}: missing job {n}" for n in ("build", "validate", "publish") if n not in jobs]
    if errors:
        return errors
    build, validate, publish = jobs["build"], jobs["validate"], jobs["publish"]
    errors.extend(_tester_job_hygiene(relative, jobs))
    errors.extend(_tester_timeouts(relative, jobs))
    if TESTER_BUILD_IF not in build:
        errors.append(
            f"{relative}: build job must run only on pull_request or the schedule of VMAFx/pelorus"
        )
    errors.extend(f"{relative}: build job is missing {t.strip()}" for t in TESTER_BUILD_TOKENS if t not in build)
    draft = build.find("name: Stop on a draft pull request")
    first_checkout = build.find("uses: actions/checkout@")
    if draft < 0 or first_checkout < 0 or draft > first_checkout:
        errors.append(
            f"{relative}: build job must stop on a draft pull request before it checks out (HISS-18)"
        )
    for job_name, block in (("validate", validate), ("publish", publish)):
        if "    if: github.event_name == 'workflow_dispatch'\n" not in block:
            errors.append(f"{relative}: {job_name} job must run only on workflow_dispatch")
    if "validate" not in job_needs(publish):
        errors.append(f"{relative}: publish job must need validate")
    for token in (
        "git merge-base --is-ancestor",
        "origin/master",
        "fetch-depth: 0",
        'for commit in "$sha" "$GITHUB_SHA"',
    ):
        if token not in validate:
            errors.append(f"{relative}: validate job is missing {token}")
    return errors


def _tester_publish_steps(relative: str, publish: str) -> list[str]:
    errors = [
        f"{relative}: publish job is missing {t}" for t in TESTER_PUBLISH_TOKENS if t not in publish
    ]
    positions = [publish.find(token) for token in TESTER_PUBLISH_ORDER]
    errors.extend(
        f"{relative}: publish job is missing {token}"
        for token, position in zip(TESTER_PUBLISH_ORDER, positions)
        if position < 0
    )
    found = [position for position in positions if position >= 0]
    if found != sorted(found):
        errors.append(
            f"{relative}: publish steps are out of order (expected: "
            + " -> ".join(TESTER_PUBLISH_ORDER)
            + ")"
        )
    if publish.count("push-to-registry: true") < 3:
        errors.append(
            f"{relative}: publish job must attest the image, its SBOM and the -source image to the registry"
        )
    if publish.count("cosign sign --yes") != 2:
        errors.append(f"{relative}: publish job must sign both the image and the -source image")
    return errors


def validate_tester_publish_text(relative: str, text: str) -> list[str]:
    """Shape of the dispatch-only tester publish workflow (ADR-0173/0176)."""
    errors: list[str] = []
    jobs = workflow_job_blocks(text)
    if not jobs:
        return [f"{relative}: no jobs found"]
    if workflow_triggers(text) != TESTER_TRIGGERS:
        errors.append(f"{relative}: triggers must be exactly pull_request, schedule and workflow_dispatch")
    errors.extend(_tester_paths(relative, text))
    errors.extend(_tester_nightly(relative, text, jobs))
    if not re.search(r"^permissions:\n  contents: read\n", text, re.MULTILINE):
        errors.append(f"{relative}: top-level permissions must be contents: read only")
    for token in ("ubuntu-latest", "windows-latest", "/home/kilian/", *BANNED_FFMPEG_FLAGS):
        if token in text:
            errors.append(f"{relative}: forbidden workflow token {token}")
    errors.extend(_tester_pins(relative, text))
    errors.extend(_tester_jobs(relative, jobs))
    errors.extend(_tester_publish_steps(relative, jobs.get("publish", "")))
    return errors


def tester_stage(text: str, name: str) -> str:
    """The instructions of Containerfile stage `name`, up to the next FROM."""
    match = re.search(rf"^FROM \S+ AS {re.escape(name)}\n(.*?)(?=^FROM |\Z)", text, re.MULTILINE | re.DOTALL)
    return match.group(0) if match else ""


def _tester_licence_stage(relative: str, text: str, kit: str) -> list[str]:
    """ADR-0178: each kit's licence record gate is a stage its final stage depends on."""
    stage = tester_stage(text, f"licence-{kit}")
    errors: list[str] = []
    arg = f"{kit} "
    steps = ("licensing.py self-test", f"licensing.py notices --kit {arg}", f"licensing.py check --kit {arg}")
    positions = [stage.find(t) for t in steps]
    if min(positions) < 0 or positions != sorted(positions):
        errors.append(
            f"{relative}: stage licence-{kit} must run licensing.py self-test, notices --kit {kit} "
            f"and check --kit {kit} in that order"
        )
    if f"FROM assembled-{kit} AS licence-{kit}" not in text or f"FROM assembled-{kit} AS final-{kit}" not in text:
        errors.append(f"{relative}: stages licence-{kit} and final-{kit} must both start from assembled-{kit}")
    if f"COPY --from=licence-{kit} " not in tester_stage(text, f"final-{kit}"):
        errors.append(f"{relative}: stage final-{kit} must copy from stage licence-{kit}, so the gate cannot be skipped")
    for arg in ("ARG FFMPEG_REMOTE", "ARG FFMPEG_COMMIT", "ARG PELORUS_COMMIT"):
        if arg not in stage:
            errors.append(f"{relative}: stage licence-{kit} needs {arg}")
    return errors


def _tester_kit_stages(relative: str, text: str) -> list[str]:
    """ADR-0180: every kit has its build, gate, image and -source stages."""
    errors: list[str] = []
    for kit in TESTER_KITS:
        missing = [
            f"{relative}: missing stage AS {stage}-{kit}"
            for stage in ("build", "assembled", "licence", "final", "debian-sources", "source")
            if not tester_stage(text, f"{stage}-{kit}")
        ]
        if missing:
            errors.extend(missing)
            continue
        errors.extend(_tester_licence_stage(relative, text, kit))
        if "/opt/gate/build-ffmpeg.sh" not in tester_stage(text, f"build-{kit}"):
            errors.append(f"{relative}: stage build-{kit} must build FFmpeg with /opt/gate/build-ffmpeg.sh")
        if f"PELORUS_TESTER_KIT={kit} " not in tester_stage(text, f"final-{kit}"):
            errors.append(f"{relative}: stage final-{kit} must set PELORUS_TESTER_KIT={kit}")
        source = tester_stage(text, f"source-{kit}")
        for token in (f"COPY --from=build-{kit} /opt/source /source/ffmpeg",
                      f"COPY --from=debian-sources-{kit} /debian-source /source/debian"):
            if token not in source:
                errors.append(f"{relative}: stage source-{kit} is missing {token}")
    for stage, tokens in TESTER_KIT_TOKENS.items():
        errors.extend(
            f"{relative}: stage {stage} is missing {token}" for token in tokens if token not in tester_stage(text, stage)
        )
    return errors


def tester_apt_packages(text: str) -> list[str]:
    """Package names of every `apt-get install` in a Containerfile."""
    joined = re.sub(r"\\\n", " ", text)
    packages: list[str] = []
    for segment in re.split(r"&&|;|\n", joined):
        if "apt-get install" not in segment:
            continue
        words = segment.split("apt-get install", 1)[1].split()
        packages.extend(w for w in words if not w.startswith("-") and "=" not in w)
    return packages


def _tester_without_nonfree_exception(text: str) -> str:
    """The Containerfile without the one package and the one source-area edit the Intel kit may name."""
    stage = tester_stage(text, TESTER_NONFREE_PACKAGE_STAGE)
    text = text.replace(stage, stage.replace(TESTER_NONFREE_PACKAGE, ""), 1) if stage else text
    for name in TESTER_NONFREE_AREA_STAGES:
        stage = tester_stage(text, name)
        text = text.replace(stage, stage.replace(TESTER_NONFREE_AREA_SED, ""), 1) if stage else text
    return text


def _tester_vendor_files(relative: str, text: str) -> list[str]:
    """No kit installs a vendor driver or enables a non-free archive component."""
    text = _tester_without_nonfree_exception(text)
    errors = [
        f"{relative}: forbidden package {name} (vendor driver or non-free, ADR-0180)"
        for name in tester_apt_packages(text)
        if TESTER_APT_FORBIDDEN.search(name)
    ]
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        if "Components:" in line or "debian.sources" in line:
            if TESTER_APT_FORBIDDEN.search(line):
                errors.append(f"{relative}: a non-free or contrib archive component is enabled: {line.strip()}")
    return errors


def validate_tester_containerfile_text(relative: str, text: str) -> list[str]:
    """The Containerfile builds GPL-3.0-or-later FFmpeg per kit behind the licence gates."""
    errors: list[str] = []
    if LICENCE_FLAGS_ARG not in text:
        errors.append(f"{relative}: licence flags must default to --enable-gpl --enable-version3")
    for flag in BANNED_FFMPEG_FLAGS:
        if flag in text:
            errors.append(f"{relative}: forbidden FFmpeg flag {flag}")
    copy = "COPY tools/tester/build-ffmpeg.sh tools/tester/check-ffmpeg-licence.sh tools/tester/check-ffmpeg-licence-self-test.sh /opt/gate/"
    if copy not in tester_stage(text, "build"):
        errors.append(f"{relative}: stage build must copy build-ffmpeg.sh and the licence gate to /opt/gate/")
    for stage in ("build", "base"):
        if not tester_stage(text, stage):
            errors.append(f"{relative}: missing stage AS {stage}")
    errors.extend(_tester_kit_stages(relative, text))
    errors.extend(_tester_vendor_files(relative, text))
    for token in ("libpelorus-commit.txt", "installed-sources.txt"):
        if token not in text:
            errors.append(f"{relative}: -source image is missing {token}")
    if not re.search(r"^FROM \$\{DEBIAN_IMAGE\} AS build$", text, re.MULTILINE) or not re.search(
        r"^ARG DEBIAN_IMAGE=\S+@sha256:[0-9a-f]{64}$", text, re.MULTILINE
    ):
        errors.append(f"{relative}: base image must be pinned by digest")
    return errors


def validate_tester_build_script_text(relative: str, text: str) -> list[str]:
    """build-ffmpeg.sh runs the licence gate after its self-test and never takes a licence flag."""
    errors: list[str] = []
    for flag in BANNED_FFMPEG_FLAGS:
        if flag in text:
            errors.append(f"{relative}: forbidden FFmpeg flag {flag}")
    selftest = text.find("\n/opt/gate/check-ffmpeg-licence-self-test.sh\n")
    gate = text.find("\n/opt/gate/check-ffmpeg-licence.sh /opt/ffmpeg/bin/ffmpeg\n")
    if selftest < 0 or gate < 0 or selftest > gate:
        errors.append(f"{relative}: the licence gate self-test must run, then the gate on /opt/ffmpeg/bin/ffmpeg")
    for token in (
        "set -euo pipefail",
        "is a licence decision, not a kit feature; refused",
        "${FFMPEG_LICENCE_FLAGS} ${FFMPEG_FEATURE_FLAGS} \"$@\"",
        "/opt/source/ffmpeg-configure-line.txt",
        "/opt/source/series.txt",
    ):
        if token not in text:
            errors.append(f"{relative}: build-ffmpeg.sh is missing {token}")
    return errors


def validate_candidate_legs_text(
    legs_text: str, release_text: str, workflows: set[str]
) -> list[str]:
    """Every leg of candidate-legs.json names a job or workflow that exists."""
    try:
        doc = json.loads(legs_text)
    except json.JSONDecodeError as error:
        return [f"scripts/release/candidate-legs.json: {error}"]
    errors: list[str] = []
    jobs = workflow_job_blocks(release_text)
    build_needs = job_needs(jobs.get(RELEASE_BUILD_JOB, ""))
    for leg in doc.get("legs", []):
        leg_id = leg.get("id")
        if leg.get("kind") == "needs":
            job = leg.get("job")
            if job not in jobs:
                errors.append(f"candidate-legs.json: leg {leg_id} names job {job}, which release.yml lacks")
            elif job not in build_needs:
                errors.append(f"candidate-legs.json: release build does not need {job} (leg {leg_id})")
        if leg.get("kind") == "workflow_run" and leg.get("workflow") not in workflows:
            errors.append(
                f"candidate-legs.json: leg {leg_id} names workflow {leg.get('workflow')}, which does not exist"
            )
    return errors


def validate_tester_publish() -> list[str]:
    errors: list[str] = []
    for path, check in (
        (TESTER_PUBLISH, validate_tester_publish_text),
        (TESTER_CONTAINERFILE, validate_tester_containerfile_text),
        (TESTER_BUILD_SCRIPT, validate_tester_build_script_text),
    ):
        relative = path.relative_to(ROOT).as_posix()
        if not path.is_file():
            errors.append(f"{relative}: missing")
            continue
        errors.extend(check(relative, path.read_text(encoding="utf-8")))
    if CANDIDATE_LEGS.is_file():
        names = {p.name for p in (ROOT / ".github" / "workflows").glob("*.yml")}
        errors.extend(
            validate_candidate_legs_text(
                CANDIDATE_LEGS.read_text(encoding="utf-8"),
                WORKFLOWS[1].read_text(encoding="utf-8"),
                names,
            )
        )
    else:
        errors.append("scripts/release/candidate-legs.json: missing")
    return errors


def _all_rejected(name: str, mutated: str, original: str, validate, expected: str) -> list[str]:
    if mutated == original:
        return [f"tester publish regression: {name} mutation changed nothing"]
    if not any(expected in error for error in validate(mutated)):
        return [f"tester publish regression: {name} was accepted"]
    return []


def _tester_trigger_job_cases(text: str) -> dict[str, tuple[str, str]]:
    pinned = r"([0-9a-f]{40}) # v\d+\.\d+\.\d+"
    return {
        "push trigger": (
            text.replace("on:\n", "on:\n  push:\n    branches: [master]\n", 1),
            "triggers must be exactly",
        ),
        "no schedule trigger": (
            text.replace("  schedule:\n    - cron: '17 2 * * *'\n", "", 1),
            "triggers must be exactly",
        ),
        "second cron entry": (
            text.replace("    - cron: '17 2 * * *'\n", "    - cron: '17 2 * * *'\n    - cron: '17 3 * * *'\n", 1),
            "schedule needs exactly one cron entry",
        ),
        "workflow_run trigger": (
            text.replace("  workflow_dispatch:\n    inputs:", "  workflow_run:\n    workflows: [CI]\n  workflow_dispatch:\n    inputs:", 1),
            "triggers must be exactly",
        ),
        "floating action tag": (
            re.sub(r"(actions/attest@)" + pinned, r"\1v4", text, count=1),
            "must be pinned by full commit digest",
        ),
        "pin without release comment": (
            re.sub(r"(actions/checkout@[0-9a-f]{40}) # v\d+\.\d+\.\d+", r"\1", text, count=1),
            "must be pinned by full commit digest",
        ),
    }


def _tester_job_cases(text: str) -> dict[str, tuple[str, str]]:
    draft = "      - name: Stop on a draft pull request"
    return {
        "publish without environment": (
            text.replace("    environment: tester-publish\n", "", 1),
            "publish job is missing environment: tester-publish",
        ),
        "publish on pull_request": (
            text.replace(
                "    needs: validate\n    if: github.event_name == 'workflow_dispatch'\n",
                "    needs: validate\n",
                1,
            ),
            "publish job must run only on workflow_dispatch",
        ),
        "validate without master ancestry": (
            text.replace("git merge-base --is-ancestor", "git merge-base", 1),
            "validate job is missing git merge-base --is-ancestor",
        ),
        "validate skips the workflow commit": (
            text.replace('for commit in "$sha" "$GITHUB_SHA"', 'for commit in "$sha"', 1),
            "validate job is missing for commit",
        ),
        "build job pushes": (
            replace_in_job(text, "build", "          docker build \\\n", "          docker push x\n          docker build \\\n"),
            "job build must not use docker push",
        ),
        "build job holds packages: write": (
            replace_in_job(text, "build", "    timeout-minutes: 90\n", "    timeout-minutes: 90\n    permissions:\n      packages: write\n"),
            "job build must not use packages: write",
        ),
        "no draft step": (
            text.replace("name: Stop on a draft pull request", "name: Other", 1),
            "must stop on a draft pull request",
        ),
        "draft step after checkout": (
            text.replace(
                draft,
                "      - uses: actions/checkout@" + "0" * 40 + " # v1.0.0\n        with:\n          persist-credentials: false\n" + draft,
                1,
            ),
            "must stop on a draft pull request",
        ),
        "checkout keeps credentials": (
            text.replace("          persist-credentials: false\n", "", 1),
            "must set persist-credentials: false",
        ),
        "write permission at top level": (
            text.replace("permissions:\n  contents: read\n", "permissions:\n  contents: write\n", 1),
            "top-level permissions must be contents: read only",
        ),
    }


def _tester_cost_cases(text: str) -> dict[str, tuple[str, str]]:
    """#235: a docs-only pull request does not start the image build."""
    return {
        "docs path in the filter": (
            text.replace("      - 'LICENSES/**'\n", "      - 'LICENSES/**'\n      - 'docs/**'\n", 1),
            "match a docs-only change (docs/development/tester-image.md)",
        ),
        "catch-all path in the filter": (
            text.replace("      - 'LICENSES/**'\n", "      - '**'\n", 1),
            "match a docs-only change (CHANGELOG.md)",
        ),
        "markdown path in the filter": (
            text.replace("      - 'LICENSES/**'\n", "      - '**/*.md'\n", 1),
            "match a docs-only change (README.md)",
        ),
        "tester tools dropped from the filter": (
            text.replace("      - 'tools/tester/**'\n", "", 1),
            "miss an image input (tools/tester/Containerfile)",
        ),
        "libpelorus dropped from the filter": (
            text.replace("      - 'libpelorus/**'\n", "", 1),
            "miss an image input (libpelorus/src/pelorus.c)",
        ),
        "no paths filter": (
            re.sub(r"    paths:\n(?:      - .*\n)+", "", text, count=1),
            "pull_request needs a paths filter",
        ),
    }


def _tester_nightly_cases(text: str) -> dict[str, tuple[str, str]]:
    """#235: the nightly schedule never reaches publish; every job has a bounded timeout."""
    publish_if = "    needs: validate\n    if: github.event_name == 'workflow_dispatch'\n"
    validate_if = "    if: github.event_name == 'workflow_dispatch'\n    runs-on"
    return {
        "nightly reaches publish": (
            text.replace(publish_if, "    needs: validate\n    if: github.event_name == 'workflow_dispatch' || github.event_name == 'schedule'\n", 1),
            "job publish must not run on schedule",
        ),
        "nightly reaches validate": (
            text.replace(validate_if, "    if: github.event_name == 'workflow_dispatch' || github.event_name == 'schedule'\n    runs-on", 1),
            "job validate must not run on schedule",
        ),
        "publish negates dispatch": (
            text.replace(publish_if, "    needs: validate\n    if: github.event_name != 'workflow_dispatch'\n", 1),
            "job publish must not negate workflow_dispatch",
        ),
        "publish runs always": (
            text.replace(publish_if, "    needs: validate\n    if: always()\n", 1),
            "job publish must not run on schedule or use always()",
        ),
        "nightly build without the repository guard": (
            text.replace(" && github.repository == 'VMAFx/pelorus'", "", 1),
            "build job must run only on pull_request or the schedule of VMAFx/pelorus",
        ),
        "build timeout removed": (
            replace_in_job(text, "build", "    timeout-minutes: 90\n", ""),
            "job build needs a numeric timeout-minutes",
        ),
        "build timeout too large": (
            replace_in_job(text, "build", "timeout-minutes: 90", "timeout-minutes: 360"),
            "job build timeout-minutes 360 exceeds its cap 90",
        ),
        "publish timeout too large": (
            replace_in_job(text, "publish", "timeout-minutes: 150", "timeout-minutes: 151"),
            "job publish timeout-minutes 151 exceeds its cap 150",
        ),
        "timeout from an expression": (
            replace_in_job(text, "validate", "timeout-minutes: 10", "timeout-minutes: ${{ vars.T }}"),
            "job validate needs a numeric timeout-minutes",
        ),
    }


def _tester_gate_cases(text: str) -> dict[str, tuple[str, str]]:
    return {
        "no licence record step": (
            replace_in_job(text, "publish", "name: Licence record is valid and its gate refuses planted defects", "name: Other"),
            "publish job is missing name: Licence record is valid",
        ),
        "no licence record gate on the image": (
            text.replace("name: Licence record gate on the built image", "name: Other", 1),
            "publish job is missing name: Licence record gate on the built image",
        ),
        "licence record self-test dropped": (
            text.replace("python3 -I -B tools/tester/licensing.py self-test", "true"),
            "publish job is missing python3 -I -B tools/tester/licensing.py self-test",
        ),
        "licence record gate on the image dropped": (
            text.replace('/opt/pelorus/tester/licensing.py check --kit "${KIT}" --root /', "/opt/pelorus/tester/licensing.py record"),
            'publish job is missing /opt/pelorus/tester/licensing.py check --kit "${KIT}" --root /',
        ),
        "no licence gate on the image": (
            text.replace("name: Licence gate on the built image", "name: Other", 1),
            "publish job is missing name: Licence gate on the built image",
        ),
        "no planted-flag self-test": (
            text.replace("bash tools/tester/check-ffmpeg-licence-self-test.sh", "true"),
            "publish job is missing bash tools/tester/check-ffmpeg-licence-self-test.sh",
        ),
        "planted nonfree flag": (
            text + "\n# --enable-nonfree\n",
            "forbidden workflow token --enable-nonfree",
        ),
        "planted cuda-nvcc flag": (
            text + "\n# --enable-cuda-nvcc\n",
            "forbidden workflow token --enable-cuda-nvcc",
        ),
    }


def _tester_build_kit_cases(text: str) -> dict[str, tuple[str, str]]:
    """ADR-0180: the pull-request build covers every kit and checks its no-device report."""
    return {
        "build job drops a kit": (
            replace_in_job(text, "build", "kit: [generic, nvidia, intel]", "kit: [generic, nvidia]"),
            "build job is missing kit: [generic, nvidia, intel]",
        ),
        "build job skips the no-device report": (
            replace_in_job(text, "build", '/report/report.json" --kit "${KIT}"', '/report/report.json"'),
            "build job is missing pelorus_tester_report.py validate",
        ),
        "build job builds one fixed kit": (
            replace_in_job(text, "build", '--target "final-${KIT}"', "--target final-generic"),
            'build job is missing --target "final-${KIT}"',
        ),
    }


def _tester_kit_cases(text: str) -> dict[str, tuple[str, str]]:
    """ADR-0180: every kit publishes, with its own tags, and one kit never cancels another."""
    return {
        "publish drops a kit": (
            replace_in_job(text, "publish", "kit: [generic, nvidia, intel]", "kit: [generic, intel]"),
            "publish job is missing         kit: [generic, nvidia, intel]",
        ),
        "publish is fail-fast": (
            replace_in_job(text, "publish", "fail-fast: false", "fail-fast: true"),
            "publish job is missing       fail-fast: false",
        ),
        "generic tag shape changed": (
            text.replace('tag="tester-${STAMP}"', 'tag="tester-generic-${STAMP}"', 1),
            'publish job is missing tag="tester-${STAMP}"',
        ),
        "vendor tag without the kit": (
            text.replace('tag="tester-${KIT}-${STAMP}"', 'tag="tester-${STAMP}-x"', 1),
            'publish job is missing tag="tester-${KIT}-${STAMP}"',
        ),
        "report not checked against the kit": (
            replace_in_job(text, "publish", '/report/report.json" --kit "${KIT}"', '/report/report.json"'),
            'publish job is missing pelorus_tester_report.py validate "${RUNNER_TEMP}/report/report.json" --kit',
        ),
        "licence record gate without the kit": (
            text.replace('licensing.py check --kit "${KIT}" --root /', "licensing.py check --kit generic --root /", 1),
            'publish job is missing /opt/pelorus/tester/licensing.py check --kit "${KIT}"',
        ),
        "-source of one fixed kit": (
            text.replace('--target "source-${KIT}"', "--target source-generic", 1),
            'publish job is missing --target "source-${KIT}"',
        ),
    }


def _tester_publish_cases(text: str) -> dict[str, tuple[str, str]]:
    sbom = "      - name: SBOM of the tester image (SPDX JSON)\n"
    sbom_path = "sbom-path: ${{ runner.temp }}/tester.spdx.json\n          push-to-registry: "
    return {
        "no -source image": (
            text.replace('--target "source-${KIT}"', '--target "final-${KIT}"', 1),
            'publish job is missing --target "source-${KIT}"',
        ),
        "SBOM not pushed to registry": (
            text.replace(sbom_path + "true", sbom_path + "false", 1),
            "must attest the image, its SBOM",
        ),
        "no SBOM": (
            text.replace("anchore/sbom-action@", "other/sbom-action@", 1),
            "publish job is missing uses: anchore/sbom-action@",
        ),
        "-source image unsigned": (
            text.replace('          cosign sign --yes "${IMAGE}@${SOURCE_DIGEST}"\n', "", 1),
            "must sign both",
        ),
        "no signature verification": (
            text.replace("cosign verify \\\n", "cosign inspect \\\n", 1),
            "publish job is missing cosign verify",
        ),
        "no attestation verification": (
            text.replace("gh attestation verify", "gh attestation download"),
            "publish job is missing gh attestation verify",
        ),
        "signing before the push": (
            text.replace(sbom, "      - run: cosign sign --yes x\n" + sbom, 1),
            "publish steps are out of order",
        ),
        "no anonymous pull check": (
            text.replace("name: The package is public", "name: Other", 1),
            "publish job is missing name: The package is public",
        ),
        "runner floats": (
            text.replace("ubuntu-26.04", "ubuntu-latest", 1),
            "forbidden workflow token ubuntu-latest",
        ),
    }


def _tester_workflow_cases(text: str) -> dict[str, tuple[str, str]]:
    return {
        **_tester_trigger_job_cases(text),
        **_tester_job_cases(text),
        **_tester_cost_cases(text),
        **_tester_nightly_cases(text),
        **_tester_gate_cases(text),
        **_tester_publish_cases(text),
        **_tester_kit_cases(text),
        **_tester_build_kit_cases(text),
    }


def _tester_containerfile_licence_cases(text: str) -> dict[str, tuple[str, str]]:
    """The licence flags, the gates and their order in every kit."""
    flags = '"--enable-gpl --enable-version3"'
    return {
        "nonfree default": (
            text.replace(flags, '"--enable-gpl --enable-version3 --enable-nonfree"', 1),
            "forbidden FFmpeg flag --enable-nonfree",
        ),
        "planted cuda-sdk": (text + "\n# --enable-cuda-sdk\n", "forbidden FFmpeg flag --enable-cuda-sdk"),
        "planted fdk-aac": (text + "\n# --enable-libfdk-aac\n", "forbidden FFmpeg flag --enable-libfdk-aac"),
        "planted decklink": (text + "\n# --enable-decklink\n", "forbidden FFmpeg flag --enable-decklink"),
        "LGPL default": (
            text.replace(flags, '"--enable-version3"', 1),
            "licence flags must default to",
        ),
        "gate scripts not copied": (
            text.replace("tools/tester/check-ffmpeg-licence-self-test.sh /opt/gate/", "/opt/gate/", 1),
            "stage build must copy build-ffmpeg.sh and the licence gate",
        ),
        "kit built without the gate script": (
            text.replace("RUN /opt/gate/build-ffmpeg.sh --enable-libvpl", "RUN /src/ffmpeg/configure --enable-libvpl", 1),
            "stage build-intel must build FFmpeg with /opt/gate/build-ffmpeg.sh",
        ),
        "licence self-test removed": (
            text.replace("licensing.py self-test", "licensing.py record", 1),
            "stage licence-generic must run licensing.py self-test",
        ),
        "licence check of another kit": (
            text.replace("licensing.py check --kit nvidia ", "licensing.py check --kit generic ", 1),
            "stage licence-nvidia must run licensing.py self-test, notices --kit nvidia and check --kit nvidia",
        ),
        "licence check before notices": (
            text.replace("licensing.py notices --kit nvidia", "licensing.py zzz --kit nvidia", 1)
            .replace("licensing.py check --kit nvidia", "licensing.py notices --kit nvidia", 1)
            .replace("licensing.py zzz --kit nvidia", "licensing.py check --kit nvidia", 1),
            "stage licence-nvidia must run licensing.py self-test",
        ),
        "final skips the licence stage": (
            text.replace("COPY --from=licence-nvidia ", "COPY --from=assembled-nvidia ", 1),
            "stage final-nvidia must copy from stage licence-nvidia",
        ),
        "licence stage without the commit": (
            text.replace("ARG FFMPEG_COMMIT\nARG PELORUS_COMMIT\nRUN python3", "ARG PELORUS_COMMIT\nRUN python3", 1),
            "stage licence-generic needs ARG FFMPEG_COMMIT",
        ),
        "floating base image": (
            re.sub(r"(ARG DEBIAN_IMAGE=\S+)@sha256:[0-9a-f]{64}", r"\1", text, count=1),
            "base image must be pinned by digest",
        ),
    }


def _tester_containerfile_stage_cases(text: str) -> dict[str, tuple[str, str]]:
    """ADR-0180: every kit has its stages, its kit name and its own FFmpeg tree in -source."""
    return {
        "no nvidia -source stage": (
            text.replace("AS source-nvidia", "AS other", 1),
            "missing stage AS source-nvidia",
        ),
        "no intel licence stage": (
            text.replace("AS licence-intel", "AS other", 1),
            "missing stage AS licence-intel",
        ),
        "final without its kit": (
            text.replace("PELORUS_TESTER_KIT=intel ", "PELORUS_TESTER_KIT=generic ", 1),
            "stage final-intel must set PELORUS_TESTER_KIT=intel",
        ),
        "-source without the kit's FFmpeg tree": (
            text.replace("COPY --from=build-intel /opt/source /source/ffmpeg", "COPY --from=build-generic /opt/source /source/ffmpeg", 1),
            "stage source-intel is missing COPY --from=build-intel /opt/source /source/ffmpeg",
        ),
    }


def _tester_containerfile_vendor_cases(text: str) -> dict[str, tuple[str, str]]:
    """ADR-0180: what each vendor kit needs, and no vendor driver or non-free package."""
    intel_apt = "        intel-media-va-driver-non-free libdav1d7"
    return {
        "nvidia without the graphics capability": (
            text.replace("NVIDIA_DRIVER_CAPABILITIES=compute,utility,video,graphics", "NVIDIA_DRIVER_CAPABILITIES=compute,utility,video", 1),
            "stage final-nvidia is missing NVIDIA_DRIVER_CAPABILITIES=compute,utility,video,graphics",
        ),
        "nvidia without libXext for the host ICD": (
            text.replace("libdav1d7 libegl1 libxext6", "libdav1d7 libegl1", 1),
            "stage assembled-nvidia is missing libxext6",
        ),
        "nvidia without the GLVND libEGL for the host ICD": (
            text.replace("libdav1d7 libegl1 libxext6", "libdav1d7 libxext6", 1),
            "stage assembled-nvidia is missing libegl1",
        ),
        "intel without libdrm picks the first render node": (
            text.replace("--enable-vaapi --enable-libdrm", "--enable-vaapi", 1),
            "stage build-intel is missing --enable-libvpl --enable-vaapi --enable-libdrm",
        ),
        "intel without an AV1 decoder for the steering check": (
            text.replace("--disable-xlib --enable-libdav1d", "--disable-xlib", 1),
            "stage build-intel is missing --enable-libdav1d",
        ),
        "nvidia without NVENC": (
            text.replace("--enable-ffnvcodec --enable-nvenc", "--enable-ffnvcodec", 1),
            "stage build-nvidia is missing --enable-ffnvcodec --enable-nvenc",
        ),
        "planted NVIDIA driver package": (
            text.replace(intel_apt, "        libnvidia-encode1 " + intel_apt.strip(), 1),
            "forbidden package libnvidia-encode1",
        ),
        "planted CUDA toolkit": (
            text.replace("libva-dev libvpl-dev", "libva-dev libvpl-dev nvidia-cuda-toolkit", 1),
            "forbidden package nvidia-cuda-toolkit",
        ),
        "non-free archive component": (
            text.replace("RUN sed -i 's/^Types: deb$/Types: deb deb-src/' /etc/apt/sources.list.d/debian.sources",
                         "RUN sed -i 's/^Components: main$/Components: main contrib/' /etc/apt/sources.list.d/debian.sources", 1),
            "a non-free or contrib archive component is enabled",
        ),
    }


def _tester_containerfile_cases(text: str) -> dict[str, tuple[str, str]]:
    return {
        **_tester_containerfile_licence_cases(text),
        **_tester_containerfile_stage_cases(text),
        **_tester_containerfile_vendor_cases(text),
        **_tester_nonfree_exception_cases(text),
    }


def _tester_nonfree_exception_cases(text: str) -> dict[str, tuple[str, str]]:
    """ADR-0180 decision 4a: the one non-free package stays in the Intel kit, and nothing else follows it."""
    nvidia_apt = "    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends libdav1d7 libegl1"
    return {
        "non-free media driver in the NVIDIA kit": (
            text.replace(nvidia_apt, nvidia_apt.replace("libdav1d7", TESTER_NONFREE_PACKAGE + " libdav1d7"), 1),
            f"forbidden package {TESTER_NONFREE_PACKAGE}",
        ),
        "non-free media driver in the build stage": (
            text.replace("libva-dev libvpl-dev", "libva-dev libvpl-dev " + TESTER_NONFREE_PACKAGE, 1),
            f"forbidden package {TESTER_NONFREE_PACKAGE}",
        ),
        "non-free area enabled in the generic kit": (
            text.replace("FROM base AS assembled-generic\nSHELL [\"/bin/bash\", \"-o\", \"pipefail\", \"-c\"]\n",
                         "FROM base AS assembled-generic\nSHELL [\"/bin/bash\", \"-o\", \"pipefail\", \"-c\"]\n"
                         f"RUN sed -i '{TESTER_NONFREE_AREA_SED}' /etc/apt/sources.list.d/debian.sources\n", 1),
            "a non-free or contrib archive component is enabled",
        ),
        "contrib area enabled in the Intel kit": (
            text.replace(TESTER_NONFREE_AREA_SED, "s/^Components: main$/Components: main non-free contrib/", 1),
            "a non-free or contrib archive component is enabled",
        ),
        "second non-free package in the Intel kit": (
            text.replace(TESTER_NONFREE_PACKAGE + " libdav1d7", TESTER_NONFREE_PACKAGE + " firmware-misc-nonfree libdav1d7", 1),
            "forbidden package firmware-misc-nonfree",
        ),
        "Intel kit without the non-free media driver": (
            text.replace(TESTER_NONFREE_PACKAGE + " libdav1d7", "intel-media-va-driver libdav1d7", 1),
            f"stage assembled-intel is missing {TESTER_NONFREE_PACKAGE}",
        ),
        "Intel -source without the non-free area": (
            text.replace(" -e '" + TESTER_NONFREE_AREA_SED + "'", "", 1),
            f"stage debian-sources-intel is missing {TESTER_NONFREE_AREA_SED}",
        ),
    }


def _tester_build_script_cases(text: str) -> dict[str, tuple[str, str]]:
    gate = "/opt/gate/check-ffmpeg-licence.sh /opt/ffmpeg/bin/ffmpeg\n"
    selftest = "/opt/gate/check-ffmpeg-licence-self-test.sh\n"
    return {
        "gate removed": (text.replace(gate, "true\n", 1), "the licence gate self-test must run, then the gate"),
        "gate self-test removed": (text.replace(selftest, "true\n", 1), "the licence gate self-test must run, then the gate"),
        "gate before its self-test": (
            text.replace(selftest, "SELFTEST\n", 1).replace(gate, selftest, 1).replace("SELFTEST\n", gate, 1),
            "the licence gate self-test must run, then the gate",
        ),
        "kit flag guard removed": (
            text.replace("is a licence decision, not a kit feature; refused", "accepted", 1),
            "build-ffmpeg.sh is missing is a licence decision",
        ),
        "planted nonfree flag": (text + "\n# --enable-nonfree\n", "forbidden FFmpeg flag --enable-nonfree"),
        "configure line not kept": (
            text.replace("/opt/source/ffmpeg-configure-line.txt", "/dev/null", 1),
            "build-ffmpeg.sh is missing /opt/source/ffmpeg-configure-line.txt",
        ),
    }


def _candidate_legs_failures() -> list[str]:
    release = WORKFLOWS[1].read_text(encoding="utf-8")
    legs = CANDIDATE_LEGS.read_text(encoding="utf-8")
    names = {p.name for p in (ROOT / ".github" / "workflows").glob("*.yml")}
    failures: list[str] = []
    if validate_candidate_legs_text(legs, release, names):
        failures.append("tester publish regression: the shipped candidate legs are rejected")
    cases = {
        "leg names a missing job": (legs.replace('"job": "ci"', '"job": "nope"', 1), release),
        "leg names a missing workflow": (legs.replace("tester-publish.yml", "gone.yml", 1), release),
        "needs leg the release build does not need": (
            legs,
            release.replace("    needs: ci\n", "    needs: []\n", 1),
        ),
        "legs file is not JSON": ("{", release),
    }
    for name, (legs_text, release_text) in cases.items():
        if release_text == release and legs_text == legs and "need" not in name:
            failures.append(f"tester publish regression: {name} mutation changed nothing")
        elif not validate_candidate_legs_text(legs_text, release_text, names):
            failures.append(f"tester publish regression: {name} was accepted")
    return failures


def tester_publish_regressions() -> list[str]:
    """Prove each ADR-0173/0176 publish rule rejects its planted defect."""
    failures: list[str] = []
    workflow = TESTER_PUBLISH.read_text(encoding="utf-8")
    wf_rel = TESTER_PUBLISH.relative_to(ROOT).as_posix()
    container = TESTER_CONTAINERFILE.read_text(encoding="utf-8")
    cf_rel = TESTER_CONTAINERFILE.relative_to(ROOT).as_posix()
    script = TESTER_BUILD_SCRIPT.read_text(encoding="utf-8")
    sc_rel = TESTER_BUILD_SCRIPT.relative_to(ROOT).as_posix()
    if validate_tester_publish_text(wf_rel, workflow):
        failures.append("tester publish regression: the current workflow is rejected")
    if validate_tester_containerfile_text(cf_rel, container):
        failures.append("tester publish regression: the current Containerfile is rejected")
    if validate_tester_build_script_text(sc_rel, script):
        failures.append("tester publish regression: the current build-ffmpeg.sh is rejected")
    for name, (mutated, expected) in _tester_build_script_cases(script).items():
        failures.extend(
            _all_rejected(name, mutated, script, lambda t: validate_tester_build_script_text(sc_rel, t), expected)
        )
    for name, (mutated, expected) in _tester_workflow_cases(workflow).items():
        failures.extend(
            _all_rejected(name, mutated, workflow, lambda t: validate_tester_publish_text(wf_rel, t), expected)
        )
    for name, (mutated, expected) in _tester_containerfile_cases(container).items():
        failures.extend(
            _all_rejected(name, mutated, container, lambda t: validate_tester_containerfile_text(cf_rel, t), expected)
        )
    return failures + _candidate_legs_failures() + rc_release_regressions()


def _rc_build_cases(build: str) -> dict[str, tuple[str, str]]:
    return {
        "no tag shape step": (
            build.replace("name: Release tag shape and rc numbering", "name: Other", 1),
            "release build is missing the tag shape and rc numbering step",
        ),
        "shape step does not read existing tags": (
            build.replace("git ls-remote --tags --refs origin", "git tag --list", 1),
            "tag shape step is missing git ls-remote",
        ),
        "shape step accepts an empty tag list": (
            build.replace('          test -s "$RUNNER_TEMP/tags.txt"\n', "", 1),
            "tag shape step is missing test -s",
        ),
        "no version file check": (
            build.replace("verify-release.py version", "verify-release.py other", 1),
            "tag==version step is missing python3 -I scripts/release/verify-release.py version",
        ),
        "no candidate legs step": (
            build.replace("name: Candidate legs are green on this commit", "name: Other", 1),
            "release build is missing the candidate legs step",
        ),
        "legs step for every tag": (
            build.replace(" && steps.tag.outputs.prerelease == 'true'", "", 1),
            "candidate legs step is missing steps.tag.outputs.prerelease == 'true'",
        ),
        "legs checked against another commit": (
            build.replace('--sha "$GITHUB_SHA"', '--sha "$GITHUB_REF"', 1),
            'candidate legs step is missing --sha "$GITHUB_SHA"',
        ),
        "no prerelease output": (
            build.replace("prerelease: ${{ steps.tag.outputs.prerelease }}", "", 1),
            "release build is missing prerelease: ${{ steps.tag.outputs.prerelease }}",
        ),
    }


def _rc_release_cases(release: str) -> dict[str, tuple[str, str]]:
    return {
        "rc published as a normal release": (
            release.replace("true) kind=(--prerelease --latest=false) ;;", "true) kind=() ;;", 1),
            "publish job is missing true) kind=(--prerelease --latest=false) ;;",
        ),
        "kind flags not passed": (
            release.replace('"${kind[@]}"', "", 1),
            'publish job is missing "${kind[@]}"',
        ),
        "unclassified tag treated as final": (
            release.replace('*) echo "::error::release build did not classify', '*) echo "::notice::', 1),
            "publish job is missing *) echo",
        ),
        "no flag read-back": (
            release.replace("--json isPrerelease", "--json url", 1),
            "publish job is missing --json isPrerelease",
        ),
        "prerelease output not consumed": (
            release.replace("PRERELEASE: ${{ needs.build.outputs.prerelease }}", "PRERELEASE: ''", 1),
            "publish job is missing PRERELEASE:",
        ),
    }


def rc_release_regressions() -> list[str]:
    """Prove the ADR-0176 rc rules of release.yml and release-build.yml are pinned."""
    failures: list[str] = []
    build_path, release_path = WORKFLOWS[2], WORKFLOWS[1]
    build = build_path.read_text(encoding="utf-8")
    release = release_path.read_text(encoding="utf-8")
    build_rel = build_path.relative_to(ROOT).as_posix()
    release_rel = release_path.relative_to(ROOT).as_posix()
    build_cases = _rc_build_cases(build)
    release_cases = _rc_release_cases(release)
    for name, (mutated, expected) in build_cases.items():
        failures.extend(
            _all_rejected(name, mutated, build, lambda t: validate_workflow_text(build_rel, t), expected)
        )
    for name, (mutated, expected) in release_cases.items():
        failures.extend(
            _all_rejected(name, mutated, release, lambda t: validate_workflow_text(release_rel, t), expected)
        )
    return failures


def _docs_pin_shape_cases(source: str) -> dict:
    """Shape mutations of the docs job's setup-go and actionlint pins (ADR-0170)."""
    return {
        "floating setup-go tag": (
            re.sub(r"actions/setup-go@[0-9a-f]{40} # v\d+\.\d+\.\d+", "actions/setup-go@v7", source, count=1),
            "docs job must pin actions/setup-go once by full commit digest",
        ),
        "setup-go pin without release comment": (
            re.sub(r"(actions/setup-go@[0-9a-f]{40}) # v\d+\.\d+\.\d+", r"\1", source, count=1),
            "docs job must pin actions/setup-go once by full commit digest",
        ),
        "short setup-go digest": (
            re.sub(r"actions/setup-go@[0-9a-f]{40}", "actions/setup-go@b7ad1da", source, count=1),
            "docs job must pin actions/setup-go once by full commit digest",
        ),
        "second setup-go step": (
            re.sub(
                r"(\n( +)- name: Validate workflow syntax)",
                r"\n\2- uses: actions/setup-go@v7\1",
                source,
                count=1,
            ),
            "docs job must pin actions/setup-go once by full commit digest",
        ),
        "actionlint @latest": (
            re.sub(r"(actionlint/cmd/actionlint)@v\d+\.\d+\.\d+", r"\1@latest", source, count=1),
            "docs job must run actionlint once as",
        ),
        "actionlint branch ref": (
            re.sub(r"(actionlint/cmd/actionlint)@v\d+\.\d+\.\d+", r"\1@main", source, count=1),
            "docs job must run actionlint once as",
        ),
        "actionlint without version": (
            re.sub(r"(actionlint/cmd/actionlint)@v\d+\.\d+\.\d+", r"\1", source, count=1),
            "docs job must run actionlint once as",
        ),
    }


def lavapipe_workflow_cases(source: str) -> dict[str, tuple[str, str]]:
    """Mutations of the #228 lavapipe lane that the validator must reject."""
    lane = workflow_job_blocks(source).get(LAVAPIPE_JOB, "")

    def mutate(old: str, new: str) -> str:
        return replace_in_job(source, LAVAPIPE_JOB, old, new)

    pin = re.search(r"actions/checkout@[0-9a-f]{40}", lane)
    return {
        "missing lavapipe job": (
            source.replace(lane, "", 1) if lane else source,
            f"missing lavapipe job {LAVAPIPE_JOB}",
        ),
        "lavapipe lane without draft stop": (
            mutate(LAVAPIPE_DRAFT_STEP, "- name: Something else"),
            "must start with the draft stop step",
        ),
        "lavapipe step without draft guard": (
            mutate(f"        {LAVAPIPE_DRAFT_GUARD}\n", "", ),
            "must guard every step after the draft stop",
        ),
        "lavapipe unbounded timeout": (
            mutate("timeout-minutes: 45", "timeout-minutes: 90"),
            "must set timeout-minutes of at most",
        ),
        "lavapipe unpinned action": (
            mutate(pin.group(0), "actions/checkout@v7") if pin else source,
            "must pin actions/checkout@v7 by full commit digest",
        ),
    }


def lavapipe_step_cases(source: str) -> dict[str, tuple[str, str]]:
    """Mutations of the lavapipe lane's packages and guard steps."""

    def mutate(old: str, new: str) -> str:
        return replace_in_job(source, LAVAPIPE_JOB, old, new)

    return {
        "lavapipe without lavapipe driver": (
            mutate("mesa-vulkan-drivers", "mesa-drivers-removed"),
            "must install mesa-vulkan-drivers",
        ),
        "lavapipe without validation layers": (
            mutate("vulkan-validationlayers", "layers-removed"),
            "must install vulkan-validationlayers",
        ),
        "lavapipe without validation": (
            mutate('echo "PELORUS_VALIDATE=1"', 'echo "PELORUS_VALIDATE=0"'),
            "must not contain PELORUS_VALIDATE=0",
        ),
        "lavapipe device guard dropped": (
            mutate("ffmpeg-patches/test/vulkan-lavapipe-guard.sh\n", "true\n"),
            "is missing ffmpeg-patches/test/vulkan-lavapipe-guard.sh",
        ),
        "lavapipe guard red-proof dropped": (
            mutate("vulkan-lavapipe-guard.sh --self-test --disable", "vulkan-lavapipe-guard.sh --self-test"),
            "is missing ffmpeg-patches/test/vulkan-lavapipe-guard.sh --self-test --disable",
        ),
        "lavapipe ten-filter run dropped": (
            mutate("ffmpeg-patches/test/vulkan-lavapipe-filters.sh\n", "true\n"),
            "is missing ffmpeg-patches/test/vulkan-lavapipe-filters.sh",
        ),
        "lavapipe format matrix dropped": (
            mutate("ffmpeg-patches/test/vulkan-format-matrix.sh\n", "true\n"),
            "is missing ffmpeg-patches/test/vulkan-format-matrix.sh",
        ),
        "lavapipe report guard dropped": (
            mutate("ffmpeg-patches/test/vulkan-lavapipe-report.sh\n", "true\n"),
            "is missing ffmpeg-patches/test/vulkan-lavapipe-report.sh",
        ),
        "lavapipe failure ignored": (
            mutate("vulkan-lavapipe-filters.sh\n", "vulkan-lavapipe-filters.sh || true\n"),
            "must not contain || true",
        ),
        "lavapipe continue-on-error": (
            mutate("    timeout-minutes: 45\n", "    timeout-minutes: 45\n    continue-on-error: true\n"),
            "must not contain continue-on-error",
        ),
        "lavapipe binary not kept": (
            mutate("KEEP_DIR=", "KEEP_DIR_REMOVED="),
            "is missing KEEP_DIR=",
        ),
    }



def workflow_validator_regressions() -> list[str]:
    """Prove runner and native-toolchain regressions are rejected."""
    failures: list[str] = []
    ci_path = WORKFLOWS[0]
    source = ci_path.read_text(encoding="utf-8")
    cases = {
        **windows_workflow_cases(source),
        **lavapipe_workflow_cases(source),
        **lavapipe_step_cases(source),
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
    cases.update(_docs_pin_shape_cases(source))
    relative = ci_path.relative_to(ROOT).as_posix()
    failures.extend(windows_pin_bump_regression(source, relative))
    for name, (mutated, expected) in cases.items():
        if mutated == source:
            failures.append(f"workflow regression: {name} mutation changed nothing")
            continue
        errors = validate_workflow_text(relative, mutated)
        if not any(expected in error for error in errors):
            failures.append(f"workflow regression: {name} was accepted")

    failures.extend(release_gate_regressions(source, relative))
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


def _validate_go_manager_text(text: str, checker_source: str) -> list[str]:
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


def _validate_actionlint_manager(managers: object, ci_source: str) -> list[str]:
    """Require one ci.yml regex manager that bumps the actionlint tag (ADR-0170)."""
    prefix = f"renovate.json: {CI_RELATIVE} manager"
    try:
        covering = [
            manager
            for manager in (managers if isinstance(managers, list) else [])
            if isinstance(manager, dict)
            and manager.get("customType") == "regex"
            and isinstance(manager.get("managerFilePatterns"), list)
            and any(
                renovate_file_pattern_matches(pattern, CI_RELATIVE)
                for pattern in manager["managerFilePatterns"]
            )
        ]
    except RenovatePatternError as exc:
        return [f"renovate.json: {exc}"]
    if len(covering) != 1:
        return [
            (
                f"renovate.json: expected exactly one regex customManager for "
                f"{CI_RELATIVE} (found {len(covering)})"
            )
        ]
    manager = covering[0]
    errors = [
        f"{prefix} needs {key} {value!r}"
        for key, value in ACTIONLINT_RENOVATE_TEMPLATES.items()
        if manager.get(key) != value
    ]
    pinned = ACTIONLINT_RUN.search(ci_source)
    match_strings = manager.get("matchStrings")
    if pinned is None:
        return errors + [f"{prefix}: ci.yml has no exact-tag actionlint run line"]
    if not isinstance(match_strings, list) or len(match_strings) != 1:
        return errors + [f"{prefix} needs one matchString"]
    try:
        matches = list(re.finditer(python_regex(str(match_strings[0])), ci_source))
    except re.error as exc:
        return errors + [f"{prefix} matchString: {exc}"]
    if len(matches) != 1 or matches[0].groupdict().get("currentValue") != pinned["tag"]:
        errors.append(
            f"{prefix} matchString must capture exactly the actionlint tag "
            f"{pinned['tag']} as currentValue (found {len(matches)} matches)"
        )
    return errors


def validate_renovate_text(text: str, checker_source: str, ci_source: str) -> list[str]:
    """Validate the Go-mirroring manager (ADR-0151/0152) and the actionlint manager (ADR-0170)."""
    errors = _validate_go_manager_text(text, checker_source)
    try:
        config = json.loads(text)
    except json.JSONDecodeError:
        return errors
    managers = config.get("customManagers") if isinstance(config, dict) else None
    return errors + _validate_actionlint_manager(managers, ci_source)


def validate_renovate() -> list[str]:
    """Validate the checked-in Renovate config against this checker."""
    if not RENOVATE_CONFIG.is_file():
        return ["renovate.json: missing"]
    checker = Path(__file__).resolve()
    return validate_renovate_text(
        RENOVATE_CONFIG.read_text(encoding="utf-8"),
        checker.read_text(encoding="utf-8"),
        (ROOT / CI_RELATIVE).read_text(encoding="utf-8"),
    )


def _managers_matching_checker(manager: dict) -> bool:
    """Whether one renovate customManager targets the checker file."""
    return any(
        renovate_file_pattern_matches(pattern, CHECKER_RELATIVE)
        for pattern in manager.get("managerFilePatterns", [])
    )


def _without_checker_manager(value: dict) -> dict:
    value["customManagers"] = [
        manager for manager in value["customManagers"] if not _managers_matching_checker(manager)
    ]
    return value


def _mutate_checker_manager(key: str, replacement: object):
    def mutate(value: dict) -> dict:
        for manager in value["customManagers"]:
            if _managers_matching_checker(manager):
                manager[key] = replacement
        return value

    return mutate


def _ci_manager(manager: dict) -> bool:
    return any(
        renovate_file_pattern_matches(pattern, CI_RELATIVE)
        for pattern in manager.get("managerFilePatterns", [])
    )


def _mutate_ci_manager(key: str, replacement: object):
    def mutate(value: dict) -> dict:
        for manager in value["customManagers"]:
            if _ci_manager(manager):
                manager[key] = replacement
        return value

    return mutate


def _without_ci_manager(value: dict) -> dict:
    value["customManagers"] = [m for m in value["customManagers"] if not _ci_manager(m)]
    return value


def _ci_manager_also_targets_checker(value: dict) -> dict:
    for manager in value["customManagers"]:
        if _ci_manager(manager):
            manager["managerFilePatterns"].append("/^scripts/check-build-config\\.py$/")
    return value


def _with_extra_manager(patterns: list[str]):
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


def _actionlint_renovate_cases() -> dict:
    """Broken-manager mutations for the ci.yml actionlint manager (ADR-0170)."""
    return {
        "missing actionlint manager": (
            _without_ci_manager,
            f"expected exactly one regex customManager for {CI_RELATIVE}",
        ),
        "second ci.yml manager": (
            _with_extra_manager(["/^\\.github/workflows/ci\\.yml$/"]),
            f"expected exactly one regex customManager for {CI_RELATIVE}",
        ),
        "actionlint manager also targets the checker": (
            _ci_manager_also_targets_checker,
            f"expected exactly one regex customManager for {CHECKER_RELATIVE}",
        ),
        "actionlint github-tags datasource": (
            _mutate_ci_manager("datasourceTemplate", "github-tags"),
            "needs datasourceTemplate 'go'",
        ),
        "actionlint different depName": (
            _mutate_ci_manager("depNameTemplate", "rhysd/actionlint"),
            f"needs depNameTemplate {ACTIONLINT_MODULE!r}",
        ),
        "actionlint floating matchString": (
            _mutate_ci_manager(
                "matchStrings", ["actionlint@(?<currentValue>[a-z]+)"]
            ),
            "matchString must capture exactly the actionlint tag",
        ),
    }


def _renovate_regression_cases() -> dict:
    """Each broken-manager mutation with the diagnostic the validator must give."""
    cases = {
        "second manager via /regex/i": (
            _with_extra_manager(["/SCRIPTS/CHECK-BUILD-CONFIG\\.PY$/i"]),
            "expected exactly one regex customManager",
        ),
        "second manager via glob": (
            _with_extra_manager(["**/*.py"]),
            "expected exactly one regex customManager",
        ),
        "unevaluable file pattern": (
            _with_extra_manager(["scripts/[a-z]*.py"]),
            "is not evaluated by this checker",
        ),
        "missing Go manager": (
            _without_checker_manager,
            "expected exactly one regex customManager",
        ),
        "golang-version datasource": (
            _mutate_checker_manager("datasourceTemplate", "golang-version"),
            "needs datasourceTemplate 'github-releases'",
        ),
        "different depName": (
            _mutate_checker_manager("depNameTemplate", "golang"),
            "needs depNameTemplate 'go'",
        ),
        "semver versioning": (
            _mutate_checker_manager("versioningTemplate", "semver"),
            "needs versioningTemplate 'npm'",
        ),
        "matchString misses literal": (
            _mutate_checker_manager(
                "matchStrings", ['GO_VERSION = "(?<currentValue>\\d+\\.\\d+)"']
            ),
            "matchString must capture exactly",
        ),
    }
    return cases | _actionlint_renovate_cases()


def _renovate_case_failures(config: dict, checker: str, ci: str) -> list[str]:
    """Run every mutation case and report the ones the validator accepted."""
    failures: list[str] = []
    for name, (mutate, expected) in _renovate_regression_cases().items():
        try:
            mutated = json.dumps(mutate(json.loads(json.dumps(config))))
        except RenovatePatternError as exc:
            failures.append(f"renovate regression: {name} not run: {exc}")
            continue
        if json.loads(mutated) == config:
            failures.append(f"renovate regression: {name} mutation changed nothing")
            continue
        errors = validate_renovate_text(mutated, checker, ci)
        if not any(expected in error for error in errors):
            failures.append(f"renovate regression: {name} was accepted")
    return failures


def renovate_validator_regressions() -> list[str]:
    """Prove that removing or breaking the mirroring Go manager fails (ADR-0152)."""
    source = RENOVATE_CONFIG.read_text(encoding="utf-8")
    checker = Path(__file__).resolve().read_text(encoding="utf-8")
    ci = (ROOT / CI_RELATIVE).read_text(encoding="utf-8")
    failures = _renovate_case_failures(json.loads(source), checker, ci)
    stale = checker.replace(
        f'ACTIONLINT_GO_VERSION = "{ACTIONLINT_GO_VERSION}"',
        'ACTIONLINT_GO_VERSION = "go1.0"',
        1,
    )
    if stale == checker or not validate_renovate_text(source, stale, ci):
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


# Variables that make Git pick a repository instead of discovering one from the working
# directory. Git sets them for every hook it runs; a fixture that inherited them would run
# its `git init`, `git branch` and `git commit` against the repository being pushed.
GIT_REPOSITORY_ENV = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_PREFIX",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_NAMESPACE",
)


def scrub_git_repository_env() -> None:
    """Drop the repository-selecting Git variables so fixtures stay in their temp dirs."""
    for key in GIT_REPOSITORY_ENV:
        os.environ.pop(key, None)


def git_repository_env_regression() -> list[str]:
    """Prove a hook's GIT_DIR cannot reach a fixture command."""
    saved = {key: os.environ.get(key) for key in GIT_REPOSITORY_ENV}
    os.environ["GIT_DIR"] = "/definitely/not/a/repository"
    os.environ["GIT_WORK_TREE"] = "/definitely/not/a/worktree"
    try:
        scrub_git_repository_env()
        leaked = [key for key in GIT_REPOSITORY_ENV if key in os.environ]
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    if leaked:
        return [f"fixture regression: {', '.join(leaked)} survived the Git env scrub"]
    return []


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


# ADR-0179 (#256): the pelorus-dev image starts from plain Ubuntu, records every
# file it holds, and runs the licence gate in a stage the final stage depends on.
DEV_IMAGE_CONTAINERFILE = ROOT / ".devcontainer" / "base" / "Containerfile"
DEV_IMAGE_RECORD = ROOT / ".devcontainer" / "base" / "licensing.json"
DEV_IMAGE_WORKFLOW = ROOT / ".github" / "workflows" / "devcontainer-image.yml"
DEV_IMAGE_NOTICES_LABEL = "dev.vmafx.pelorus.notices"
DEV_IMAGE_FROM = re.compile(r"^FROM ubuntu:26\.04@sha256:[0-9a-f]{64} AS assembled$", re.MULTILINE)
DEV_IMAGE_GATE = ("licensing.py self-test", "licensing.py notices", "licensing.py check")
DEV_IMAGE_WORKFLOW_TOKENS = (
    "--build-arg PELORUS_COMMIT=\"$GITHUB_SHA\"",
    "--file .devcontainer/base/Containerfile",
    "'tools/tester/licensing.py'",
    "'LICENSES/EUPL-1.2.txt'",
)


def _dev_image_stages(relative: str, text: str) -> list[str]:
    errors: list[str] = []
    start = text.find("FROM assembled AS licence")
    final = text.find("FROM assembled AS final")
    if start < 0 or final < start:
        return [f"{relative}: stage licence must come before stage final, both starting from assembled"]
    positions = [text.find(token, start, final) for token in DEV_IMAGE_GATE]
    if min(positions) < 0 or positions != sorted(positions):
        errors.append(f"{relative}: stage licence must run licensing.py self-test, notices and check in that order")
    if "ARG PELORUS_COMMIT" not in text[start:final]:
        errors.append(f"{relative}: stage licence needs ARG PELORUS_COMMIT")
    if "COPY --from=licence " not in text[final:]:
        errors.append(f"{relative}: stage final must copy from stage licence, so the gate cannot be skipped")
    return errors


def validate_dev_image_text(relative: str, text: str, record: dict) -> list[str]:
    """The Containerfile builds on plain Ubuntu behind the licence gate and agrees with its record."""
    errors: list[str] = []
    if not DEV_IMAGE_FROM.search(text):
        errors.append(f"{relative}: must start FROM a digest-pinned ubuntu:26.04 AS assembled, not a vendor base image")
    if "image.licenses=" in text:
        errors.append(f"{relative}: must not set org.opencontainers.image.licenses; the notices file names the licences")
    notices = "/" + str(record.get("notices_path", ""))
    if f'{DEV_IMAGE_NOTICES_LABEL}="{notices}"' not in text:
        errors.append(f"{relative}: label {DEV_IMAGE_NOTICES_LABEL} must name the record's notices file {notices}")
    for component in record.get("components", []):
        for licence_file in component.get("licence_files", []):
            directory = "/" + licence_file.rsplit("/", 1)[0]
            if directory not in text and not licence_file.startswith("usr/share/common-licenses/"):
                errors.append(f"{relative}: nothing installs {directory} for component {component.get('id')}")
    errors.extend(_dev_image_stages(relative, text))
    return errors


def validate_dev_image_workflow_text(relative: str, text: str) -> list[str]:
    errors = [f"{relative}: missing {token}" for token in DEV_IMAGE_WORKFLOW_TOKENS if token not in text]
    if text.count("--build-arg PELORUS_COMMIT=") != 2:
        errors.append(f"{relative}: both docker build steps must pass --build-arg PELORUS_COMMIT")
    if ".devcontainer/base\n" in text.replace(" ", "").replace("\\\n", ""):
        errors.append(f"{relative}: the build context must be the repository root, which holds the licence gate")
    return errors


def validate_dev_image() -> list[str]:
    errors: list[str] = []
    try:
        record = json.loads(DEV_IMAGE_RECORD.read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        return [f"{DEV_IMAGE_RECORD.relative_to(ROOT).as_posix()}: {err}"]
    for path, check in (
        (DEV_IMAGE_CONTAINERFILE, lambda rel, text: validate_dev_image_text(rel, text, record)),
        (DEV_IMAGE_WORKFLOW, validate_dev_image_workflow_text),
    ):
        relative = path.relative_to(ROOT).as_posix()
        if not path.is_file():
            errors.append(f"{relative}: missing")
            continue
        errors.extend(check(relative, path.read_text(encoding="utf-8")))
    return errors


def _dev_image_cases(text: str) -> dict[str, tuple[str, str]]:
    first = DEV_IMAGE_FROM.search(text)
    base = first.group(0) if first else ""
    return {
        "vendor base image": (
            text.replace(base, "FROM mcr.microsoft.com/devcontainers/base:ubuntu26.04@sha256:" + "0" * 64 + " AS assembled", 1),
            "must start FROM a digest-pinned ubuntu:26.04",
        ),
        "tag-only base": (text.replace("@sha256:", "@sha256x:", 1), "must start FROM a digest-pinned ubuntu:26.04"),
        "licence label back": (
            text + '\nLABEL org.opencontainers.image.licenses="EUPL-1.2"\n',
            "must not set org.opencontainers.image.licenses",
        ),
        "notices label dropped": (text.replace(DEV_IMAGE_NOTICES_LABEL, "dev.example.notices", 1), "must name the record's notices file"),
        "gate check removed": (text.replace("licensing.py check --root", "licensing.py record --root", 1), "self-test, notices and check in that order"),
        "gate order swapped": (
            text.replace("licensing.py notices --root", "licensing.py zzz", 1).replace("licensing.py check --root", "licensing.py notices --root", 1)
            .replace("licensing.py zzz", "licensing.py check --root", 1),
            "self-test, notices and check in that order",
        ),
        "final stage skips the gate": (text.replace("COPY --from=licence ", "COPY --from=assembled ", 1), "must copy from stage licence"),
        "node licence not installed": (text.replace("/usr/local/share/licenses/node", "/opt/n", -1), "nothing installs /usr/local/share/licenses/node"),
        "lefthook licence not installed": (
            text.replace("/usr/local/share/licenses/lefthook", "/opt/l", -1),
            "nothing installs /usr/local/share/licenses/lefthook",
        ),
    }


def _dev_image_workflow_cases(text: str) -> dict[str, tuple[str, str]]:
    return {
        "no commit argument": (text.replace("--build-arg PELORUS_COMMIT=", "--build-arg OTHER=", 1), "both docker build steps must pass"),
        "gate not a trigger": (text.replace("      - 'tools/tester/licensing.py'\n", "", -1), "missing 'tools/tester/licensing.py'"),
        "context without the gate": (
            text.replace("--file .devcontainer/base/Containerfile \\\n            .\n", "--file .devcontainer/base/Containerfile \\\n            .devcontainer/base\n", 1),
            "the build context must be the repository root",
        ),
    }


def _dev_image_regressions_for(name: str, original: str, cases: dict[str, tuple[str, str]], validate) -> list[str]:
    failures: list[str] = []
    for case, (mutated, expected) in cases.items():
        if mutated == original:
            failures.append(f"dev image regression: {name} {case} mutation changed nothing")
        elif not any(expected in error for error in validate(mutated)):
            failures.append(f"dev image regression: {name} {case} was accepted")
    return failures


def dev_image_regressions() -> list[str]:
    """Prove each ADR-0179 rule rejects its planted defect."""
    record = json.loads(DEV_IMAGE_RECORD.read_text(encoding="utf-8"))
    container = DEV_IMAGE_CONTAINERFILE.read_text(encoding="utf-8")
    workflow = DEV_IMAGE_WORKFLOW.read_text(encoding="utf-8")
    failures: list[str] = []
    if validate_dev_image_text("Containerfile", container, record):
        failures.append("dev image regression: the current Containerfile is rejected")
    if validate_dev_image_workflow_text("workflow", workflow):
        failures.append("dev image regression: the current workflow is rejected")
    failures += _dev_image_regressions_for(
        "Containerfile", container, _dev_image_cases(container), lambda t: validate_dev_image_text("Containerfile", t, record)
    )
    failures += _dev_image_regressions_for(
        "workflow", workflow, _dev_image_workflow_cases(workflow), lambda t: validate_dev_image_workflow_text("workflow", t)
    )
    return failures


def main() -> int:
    scrub_git_repository_env()
    if not CONFIG.is_file():
        print("build-config.env: missing", file=sys.stderr)
        return 1

    config_text = CONFIG.read_text(encoding="utf-8")
    values, parse_errors = parse_assignments(config_text)
    errors = validate_config(config_text)
    errors.extend(validate_consumers())
    errors.extend(validate_renovate())
    errors.extend(validate_tester_publish())
    errors.extend(validate_dev_image())
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
        errors.extend(git_repository_env_regression())
        errors.extend(git_fixture_policy_regression())
        errors.extend(git_tag_ref_regression())
        errors.extend(git_dirty_worktree_cleanup_regression())
        errors.extend(git_worktree_hook_regression())
        errors.extend(git_am_hook_regression())
        errors.extend(git_smudge_cleanup_regression())
        errors.extend(git_format_config_regression())
        errors.extend(workflow_validator_regressions())
        errors.extend(tester_publish_regressions())
        errors.extend(dev_image_regressions())
        errors.extend(renovate_pattern_regressions())
        errors.extend(renovate_validator_regressions())

    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
