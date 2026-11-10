#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Check that CHANGELOG.md is rendered or at most 24 h stale from fragments.

Usage:
    check-changelog-staleness.py [--ref REF] [--max-age-hours HOURS] [--repo REPO]
    check-changelog-staleness.py --open-after-hours HOURS [--ref REF] [--repo REPO]
    check-changelog-staleness.py --pull-request [--ref REF] [--max-age-hours HOURS] [--repo REPO]
    check-changelog-staleness.py --self-test
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import os
import pathlib
import re
import shutil
import subprocess  # nosec B404 - fixed git argv, no shell, explicit deadline
import sys
import tempfile
import time
from typing import Any, Callable

SUBPROCESS_TIMEOUT_S = 120
VALID_DIFF_STATUSES = frozenset({"A", "D", "M", "T"})

# praetorctl compile-context outputs; no machine list exists
# (praetorctl compile-context --help has none; AGENTS.readonly.md lacks the marker).
CONTEXT_PROJECTIONS: frozenset[str] = frozenset(
    {
        "CLAUDE.md",
        "AGENTS.readonly.md",
        ".codex/rules.md",
        ".cursor/rules/hiss-invariants.mdc",
        ".gemini/GEMINI.md",
        ".github/copilot-instructions.md",
        ".windsurfrules",
    }
)

# Measured release cuts (e2e4040, 4617be1, 91826e5, 7cae980, 0c2f79d, 5737c8f).
RELEASE_DOCS: frozenset[str] = frozenset(
    {
        "docs/roadmap.md",
        "docs/rebase-notes.md",
    }
)


