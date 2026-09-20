#!/usr/bin/env python3
"""Validate Pelorus's machine-maintained build dependency contract."""

from __future__ import annotations

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
WORKFLOWS = (
    ROOT / ".github" / "workflows" / "ci.yml",
    ROOT / ".github" / "workflows" / "release.yml",
)
SETUP_GO_COMMIT = "b7ad1dad31e06c5925ef5d2fc7ad053ef454303e"
RELEASE_TAG = re.compile(r"(?<![A-Za-z0-9_])n\d+\.\d+\.\d+(?![A-Za-z0-9_])")
FORMAT_PATCH_CONFIG = (
    "format.mboxrd=false",
    "format.pretty=medium",
    "format.encodeEmailHeaders=true",
    "diff.orderFile=/dev/null",
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

    hookful_worktree = source.replace(
        'git -C "$FFMPEG_REPO" -c core.hooksPath=/dev/null \\\n'
        '    worktree add --no-checkout --detach "$WORKTREE" "$FFMPEG_COMMIT"',
        'git -C "$FFMPEG_REPO" \\\n'
        '    worktree add --no-checkout --detach "$WORKTREE" "$FFMPEG_COMMIT"',
        1,
    )
    if hookful_worktree == source:
        failures.append("consumer regression: hookful worktree mutation changed nothing")
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
        'git -C "$WORKTREE" \\\n'
        '    checkout --force --detach "$FFMPEG_COMMIT"',
        1,
    )
    if hookful_checkout == source:
        failures.append("consumer regression: hookful checkout mutation changed nothing")
    elif not any(
        "deferred checkout must disable Git hooks" in error
        for error in validate_consumer_text(relative, hookful_checkout)
    ):
        failures.append("consumer regression: hookful deferred checkout was accepted")

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

    replay_relative = "ffmpeg-patches/test/build-and-run.sh"
    replay = CONSUMERS[1].read_text(encoding="utf-8")
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
    hookful_am = replay.replace(
        'git -C "$WORKTREE" -c core.hooksPath=/dev/null \\\n'
        '            am --3way',
        'git -C "$WORKTREE" \\\n            am --3way',
        1,
    )
    if hookful_am == replay:
        failures.append("consumer regression: hookful git-am mutation changed nothing")
    elif not any(
        "git am must disable Git hooks" in error
        for error in validate_replay_text(hookful_am)
    ):
        failures.append("consumer regression: hookful git am was accepted")
    return failures


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
        except (OSError, subprocess.SubprocessError) as error:
            return [f"worktree cleanup fixture probe failed: {error}"]

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
        ordinary_list = subprocess.run(
            ("git", "-C", str(repo), "worktree", "list", "--porcelain"),
            check=False,
            capture_output=True,
            text=True,
        )
        ordinary_cleanup = subprocess.run(
            (
                "git",
                "-C",
                str(repo),
                "-c",
                "core.hooksPath=/dev/null",
                "worktree",
                "remove",
                "--force",
                str(ordinary),
            ),
            check=False,
            capture_output=True,
            text=True,
        )
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
        hardened_cleanup = subprocess.run(
            (
                "git",
                "-C",
                str(repo),
                "-c",
                "core.hooksPath=/dev/null",
                "worktree",
                "remove",
                "--force",
                str(hardened),
            ),
            check=False,
            capture_output=True,
            text=True,
        )
        final_list = subprocess.run(
            ("git", "-C", str(repo), "worktree", "list", "--porcelain"),
            check=False,
            capture_output=True,
            text=True,
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


def git_am_hook_regression() -> list[str]:
    """Prove applypatch hooks cannot block or mutate deterministic replay."""
    with tempfile.TemporaryDirectory(prefix="pelorus-am-hooks-") as temp_dir:
        root = Path(temp_dir)
        repo = root / "repo"
        ordinary = root / "ordinary"
        hardened = root / "hardened"
        hooks = root / "hooks"
        hooks.mkdir()

        errors = run_fixture_commands(
            "git am hook fixture setup",
            (("git", "init", "-q", str(repo)),),
        )
        if errors:
            return errors
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
            return errors
        sample.write_text("base\npatched\n", encoding="utf-8")
        errors = run_fixture_commands(
            "git am hook fixture patch",
            (
                ("git", "-C", str(repo), "add", "sample.txt"),
                fixture_commit(str(repo), "patch"),
            ),
        )
        if errors:
            return errors
        patch_result = subprocess.run(
            ("git", "-C", str(repo), "format-patch", "-1", "--stdout"),
            check=False,
            capture_output=True,
            text=True,
        )
        if patch_result.returncode != 0:
            return ["git am hook regression: could not create fixture patch"]
        patch = root / "fixture.patch"
        patch.write_text(patch_result.stdout, encoding="utf-8")

        errors = run_fixture_commands(
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
        if errors:
            return errors
        for name in ("applypatch-msg", "pre-applypatch", "post-applypatch"):
            hook = hooks / name
            hook.write_text("#!/bin/sh\nexit 91\n", encoding="utf-8")
            hook.chmod(0o755)
        errors = run_fixture_commands(
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
            ),
        )
        if errors:
            return errors

        ordinary_am = subprocess.run(
            (
                "git",
                "-C",
                str(ordinary),
                "-c",
                f"core.hooksPath={hooks}",
                "am",
                "--3way",
                str(patch),
            ),
            check=False,
            capture_output=True,
            text=True,
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
        hardened_am = subprocess.run(
            (
                "git",
                "-C",
                str(hardened),
                "-c",
                "core.hooksPath=/dev/null",
                "am",
                "--3way",
                str(patch),
            ),
            check=False,
            capture_output=True,
            text=True,
        )
        patched = (hardened / "sample.txt").read_text(encoding="utf-8")

    failures = []
    if ordinary_am.returncode == 0:
        failures.append("git am hook regression: hostile applypatch hook did not run")
    if hardened_am.returncode != 0 or patched != "base\npatched\n":
        failures.append("git am hook regression: hook-neutralized replay failed")
    return failures


def git_smudge_cleanup_regression() -> list[str]:
    """Prove a required-filter checkout failure leaves no owned worktree."""
    with tempfile.TemporaryDirectory(prefix="pelorus-smudge-cleanup-") as temp_dir:
        root = Path(temp_dir)
        repo = root / "repo"
        worktree = root / "owned-worktree"
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
        errors = run_fixture_commands(
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
        if errors:
            return errors
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
        cleanup = subprocess.run(
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
        registered = subprocess.run(
            ("git", "-C", str(repo), "worktree", "list", "--porcelain"),
            check=False,
            capture_output=True,
            text=True,
        )

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
        errors = helper(
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
        if errors:
            return errors

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

        hostile_settings = (
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
        errors = helper(
            "hostile format configuration",
            tuple(
                ("git", "-C", str(repo), "config", key, value)
                for key, value in hostile_settings
            ),
        )
        if errors:
            return errors
        errors = helper(
            "hostile format-patch",
            (
                format_command
                + ("--output-directory", str(hostile_output), "base..HEAD"),
            ),
        )
        if errors:
            return errors

        clean = {
            path.name: path.read_bytes() for path in sorted(clean_output.iterdir())
        }
        hostile = {
            path.name: path.read_bytes() for path in sorted(hostile_output.iterdir())
        }
    if clean != hostile:
        return ["format regression: hostile Git configuration changed patch bytes"]
    if len(clean) != 2 or not all(name.endswith(".patch") for name in clean):
        return ["format regression: fixture did not produce two patch artifacts"]
    return []


def validate_consumer_text(relative: str, text: str) -> list[str]:
    """Check that an FFmpeg consumer follows the shared, safe pin contract."""
    errors: list[str] = []
    shell_text = text.replace("\\\n", " ")
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
    worktree_pattern = re.compile(
        r'git\s+-C\s+"\$FFMPEG_REPO"\s+-c\s+core\.hooksPath=/dev/null\s+'
        r'worktree\s+add\s+--no-checkout\s+--detach\s+'
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
            r'core\.hooksPath=/dev/null\s+worktree\s+add',
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
    if (
        ownership_assignment < worktree_add
        or checkout_start < ownership_assignment
    ):
        errors.append(
            f"{relative}: worktree ownership must be recorded between registration "
            "and checkout"
        )
    if not re.search(
        r'git\s+-C\s+"\$OWNED_WORKTREE"\s+-c\s+core\.hooksPath=/dev/null\s+'
        r'am\s+--abort',
        shell_text,
    ):
        errors.append(f"{relative}: cleanup git am must disable Git hooks")
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
    return errors


def validate_replay_text(replay: str) -> list[str]:
    """Validate requirements specific to full stack replay."""
    errors: list[str] = []
    shell_replay = replay.replace("\\\n", " ")
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
    if not re.search(
        r'git\s+-C\s+"\$WORKTREE"\s+-c\s+core\.hooksPath=/dev/null\s+'
        r'am\s+--3way',
        shell_replay,
    ):
        errors.append(
            "ffmpeg-patches/test/build-and-run.sh: git am must disable Git hooks"
        )
    if "configure_ffmpeg() (" not in replay or "exec ./configure" not in replay:
        errors.append(
            "ffmpeg-patches/test/build-and-run.sh: configure must run in a " "subshell"
        )

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


def validate_workflow_text(relative: str, text: str) -> list[str]:
    """Validate the pinned runner, toolchain, and shared FFmpeg workflow contract."""
    errors: list[str] = []
    jobs = workflow_job_blocks(text)
    if not jobs:
        return [f"{relative}: no jobs found"]

    forbidden = (
        "ubuntu-latest",
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
    for name, block in jobs.items():
        runners = re.findall(r"^\s+runs-on:\s*([^\s#]+)", block, re.MULTILINE)
        if runners != ["ubuntu-26.04"]:
            errors.append(
                f"{relative}: job {name} must run exactly on ubuntu-26.04"
            )
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
    if Path(relative).name == "ci.yml":
        ffmpeg = jobs.get("ffmpeg-stack", "")
        for token in (
            "libvulkan-dev",
            'refs/tags/${FFMPEG_TAG}^{commit}',
            '"$FFMPEG_COMMIT"',
            "ffmpeg-patches/generate.sh",
            "ffmpeg-patches/test/build-and-run.sh",
        ):
            if token not in ffmpeg:
                errors.append(f"{relative}: FFmpeg job is missing {token}")
        docs = jobs.get("docs", "")
        for token in (
            f"actions/setup-go@{SETUP_GO_COMMIT}",
            "go-version: '1.26.x'",
            "github.com/rhysd/actionlint/cmd/actionlint@v1.7.12",
        ):
            if token not in docs:
                errors.append(f"{relative}: docs job is missing {token}")
    elif Path(relative).name == "release.yml":
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
                errors.append(
                    f"{relative}: manual package naming is missing {token}"
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


def validate_workflows() -> list[str]:
    """Validate all hosted workflow consumers of the build contract."""
    errors: list[str] = []
    for path in WORKFLOWS:
        relative = path.relative_to(ROOT).as_posix()
        if not path.is_file():
            errors.append(f"{relative}: missing")
            continue
        errors.extend(validate_workflow_text(relative, path.read_text(encoding="utf-8")))
    return errors


def workflow_validator_regressions() -> list[str]:
    """Prove runner and native-toolchain regressions are rejected."""
    failures: list[str] = []
    ci_path = WORKFLOWS[0]
    source = ci_path.read_text(encoding="utf-8")
    cases = {
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
    }
    relative = ci_path.relative_to(ROOT).as_posix()
    for name, (mutated, expected) in cases.items():
        if mutated == source:
            failures.append(f"workflow regression: {name} mutation changed nothing")
            continue
        errors = validate_workflow_text(relative, mutated)
        if not any(expected in error for error in errors):
            failures.append(f"workflow regression: {name} was accepted")
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
    errors.extend(validate_workflows())
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
        errors.extend(fixture_subprocess_regression())
        errors.extend(git_fixture_policy_regression())
        errors.extend(git_tag_ref_regression())
        errors.extend(git_dirty_worktree_cleanup_regression())
        errors.extend(git_worktree_hook_regression())
        errors.extend(git_am_hook_regression())
        errors.extend(git_smudge_cleanup_regression())
        errors.extend(git_format_config_regression())
        errors.extend(workflow_validator_regressions())

    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
