#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Verify the Developer Certificate of Origin sign-off on a commit range.

The Standards workflow runs this on every pull request commit. Each
non-merge commit in BASE..HEAD needs a line that matches
`Signed-off-by: Full Name <user@example.org>` in its message.

Rules:
- Renovate commits are exempt (CONTRIBUTING.md), but only when Renovate
  opened the pull request (`--pr-author renovate[bot]`) and authored the
  commit, so a person cannot opt out by copying the bot's author line.
- Merge commits are skipped (`git rev-list --no-merges`).
- A range that cannot be listed or holds no commits fails, so the check
  never passes on nothing.

- BASE and HEAD must be plain revisions: printable ASCII that git allows
  in a ref name or revision (no space, backslash, `:`, `?`, `*` or `[`),
  not starting with `-`, no `..`. Anything else is a usage error, so a
  value can never reach git as an option or hold a range.

Exit codes: 0 every commit signed or exempt, 1 a commit lacks the
trailer or the range is unusable, 2 usage error or invalid revision.

Usage:
    python3 -I scripts/check-dco.py --base origin/master --head HEAD
    python3 -I scripts/check-dco.py --self-test
"""

from __future__ import annotations

import argparse
import io
import os
import pathlib
import re
import shutil
import subprocess  # nosec B404 - fixed git argv, no shell, explicit deadline
import sys
import tempfile
from typing import NamedTuple, TextIO

RENOVATE_PR_AUTHOR = "renovate[bot]"
RENOVATE_COMMIT_AUTHOR = "29139614+renovate[bot]@users.noreply.github.com"
DCO_TRAILER = re.compile(r"^Signed-off-by: [^<>]+ <[^<>\s]+@[^<>\s]+>$")
MAX_COMMITS = 10000
GIT_TIMEOUT = 60.0


class GitResult(NamedTuple):
    """Exit code and decoded output of one git call."""

    returncode: int
    stdout: str
    stderr: str


# Printable ASCII that git allows in a ref name or revision: no space, control
# character, backslash, colon, question mark, asterisk or open bracket.
_REV_CHARS = r"""A-Za-z0-9!"#$%&'()+,./;<=>@\]^_`{|}~"""
REVISION = re.compile(f"[{_REV_CHARS}][{_REV_CHARS}-]{{0,255}}")


def find_git(env: dict[str, str] | None = None) -> str | None:
    """Return the absolute path of an executable git file on PATH, or None."""
    search = (env if env is not None else os.environ).get("PATH", "")
    git = shutil.which("git", path=search)
    if git is None or not pathlib.Path(git).is_file():
        return None
    return str(pathlib.Path(git).resolve())


def valid_revision(rev: str) -> bool:
    """Return True for a revision that cannot start an option or hold a range."""
    return REVISION.fullmatch(rev) is not None and ".." not in rev


