#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Refuse pushes whose commits differ from the checkout the pre-push jobs scan."""

from __future__ import annotations

import argparse
import io
import os
from pathlib import Path
import re
import shlex
import sys
import tempfile
from typing import Callable, TextIO

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from tracked_python import _git_executable, _run_git, repo_discovery_env  # noqa: E402

MAX_LINES = 4096
MAX_LINE_LENGTH = 65536
SHA = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})")


class Refused(ValueError):
    """A pushed update cannot be verified by the checked-out jobs."""


def git(root: Path, args: list[str], env: dict[str, str] | None) -> str:
    """Use the shared bounded Git runner in the selected repository."""
    result = _run_git([_git_executable(), "-C", str(root), *args], env=env)
    return result.stdout.decode("utf-8", "replace").strip()


def parse_update(line: str, number: int) -> tuple[str, str] | None:
    """Validate both object IDs, including those in deletion updates."""
    fields = line.split()
    if not fields:
        return None
    if len(fields) != 4:
        raise Refused(f"pre-push: malformed input on line {number}: expected 4 fields")
    local_ref, local_sha, _, remote_sha = fields
    if SHA.fullmatch(local_sha) is None or SHA.fullmatch(remote_sha) is None:
        raise Refused(f"pre-push: malformed SHA on line {number}: expected 40 or 64 hex digits")
    return None if local_sha == "0" * len(local_sha) else (local_ref, local_sha)


def check_commit(root: Path, ref: str, sha: str, head: str,
                 env: dict[str, str] | None) -> None:
    """Peel annotated tags before comparing the pushed commit with HEAD."""
    try:
        commit = git(root, ["rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}"], env)
    except RuntimeError as exc:
        raise Refused(f"pre-push: refusing {ref}: cannot peel {sha[:12]} to a commit: {exc}") from exc
    if commit != head:
        short = ref.removeprefix("refs/heads/").removeprefix("refs/tags/")
        raise Refused(
            f"pre-push: refusing {ref} at {sha[:12]}: the pre-push jobs verify "
            f"the checked-out HEAD ({head[:12]}), not the pushed commit.\n"
            f"pre-push: check out {short} and push again."
        )


def check_updates(stream: TextIO, root: Path, env: dict[str, str] | None) -> None:
    """Read a bounded pre-push stream; fail closed on malformed or excess input."""
    head = None
    for index in range(MAX_LINES + 1):
        line = stream.readline(MAX_LINE_LENGTH + 1)
        if not line:
            return
        if index == MAX_LINES:
            raise Refused(f"pre-push: input exceeds {MAX_LINES} lines")
        if len(line) > MAX_LINE_LENGTH:
            raise Refused(f"pre-push: input line {index + 1} exceeds {MAX_LINE_LENGTH} characters")
        update = parse_update(line, index + 1)
        if update is not None:
            if head is None:
                head = git(root, ["rev-parse", "--verify", "HEAD"], env)
            check_commit(root, *update, head, env)


def check_pushed_head(stream: TextIO, root: Path, env: dict[str, str] | None = None,
                      err: TextIO | None = None) -> int:
    """Return 1 for refused input and 2 for a Git or runtime failure."""
    try:
        check_updates(stream, root, env)
    except Refused as exc:
        print(str(exc), file=err or sys.stderr)
        return 1
    except (RuntimeError, OSError, UnicodeError) as exc:
        print(f"pre-push: {exc}", file=err or sys.stderr)
        return 2
    return 0


def fixture_env() -> dict[str, str]:
    """Detach every fixture call from inherited Git state and user configuration."""
    env = {key: value for key, value in repo_discovery_env().items()
           if not key.startswith("GIT_")}
    env.update({
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "self-test",
        "GIT_AUTHOR_EMAIL": "self-test@pelorus.invalid",
        "GIT_COMMITTER_NAME": "self-test",
        "GIT_COMMITTER_EMAIL": "self-test@pelorus.invalid",
    })
    return env


