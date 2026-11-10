#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Check that CHANGELOG.md is rendered or at most 24 h stale from fragments.

Usage:
    check-changelog-staleness.py [--ref REF] [--max-age-hours HOURS] [--repo REPO]
    check-changelog-staleness.py --pull-request [--ref REF] [--max-age-hours HOURS] [--repo REPO]
    check-changelog-staleness.py --self-test
"""

from __future__ import annotations

import argparse
import contextlib
import io
import os
import pathlib
import shutil
import subprocess  # nosec B404 - fixed git argv, no shell, explicit deadline
import sys
import tempfile
import time
from typing import Any, Callable

SUBPROCESS_TIMEOUT_S = 120
VALID_DIFF_STATUSES = frozenset({"A", "D", "M", "T"})


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


def check_changelog_base(
    repo: pathlib.Path,
    ref: str,
    env: dict[str, str] | None = None,
) -> tuple[str | None, int]:
    """Find the last first-parent CHANGELOG.md commit on ref and verify it was not hand-edited."""
    sha = run_git_log(
        repo,
        ["--first-parent", "-1", "--format=%H", ref, "--", "CHANGELOG.md"],
        env=env,
    )
    if not sha:
        print(
            f"error: no commits touching CHANGELOG.md found in {ref}", file=sys.stderr
        )
        return None, 1

    if not is_tree_current(repo, sha, env=env):
        print(
            f"CHANGELOG.md hand-edited or stale at {sha}; render it with --write",
            file=sys.stderr,
        )
        return None, 1

    return sha, 0


def judge_owed_render(
    repo: pathlib.Path,
    sha: str,
    ref: str,
    max_age_hours: int,
    env: dict[str, str] | None = None,
    remedy: str = "land a render-only pull request",
) -> int:
    """Judge how long a render has been owed since the last rendered CHANGELOG.md."""
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
        print(
            f"render owed on {ref}, but no changelog.d/ commits found since {sha}",
            file=sys.stderr,
        )
        return 1

    first_line = lines[0].strip()
    first_sha, committer_time_str = first_line.split()[:2]
    committer_time = int(committer_time_str)
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

    has_fragment_deletions = any(
        path.startswith("changelog.d/") and status == "D"
        for status, path in diff_entries
    )
    offending: list[tuple[str, str]] = []
    for status, path in diff_entries:
        if path.startswith("changelog.d/"):
            if status != "D":
                offending.append((status, path))
        elif not has_fragment_deletions:
            if path != "CHANGELOG.md":
                offending.append((status, path))

    if offending:
        for status, path in offending:
            print(
                f"render pull request changes {path} ({status}): "
                "a render pull request carries CHANGELOG.md only "
                "(plus fragment deletions at a release cut); "
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

    # c. Merge tree current
    if is_tree_current(repo, ref, env=env):
        print("changelog render current")
        return 0

    # d. Render PR and merge tree not current
    if is_render_pr:
        print(
            "render pull request leaves CHANGELOG.md stale on the merged tree: "
            "merge the default branch and run concat-changelog-fragments.sh --write again",
            file=sys.stderr,
        )
        return 1

    # e. Else judge the base
    if is_tree_current(repo, base, env=env):
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
) -> None:
    """Set up a release PR branch with consumed fragment deleted and version updated."""
    _fixture_git(repo, env, "checkout", "-q", "-b", base_name, "master")
    (repo / f"changelog.d/added/{frag_name}").write_text("- Add.\n", encoding="utf-8")
    commit_in_fixture(repo, env, "Add fragment 5h ago", now - 5 * 3600)
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
    """Test pull request case p10 (single parent ref)."""
    rc10, _, err10 = _run_captured(check_pull_request, repo, "master", 20, env=env)
    if rc10 != 1 or "is not a merge commit" not in err10:
        print(f"self-test FAIL: p10 failed: rc={rc10}, err={err10!r}", file=sys.stderr)
        return 1
    print("self-test ok: p10 ref with one parent -> rc 1")
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


def _run_pr_self_tests(
    repo: pathlib.Path,
    env: dict[str, str],
    script_dest: pathlib.Path,
    now: int,
) -> int:
    """Run pull request self-test cases p1 through p12."""
    if _test_pr_p1(repo, env, now) != 0:
        return 1
    if _test_pr_p2(repo, env, now) != 0:
        return 1
    if _test_pr_p3(repo, env, script_dest, now) != 0:
        return 1
    if _test_pr_p4(repo, env, now) != 0:
        return 1
    if _test_pr_p5(repo, env, script_dest, now) != 0:
        return 1
    if _test_pr_p6(repo, env, script_dest, now) != 0:
        return 1
    if _test_pr_p7(repo, env, script_dest, now) != 0:
        return 1
    if _test_pr_p8(repo, env, script_dest, now) != 0:
        return 1
    if _test_pr_p9(repo, env, script_dest, now) != 0:
        return 1
    if _test_pr_p10(repo, env) != 0:
        return 1
    if _test_pr_p11() != 0:
        return 1
    if _test_pr_p12(repo, env, script_dest, now) != 0:
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

        # case e: after (c), render-only commit -> 0
        _fixture_git(repo, env, "checkout", "-q", "branch-c")
        _fixture_render(repo, env, script_dest)
        commit_in_fixture(repo, env, "Render changelog", now)
        if check_staleness(repo, "branch-c", 24, env=env) != 0:
            print("self-test FAIL: case e returned non-zero, want 0", file=sys.stderr)
            return 1

        if _test_case_f(repo, env) != 0:
            return 1

        if _run_pr_self_tests(repo, env, script_dest, now) != 0:
            return 1

    print("self-test passed")
    return 0


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
    parser.add_argument(
        "--pull-request",
        action="store_true",
        help="check pull request merge commit against its first parent",
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
    if args.pull_request:
        return check_pull_request(args.repo, args.ref, args.max_age_hours)
    return check_staleness(args.repo, args.ref, args.max_age_hours)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