def _load_version_sites() -> tuple[str, ...]:
    """Load VERSION_SITES from scripts/prepare-release.py; raise RuntimeError on failure."""
    path = pathlib.Path(__file__).resolve().parent.parent / "prepare-release.py"
    spec = importlib.util.spec_from_file_location("prepare_release", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load VERSION_SITES: no loader for {path}")
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except (OSError, ImportError, SyntaxError) as exc:
        raise RuntimeError(f"cannot load VERSION_SITES from {path}: {exc}") from exc
    sites = getattr(mod, "VERSION_SITES", None)
    if not isinstance(sites, tuple) or not all(isinstance(s, str) for s in sites):
        raise RuntimeError(f"cannot load VERSION_SITES: {path} has no tuple of paths")
    return sites


def _load_changelog_sections(
    path: pathlib.Path | None = None,
) -> tuple[str, ...]:
    """Load SECTIONS from concat-changelog-fragments.sh; raise RuntimeError on failure."""
    target = (
        path
        if path is not None
        else (pathlib.Path(__file__).resolve().parent / "concat-changelog-fragments.sh")
    )
    try:
        content = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise RuntimeError(
            f"cannot load changelog sections from {target}: {exc}"
        ) from exc
    matches = re.findall(r"^SECTIONS=\(([a-z]+(?: [a-z]+)*)\)$", content, re.MULTILINE)
    if len(matches) != 1:
        raise RuntimeError(
            f"cannot load changelog sections: {target} has {len(matches)} SECTIONS lines"
        )
    return tuple(matches[0].split())


def _is_fragment_path(path: str, sections: tuple[str, ...]) -> bool:
    """Return True if path is a changelog fragment (changelog.d/<section>/<name>.md).

    Matches concat-changelog-fragments.sh: SECTIONS and find -maxdepth 1 -name '*.md'.
    """
    parts = path.split("/")
    return (
        len(parts) == 3
        and parts[0] == "changelog.d"
        and parts[1] in sections
        and parts[-1].endswith(".md")
    )


def _run_git_step(cmd: list[str], env: dict[str, str], what: str) -> None:
    """Run one git command; raise RuntimeError with context on failure or timeout."""
    try:
        subprocess.run(  # nosec B603 - fixed git argv, no shell
            cmd,
            env=env,
            capture_output=True,
            timeout=SUBPROCESS_TIMEOUT_S,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        err = exc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"{what} failed: {err}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{what} timed out after {SUBPROCESS_TIMEOUT_S}s") from exc


def extract_tree_at_ref(
    repo: pathlib.Path,
    ref: str,
    dest_dir: pathlib.Path,
    env: dict[str, str] | None = None,
) -> None:
    """Write the tree of ref into dest_dir through a private git index.

    git itself writes the files (read-tree plus checkout-index), so no archive
    is unpacked, and GIT_INDEX_FILE keeps the repository's own index untouched.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    index_file = dest_dir / ".git-index"
    env2 = dict(env or os.environ)
    env2["GIT_INDEX_FILE"] = str(index_file.resolve())
    prefix = f"{dest_dir.resolve()}/"
    try:
        _run_git_step(
            ["git", "-C", str(repo), "read-tree", ref],
            env2,
            f"git read-tree of {ref} in {repo}",
        )
        _run_git_step(
            [
                "git",
                "-C",
                str(repo),
                "checkout-index",
                "-a",
                "-f",
                f"--prefix={prefix}",
            ],
            env2,
            f"git checkout-index of {ref} in {repo}",
        )
    finally:
        if index_file.exists():
            index_file.unlink()


def run_fragment_check(
    tree_dir: pathlib.Path,
    env: dict[str, str] | None = None,
) -> int:
    """Run concat-changelog-fragments.sh --check in tree_dir."""
    script = tree_dir / "scripts/release/concat-changelog-fragments.sh"
    if not script.is_file():
        raise RuntimeError(f"missing render script at {script}")
    cmd = ["bash", "scripts/release/concat-changelog-fragments.sh", "--check"]
    try:
        proc = subprocess.run(  # nosec B603 - fixed script argv, no shell
            cmd,
            cwd=str(tree_dir),
            env=env,
            capture_output=True,
            timeout=SUBPROCESS_TIMEOUT_S,
            check=False,
        )
        return proc.returncode
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"changelog check timed out in {tree_dir} after {SUBPROCESS_TIMEOUT_S}s"
        ) from exc


def run_git_log(
    repo: pathlib.Path,
    args: list[str],
    env: dict[str, str] | None = None,
    subcommand: str = "log",
) -> str:
    """Run git subcommand with given args in repo and return stripped stdout."""
    cmd = ["git", "-C", str(repo), subcommand, *args]
    try:
        proc = subprocess.run(  # nosec B603 - fixed git argv, no shell
            cmd,
            env=env,
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_S,
            check=True,
        )
        return proc.stdout.strip()
    except subprocess.CalledProcessError as exc:
        err = exc.stderr.strip()
        raise RuntimeError(
            f"git {subcommand} {' '.join(args)} failed in {repo}: {err}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"git {subcommand} {' '.join(args)} timed out in {repo} after {SUBPROCESS_TIMEOUT_S}s"
        ) from exc


def is_tree_current(
    repo: pathlib.Path,
    ref: str,
    env: dict[str, str] | None = None,
) -> bool:
    """Return True if ref's tree renders current changelog, False otherwise."""
    with tempfile.TemporaryDirectory() as tmp_a:
        extract_tree_at_ref(repo, ref, pathlib.Path(tmp_a), env=env)
        return run_fragment_check(pathlib.Path(tmp_a), env=env) == 0


def _last_render_commit(
    repo: pathlib.Path,
    ref: str,
    env: dict[str, str] | None = None,
) -> tuple[str | None, int | None, bool]:
    """Find the last first-parent CHANGELOG.md commit on ref.

    Returns (sha, committer_time, is_hand_edited).
    If no commit is found, returns (None, None, False).
    """
    output = run_git_log(
        repo,
        ["--first-parent", "-1", "--format=%H %ct", ref, "--", "CHANGELOG.md"],
        env=env,
    )
    if not output:
        return None, None, False
    parts = output.split()
    sha = parts[0]
    committer_time = int(parts[1]) if len(parts) > 1 else None
    is_hand_edited = not is_tree_current(repo, sha, env=env)
    return sha, committer_time, is_hand_edited


def check_changelog_base(
    repo: pathlib.Path,
    ref: str,
    env: dict[str, str] | None = None,
) -> tuple[str | None, int]:
    """Find the last first-parent CHANGELOG.md commit on ref and verify it was not hand-edited."""
    sha, _, is_hand_edited = _last_render_commit(repo, ref, env=env)
    if not sha:
        print(
            f"error: no commits touching CHANGELOG.md found in {ref}", file=sys.stderr
        )
        return None, 1

    if is_hand_edited:
        print(
            f"CHANGELOG.md hand-edited or stale at {sha}; render it with --write",
            file=sys.stderr,
        )
        return None, 1

    return sha, 0


def _first_owed_commit(
    repo: pathlib.Path,
    sha: str,
    ref: str,
    env: dict[str, str] | None = None,
) -> tuple[str | None, int | None]:
    """Find the first first-parent commit touching changelog.d/ since sha.

    Returns (first_sha, committer_time) or (None, None).
    """
    log_output = run_git_log(
        repo,
        [
            "--first-parent",
            "--reverse",
            "--format=%H %ct",
            f"{sha}..{ref}",
            "--",
            ":(glob)changelog.d/*/*.md",
        ],
        env=env,
    )
    lines = [line for line in log_output.splitlines() if line.strip()]
    if not lines:
        return None, None
    first_sha, committer_time_str = lines[0].strip().split()[:2]
    return first_sha, int(committer_time_str)


def judge_owed_render(
    repo: pathlib.Path,
    sha: str,
    ref: str,
    max_age_hours: int,
    env: dict[str, str] | None = None,
    remedy: str = "land a render-only pull request",
) -> int:
    """Judge how long a render has been owed since the last rendered CHANGELOG.md."""
    first_sha, committer_time = _first_owed_commit(repo, sha, ref, env=env)
    if not first_sha or committer_time is None:
        print(
            f"render owed on {ref}, but no changelog.d/ commits found since {sha}",
            file=sys.stderr,
        )
        return 1

    now = int(time.time())
    age_seconds = max(0, now - committer_time)
    age_hours = f"{age_seconds / 3600:.1f}"

    if age_seconds > max_age_hours * 3600:
        print(
            f"render owed for {age_hours} h since {first_sha} (limit {max_age_hours} h):",
            remedy,
            file=sys.stderr,
        )
        return 1

    print(f"warning: render owed since {first_sha} ({age_hours} h)")
    return 0


def _emit_decision(
    current: bool,
    is_open: bool,
    owed_since: str,
    owed_hours: str,
) -> None:
    """Print standard four stdout lines and decision repetition to stderr."""
    cur_str = "true" if current else "false"
    open_str = "true" if is_open else "false"
    print(f"current={cur_str}")
    print(f"open={open_str}")
    print(f"owed_since={owed_since}")
    print(f"owed_hours={owed_hours}")
    print(
        f"decision: current={cur_str} open={open_str} owed_since={owed_since} owed_hours={owed_hours}",
        file=sys.stderr,
    )


def _emit_owed_at_render(sha: str, committer_time: int | None, now: int) -> int:
    """Emit a due decision owed since the last render commit sha (ADR-0252)."""
    age_seconds = max(0, now - (committer_time or now))
    age_hours = f"{age_seconds / 3600:.1f}"
    _emit_decision(current=False, is_open=True, owed_since=sha, owed_hours=age_hours)
    return 0


def decide_render_pr(
    repo: pathlib.Path,
    ref: str,
    open_after_hours: int,
    env: dict[str, str] | None = None,
    now: int | None = None,
) -> int:
    """Decide whether a scheduled changelog render pull request is due (ADR-0252)."""
    if is_tree_current(repo, ref, env=env):
        _emit_decision(current=True, is_open=False, owed_since="", owed_hours="")
        return 0

    sha, committer_time, is_hand_edited = _last_render_commit(repo, ref, env=env)
    if not sha:
        print(
            f"error: no commits touching CHANGELOG.md found in {ref}", file=sys.stderr
        )
        return 1

    if now is None:
        now = int(time.time())
    if is_hand_edited:
        print(
            f"CHANGELOG.md hand-edited or stale at {sha}; render it with --write",
            file=sys.stderr,
        )
        return _emit_owed_at_render(sha, committer_time, now)

    first_sha, first_ct = _first_owed_commit(repo, sha, ref, env=env)
    if not first_sha or first_ct is None:
        print(
            f"render owed on {ref}, but no changelog.d/ commits found since {sha}; "
            "a render input other than a fragment changed, so the render is due now",
            file=sys.stderr,
        )
        return _emit_owed_at_render(sha, committer_time, now)

    age_seconds = max(0, now - first_ct)
    age_hours = f"{age_seconds / 3600:.1f}"
    is_open = age_seconds >= open_after_hours * 3600
    msg = f"render owed for {age_hours} h since {first_sha} (threshold {open_after_hours} h): "
    print(
        msg + ("render PR is due" if is_open else "render PR not yet due"),
        file=sys.stderr,
    )
    _emit_decision(
        current=False, is_open=is_open, owed_since=first_sha, owed_hours=age_hours
    )
    return 0


def _parse_diff_status(
    diff_text: str,
) -> tuple[list[tuple[str, str]] | None, str | None]:
    """Parse git diff --name-status -z output using strict allow-list validation."""
    if not diff_text:
        return [], None
    if not diff_text.endswith("\0"):
        return (
            None,
            f"error: invalid diff output: missing NUL terminator in {diff_text!r}",
        )
    fields = diff_text.split("\0")[:-1]
    if len(fields) % 2 != 0:
        return (
            None,
            f"error: invalid diff output: odd number of NUL fields: {len(fields)}",
        )
    entries: list[tuple[str, str]] = []
    for i in range(0, len(fields), 2):
        status, path = fields[i], fields[i + 1]
        if status not in VALID_DIFF_STATUSES or not path:
            return None, f"error: invalid diff entry: ({status!r}, {path!r})"
        entries.append((status, path))
    return entries, None


def _check_render_pr_scope(
    diff_entries: list[tuple[str, str]],
) -> tuple[bool, int]:
    """Check render PR scope; return (is_render_pr, exit_code)."""
    is_render_pr = any(path == "CHANGELOG.md" for _, path in diff_entries)
    if not is_render_pr:
        return False, 0

    is_release_cut = ("M", "meson.build") in diff_entries
    if is_release_cut:
        try:
            version_sites = _load_version_sites()
            sections = _load_changelog_sections()
        except RuntimeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return True, 1
        release_files = (
            frozenset(version_sites)
            | CONTEXT_PROJECTIONS
            | RELEASE_DOCS
            | {"CHANGELOG.md"}
        )
        offending = [
            (status, path)
            for status, path in diff_entries
            if not (
                (status == "D" and _is_fragment_path(path, sections))
                or (status == "M" and path in release_files)
            )
        ]
        if offending:
            for status, path in offending:
                print(
                    f"release pull request changes {path} ({status}): "
                    "a release pull request modifies release files and deletes fragments only; "
                    "land fragment and code changes in their own pull request first",
                    file=sys.stderr,
                )
            return True, 1
        return True, 0

    offending = [
        (status, path) for status, path in diff_entries if path != "CHANGELOG.md"
    ]
    if offending:
        for status, path in offending:
            print(
                f"render pull request changes {path} ({status}): "
                "a render pull request carries CHANGELOG.md only; "
                "land fragment and code changes in their own pull request first",
                file=sys.stderr,
            )
        return True, 1

    return True, 0


def _get_pull_request_parents(
    repo: pathlib.Path,
    ref: str,
    env: dict[str, str] | None = None,
) -> tuple[str | None, int]:
    """Return the base (first parent) of ref, or print error and return (None, 1)."""
    rev_output = run_git_log(
        repo,
        ["--parents", "-n", "1", ref],
        env=env,
        subcommand="rev-list",
    )
    parts = rev_output.split()
    parents = parts[1:] if len(parts) > 1 else []
    if len(parents) != 2:
        print(
            f"error: {ref} is not a merge commit with two parents; "
            "--pull-request expects the refs/pull/<n>/merge checkout",
            file=sys.stderr,
        )
        return None, 1
    return parents[0], 0


def _judge_render_pr(
    repo: pathlib.Path,
    ref: str,
    env: dict[str, str] | None = None,
) -> int:
    """Judge a render pull request; merge tree ref decides whether current."""
    if is_tree_current(repo, ref, env=env):
        print("changelog render current")
        return 0
    print(
        "render pull request leaves CHANGELOG.md stale on the merged tree: "
        "merge the default branch and run concat-changelog-fragments.sh --write again",
        file=sys.stderr,
    )
    return 1


def _judge_non_render_pr(
    repo: pathlib.Path,
    base: str,
    ref: str,
    max_age_hours: int,
    env: dict[str, str] | None = None,
) -> int:
    """Judge a non-render pull request; base tree decides owed render, merge tree decides current when base is current."""
    if is_tree_current(repo, base, env=env):
        if is_tree_current(repo, ref, env=env):
            print("changelog render current")
            return 0
        print(
            f"warning: render owed for this pull request's fragments only (base {base[:12]} renders current)"
        )
        return 0

    sha, rc = check_changelog_base(repo, base, env=env)
    if rc != 0 or sha is None:
        return rc

    return judge_owed_render(
        repo, sha, base, max_age_hours, env=env, remedy="land the render PR first"
    )


def check_pull_request(
    repo: pathlib.Path,
    ref: str,
    max_age_hours: int,
    env: dict[str, str] | None = None,
) -> int:
    """Check pull request merge commit against its first parent."""
    # a. Parents via git rev-list --parents -n 1 <ref>
    base, rc = _get_pull_request_parents(repo, ref, env=env)
    if rc != 0 or base is None:
        return rc

    # b. Scope via git diff --name-status -z --no-renames <base> <ref>
    diff_output = run_git_log(
        repo,
        ["--name-status", "-z", "--no-renames", base, ref],
        env=env,
        subcommand="diff",
    )
    diff_entries, err = _parse_diff_status(diff_output)
    if err is not None or diff_entries is None:
        if err is not None:
            print(err, file=sys.stderr)
        return 1

    is_render_pr, scope_rc = _check_render_pr_scope(diff_entries)
    if scope_rc != 0:
        return scope_rc

    # c. Render PR path
    if is_render_pr:
        return _judge_render_pr(repo, ref, env=env)

    # d. Non-render PR path
    return _judge_non_render_pr(repo, base, ref, max_age_hours, env=env)


def check_staleness(
    repo: pathlib.Path,
    ref: str,
    max_age_hours: int,
    env: dict[str, str] | None = None,
) -> int:
    """Check changelog render status and staleness against git history."""
    if is_tree_current(repo, ref, env=env):
        print("changelog render current")
        return 0

    sha, rc = check_changelog_base(repo, ref, env=env)
    if rc != 0 or sha is None:
        return rc

    return judge_owed_render(repo, sha, ref, max_age_hours, env=env)


def make_test_env(tmp: pathlib.Path) -> dict[str, str]:
    """Create isolated environment for git commands in self-test."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["HOME"] = str(tmp)
    return env


def _fixture_git(
    repo: pathlib.Path,
    env: dict[str, str],
    *args: str,
) -> str:
    """Run a git command in the fixture repo; wrap failures to RuntimeError."""
    cmd = ["git", "-C", str(repo), *args]
    try:
        proc = subprocess.run(  # nosec B603 - fixed git argv  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - fixed git argv
            cmd,
            env=env,
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_S,
            check=True,
        )
        return proc.stdout.strip()
    except subprocess.CalledProcessError as exc:
        err = exc.stderr.strip()
        raise RuntimeError(
            f"git {' '.join(args)} failed in fixture {repo}: {err}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"git {' '.join(args)} timed out in fixture {repo}") from exc


def _fixture_render(
    repo: pathlib.Path,
    env: dict[str, str],
    script_dest: pathlib.Path,
) -> None:
    """Run concat-changelog-fragments.sh --write in the fixture repo."""
    try:
        subprocess.run(  # nosec B603 - fixed script argv  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - fixed script argv
            ["bash", str(script_dest), "--write"],
            cwd=str(repo),
            env=env,
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_S,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        err = exc.stderr.strip()
        raise RuntimeError(f"render failed in fixture {repo}: {err}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"render timed out in fixture {repo}") from exc


def commit_in_fixture(
    repo: pathlib.Path,
    env: dict[str, str],
    msg: str,
    timestamp: int,
) -> None:
    """Commit changes in the fixture repo with explicit author and committer dates."""
    c_env = dict(env)
    date_str = f"{timestamp} +0000"
    c_env["GIT_AUTHOR_DATE"] = date_str
    c_env["GIT_COMMITTER_DATE"] = date_str
    _fixture_git(repo, c_env, "add", ".")
    _fixture_git(
        repo,
        c_env,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-q",
        "-m",
        msg,
    )


def init_fixture_repo(
    repo: pathlib.Path,
    env: dict[str, str],
    script_source: pathlib.Path,
    now: int,
) -> pathlib.Path:
    """Initialize fixture git repository with minimal changelog and render script."""
    repo.mkdir(parents=True, exist_ok=True)
    _fixture_git(repo, env, "init", "-q", "-b", "master")
    script_dest = repo / "scripts/release/concat-changelog-fragments.sh"
    script_dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(script_source, script_dest)
    script_dest.chmod(0o500)

    cl = repo / "CHANGELOG.md"
    cl.write_text(
        "# Changelog\n\n"
        "<!-- BEGIN UNRELEASED (generated by scripts/release/concat-changelog-fragments.sh) -->\n"
        "<!-- END UNRELEASED -->\n\n"
        "## [0.1.0] - 2026-01-01\n\n"
        "### Added\n\n"
        "- Initial release.\n",
        encoding="utf-8",
    )
    (repo / "changelog.d/added").mkdir(parents=True, exist_ok=True)
    (repo / "changelog.d/added/.gitkeep").touch()

    _fixture_render(repo, env, script_dest)
    commit_in_fixture(repo, env, "Initial commit", now - 50 * 3600)
    return script_dest


def _change_fixture_render_script(repo: pathlib.Path, placeholder: str) -> None:
    """Change the fixture render script's empty-block placeholder; keep it read-only."""
    script = repo / "scripts/release/concat-changelog-fragments.sh"
    script.chmod(0o600)
    script.write_text(
        script.read_text(encoding="utf-8").replace(
            "_No unreleased changes._", placeholder
        ),
        encoding="utf-8",
    )
    script.chmod(0o500)


def _hand_edit_fixture_changelog(repo: pathlib.Path, entry: str) -> None:
    """Replace the rendered empty-block placeholder in CHANGELOG.md by hand."""
    cl = repo / "CHANGELOG.md"
    cl.write_text(
        cl.read_text(encoding="utf-8").replace("_No unreleased changes._", entry),
        encoding="utf-8",
    )


def _test_cases_g_h(
    repo: pathlib.Path,
    env: dict[str, str],
    now: int,
) -> int:
    """Cases g and h: a hand edit or a render script change fails at once."""
    # case g: hand edit, then a fragment 1 h ago -> 1 (no 24 h grace)
    _fixture_git(repo, env, "checkout", "-q", "-b", "branch-g", "master")
    _hand_edit_fixture_changelog(repo, "Hand edited entry in g.")
    commit_in_fixture(repo, env, "Hand edit CHANGELOG for g", now - 3600)
    (repo / "changelog.d/added/0005-g.md").write_text("- Add g.\n", encoding="utf-8")
    commit_in_fixture(repo, env, "Add fragment g 1h ago", now - 3600)
    rc_g, _, err_g = _run_captured(check_staleness, repo, "branch-g", 24, env=env)
    if rc_g != 1 or "hand-edited or stale" not in err_g:
        print(f"self-test FAIL: case g: rc={rc_g}, err={err_g!r}", file=sys.stderr)
        return 1

    # case h: render script changed, no fragment since the last render -> 1
    _fixture_git(repo, env, "checkout", "-q", "-b", "branch-h", "master")
    _change_fixture_render_script(repo, "_Nothing unreleased in h._")
    commit_in_fixture(repo, env, "Change the render script for h", now)
    rc_h, _, err_h = _run_captured(check_staleness, repo, "branch-h", 24, env=env)
    if rc_h != 1 or "no changelog.d/ commits found" not in err_h:
        print(f"self-test FAIL: case h: rc={rc_h}, err={err_h!r}", file=sys.stderr)
        return 1
    print("self-test ok: g hand edit and h render script change -> rc 1 at once")
    return 0


def _test_cases_b_c_d(
    repo: pathlib.Path,
    env: dict[str, str],
    now: int,
) -> int:
    """Test cases b (1 h warning), c (30 h failure), d (hand edit failure)."""
    failed = 0
    # case b: fragment added 1 h ago, no render -> 0 (warning)
    _fixture_git(repo, env, "checkout", "-q", "-b", "branch-b", "master")
    (repo / "changelog.d/added/0001-frag.md").write_text("- Add 1.\n", encoding="utf-8")
    commit_in_fixture(repo, env, "Add fragment 1h ago", now - 3600)
    if check_staleness(repo, "branch-b", 24, env=env) != 0:
        print("self-test FAIL: case b returned non-zero, want 0", file=sys.stderr)
        failed = 1

    # case c: fragment added 30 h ago, no render -> 1
    _fixture_git(repo, env, "checkout", "-q", "-b", "branch-c", "master")
    (repo / "changelog.d/added/0002-frag.md").write_text("- Add 2.\n", encoding="utf-8")
    commit_in_fixture(repo, env, "Add fragment 30h ago", now - 30 * 3600)
    if check_staleness(repo, "branch-c", 24, env=env) != 1:
        print("self-test FAIL: case c returned non-one, want 1", file=sys.stderr)
        failed = 1

    # case d: CHANGELOG.md hand-edited, 0 h old -> 1
    _fixture_git(repo, env, "checkout", "-q", "-b", "branch-d", "master")
    cl = repo / "CHANGELOG.md"
    cl_text = cl.read_text(encoding="utf-8")
    cl.write_text(
        cl_text.replace("_No unreleased changes._", "Hand edited invalid entry."),
        encoding="utf-8",
    )
    commit_in_fixture(repo, env, "Hand edit CHANGELOG", now)
    if check_staleness(repo, "branch-d", 24, env=env) != 1:
        print("self-test FAIL: case d returned non-one, want 1", file=sys.stderr)
        failed = 1

    return failed


def _test_cases_readme_boundary(
    repo: pathlib.Path,
    env: dict[str, str],
    now: int,
) -> int:
    """Test README pathspec exclusion and exact-hour staleness boundary."""
    # README-only edit 30 h ago: not a fragment, so render is not owed -> 0
    _fixture_git(repo, env, "checkout", "-q", "-b", "branch-readme", "master")
    (repo / "changelog.d/README.md").write_text("# Updated README\n", encoding="utf-8")
    commit_in_fixture(repo, env, "Update changelog README 30h ago", now - 30 * 3600)
    if check_staleness(repo, "branch-readme", 24, env=env) != 0:
        print(
            "self-test FAIL: README-only commit returned non-zero, want 0",
            file=sys.stderr,
        )
        return 1

    # Add fragment 1 h ago on top of README edit: only fragment counts -> 0
    (repo / "changelog.d/added/0003-frag.md").write_text("- Add 3.\n", encoding="utf-8")
    commit_in_fixture(repo, env, "Add fragment 1h ago", now - 3600)
    if check_staleness(repo, "branch-readme", 24, env=env) != 0:
        print(
            "self-test FAIL: fragment after README returned non-zero, want 0",
            file=sys.stderr,
        )
        return 1

    # Boundary case: fragment at 24 h + 30 min (limit*3600 + 1800 s) -> 1
    _fixture_git(repo, env, "checkout", "-q", "-b", "branch-boundary", "master")
    (repo / "changelog.d/added/0004-boundary.md").write_text(
        "- Add boundary.\n", encoding="utf-8"
    )
    commit_in_fixture(repo, env, "Add fragment 24h30m ago", now - (24 * 3600 + 1800))
    if check_staleness(repo, "branch-boundary", 24, env=env) != 1:
        print("self-test FAIL: boundary case returned non-one, want 1", file=sys.stderr)
        return 1

    return 0


def _test_case_f(repo: pathlib.Path, env: dict[str, str]) -> int:
    """Case f: extract_tree_at_ref leaves the repository index unchanged."""
    tree_before = _fixture_git(repo, env, "write-tree")
    index_bytes_before = (repo / ".git/index").read_bytes()
    with tempfile.TemporaryDirectory() as tmp_f:
        extract_tree_at_ref(repo, "master", pathlib.Path(tmp_f), env=env)
        if not (pathlib.Path(tmp_f) / "CHANGELOG.md").is_file():
            print(
                "self-test FAIL: case f extracted tree missing CHANGELOG.md",
                file=sys.stderr,
            )
            return 1
    tree_after = _fixture_git(repo, env, "write-tree")
    index_bytes_after = (repo / ".git/index").read_bytes()
    if tree_before != tree_after or index_bytes_before != index_bytes_after:
        print(
            f"self-test FAIL: case f repo index modified by extract_tree_at_ref "
            f"({tree_before} != {tree_after})",
            file=sys.stderr,
        )
        return 1
    return 0


def merge_in_fixture(
    repo: pathlib.Path,
    env: dict[str, str],
    branch_or_ref: str,
    msg: str,
    timestamp: int,
) -> None:
    """Create a 2-parent --no-ff merge commit with deterministic timestamp."""
    c_env = dict(env)
    date_str = f"{timestamp} +0000"
    c_env["GIT_AUTHOR_DATE"] = date_str
    c_env["GIT_COMMITTER_DATE"] = date_str
    try:
        subprocess.run(  # nosec B603 - fixed git argv  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - fixed git argv
            [
                "git",
                "-C",
                str(repo),
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@example.invalid",
                "-c",
                "commit.gpgsign=false",
                "merge",
                "--no-ff",
                "-q",
                "-m",
                msg,
                branch_or_ref,
            ],
            env=c_env,
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_S,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        err = exc.stderr.strip()
        raise RuntimeError(f"merge failed in fixture {repo}: {err}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"merge timed out in fixture {repo}") from exc


def _run_captured(
    fn: Callable[..., int], *args: Any, **kwargs: Any
) -> tuple[int, str, str]:
    """Run fn(*args, **kwargs) capturing stdout and stderr."""
    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = fn(*args, **kwargs)
    return rc, out.getvalue(), err.getvalue()


def _test_pr_p1(
    repo: pathlib.Path,
    env: dict[str, str],
    now: int,
) -> int:
    """Test pull request case p1 (base owes 21 h)."""
    _fixture_git(repo, env, "checkout", "-q", "-b", "base-p1", "master")
    (repo / "changelog.d/added/p1-base.md").write_text("- Base p1.\n", encoding="utf-8")
    commit_in_fixture(repo, env, "Add base p1 21h ago", now - 21 * 3600)
    _fixture_git(repo, env, "checkout", "-q", "-b", "pr-p1", "base-p1")
    (repo / "changelog.d/added/p1-pr.md").write_text("- PR p1.\n", encoding="utf-8")
    commit_in_fixture(repo, env, "Add pr p1", now)
    _fixture_git(repo, env, "checkout", "-q", "-b", "merge-p1", "base-p1")
    merge_in_fixture(repo, env, "pr-p1", "Merge pr-p1 into base-p1", now)
    rc1, _, err1 = _run_captured(check_pull_request, repo, "HEAD", 20, env=env)
    if rc1 != 1 or "limit 20 h" not in err1 or "land the render PR first" not in err1:
        print(f"self-test FAIL: p1 failed: rc={rc1}, err={err1!r}", file=sys.stderr)
        return 1
    print("self-test ok: p1 base owes 21 h -> rc 1")
    return 0


def _test_pr_p2(
    repo: pathlib.Path,
    env: dict[str, str],
    now: int,
) -> int:
    """Test pull request case p2 (base owes 19 h)."""
    _fixture_git(repo, env, "checkout", "-q", "-b", "base-p2", "master")
    (repo / "changelog.d/added/p2-base.md").write_text("- Base p2.\n", encoding="utf-8")
    commit_in_fixture(repo, env, "Add base p2 19h ago", now - 19 * 3600)
    _fixture_git(repo, env, "checkout", "-q", "-b", "pr-p2", "base-p2")
    (repo / "changelog.d/added/p2-pr.md").write_text("- PR p2.\n", encoding="utf-8")
    commit_in_fixture(repo, env, "Add pr p2", now)
    _fixture_git(repo, env, "checkout", "-q", "-b", "merge-p2", "base-p2")
    merge_in_fixture(repo, env, "pr-p2", "Merge pr-p2 into base-p2", now)
    rc2, out2, _ = _run_captured(check_pull_request, repo, "HEAD", 20, env=env)
    if rc2 != 0 or "warning: render owed since" not in out2:
        print(f"self-test FAIL: p2 failed: rc={rc2}, out={out2!r}", file=sys.stderr)
        return 1
    print("self-test ok: p2 base owes 19 h -> rc 0")
    return 0


def _test_pr_p3(
    repo: pathlib.Path,
    env: dict[str, str],
    script_dest: pathlib.Path,
    now: int,
) -> int:
    """Test pull request case p3 (render PR while base owes 21 h)."""
    _fixture_git(repo, env, "checkout", "-q", "-b", "base-p3", "master")
    (repo / "changelog.d/added/p3-base.md").write_text("- Base p3.\n", encoding="utf-8")
    commit_in_fixture(repo, env, "Add base p3 21h ago", now - 21 * 3600)
    _fixture_git(repo, env, "checkout", "-q", "-b", "pr-p3", "base-p3")
    _fixture_render(repo, env, script_dest)
    commit_in_fixture(repo, env, "Render changelog in PR", now)
    _fixture_git(repo, env, "checkout", "-q", "-b", "merge-p3", "base-p3")
    merge_in_fixture(repo, env, "pr-p3", "Merge render PR into base-p3", now)
    rc3, out3, _ = _run_captured(check_pull_request, repo, "HEAD", 20, env=env)
    if rc3 != 0 or "changelog render current" not in out3:
        print(f"self-test FAIL: p3 failed: rc={rc3}, out={out3!r}", file=sys.stderr)
        return 1
    print("self-test ok: p3 render PR while base owes 21 h -> rc 0")
    return 0


def _test_pr_p4(
    repo: pathlib.Path,
    env: dict[str, str],
    now: int,
) -> int:
    """Test pull request case p4 (rerun older merge over current base)."""
    _fixture_git(repo, env, "checkout", "-q", "-b", "pr-p4", "master")
    (repo / "changelog.d/added/p4.md").write_text("- PR p4.\n", encoding="utf-8")
    commit_in_fixture(repo, env, "Add pr p4 48h ago", now - 48 * 3600)
    _fixture_git(repo, env, "checkout", "-q", "-b", "merge-p4", "master")
    merge_in_fixture(repo, env, "pr-p4", "Merge pr-p4 48h ago", now - 48 * 3600)
    rc4, out4, _ = _run_captured(check_pull_request, repo, "HEAD", 20, env=env)
    rc4_push, _, _ = _run_captured(check_staleness, repo, "HEAD", 20, env=env)
    if rc4 != 0 or "fragments only" not in out4 or rc4_push != 1:
        print(
            f"self-test FAIL: p4 failed: rc={rc4}, rc_push={rc4_push}, out={out4!r}",
            file=sys.stderr,
        )
        return 1
    print("self-test ok: p4 rerun older merge over current base -> rc 0")
    return 0


def _test_pr_p5(
    repo: pathlib.Path,
    env: dict[str, str],
    script_dest: pathlib.Path,
    now: int,
) -> int:
    """Test pull request case p5 (#586 modified fragment)."""
    _fixture_git(repo, env, "checkout", "-q", "-b", "base-p5", "master")
    frag = repo / "changelog.d/fixed/p5.md"
    frag.parent.mkdir(parents=True, exist_ok=True)
    frag.write_text("- Fix p5 original.\n", encoding="utf-8")
    commit_in_fixture(repo, env, "Add fragment 10h ago", now - 10 * 3600)
    _fixture_git(repo, env, "checkout", "-q", "-b", "pr-p5", "base-p5")
    frag.write_text("- Fix p5 modified.\n", encoding="utf-8")
    _fixture_render(repo, env, script_dest)
    commit_in_fixture(repo, env, "Render and edit fragment", now)
    _fixture_git(repo, env, "checkout", "-q", "-b", "merge-p5", "base-p5")
    merge_in_fixture(repo, env, "pr-p5", "Merge pr-p5", now)
    if not is_tree_current(repo, "HEAD", env):
        print("self-test FAIL: p5 fixture merged tree not current", file=sys.stderr)
        return 1
    rc5, _, err5 = _run_captured(check_pull_request, repo, "HEAD", 20, env=env)
    if rc5 != 1 or "changelog.d/fixed/p5.md (M)" not in err5:
        print(f"self-test FAIL: p5 failed: rc={rc5}, err={err5!r}", file=sys.stderr)
        return 1
    print("self-test ok: p5 render PR modifies fragment -> rc 1")
    return 0


def _test_pr_p6(
    repo: pathlib.Path,
    env: dict[str, str],
    script_dest: pathlib.Path,
    now: int,
) -> int:
    """Test pull request case p6 (extra file)."""
    _fixture_git(repo, env, "checkout", "-q", "-b", "base-p6", "master")
    (repo / "changelog.d/added/p6.md").write_text("- Add p6.\n", encoding="utf-8")
    commit_in_fixture(repo, env, "Add fragment 10h ago", now - 10 * 3600)
    _fixture_git(repo, env, "checkout", "-q", "-b", "pr-p6", "base-p6")
    _fixture_render(repo, env, script_dest)
    (repo / "README.md").write_text("# Extra README\n", encoding="utf-8")
    commit_in_fixture(repo, env, "Render and add README", now)
    _fixture_git(repo, env, "checkout", "-q", "-b", "merge-p6", "base-p6")
    merge_in_fixture(repo, env, "pr-p6", "Merge pr-p6", now)
    rc6, _, err6 = _run_captured(check_pull_request, repo, "HEAD", 20, env=env)
    if rc6 != 1 or "README.md (A)" not in err6:
        print(f"self-test FAIL: p6 failed: rc={rc6}, err={err6!r}", file=sys.stderr)
        return 1
    print("self-test ok: p6 render PR extra file -> rc 1")
    return 0


def _setup_release_pr(
    repo: pathlib.Path,
    env: dict[str, str],
    base_name: str,
    pr_name: str,
    frag_name: str,
    script_dest: pathlib.Path,
    now: int,
    base_files: dict[str, str] | None = None,
) -> None:
    """Set up a release PR branch with consumed fragment deleted and version updated.

    base_files are extra paths committed on the base branch with the fragment.
    """
    _fixture_git(repo, env, "checkout", "-q", "-b", base_name, "master")
    (repo / "meson.build").write_text(
        "project('p', 'c', version: '0.1.0')\n", encoding="utf-8"
    )
    for rel, content in (base_files or {}).items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(content, encoding="utf-8")
    (repo / f"changelog.d/added/{frag_name}").write_text("- Add.\n", encoding="utf-8")
    commit_in_fixture(repo, env, "Add version and fragment 5h ago", now - 5 * 3600)
    _fixture_git(repo, env, "checkout", "-q", "-b", pr_name, base_name)
    (repo / "meson.build").write_text(
        "project('p', 'c', version: '0.2.0')\n", encoding="utf-8"
    )
    _fixture_git(repo, env, "rm", "-q", f"changelog.d/added/{frag_name}")
    cl = repo / "CHANGELOG.md"
    cl.write_text(
        cl.read_text(encoding="utf-8").replace(
            "## [0.1.0]",
            "## [0.2.0] - 2026-02-01\n\n### Added\n\n- Released.\n\n## [0.1.0]",
        ),
        encoding="utf-8",
    )
    _fixture_render(repo, env, script_dest)


def _release_cut_deleting(
    repo: pathlib.Path,
    env: dict[str, str],
    script_dest: pathlib.Path,
    now: int,
    tag: str,
    victim: str,
) -> tuple[int, str, str]:
    """Set up and run check_pull_request on a release cut deleting victim."""
    _setup_release_pr(
        repo,
        env,
        f"base-{tag}",
        f"pr-{tag}",
        f"{tag}.md",
        script_dest,
        now,
        base_files={victim: "- Victim.\n"},
    )
    _fixture_git(repo, env, "rm", "-q", victim)
    _fixture_render(repo, env, script_dest)
    commit_in_fixture(repo, env, f"Release cut deleting {victim}", now)
    _fixture_git(repo, env, "checkout", "-q", "-b", f"merge-{tag}", f"base-{tag}")
    merge_in_fixture(repo, env, f"pr-{tag}", f"Merge release cut {tag}", now)
    return _run_captured(check_pull_request, repo, "HEAD", 20, env=env)


def _test_pr_p7(
    repo: pathlib.Path,
    env: dict[str, str],
    script_dest: pathlib.Path,
    now: int,
) -> int:
    """Test pull request case p7 (release shape deletions)."""
    _setup_release_pr(repo, env, "base-p7", "pr-p7", "p7.md", script_dest, now)
    commit_in_fixture(repo, env, "Release cut 0.2.0", now)
    _fixture_git(repo, env, "checkout", "-q", "-b", "merge-p7", "base-p7")
    merge_in_fixture(repo, env, "pr-p7", "Merge release cut", now)
    rc7, out7, _ = _run_captured(check_pull_request, repo, "HEAD", 20, env=env)
    if rc7 != 0 or "changelog render current" not in out7:
        print(f"self-test FAIL: p7 failed: rc={rc7}, out={out7!r}", file=sys.stderr)
        return 1
    print("self-test ok: p7 release shape fragments deleted -> rc 0")
    return 0


def _test_pr_p8(
    repo: pathlib.Path,
    env: dict[str, str],
    script_dest: pathlib.Path,
    now: int,
) -> int:
    """Test pull request case p8 (release shape with added fragment)."""
    _setup_release_pr(repo, env, "base-p8", "pr-p8", "p8.md", script_dest, now)
    (repo / "changelog.d/added/p8-new.md").write_text(
        "- Add new p8.\n", encoding="utf-8"
    )
    _fixture_render(repo, env, script_dest)
    commit_in_fixture(repo, env, "Release cut with added fragment", now)
    _fixture_git(repo, env, "checkout", "-q", "-b", "merge-p8", "base-p8")
    merge_in_fixture(repo, env, "pr-p8", "Merge release cut p8", now)
    if not is_tree_current(repo, "HEAD", env):
        print("self-test FAIL: p8 fixture merged tree not current", file=sys.stderr)
        return 1
    rc8, _, err8 = _run_captured(check_pull_request, repo, "HEAD", 20, env=env)
    if rc8 != 1 or "changelog.d/added/p8-new.md (A)" not in err8:
        print(f"self-test FAIL: p8 failed: rc={rc8}, err={err8!r}", file=sys.stderr)
        return 1
    print("self-test ok: p8 release shape fragment added -> rc 1")
    return 0


def _test_pr_p9(
    repo: pathlib.Path,
    env: dict[str, str],
    script_dest: pathlib.Path,
    now: int,
) -> int:
    """Test pull request case p9 (render PR with stale merge tree)."""
    _fixture_git(repo, env, "checkout", "-q", "-b", "base-p9", "master")
    (repo / "changelog.d/added/p9-old.md").write_text(
        "- Add p9 old.\n", encoding="utf-8"
    )
    commit_in_fixture(repo, env, "Add fragment 10h ago", now - 10 * 3600)
    _fixture_git(repo, env, "checkout", "-q", "-b", "pr-p9", "base-p9")
    _fixture_render(repo, env, script_dest)
    commit_in_fixture(repo, env, "Render changelog in pr-p9", now - 5 * 3600)
    _fixture_git(repo, env, "checkout", "-q", "base-p9")
    (repo / "changelog.d/added/p9-new.md").write_text(
        "- Add p9 new.\n", encoding="utf-8"
    )
    commit_in_fixture(repo, env, "Base advances with new fragment", now - 2 * 3600)
    _fixture_git(repo, env, "checkout", "-q", "-b", "merge-p9", "base-p9")
    merge_in_fixture(repo, env, "pr-p9", "Merge stale render PR", now)
    rc9, _, err9 = _run_captured(check_pull_request, repo, "HEAD", 20, env=env)
    if rc9 != 1 or "merge the default branch" not in err9:
        print(f"self-test FAIL: p9 failed: rc={rc9}, err={err9!r}", file=sys.stderr)
        return 1
    print("self-test ok: p9 render PR stale merge tree -> rc 1")
    return 0


def _test_pr_p10(repo: pathlib.Path, env: dict[str, str]) -> int:
    """Test pull request case p10 (root commit and single-parent refs)."""
    for ref in ("master", "base-p1"):
        rc10, _, err10 = _run_captured(check_pull_request, repo, ref, 20, env=env)
        if rc10 != 1 or "is not a merge commit" not in err10:
            print(
                f"self-test FAIL: p10 failed on {ref}: rc={rc10}, err={err10!r}",
                file=sys.stderr,
            )
            return 1
    print("self-test ok: p10 root commit and one-parent ref -> rc 1")
    return 0


def _test_pr_p11() -> int:
    """Test parser case p11 (diff status allow-list)."""
    expected_ok: list[tuple[str, list[tuple[str, str]]]] = [
        ("M\0CHANGELOG.md\0", [("M", "CHANGELOG.md")]),
        ("", []),
        ("T\0x\0", [("T", "x")]),
        ("M\0x \0", [("M", "x ")]),
        ("D\0a\0M\0b\0", [("D", "a"), ("M", "b")]),
    ]
    for text, expected in expected_ok:
        entries, err = _parse_diff_status(text)
        if entries != expected or err is not None:
            print(
                f"self-test FAIL: p11 {text!r} failed: {entries!r}, {err!r}",
                file=sys.stderr,
            )
            return 1

    expected_err = [
        "R100\0a.md\0b.md\0",
        "R\0x\0",
        "junk",
        "M\0\0",
        "M\0x",
    ]
    for text in expected_err:
        entries, err = _parse_diff_status(text)
        if entries is not None or err is None:
            print(
                f"self-test FAIL: p11 {text!r} expected error, got: {entries!r}",
                file=sys.stderr,
            )
            return 1

    print("self-test ok: p11 diff status allow-list")
    return 0


def _test_pr_p12(
    repo: pathlib.Path,
    env: dict[str, str],
    script_dest: pathlib.Path,
    now: int,
) -> int:
    """Test pull request case p12 (release cut with non-ASCII fragment name)."""
    _setup_release_pr(repo, env, "base-p12", "pr-p12", "p12-ä.md", script_dest, now)
    commit_in_fixture(repo, env, "Release cut 0.2.0 with non-ASCII fragment", now)
    _fixture_git(repo, env, "checkout", "-q", "-b", "merge-p12", "base-p12")
    merge_in_fixture(repo, env, "pr-p12", "Merge release cut p12", now)
    rc12, out12, _ = _run_captured(check_pull_request, repo, "HEAD", 20, env=env)
    if rc12 != 0 or "changelog render current" not in out12:
        print(f"self-test FAIL: p12 failed: rc={rc12}, out={out12!r}", file=sys.stderr)
        return 1
    print("self-test ok: p12 release shape non-ascii fragment deleted -> rc 0")
    return 0


def _test_pr_p13(
    repo: pathlib.Path,
    env: dict[str, str],
    script_dest: pathlib.Path,
    now: int,
) -> int:
    """Test pull request case p13 (Codacy: plain render deleting fragment and adding code)."""
    _fixture_git(repo, env, "checkout", "-q", "-b", "base-p13", "master")
    (repo / "changelog.d/added/base-p13.md").write_text(
        "- Add base.\n", encoding="utf-8"
    )
    (repo / "changelog.d/added/p13.md").write_text("- Add p13.\n", encoding="utf-8")
    commit_in_fixture(repo, env, "Add fragments 10h ago", now - 10 * 3600)
    _fixture_git(repo, env, "checkout", "-q", "-b", "pr-p13", "base-p13")
    _fixture_git(repo, env, "rm", "-q", "changelog.d/added/p13.md")
    src = repo / "src/p13.c"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("int p13(void) { return 0; }\n", encoding="utf-8")
    _fixture_render(repo, env, script_dest)
    commit_in_fixture(repo, env, "Delete fragment and add code in render PR", now)
    _fixture_git(repo, env, "checkout", "-q", "-b", "merge-p13", "base-p13")
    merge_in_fixture(repo, env, "pr-p13", "Merge pr-p13", now)
    rc13, _, err13 = _run_captured(check_pull_request, repo, "HEAD", 20, env=env)
    if (
        rc13 != 1
        or "changelog.d/added/p13.md (D)" not in err13
        or "src/p13.c (A)" not in err13
    ):
        print(f"self-test FAIL: p13 failed: rc={rc13}, err={err13!r}", file=sys.stderr)
        return 1
    print("self-test ok: p13 plain render deletes fragment and adds code -> rc 1")
    return 0


def _test_pr_p14(
    repo: pathlib.Path,
    env: dict[str, str],
    script_dest: pathlib.Path,
    now: int,
) -> int:
    """Test pull request case p14 (release cut that adds one code file and modifies another)."""
    old = "src/p14-old.c"
    _setup_release_pr(
        repo,
        env,
        "base-p14",
        "pr-p14",
        "p14.md",
        script_dest,
        now,
        base_files={old: "int p14_old(void) { return 0; }\n"},
    )
    (repo / old).write_text("int p14_old(void) { return 1; }\n", encoding="utf-8")
    (repo / "src/p14.c").write_text("int p14(void) { return 0; }\n", encoding="utf-8")
    commit_in_fixture(repo, env, "Release cut with code changes", now)
    _fixture_git(repo, env, "checkout", "-q", "-b", "merge-p14", "base-p14")
    merge_in_fixture(repo, env, "pr-p14", "Merge release cut p14", now)
    rc14, _, err14 = _run_captured(check_pull_request, repo, "HEAD", 20, env=env)
    if rc14 != 1 or "src/p14.c (A)" not in err14 or f"{old} (M)" not in err14:
        print(f"self-test FAIL: p14 failed: rc={rc14}, err={err14!r}", file=sys.stderr)
        return 1
    print("self-test ok: p14 release cut with code files (A and M) -> rc 1")
    return 0


def _test_pr_p15(
    repo: pathlib.Path,
    env: dict[str, str],
    script_dest: pathlib.Path,
    now: int,
) -> int:
    """Test pull request case p15 (release cut without fragment deletion: v0.4.0 final shape)."""
    _fixture_git(repo, env, "checkout", "-q", "-b", "base-p15", "master")
    (repo / "meson.build").write_text(
        "project('p', 'c', version: '0.1.0')\n", encoding="utf-8"
    )
    (repo / "README.md").write_text("# Project 0.1.0\n", encoding="utf-8")
    roadmap = repo / "docs/roadmap.md"
    roadmap.parent.mkdir(parents=True, exist_ok=True)
    roadmap.write_text("# Roadmap 0.1.0\n", encoding="utf-8")
    commit_in_fixture(repo, env, "Commit release files on base", now - 5 * 3600)
    _fixture_git(repo, env, "checkout", "-q", "-b", "pr-p15", "base-p15")
    (repo / "meson.build").write_text(
        "project('p', 'c', version: '0.2.0')\n", encoding="utf-8"
    )
    (repo / "README.md").write_text("# Project 0.2.0\n", encoding="utf-8")
    roadmap.write_text("# Roadmap 0.2.0\n", encoding="utf-8")
    cl = repo / "CHANGELOG.md"
    cl.write_text(
        cl.read_text(encoding="utf-8").replace(
            "## [0.1.0]",
            "## [0.2.0] - 2026-02-01\n\n### Added\n\n- Final cut.\n\n## [0.1.0]",
        ),
        encoding="utf-8",
    )
    _fixture_render(repo, env, script_dest)
    commit_in_fixture(repo, env, "Final release cut v0.2.0", now)
    _fixture_git(repo, env, "checkout", "-q", "-b", "merge-p15", "base-p15")
    merge_in_fixture(repo, env, "pr-p15", "Merge final release cut p15", now)
    rc15, out15, _ = _run_captured(check_pull_request, repo, "HEAD", 20, env=env)
    if rc15 != 0 or "changelog render current" not in out15:
        print(f"self-test FAIL: p15 failed: rc={rc15}, out={out15!r}", file=sys.stderr)
        return 1
    print("self-test ok: p15 release cut without fragment deletion -> rc 0")
    return 0


def _test_pr_p16(
    repo: pathlib.Path,
    env: dict[str, str],
    script_dest: pathlib.Path,
    now: int,
) -> int:
    """Test pull request case p16 (release cut deleting a release file)."""
    _fixture_git(repo, env, "checkout", "-q", "-b", "base-p16", "master")
    (repo / "AGENTS.md").write_text("# Agents\n", encoding="utf-8")
    (repo / "meson.build").write_text(
        "project('p', 'c', version: '0.1.0')\n", encoding="utf-8"
    )
    (repo / "changelog.d/added/p16.md").write_text("- Add p16.\n", encoding="utf-8")
    commit_in_fixture(
        repo, env, "Commit release files and fragment on base", now - 5 * 3600
    )
    _fixture_git(repo, env, "checkout", "-q", "-b", "pr-p16", "base-p16")
    (repo / "meson.build").write_text(
        "project('p', 'c', version: '0.2.0')\n", encoding="utf-8"
    )
    _fixture_git(repo, env, "rm", "-q", "changelog.d/added/p16.md")
    _fixture_git(repo, env, "rm", "-q", "AGENTS.md")
    cl = repo / "CHANGELOG.md"
    cl.write_text(
        cl.read_text(encoding="utf-8").replace(
            "## [0.1.0]",
            "## [0.2.0] - 2026-02-01\n\n### Added\n\n- Released.\n\n## [0.1.0]",
        ),
        encoding="utf-8",
    )
    _fixture_render(repo, env, script_dest)
    commit_in_fixture(repo, env, "Release cut deleting AGENTS.md", now)
    _fixture_git(repo, env, "checkout", "-q", "-b", "merge-p16", "base-p16")
    merge_in_fixture(repo, env, "pr-p16", "Merge release cut p16", now)
    rc16, _, err16 = _run_captured(check_pull_request, repo, "HEAD", 20, env=env)
    if rc16 != 1 or "AGENTS.md (D)" not in err16:
        print(f"self-test FAIL: p16 failed: rc={rc16}, err={err16!r}", file=sys.stderr)
        return 1
    print("self-test ok: p16 release cut deleting release file -> rc 1")
    return 0


def _test_pr_p17(repo_root: pathlib.Path) -> int:
    """Test pull request case p17 (verify release allow-lists against real repository)."""
    if not repo_root.is_dir() or not (repo_root / ".git").exists():
        print(
            f"self-test FAIL: p17 repository root not found or not a git repo: {repo_root}",
            file=sys.stderr,
        )
        return 1
    try:
        tracked_out = run_git_log(repo_root, ["-z"], subcommand="ls-files")
    except (RuntimeError, OSError) as exc:
        print(f"self-test FAIL: p17 git ls-files failed: {exc}", file=sys.stderr)
        return 1
    tracked_files = {path for path in tracked_out.split("\0") if path}
    try:
        version_sites = _load_version_sites()
    except RuntimeError as exc:
        print(f"self-test FAIL: p17 {exc}", file=sys.stderr)
        return 1
    all_release_files = CONTEXT_PROJECTIONS | RELEASE_DOCS | set(version_sites)
    for path in sorted(all_release_files):
        if path not in tracked_files:
            print(
                f"self-test FAIL: p17 release file not tracked in git: {path}",
                file=sys.stderr,
            )
            return 1
    marker = "Compiled automatically by " + "praetorctl compile-context"
    for rel_path in sorted(tracked_files):
        if rel_path == "scripts/release/check-changelog-staleness.py":
            continue
        full_path = repo_root / rel_path
        if not full_path.is_file():
            continue
        try:
            content = full_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            print(f"self-test FAIL: p17 cannot read {rel_path}: {exc}", file=sys.stderr)
            return 1
        if marker in content and rel_path not in CONTEXT_PROJECTIONS:
            print(
                f"self-test FAIL: p17 compiled context file not in CONTEXT_PROJECTIONS: {rel_path}",
                file=sys.stderr,
            )
            return 1
    print("self-test ok: p17 release allow-lists match tracked repository files")
    return 0


def _test_pr_p18(
    repo: pathlib.Path,
    env: dict[str, str],
    now: int,
) -> int:
    """Test pull request case p18 (base owes 21 h, non-render PR deletes owed fragment)."""
    _fixture_git(repo, env, "checkout", "-q", "-b", "base-p18", "master")
    (repo / "changelog.d/added/p18-base.md").write_text(
        "- Base p18.\n", encoding="utf-8"
    )
    commit_in_fixture(repo, env, "Add base p18 21h ago", now - 21 * 3600)
    _fixture_git(repo, env, "checkout", "-q", "-b", "pr-p18", "base-p18")
    _fixture_git(repo, env, "rm", "-q", "changelog.d/added/p18-base.md")
    commit_in_fixture(repo, env, "Delete owed base fragment in PR", now)
    _fixture_git(repo, env, "checkout", "-q", "-b", "merge-p18", "base-p18")
    merge_in_fixture(repo, env, "pr-p18", "Merge pr-p18 into base-p18", now)
    rc18, _, err18 = _run_captured(check_pull_request, repo, "HEAD", 20, env=env)
    if (
        rc18 != 1
        or "limit 20 h" not in err18
        or "land the render PR first" not in err18
    ):
        print(f"self-test FAIL: p18 failed: rc={rc18}, err={err18!r}", file=sys.stderr)
        return 1
    print(
        "self-test ok: p18 base owes 21 h, non-render PR deletes owed fragment -> rc 1"
    )
    return 0


def _test_pr_p19(
    repo: pathlib.Path,
    env: dict[str, str],
    now: int,
) -> int:
    """Test pull request case p19 (base owes 19 h, non-render PR deletes owed fragment)."""
    _fixture_git(repo, env, "checkout", "-q", "-b", "base-p19", "master")
    (repo / "changelog.d/added/p19-base.md").write_text(
        "- Base p19.\n", encoding="utf-8"
    )
    commit_in_fixture(repo, env, "Add base p19 19h ago", now - 19 * 3600)
    _fixture_git(repo, env, "checkout", "-q", "-b", "pr-p19", "base-p19")
    _fixture_git(repo, env, "rm", "-q", "changelog.d/added/p19-base.md")
    commit_in_fixture(repo, env, "Delete owed base fragment in PR", now)
    _fixture_git(repo, env, "checkout", "-q", "-b", "merge-p19", "base-p19")
    merge_in_fixture(repo, env, "pr-p19", "Merge pr-p19 into base-p19", now)
    rc19, out19, _ = _run_captured(check_pull_request, repo, "HEAD", 20, env=env)
    if (
        rc19 != 0
        or "warning: render owed since" not in out19
        or "changelog render current" in out19
    ):
        print(f"self-test FAIL: p19 failed: rc={rc19}, out={out19!r}", file=sys.stderr)
        return 1
    print(
        "self-test ok: p19 base owes 19 h, non-render PR deletes owed fragment -> rc 0"
    )
    return 0


def _test_pr_p20(
    repo: pathlib.Path,
    env: dict[str, str],
    now: int,
) -> int:
    """Test pull request case p20 (base renders current, non-render PR adds code only)."""
    _fixture_git(repo, env, "checkout", "-q", "-b", "base-p20", "master")
    _fixture_git(repo, env, "checkout", "-q", "-b", "pr-p20", "base-p20")
    src = repo / "src/p20.c"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("int p20(void) { return 0; }\n", encoding="utf-8")
    commit_in_fixture(repo, env, "Add code only in PR", now)
    _fixture_git(repo, env, "checkout", "-q", "-b", "merge-p20", "base-p20")
    merge_in_fixture(repo, env, "pr-p20", "Merge pr-p20 into base-p20", now)
    rc20, out20, _ = _run_captured(check_pull_request, repo, "HEAD", 20, env=env)
    if rc20 != 0 or "changelog render current" not in out20:
        print(f"self-test FAIL: p20 failed: rc={rc20}, out={out20!r}", file=sys.stderr)
        return 1
    print("self-test ok: p20 base current, non-render PR adds code only -> rc 0")
    return 0


def _test_pr_p21(
    repo: pathlib.Path,
    env: dict[str, str],
    script_dest: pathlib.Path,
    now: int,
) -> int:
    """Test pull request case p21 (release cut deleting changelog.d/README.md)."""
    rc21, _, err21 = _release_cut_deleting(
        repo, env, script_dest, now, "p21", "changelog.d/README.md"
    )
    if rc21 != 1 or "changelog.d/README.md (D)" not in err21:
        print(f"self-test FAIL: p21 failed: rc={rc21}, err={err21!r}", file=sys.stderr)
        return 1
    print("self-test ok: p21 release cut deletes changelog.d/README.md -> rc 1")
    return 0


def _test_pr_p22(
    repo: pathlib.Path,
    env: dict[str, str],
    script_dest: pathlib.Path,
    now: int,
) -> int:
    """Test pull request case p22 (release cut deleting unknown section fragment)."""
    rc22, _, err22 = _release_cut_deleting(
        repo, env, script_dest, now, "p22", "changelog.d/unknown/p22.md"
    )
    if rc22 != 1 or "changelog.d/unknown/p22.md (D)" not in err22:
        print(f"self-test FAIL: p22 failed: rc={rc22}, err={err22!r}", file=sys.stderr)
        return 1
    print("self-test ok: p22 release cut deletes unknown section fragment -> rc 1")
    return 0


def _test_pr_p23(
    repo: pathlib.Path,
    env: dict[str, str],
    script_dest: pathlib.Path,
    now: int,
) -> int:
    """Test pull request case p23 (release cut deleting nested fragment)."""
    rc23, _, err23 = _release_cut_deleting(
        repo, env, script_dest, now, "p23", "changelog.d/added/sub/p23.md"
    )
    if rc23 != 1 or "changelog.d/added/sub/p23.md (D)" not in err23:
        print(f"self-test FAIL: p23 failed: rc={rc23}, err={err23!r}", file=sys.stderr)
        return 1
    print("self-test ok: p23 release cut deletes nested fragment -> rc 1")
    return 0


def _test_pr_p24(
    repo: pathlib.Path,
    env: dict[str, str],
    script_dest: pathlib.Path,
    now: int,
) -> int:
    """Test pull request case p24 (release cut deleting non-markdown file)."""
    rc24, _, err24 = _release_cut_deleting(
        repo, env, script_dest, now, "p24", "changelog.d/added/.gitkeep"
    )
    if rc24 != 1 or "changelog.d/added/.gitkeep (D)" not in err24:
        print(f"self-test FAIL: p24 failed: rc={rc24}, err={err24!r}", file=sys.stderr)
        return 1
    print("self-test ok: p24 release cut deletes non-markdown file -> rc 1")
    return 0


def _test_pr_p25(
    repo: pathlib.Path,
    env: dict[str, str],
    script_dest: pathlib.Path,
    now: int,
) -> int:
    """Test pull request case p25 (release cut deleting maintenance fragment)."""
    rc25, out25, _ = _release_cut_deleting(
        repo, env, script_dest, now, "p25", "changelog.d/maintenance/p25.md"
    )
    if rc25 != 0 or "changelog render current" not in out25:
        print(f"self-test FAIL: p25 failed: rc={rc25}, out={out25!r}", file=sys.stderr)
        return 1
    print("self-test ok: p25 release cut deletes maintenance fragment -> rc 0")
    return 0


def _sections_loader_refuses(path: pathlib.Path) -> bool:
    """Return True when the sections loader fails closed (RuntimeError) on path."""
    try:
        _load_changelog_sections(path)
    except RuntimeError:
        return True
    except UnicodeDecodeError:
        return False
    return False


def _test_pr_p26() -> int:
    """Test pull request case p26 (sections loader on real script and invalid files)."""
    try:
        real_sections = _load_changelog_sections()
    except RuntimeError as exc:
        print(f"self-test FAIL: p26 real script: {exc}", file=sys.stderr)
        return 1
    expected = (
        "added",
        "changed",
        "deprecated",
        "removed",
        "fixed",
        "security",
        "maintenance",
    )
    if real_sections != expected:
        print(
            f"self-test FAIL: p26 real sections mismatch: {real_sections!r} != {expected!r}",
            file=sys.stderr,
        )
        return 1
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        bad_cases = [
            ("no_sections", b"#!/bin/bash\necho hi\n"),
            ("two_sections", b"SECTIONS=(added)\nSECTIONS=(fixed)\n"),
            ("invalid_syntax", b"SECTIONS=(added $(x))\n"),
            ("not_utf8", b"SECTIONS=(added)\n\xff\n"),
        ]
        for name, content in bad_cases:
            (tmp / f"{name}.sh").write_bytes(content)
        for name in [name for name, _ in bad_cases] + ["missing"]:
            if not _sections_loader_refuses(tmp / f"{name}.sh"):
                print(
                    f"self-test FAIL: p26 {name} did not fail closed", file=sys.stderr
                )
                return 1

    print("self-test ok: p26 changelog sections loader -> ok")
    return 0


def _test_pr_p27(
    repo: pathlib.Path,
    env: dict[str, str],
    script_dest: pathlib.Path,
    now: int,
) -> int:
    """Test pull request case p27 (release cut deleting section-named path outside changelog.d/)."""
    rc27, _, err27 = _release_cut_deleting(
        repo, env, script_dest, now, "p27", "docs/added/p27.md"
    )
    if rc27 != 1 or "docs/added/p27.md (D)" not in err27:
        print(f"self-test FAIL: p27 failed: rc={rc27}, err={err27!r}", file=sys.stderr)
        return 1
    print(
        "self-test ok: p27 release cut deletes section-named path outside changelog.d -> rc 1"
    )
    return 0


def _merge_fragment_pr(
    repo: pathlib.Path,
    env: dict[str, str],
    case: str,
    now: int,
) -> tuple[int, str]:
    """Merge a fragment-only pull request into base-<case>; return the PR check rc and stderr."""
    _fixture_git(repo, env, "checkout", "-q", "-b", f"pr-{case}", f"base-{case}")
    (repo / f"changelog.d/added/{case}-pr.md").write_text(
        f"- PR {case}.\n", encoding="utf-8"
    )
    commit_in_fixture(repo, env, f"Add pr {case}", now)
    _fixture_git(repo, env, "checkout", "-q", "-b", f"merge-{case}", f"base-{case}")
    merge_in_fixture(repo, env, f"pr-{case}", f"Merge pr-{case} into base-{case}", now)
    rc, _, err = _run_captured(check_pull_request, repo, "HEAD", 20, env=env)
    return rc, err


def _test_pr_p28(
    repo: pathlib.Path,
    env: dict[str, str],
    now: int,
) -> int:
    """Test pull request case p28 (base hand-edited 1 h ago, fragment since: fails at once)."""
    _fixture_git(repo, env, "checkout", "-q", "-b", "base-p28", "master")
    _hand_edit_fixture_changelog(repo, "Hand edited entry in p28.")
    commit_in_fixture(repo, env, "Hand edit CHANGELOG for p28", now - 3600)
    (repo / "changelog.d/added/p28-base.md").write_text(
        "- Base p28.\n", encoding="utf-8"
    )
    commit_in_fixture(repo, env, "Add base p28 1h ago", now - 3600)
    rc28, err28 = _merge_fragment_pr(repo, env, "p28", now)
    if rc28 != 1 or "hand-edited or stale" not in err28:
        print(f"self-test FAIL: p28 failed: rc={rc28}, err={err28!r}", file=sys.stderr)
        return 1
    print("self-test ok: p28 base hand-edited 1 h ago -> rc 1 at once")
    return 0


def _test_pr_p29(
    repo: pathlib.Path,
    env: dict[str, str],
    now: int,
) -> int:
    """Test pull request case p29 (base changed the render script, no fragment since: fails at once)."""
    _fixture_git(repo, env, "checkout", "-q", "-b", "base-p29", "master")
    _change_fixture_render_script(repo, "_Nothing unreleased in p29._")
    commit_in_fixture(repo, env, "Change the render script for p29", now)
    rc29, err29 = _merge_fragment_pr(repo, env, "p29", now)
    if rc29 != 1 or "no changelog.d/ commits found" not in err29:
        print(f"self-test FAIL: p29 failed: rc={rc29}, err={err29!r}", file=sys.stderr)
        return 1
    print("self-test ok: p29 base render script changed, no fragment -> rc 1 at once")
    return 0


def _run_pr_self_tests(
    repo: pathlib.Path,
    env: dict[str, str],
    script_dest: pathlib.Path,
    now: int,
    repo_root: pathlib.Path,
) -> int:
    """Run pull request self-test cases p1 through p29."""
    cases: list[Callable[[], int]] = [
        lambda: _test_pr_p1(repo, env, now),
        lambda: _test_pr_p2(repo, env, now),
        lambda: _test_pr_p3(repo, env, script_dest, now),
        lambda: _test_pr_p4(repo, env, now),
        lambda: _test_pr_p5(repo, env, script_dest, now),
        lambda: _test_pr_p6(repo, env, script_dest, now),
        lambda: _test_pr_p7(repo, env, script_dest, now),
        lambda: _test_pr_p8(repo, env, script_dest, now),
        lambda: _test_pr_p9(repo, env, script_dest, now),
        lambda: _test_pr_p10(repo, env),
        _test_pr_p11,
        lambda: _test_pr_p12(repo, env, script_dest, now),
        lambda: _test_pr_p13(repo, env, script_dest, now),
        lambda: _test_pr_p14(repo, env, script_dest, now),
        lambda: _test_pr_p15(repo, env, script_dest, now),
        lambda: _test_pr_p16(repo, env, script_dest, now),
        lambda: _test_pr_p17(repo_root),
        lambda: _test_pr_p18(repo, env, now),
        lambda: _test_pr_p19(repo, env, now),
        lambda: _test_pr_p20(repo, env, now),
        lambda: _test_pr_p21(repo, env, script_dest, now),
        lambda: _test_pr_p22(repo, env, script_dest, now),
        lambda: _test_pr_p23(repo, env, script_dest, now),
        lambda: _test_pr_p24(repo, env, script_dest, now),
        lambda: _test_pr_p25(repo, env, script_dest, now),
        _test_pr_p26,
        lambda: _test_pr_p27(repo, env, script_dest, now),
        lambda: _test_pr_p28(repo, env, now),
        lambda: _test_pr_p29(repo, env, now),
    ]
    for case in cases:
        if case() != 0:
            return 1
    return 0


def run_self_test(repo_root: pathlib.Path) -> int:
    """Run self-test suite in an isolated temporary git repository."""
    script_source = repo_root / "scripts/release/concat-changelog-fragments.sh"
    if not script_source.is_file():
        print(f"self-test: missing render script at {script_source}", file=sys.stderr)
        return 1

    now = int(time.time())
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = pathlib.Path(tmp_str)
        env = make_test_env(tmp)
        repo = tmp / "fixture"
        script_dest = init_fixture_repo(repo, env, script_source, now)

        # case a: current render -> 0
        if check_staleness(repo, "master", 24, env=env) != 0:
            print("self-test FAIL: case a returned non-zero, want 0", file=sys.stderr)
            return 1

        if _test_cases_b_c_d(repo, env, now) != 0:
            return 1

        if _test_cases_readme_boundary(repo, env, now) != 0:
            return 1

        if _test_cases_g_h(repo, env, now) != 0:
            return 1

        # case e: after (c), render-only commit -> 0
        _fixture_git(repo, env, "checkout", "-q", "branch-c")
        _fixture_render(repo, env, script_dest)
        commit_in_fixture(repo, env, "Render changelog", now)
        if check_staleness(repo, "branch-c", 24, env=env) != 0:
            print("self-test FAIL: case e returned non-zero, want 0", file=sys.stderr)
            return 1

        if _test_case_f(repo, env) != 0:
            return 1

        if _run_pr_self_tests(repo, env, script_dest, now, repo_root) != 0:
            return 1

        if _run_decide_self_tests(repo, env, now) != 0:
            return 1

    print("self-test passed")
    return 0


def _run_decide(
    repo: pathlib.Path,
    ref: str,
    open_after_hours: int,
    env: dict[str, str],
    now: int | None = None,
) -> tuple[int, list[str]]:
    """Helper to run decide_render_pr and capture stdout lines."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = decide_render_pr(repo, ref, open_after_hours, env=env, now=now)
    return rc, buf.getvalue().splitlines()


def _test_decide_d1(repo: pathlib.Path, env: dict[str, str], now: int) -> int:
    """Test case d1: owed 7 h -> open=true."""
    _fixture_git(repo, env, "checkout", "-q", "-b", "branch-d1", "master")
    (repo / "changelog.d/added/0001-d1.md").write_text("- Add d1.\n", encoding="utf-8")
    commit_in_fixture(repo, env, "Add fragment 7h ago", now - 7 * 3600)
    rc, lines = _run_decide(repo, "branch-d1", 6, env, now=now)
    sha = run_git_log(repo, ["-1", "--format=%H", "branch-d1"], env=env)
    expected = ["current=false", "open=true", f"owed_since={sha}", "owed_hours=7.0"]
    if rc != 0 or lines != expected:
        print(f"self-test FAIL: d1 failed: rc={rc}, lines={lines!r}", file=sys.stderr)
        return 1
    print("self-test ok: d1 owed 7 h -> open=true")
    return 0


def _test_decide_d2(repo: pathlib.Path, env: dict[str, str], now: int) -> int:
    """Test case d2: owed exactly 6 h (commit at now-6*3600) -> open=true."""
    _fixture_git(repo, env, "checkout", "-q", "-b", "branch-d2", "master")
    (repo / "changelog.d/added/0001-d2.md").write_text("- Add d2.\n", encoding="utf-8")
    commit_in_fixture(repo, env, "Add fragment 6h ago", now - 6 * 3600)
    rc, lines = _run_decide(repo, "branch-d2", 6, env, now=now)
    sha = run_git_log(repo, ["-1", "--format=%H", "branch-d2"], env=env)
    expected = ["current=false", "open=true", f"owed_since={sha}", "owed_hours=6.0"]
    if rc != 0 or lines != expected:
        print(f"self-test FAIL: d2 failed: rc={rc}, lines={lines!r}", file=sys.stderr)
        return 1
    print("self-test ok: d2 owed exactly 6 h -> open=true")
    return 0


def _test_decide_d3(repo: pathlib.Path, env: dict[str, str], now: int) -> int:
    """Test case d3: owed 5.9 h -> open=false current=false."""
    _fixture_git(repo, env, "checkout", "-q", "-b", "branch-d3", "master")
    (repo / "changelog.d/added/0001-d3.md").write_text("- Add d3.\n", encoding="utf-8")
    commit_in_fixture(repo, env, "Add fragment 5.9h ago", now - int(5.9 * 3600))
    rc, lines = _run_decide(repo, "branch-d3", 6, env, now=now)
    sha = run_git_log(repo, ["-1", "--format=%H", "branch-d3"], env=env)
    expected = ["current=false", "open=false", f"owed_since={sha}", "owed_hours=5.9"]
    if rc != 0 or lines != expected:
        print(f"self-test FAIL: d3 failed: rc={rc}, lines={lines!r}", file=sys.stderr)
        return 1
    print("self-test ok: d3 owed 5.9 h -> open=false current=false")
    return 0


def _test_decide_d4(repo: pathlib.Path, env: dict[str, str]) -> int:
    """Test case d4: base current -> current=true open=false empty owed_*."""
    rc, lines = _run_decide(repo, "master", 6, env)
    expected = ["current=true", "open=false", "owed_since=", "owed_hours="]
    if rc != 0 or lines != expected:
        print(f"self-test FAIL: d4 failed: rc={rc}, lines={lines!r}", file=sys.stderr)
        return 1
    print("self-test ok: d4 base current -> current=true open=false empty owed_*")
    return 0


def _test_decide_d5(repo: pathlib.Path, env: dict[str, str], now: int) -> int:
    """Test case d5: hand-edited block -> open=true."""
    _fixture_git(repo, env, "checkout", "-q", "-b", "branch-d5", "master")
    cl = repo / "CHANGELOG.md"
    cl_text = cl.read_text(encoding="utf-8")
    cl.write_text(
        cl_text.replace("_No unreleased changes._", "Hand edited invalid entry in d5."),
        encoding="utf-8",
    )
    commit_in_fixture(repo, env, "Hand edit CHANGELOG for d5", now)
    rc, lines = _run_decide(repo, "branch-d5", 6, env, now=now)
    sha = run_git_log(repo, ["-1", "--format=%H", "branch-d5"], env=env)
    expected = ["current=false", "open=true", f"owed_since={sha}", "owed_hours=0.0"]
    if rc != 0 or lines != expected:
        print(f"self-test FAIL: d5 failed: rc={rc}, lines={lines!r}", file=sys.stderr)
        return 1
    print("self-test ok: d5 hand-edited block -> open=true")
    return 0


def _test_decide_d6(repo: pathlib.Path) -> int:
    """Test case d6: --open-after-hours 0 -> argparse rc 2."""
    buf_err = io.StringIO()
    buf_out = io.StringIO()
    rc = 0
    try:
        with contextlib.redirect_stderr(buf_err), contextlib.redirect_stdout(buf_out):
            main(["--open-after-hours", "0", "--repo", str(repo)])
    except SystemExit as exc:
        rc = exc.code if isinstance(exc.code, int) else 1
    if rc != 2:
        print(f"self-test FAIL: d6 failed: rc={rc}, want 2", file=sys.stderr)
        return 1
    print("self-test ok: d6 --open-after-hours 0 -> argparse rc 2")
    return 0


def _test_decide_d7(repo: pathlib.Path, env: dict[str, str], now: int) -> int:
    """Test case d7: render script changed, no new fragment -> open=true at once."""
    _fixture_git(repo, env, "checkout", "-q", "-b", "branch-d7", "master")
    base = run_git_log(repo, ["-1", "--format=%H", "master"], env=env)
    _change_fixture_render_script(repo, "_Nothing unreleased yet._")
    commit_in_fixture(repo, env, "Change the render script for d7", now)
    rc, lines = _run_decide(repo, "branch-d7", 6, env, now=now)
    expected = ["current=false", "open=true", f"owed_since={base}", "owed_hours=50.0"]
    if rc != 0 or lines != expected:
        print(f"self-test FAIL: d7 failed: rc={rc}, lines={lines!r}", file=sys.stderr)
        return 1
    print("self-test ok: d7 render script changed, no fragment -> open=true")
    return 0


def _run_decide_self_tests(
    repo: pathlib.Path,
    env: dict[str, str],
    now: int,
) -> int:
    """Run scheduled render PR decide self-test cases d1 through d7."""
    cases: list[Callable[[], int]] = [
        lambda: _test_decide_d1(repo, env, now),
        lambda: _test_decide_d2(repo, env, now),
        lambda: _test_decide_d3(repo, env, now),
        lambda: _test_decide_d4(repo, env),
        lambda: _test_decide_d5(repo, env, now),
        lambda: _test_decide_d6(repo),
        lambda: _test_decide_d7(repo, env, now),
    ]
    for case in cases:
        if case() != 0:
            return 1
    return 0


def _positive_int(val: str) -> int:
    """Validate positive integer for command-line arguments."""
    try:
        ival = int(val)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{val!r} is not an integer") from exc
    if ival < 1:
        raise argparse.ArgumentTypeError(f"{val} must be >= 1")
    return ival


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    default_repo = pathlib.Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(
        description="Check changelog render staleness against git history."
    )
    parser.add_argument(
        "--ref", default="HEAD", help="git reference to check (default: HEAD)"
    )
    parser.add_argument(
        "--max-age-hours",
        type=int,
        default=24,
        help="maximum allowed age in hours for unrendered fragments (default: 24)",
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--pull-request",
        action="store_true",
        help="check pull request merge commit against its first parent",
    )
    mode_group.add_argument(
        "--open-after-hours",
        type=_positive_int,
        default=None,
        metavar="H",
        help="decide whether a scheduled changelog render pull request is due after H hours",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="run self-test suite in an isolated fixture repository",
    )
    parser.add_argument(
        "--repo",
        type=pathlib.Path,
        default=default_repo,
        help="path to repository root (default: repository root containing script)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Main entrypoint for check-changelog-staleness."""
    args = parse_args(argv)
    if args.self_test:
        return run_self_test(args.repo)
    if args.open_after_hours is not None:
        return decide_render_pr(args.repo, args.ref, args.open_after_hours)
    if args.pull_request:
        return check_pull_request(args.repo, args.ref, args.max_age_hours)
    return check_staleness(args.repo, args.ref, args.max_age_hours)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