def fixture_commit(root: Path, env: dict[str, str], message: str) -> str:
    """Make an unsigned, empty fixture commit without changing any Git config."""
    git(root, ["-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false",
               "commit", "--allow-empty", "-q", "-m", message], env)
    return git(root, ["rev-parse", "HEAD"], env)


def build_fixture(tmp: Path, env: dict[str, str]) -> tuple[Path, Path, str, str]:
    """Install the actual checker in the fixture's own pre-push hook."""
    root, remote = tmp / "local", tmp / "remote.git"
    root.mkdir()
    remote.mkdir()
    git(root, ["init", "-q", "-b", "master"], env)
    git(remote, ["init", "-q", "--bare"], env)
    old = fixture_commit(root, env, "old")
    head = fixture_commit(root, env, "head")
    git(root, ["switch", "-q", "-c", "other"], env)
    fixture_commit(root, env, "other")
    git(root, ["switch", "-q", "master"], env)
    hooks = Path(git(root, ["rev-parse", "--git-path", "hooks"], env))
    if not hooks.is_absolute():
        hooks = root / hooks
    hooks.mkdir(parents=True, exist_ok=True)
    hook = hooks / "pre-push"
    hook.write_text(
        f"#!/bin/sh\nexec {shlex.quote(sys.executable)} -I "
        f'{shlex.quote(str(Path(__file__).resolve()))} "$@"\n', encoding="utf-8")
    hook.chmod(0o500)
    return root, remote, old, head


def send(root: Path, remote: Path, env: dict[str, str], *refs: str) -> str | None:
    """Return captured failure context from a real local Git push, or None."""
    try:
        git(root, ["push", str(remote), *refs], env)
    except RuntimeError as exc:
        return str(exc)
    return None


def require(condition: bool, why: str) -> None:
    """Keep self-test assertions active even with Python optimization enabled."""
    if not condition:
        raise ValueError(why)


def remote_ref(remote: Path, env: dict[str, str], ref: str) -> str:
    """Read a fixture remote ref without treating absent refs as Git errors."""
    return git(remote, ["for-each-ref", "--format=%(objectname)", ref], env)


def case_branch(root: Path, remote: Path, env: dict[str, str], head: str) -> None:
    """The checked-out branch can be sent."""
    error = send(root, remote, env, "master")
    require(error is None, f"checked-out branch refused: {error}")
    require(remote_ref(remote, env, "refs/heads/master") == head, "remote master differs")


def case_other(root: Path, remote: Path, env: dict[str, str]) -> None:
    """The hook must explain and block the non-checked-out branch."""
    error = send(root, remote, env, "other")
    require(error is not None and "check out other and push again" in error,
            f"missing hook refusal for other: {error}")
    require(not remote_ref(remote, env, "refs/heads/other"), "remote other was created")


def case_delete(root: Path, remote: Path, env: dict[str, str]) -> None:
    """Deleting a branch needs no checked-out commit comparison."""
    error = send(root, remote, env, "HEAD:refs/heads/remove-me")
    require(error is None, f"cannot seed deletion fixture: {error}")
    error = send(root, remote, env, "--delete", "remove-me")
    require(error is None, f"deletion refused: {error}")
    require(not remote_ref(remote, env, "refs/heads/remove-me"), "remote branch remains")


def case_tags(root: Path, remote: Path, env: dict[str, str], old: str) -> None:
    """Annotated tags pass only when their peeled commit is HEAD."""
    for tag, target in (("at-head", "HEAD"), ("at-old", old)):
        git(root, ["-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false",
                   "tag", "-a", tag, target, "-m", tag], env)
    error = send(root, remote, env, "refs/tags/at-head")
    require(error is None, f"annotated tag at HEAD refused: {error}")
    require(bool(remote_ref(remote, env, "refs/tags/at-head")), "HEAD tag absent")
    error = send(root, remote, env, "refs/tags/at-old")
    require(error is not None and "check out at-old and push again" in error,
            f"missing old annotated tag refusal: {error}")
    require(not remote_ref(remote, env, "refs/tags/at-old"), "old tag was created")