def run_git(
    args: list[str], repo_root: pathlib.Path, env: dict[str, str] | None = None
) -> GitResult:
    """Run git in repo_root without a shell; a timeout returns exit code 124."""
    git = find_git(env)
    if git is None:
        return GitResult(127, "", "git executable not found")
    argv = [git, "-C", str(repo_root), *args]
    try:
        proc = subprocess.run(  # nosec B603 - argv is the resolved git binary plus checked revisions, no shell  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - argv is the absolute git path plus constants and allow-listed revisions, no shell, explicit deadline
            argv,  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-tainted-env-args.dangerous-subprocess-use-tainted-env-args - env is None in CI or the self-test's isolated copy of os.environ; it only selects the git found on PATH, run by absolute path without a shell
            capture_output=True,
            timeout=GIT_TIMEOUT,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return GitResult(124, "", "git command timed out")
    # Decode by hand: text mode turns a lone CR into a line break (universal newlines).
    return GitResult(
        proc.returncode,
        proc.stdout.decode("utf-8", "replace"),
        proc.stderr.decode("utf-8", "replace"),
    )


def has_dco_trailer(body: str) -> bool:
    """Return True when an LF-delimited line of the message is a valid DCO trailer."""
    # Split on LF only, as git does; splitlines() also breaks at CR, FF, U+2028 and more.
    return any(DCO_TRAILER.match(line) for line in body.split("\n"))


def list_range(
    base: str, head: str, repo_root: pathlib.Path, env: dict[str, str] | None
) -> tuple[list[str], str | None]:
    """List non-merge commits in base..head; return (shas, error message)."""
    proc = run_git(
        ["rev-list", "--no-merges", "--end-of-options", f"{base}..{head}"],
        repo_root,
        env,
    )
    shas = proc.stdout.split()
    if proc.returncode != 0 or not shas:
        return [], f"::error::no commits between {base} and {head}; cannot verify DCO"
    if len(shas) > MAX_COMMITS:
        return (
            [],
            f"::error::{len(shas)} commits between {base} and {head} exceed {MAX_COMMITS}",
        )
    return shas, None


def commit_verdict(
    sha: str, pr_author: str, repo_root: pathlib.Path, env: dict[str, str] | None
) -> tuple[str, str]:
    """Judge one commit: ("exempt" | "signed" | "missing" | "error", message)."""
    author = run_git(["show", "-s", "--format=%ae", sha], repo_root, env)
    if author.returncode != 0:
        return (
            "error",
            f"::error::cannot read the author of {sha}: {author.stderr.strip()}",
        )
    if (
        pr_author == RENOVATE_PR_AUTHOR
        and author.stdout.strip() == RENOVATE_COMMIT_AUTHOR
    ):
        return (
            "exempt",
            f"{sha}: Renovate commit in a Renovate pull request, DCO exempt",
        )
    body = run_git(["show", "-s", "--format=%B", sha], repo_root, env)
    if body.returncode != 0:
        return (
            "error",
            f"::error::cannot read the message of {sha}: {body.stderr.strip()}",
        )
    if has_dco_trailer(body.stdout):
        return "signed", ""
    return (
        "missing",
        f"::error::{sha} has no Signed-off-by trailer (DCO); amend with git commit -s",
    )


def check_dco(
    base: str,
    head: str,
    pr_author: str = "",
    repo_root: pathlib.Path | None = None,
    env: dict[str, str] | None = None,
    out: TextIO = sys.stdout,
    err: TextIO = sys.stderr,
) -> int:
    """Check every non-merge commit in base..head; return the exit code."""
    bad = [rev for rev in (base, head) if not valid_revision(rev)]
    if bad:
        print(f"::error::invalid revision {bad[0]!r}", file=err)
        return 2
    root = repo_root if repo_root is not None else pathlib.Path.cwd()
    shas, error = list_range(base, head, root, env)
    if error is not None:
        print(error, file=out)
        return 1
    missing = 0
    for sha in shas:
        verdict, message = commit_verdict(sha, pr_author, root, env)
        if verdict == "error":
            print(message, file=err)
            return 1
        if message:
            print(message, file=out)
        missing += verdict == "missing"
    if missing:
        print(f"{missing} commit(s) lack a Signed-off-by trailer", file=err)
        return 1
    return 0


SIGNED = "\n\nSigned-off-by: User <user@pelorus.invalid>"
BOT = ("renovate[bot]", RENOVATE_COMMIT_AUTHOR)
HUMAN = ("Human Contributor", "human@pelorus.invalid")
# Fixture commits: name -> (message, parent names, (author name, email) or None).
FIXTURE = (
    ("base", "base" + SIGNED, (), None),
    ("c1", "case 1 signed" + SIGNED, ("base",), None),
    ("c2", "case 2 unsigned", ("base",), None),
    ("c3", "case 3 no email\n\nSigned-off-by: User", ("base",), None),
    ("c3b", "case 3b no address\n\nSigned-off-by: User Name <user>", ("base",), None),
    (
        "c4",
        "case 4 indented\n\n    Signed-off-by: User <user@pelorus.invalid>",
        ("base",),
        None,
    ),
    (
        "c4b",
        "case 4b CR\n\nprefix\rSigned-off-by: User <user@pelorus.invalid>",
        ("base",),
        None,
    ),
    (
        "c4c",
        "case 4c LS\n\nprefix\u2028Signed-off-by: User <user@pelorus.invalid>",
        ("base",),
        None,
    ),
    ("c5", "case 5 renovate", ("base",), BOT),
    ("c6", "case 6 copied bot author", ("base",), BOT),
    ("c7", "case 7 human in bot pr", ("base",), HUMAN),
    ("m_a", "branch a" + SIGNED, ("base",), None),
    ("m_b", "branch b" + SIGNED, ("base",), None),
    ("m_merge", "merge without trailer", ("m_a", "m_b"), None),
    ("c10", "head after merge" + SIGNED, ("m_merge",), None),
    ("c11_1", "case 11 signed" + SIGNED, ("base",), None),
    ("c11_2", "case 11 unsigned 1", ("c11_1",), None),
    ("c11_3", "case 11 unsigned 2", ("c11_2",), None),
)
# Cases: name, base, head, pr author, exit code, stdout substring, stderr substring.
# A base or head starting with "ref:" is passed literally; "{c5}" names a fixture sha.
CASES = (
    ("1 signed commit", "base", "c1", "", 0, None, None),
    ("2 unsigned commit", "base", "c2", "", 1, "has no Signed-off-by", None),
    ("3 trailer without email", "base", "c3", "", 1, "has no Signed-off-by", None),
    ("3b trailer without address", "base", "c3b", "", 1, "has no Signed-off-by", None),
    ("4 indented trailer", "base", "c4", "", 1, "has no Signed-off-by", None),
    ("4b trailer after a CR", "base", "c4b", "", 1, "has no Signed-off-by", None),
    ("4c trailer after U+2028", "base", "c4c", "", 1, "has no Signed-off-by", None),
    (
        "5 Renovate pr and author",
        "base",
        "c5",
        BOT[0],
        0,
        "{c5}: Renovate commit",
        None,
    ),
    (
        "6 person copies Renovate author",
        "base",
        "c6",
        "someone",
        1,
        "has no Signed-off-by",
        None,
    ),
    ("7 person in Renovate pr", "base", "c7", BOT[0], 1, "has no Signed-off-by", None),
    ("8 empty range", "base", "base", "", 1, "no commits", None),
    (
        "9 unknown base",
        "ref:refs/heads/no-such-base",
        "base",
        "",
        1,
        "no commits",
        None,
    ),
    ("10 merge without trailer skipped", "base", "c10", "", 0, None, None),
    ("11 two unsigned of three", "base", "c11_3", "", 1, None, "2 commit(s)"),
    ("13 base read as option", "ref:--all", "c1", "", 2, None, "invalid revision"),
    ("13b base branch with a dollar sign", "ref:release$2026", "c1", "", 0, None, None),
    ("13c base with a question mark", "ref:release?2026", "c1", "", 2, None, "invalid"),
    ("14 head holds a range", "base", "ref:main..main", "", 2, None, "invalid"),
    ("15 head with a space", "base", "ref:HEAD x", "", 2, None, "invalid"),
)


def fixture_env() -> dict[str, str] | None:
    """Return a git environment detached from the invoking repository, or None."""
    probe = run_git(["rev-parse", "--local-env-vars"], pathlib.Path.cwd())
    if probe.returncode != 0:
        return None
    drop = set(probe.stdout.split())
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in drop and not k.startswith("GIT_")
    }
    env.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "self-test",
            "GIT_AUTHOR_EMAIL": "self-test@pelorus.invalid",
            "GIT_COMMITTER_NAME": "self-test",
            "GIT_COMMITTER_EMAIL": "self-test@pelorus.invalid",
        }
    )
    return env


