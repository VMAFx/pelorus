#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Check that CHANGELOG.md is rendered or at most 24 h stale from fragments.

Usage:
    check-changelog-staleness.py [--ref REF] [--max-age-hours HOURS] [--repo REPO]
    check-changelog-staleness.py --self-test
"""

from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import subprocess  # nosec B404 - fixed git argv, no shell, explicit deadline
import sys
import tempfile
import time

SUBPROCESS_TIMEOUT_S = 120


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
            ["git", "-C", str(repo), "checkout-index", "-a", "-f", f"--prefix={prefix}"],
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
) -> str:
    """Run git log with given args in repo and return stripped stdout."""
    cmd = ["git", "-C", str(repo), "log", *args]
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
            f"git log {' '.join(args)} failed in {repo}: {err}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"git log {' '.join(args)} timed out in {repo} after {SUBPROCESS_TIMEOUT_S}s"
        ) from exc


def judge_owed_render(
    repo: pathlib.Path,
    sha: str,
    ref: str,
    max_age_hours: int,
    env: dict[str, str] | None = None,
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
            f"render owed for {age_hours} h since {first_sha} (limit {max_age_hours} h): "
            "land a render-only pull request",
            file=sys.stderr,
        )
        return 1

    print(f"warning: render owed since {first_sha} ({age_hours} h)")
    return 0


def check_staleness(
    repo: pathlib.Path,
    ref: str,
    max_age_hours: int,
    env: dict[str, str] | None = None,
) -> int:
    """Check changelog render status and staleness against git history."""
    with tempfile.TemporaryDirectory() as tmp_a:
        extract_tree_at_ref(repo, ref, pathlib.Path(tmp_a), env=env)
        if run_fragment_check(pathlib.Path(tmp_a), env=env) == 0:
            print("changelog render current")
            return 0

    sha = run_git_log(
        repo,
        ["--first-parent", "-1", "--format=%H", ref, "--", "CHANGELOG.md"],
        env=env,
    )
    if not sha:
        print(f"error: no commits touching CHANGELOG.md found in {ref}", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory() as tmp_sha:
        extract_tree_at_ref(repo, sha, pathlib.Path(tmp_sha), env=env)
        if run_fragment_check(pathlib.Path(tmp_sha), env=env) != 0:
            print(
                f"CHANGELOG.md hand-edited or stale at {sha}; render it with --write",
                file=sys.stderr,
            )
            return 1

    return judge_owed_render(repo, sha, ref, max_age_hours, env=env)


def make_test_env(tmp: pathlib.Path) -> dict[str, str]:
    """Create isolated environment for git commands in self-test."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["HOME"] = str(tmp)
    return env


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
    try:
        subprocess.run(  # nosec B603 - fixed git argv
            ["git", "-C", str(repo), "add", "."],
            env=c_env,
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_S,
            check=True,
        )
        subprocess.run(  # nosec B603 - fixed git argv
            [
                "git", "-C", str(repo),
                "-c", "user.name=t",
                "-c", "user.email=t@example.invalid",
                "-c", "commit.gpgsign=false",
                "commit", "-q", "-m", msg,
            ],
            env=c_env,
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_S,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        err = exc.stderr.strip()
        raise RuntimeError(f"commit failed in fixture {repo}: {err}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"commit timed out in fixture {repo}") from exc


def init_fixture_repo(
    repo: pathlib.Path,
    env: dict[str, str],
    script_source: pathlib.Path,
    now: int,
) -> pathlib.Path:
    """Initialize fixture git repository with minimal changelog and render script."""
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(  # nosec B603 - fixed git argv
        ["git", "-C", str(repo), "init", "-q", "-b", "master"],
        env=env,
        capture_output=True,
        text=True,
        timeout=SUBPROCESS_TIMEOUT_S,
        check=True,
    )
    script_dest = repo / "scripts/release/concat-changelog-fragments.sh"
    script_dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(script_source, script_dest)
    script_dest.chmod(0o755)

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

    subprocess.run(  # nosec B603 - fixed script argv
        ["bash", str(script_dest), "--write"],
        cwd=str(repo),
        env=env,
        capture_output=True,
        text=True,
        timeout=SUBPROCESS_TIMEOUT_S,
        check=True,
    )
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
    subprocess.run(  # nosec B603 - fixed git argv
        ["git", "-C", str(repo), "checkout", "-q", "-b", "branch-b", "master"],
        env=env, check=True, timeout=SUBPROCESS_TIMEOUT_S,
    )
    (repo / "changelog.d/added/0001-frag.md").write_text("- Add 1.\n", encoding="utf-8")
    commit_in_fixture(repo, env, "Add fragment 1h ago", now - 3600)
    if check_staleness(repo, "branch-b", 24, env=env) != 0:
        print("self-test FAIL: case b returned non-zero, want 0", file=sys.stderr)
        failed = 1

    # case c: fragment added 30 h ago, no render -> 1
    subprocess.run(  # nosec B603 - fixed git argv
        ["git", "-C", str(repo), "checkout", "-q", "-b", "branch-c", "master"],
        env=env, check=True, timeout=SUBPROCESS_TIMEOUT_S,
    )
    (repo / "changelog.d/added/0002-frag.md").write_text("- Add 2.\n", encoding="utf-8")
    commit_in_fixture(repo, env, "Add fragment 30h ago", now - 30 * 3600)
    if check_staleness(repo, "branch-c", 24, env=env) != 1:
        print("self-test FAIL: case c returned non-one, want 1", file=sys.stderr)
        failed = 1

    # case d: CHANGELOG.md hand-edited, 0 h old -> 1
    subprocess.run(  # nosec B603 - fixed git argv
        ["git", "-C", str(repo), "checkout", "-q", "-b", "branch-d", "master"],
        env=env, check=True, timeout=SUBPROCESS_TIMEOUT_S,
    )
    cl = repo / "CHANGELOG.md"
    cl_text = cl.read_text(encoding="utf-8")
    cl.write_text(cl_text.replace("_No unreleased changes._", "Hand edited invalid entry."), encoding="utf-8")
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
    subprocess.run(  # nosec B603 - fixed git argv
        ["git", "-C", str(repo), "checkout", "-q", "-b", "branch-readme", "master"],
        env=env,
        check=True,
        timeout=SUBPROCESS_TIMEOUT_S,
    )
    (repo / "changelog.d/README.md").write_text("# Updated README\n", encoding="utf-8")
    commit_in_fixture(repo, env, "Update changelog README 30h ago", now - 30 * 3600)
    if check_staleness(repo, "branch-readme", 24, env=env) != 0:
        print("self-test FAIL: README-only commit returned non-zero, want 0", file=sys.stderr)
        return 1

    # Add fragment 1 h ago on top of README edit: only fragment counts -> 0
    (repo / "changelog.d/added/0003-frag.md").write_text("- Add 3.\n", encoding="utf-8")
    commit_in_fixture(repo, env, "Add fragment 1h ago", now - 3600)
    if check_staleness(repo, "branch-readme", 24, env=env) != 0:
        print("self-test FAIL: fragment after README returned non-zero, want 0", file=sys.stderr)
        return 1

    # Boundary case: fragment at 24 h + 30 min (limit*3600 + 1800 s) -> 1
    subprocess.run(  # nosec B603 - fixed git argv
        ["git", "-C", str(repo), "checkout", "-q", "-b", "branch-boundary", "master"],
        env=env,
        check=True,
        timeout=SUBPROCESS_TIMEOUT_S,
    )
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
    tree_before = subprocess.run(  # nosec B603 - fixed git argv
        ["git", "-C", str(repo), "write-tree"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=SUBPROCESS_TIMEOUT_S,
    ).stdout.strip()
    index_bytes_before = (repo / ".git/index").read_bytes()
    with tempfile.TemporaryDirectory() as tmp_f:
        extract_tree_at_ref(repo, "master", pathlib.Path(tmp_f), env=env)
        if not (pathlib.Path(tmp_f) / "CHANGELOG.md").is_file():
            print(
                "self-test FAIL: case f extracted tree missing CHANGELOG.md",
                file=sys.stderr,
            )
            return 1
    tree_after = subprocess.run(  # nosec B603 - fixed git argv
        ["git", "-C", str(repo), "write-tree"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=SUBPROCESS_TIMEOUT_S,
    ).stdout.strip()
    index_bytes_after = (repo / ".git/index").read_bytes()
    if tree_before != tree_after or index_bytes_before != index_bytes_after:
        print(
            f"self-test FAIL: case f repo index modified by extract_tree_at_ref "
            f"({tree_before} != {tree_after})",
            file=sys.stderr,
        )
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
        subprocess.run(  # nosec B603 - fixed git argv
            ["git", "-C", str(repo), "checkout", "-q", "branch-c"],
            env=env, check=True, timeout=SUBPROCESS_TIMEOUT_S,
        )
        subprocess.run(  # nosec B603 - fixed script argv
            ["bash", str(script_dest), "--write"],
            cwd=str(repo), env=env, check=True, timeout=SUBPROCESS_TIMEOUT_S,
        )
        commit_in_fixture(repo, env, "Render changelog", now)
        if check_staleness(repo, "branch-c", 24, env=env) != 0:
            print("self-test FAIL: case e returned non-zero, want 0", file=sys.stderr)
            return 1

        if _test_case_f(repo, env) != 0:
            return 1

    print("self-test passed")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    default_repo = pathlib.Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(
        description="Check changelog render staleness against git history."
    )
    parser.add_argument("--ref", default="HEAD", help="git reference to check (default: HEAD)")
    parser.add_argument(
        "--max-age-hours",
        type=int,
        default=24,
        help="maximum allowed age in hours for unrendered fragments (default: 24)",
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
    return check_staleness(args.repo, args.ref, args.max_age_hours)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