def case_multi(root: Path, remote: Path, env: dict[str, str]) -> None:
    """A matching first ref must not hide a later mismatched ref."""
    error = send(root, remote, env, "HEAD:refs/heads/multi", "other")
    require(error is not None and "check out other and push again" in error,
            f"missing multi-ref refusal: {error}")
    require(not remote_ref(remote, env, "refs/heads/multi"), "partial multi-ref push")
    require(not remote_ref(remote, env, "refs/heads/other"), "other was sent")


def case_input(root: Path, env: dict[str, str], head: str) -> None:
    """Cover empty, malformed, deletion, unknown-object and bounded input."""
    zero = "0" * 40
    valid = f"refs/heads/master {head} refs/heads/master {zero}\n"
    cases = (
        ("", 0, ""), ("\n", 0, ""), (valid, 0, ""),
        ("malformed\n", 1, "line 1"),
        (f"r {'g' * 40} r {zero}\n", 1, "line 1"),
        (f"r {head} r bad\n", 1, "line 1"),
        (f"(delete) {zero} r {head}\n", 0, ""),
        (f"(delete) {'0' * 64} r {'a' * 64}\n", 0, ""),
        (f"refs/heads/missing {'f' * 40} r {zero}\n", 1, "refs/heads/missing"),
        ("\n" * MAX_LINES, 0, ""),
        ("\n" * (MAX_LINES + 1), 1, "exceeds 4096 lines"),
        ("x" * (MAX_LINE_LENGTH + 1), 1, "line 1 exceeds"),
    )
    for number, (data, expected, message) in enumerate(cases):
        err = io.StringIO()
        rc = check_pushed_head(io.StringIO(data), root, env, err)
        require(rc == expected and message in err.getvalue(),
                f"input case {number}: exit {rc}, stderr {err.getvalue()!r}")
        require(expected != 0 or not err.getvalue(), f"input case {number}: unexpected output")
    unborn = root.parent / "unborn"
    unborn.mkdir()
    git(unborn, ["init", "-q", "-b", "master"], env)
    err = io.StringIO()
    require(check_pushed_head(io.StringIO(valid), unborn, env, err) == 2,
            "unborn HEAD did not report a Git failure")


def run_self_test() -> int:
    """Exercise real pushes and planted refusals, one result per case."""
    try:
        env = fixture_env()
        with tempfile.TemporaryDirectory(prefix="pelorus-prepush-head-") as directory:
            root, remote, old, head = build_fixture(Path(directory), env)
            cases: tuple[tuple[str, Callable[[], None]], ...] = (
                ("p1 checked-out branch", lambda: case_branch(root, remote, env, head)),
                ("p2 other branch", lambda: case_other(root, remote, env)),
                ("p3 deletion", lambda: case_delete(root, remote, env)),
                ("p4 annotated tags", lambda: case_tags(root, remote, env, old)),
                ("p5 two refs", lambda: case_multi(root, remote, env)),
                ("p6 input validation", lambda: case_input(root, env, head)),
            )
            failures = 0
            for name, case in cases:
                try:
                    case()
                except (RuntimeError, OSError, ValueError) as exc:
                    print(f"FAIL {name}: {exc}")
                    failures += 1
                else:
                    print(f"PASS {name}")
            return int(failures != 0)
    except (RuntimeError, OSError) as exc:
        print(f"FAIL fixture setup: {exc}")
        return 1


def main() -> int:
    """Accept Git's remote arguments and the standalone self-test option."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("remote", nargs="?", help="remote name (ignored)")
    parser.add_argument("url", nargs="?", help="remote URL (ignored)")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return run_self_test()
    return check_pushed_head(sys.stdin, Path.cwd())


if __name__ == "__main__":
    sys.exit(main())