def build_fixture(tmp: pathlib.Path, env: dict[str, str]) -> dict[str, str]:
    """Create the fixture commits in tmp; return name -> sha (raises on failure)."""
    for args in (["init", "-q"], ["write-tree"]):
        proc = run_git(args, tmp, env)
        if proc.returncode != 0:
            raise RuntimeError(f"git {args[0]}: {proc.stderr.strip()}")
    tree = proc.stdout.strip()
    shas: dict[str, str] = {}
    for name, message, parents, author in FIXTURE:
        commit_env = dict(env)
        if author is not None:
            commit_env["GIT_AUTHOR_NAME"], commit_env["GIT_AUTHOR_EMAIL"] = author
        args = ["-c", "commit.gpgsign=false", "commit-tree", tree, "-m", message]
        for parent in parents:
            args += ["-p", shas[parent]]
        proc = run_git(args, tmp, commit_env)
        if proc.returncode != 0:
            raise RuntimeError(f"commit {name}: {proc.stderr.strip()}")
        shas[name] = proc.stdout.strip()
    proc = run_git(["update-ref", "refs/heads/release$2026", shas["base"]], tmp, env)
    if proc.returncode != 0:
        raise RuntimeError(f"update-ref: {proc.stderr.strip()}")
    return shas


def resolve(token: str, shas: dict[str, str]) -> str:
    """Map a case token to a fixture sha, or strip the literal "ref:" prefix."""
    return token[len("ref:") :] if token.startswith("ref:") else shas[token]


