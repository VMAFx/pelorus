#!/usr/bin/env python3
"""Regression test: check-build-config.py --self-test must not touch the invoking repo.

Git hooks run with GIT_DIR, GIT_WORK_TREE and GIT_INDEX_FILE exported. When the
pre-push `make verify-all` ran the self-test, its fixture `git init`/`config`/
`commit` calls followed those variables into the developer's repository and left
core.bare=true, a core.hooksPath into /tmp, gpg.program=/bin/false, a fixture
user.* identity, fixture branches and moved branch refs behind.

This test points the same variables at a sacrificial repository, runs the
self-test, and fails if that repository's config, refs or worktree list changed.
A positive control (the self-test must still pass) keeps a broken harness from
looking like isolation.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
CHECKER = pathlib.Path(
    os.environ.get("BUILD_CONFIG_CHECKER", ROOT / "scripts" / "check-build-config.py")
)
GIT_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "isolation",
    "GIT_AUTHOR_EMAIL": "isolation@pelorus.invalid",
    "GIT_COMMITTER_NAME": "isolation",
    "GIT_COMMITTER_EMAIL": "isolation@pelorus.invalid",
}


def git(repo: pathlib.Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_ENV)
    done = subprocess.run(
        ("git", "-C", str(repo), *args),
        capture_output=True,
        text=True,
        check=True,
        env=env,
        timeout=60,
    )
    return done.stdout


def snapshot(repo: pathlib.Path) -> tuple[str, str, str]:
    config = (repo / ".git" / "config").read_text(encoding="utf-8")
    refs = git(repo, "for-each-ref", "--format=%(refname) %(objectname)")
    worktrees = git(repo, "worktree", "list", "--porcelain")
    return config, refs, worktrees


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="pelorus-isolation-") as tmp:
        repo = pathlib.Path(tmp) / "invoking"
        repo.mkdir()
        git(repo, "init", "-q", "-b", "main")
        (repo / "tracked.txt").write_text("invoking repository\n", encoding="utf-8")
        git(repo, "add", "tracked.txt")
        git(repo, "-c", "commit.gpgSign=false", "commit", "-q", "-m", "base")
        before = snapshot(repo)

        env = dict(os.environ)
        env.update(
            {
                "GIT_DIR": str(repo / ".git"),
                "GIT_WORK_TREE": str(repo),
                "GIT_INDEX_FILE": str(repo / ".git" / "index"),
            }
        )
        run = subprocess.run(
            (sys.executable, str(CHECKER), "--self-test"),
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            env=env,
            timeout=600,
        )
        after = snapshot(repo)

    failures = []
    for label, old, new in zip(("config", "refs", "worktrees"), before, after):
        if old != new:
            failures.append(f"self-test changed the invoking repository's {label}")
    if run.returncode != 0:
        failures.append(
            "self-test failed under hook-style GIT_* variables:\n"
            + run.stdout[-2000:]
            + run.stderr[-2000:]
        )
    for failure in failures:
        print(f"FAIL: {failure}", file=sys.stderr)
    if failures:
        return 1
    print("self-test leaves the invoking repository untouched")
    return 0


if __name__ == "__main__":
    sys.exit(main())