def run_case(
    case: tuple, shas: dict[str, str], tmp: pathlib.Path, env: dict[str, str]
) -> str | None:
    """Run one planted case; return a failure description or None."""
    name, base, head, pr_author, want_rc, want_out, want_err = case
    out, err = io.StringIO(), io.StringIO()
    rc = check_dco(
        resolve(base, shas), resolve(head, shas), pr_author, tmp, env, out, err
    )
    if rc != want_rc:
        return f"{name}: exit {rc}, want {want_rc}\n{out.getvalue()}{err.getvalue()}"
    if want_out and want_out.format(**shas) not in out.getvalue():
        return f"{name}: stdout lacks {want_out!r}\n{out.getvalue()}"
    if want_err and want_err not in err.getvalue():
        return f"{name}: stderr lacks {want_err!r}\n{err.getvalue()}"
    return None


def run_self_test() -> int:
    """Run every planted case against a throwaway repository."""
    env = fixture_env()
    if env is None:
        print("FAIL self-test: git rev-parse --local-env-vars failed", file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory(prefix="pelorus-dco-selftest-") as tmp_dir:
        tmp = pathlib.Path(tmp_dir)
        try:
            shas = build_fixture(tmp, env)
        except RuntimeError as exc:
            print(f"FAIL self-test fixture: {exc}", file=sys.stderr)
            return 1
        failures = [f for f in (run_case(c, shas, tmp, env) for c in CASES) if f]
        missing = run_git(["status"], tmp, {**env, "PATH": str(tmp)})
        if missing.returncode != 127:
            failures.append(f"16 git missing from PATH: exit {missing.returncode}")
    for failure in failures:
        print(f"FAIL self-test {failure}", file=sys.stderr)
    if failures:
        return 1
    print(f"{len(CASES) + 1} planted cases ok")
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse the command line; --base and --head are required without --self-test."""
    parser = argparse.ArgumentParser(
        description="Verify DCO sign-off on a commit range."
    )
    parser.add_argument("--base", help="base ref, e.g. origin/master")
    parser.add_argument("--head", help="head ref or sha, e.g. HEAD")
    parser.add_argument(
        "--pr-author", default="", help="login of the pull request author"
    )
    parser.add_argument(
        "--self-test", action="store_true", help="run the planted cases"
    )
    args = parser.parse_args(argv)
    if not args.self_test and (not args.base or not args.head):
        parser.error("--base and --head are required unless --self-test is set")
    return args


def main(argv: list[str]) -> int:
    """Entry point: run the self-test or check the requested range."""
    args = parse_args(argv)
    if args.self_test:
        return run_self_test()
    return check_dco(args.base, args.head, args.pr_author)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
