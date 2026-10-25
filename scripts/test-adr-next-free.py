#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Regression test for scripts/adr/next-free.sh ADR allocator.

Verifies that next-free.sh discovers ADR numbers across:
- the checked-out worktree
- sibling worktrees (including untracked .stub files)
- local and remote branch tips (via git ls-tree)
without network access, handles prunable worktrees and validation failures,
enforces ref caps and propagates git failure exit statuses,
and leaves the invoking repository's Git state strictly untouched.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import stat
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT_UNDER_TEST = pathlib.Path(
    os.environ.get("NEXT_FREE_SCRIPT", ROOT / "scripts" / "adr" / "next-free.sh")
)


def make_clean_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "fixture",
            "GIT_AUTHOR_EMAIL": "fixture@pelorus.invalid",
            "GIT_COMMITTER_NAME": "fixture",
            "GIT_COMMITTER_EMAIL": "fixture@pelorus.invalid",
        }
    )
    return env


def git(repo: pathlib.Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    cmd = ["git", "-C", str(repo), "-c", "commit.gpgSign=false", *args]
    res = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=60,
    )
    if res.returncode != 0:
        raise RuntimeError(
            f"git command failed: {' '.join(cmd)}\nstderr: {res.stderr}\nstdout: {res.stdout}"
        )
    return res


def run_allocator(
    script_path: pathlib.Path,
    env: dict[str, str],
    *args: str,
    cwd: pathlib.Path,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(script_path), *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=60,
    )


def snapshot(repo: pathlib.Path, env: dict[str, str]) -> tuple[str, str, str]:
    config_path = repo / ".git" / "config"
    if not config_path.exists():
        rev_git = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            check=True,
            env=env,
        ).stdout.strip()
        common_git = (repo / rev_git).resolve()
        config_path = common_git / "config"
    config = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    refs = subprocess.run(
        ["git", "-C", str(repo), "for-each-ref", "--format=%(refname) %(objectname)"],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    ).stdout
    worktrees = subprocess.run(
        ["git", "-C", str(repo), "worktree", "list", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    ).stdout
    return config, refs, worktrees


def init_fixture_repo(
    repo_path: pathlib.Path,
    env: dict[str, str],
    script_src: pathlib.Path,
    adrs: tuple[str, ...] = ("0001-init.md", "0002-second.md"),
) -> tuple[pathlib.Path, pathlib.Path]:
    repo_path.mkdir(parents=True, exist_ok=True)
    git(repo_path, env, "init", "-q", "-b", "master")
    sdir = repo_path / "scripts" / "adr"
    sdir.mkdir(parents=True)
    fscript = sdir / "next-free.sh"
    shutil.copy2(script_src, fscript)
    fscript.chmod(fscript.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    docs_adr = repo_path / "docs" / "adr"
    docs_adr.mkdir(parents=True)
    for i, name in enumerate(adrs, 1):
        (docs_adr / name).write_text(f"# ADR {i}\n", encoding="utf-8")
    git(repo_path, env, "add", "scripts/adr/next-free.sh", "docs/adr")
    git(repo_path, env, "commit", "-q", "-m", "base commit with adrs")
    return fscript, docs_adr


def test_base_and_sibling(
    fscript: pathlib.Path, env: dict[str, str], tmp_path: pathlib.Path, main_repo: pathlib.Path
) -> tuple[list[str], pathlib.Path]:
    failures: list[str] = []
    res = run_allocator(fscript, env, cwd=main_repo)
    if res.returncode == 0 and res.stdout.strip() == "0003":
        print("PASS: base (0003)")
    else:
        msg = f"base (expected 0003, got exit {res.returncode}, stdout: {res.stdout.strip()!r})"
        print(f"FAIL: {msg}")
        failures.append(msg)

    sibling_repo = tmp_path / "sibling"
    git(main_repo, env, "worktree", "add", "-q", "-b", "feat/sibling", str(sibling_repo))
    s_docs = sibling_repo / "docs" / "adr"
    s_docs.mkdir(parents=True, exist_ok=True)
    (s_docs / "0005-x.md.stub").write_text("", encoding="utf-8")

    res = run_allocator(fscript, env, cwd=main_repo)
    if res.returncode == 0 and res.stdout.strip() == "0006":
        print("PASS: sibling-worktree (0006)")
    else:
        msg = f"sibling-worktree (expected 0006, got exit {res.returncode}, stdout: {res.stdout.strip()!r})"
        print(f"FAIL: {msg}")
        failures.append(msg)
    return failures, sibling_repo


def test_branch_only(
    fscript: pathlib.Path, env: dict[str, str], main_repo: pathlib.Path, sibling_repo: pathlib.Path
) -> list[str]:
    failures: list[str] = []
    s_docs = sibling_repo / "docs" / "adr"
    git(sibling_repo, env, "checkout", "-q", "-b", "feat/local-branch")
    (s_docs / "0008-local.md").write_text("# ADR 8\n", encoding="utf-8")
    git(sibling_repo, env, "add", "docs/adr/0008-local.md")
    git(sibling_repo, env, "commit", "-q", "-m", "local branch commit with 0008")

    (s_docs / "0009-y.md").write_text("# ADR 9\n", encoding="utf-8")
    git(sibling_repo, env, "add", "docs/adr/0009-y.md")
    git(sibling_repo, env, "commit", "-q", "-m", "remote commit with 0009")
    commit_0009 = git(sibling_repo, env, "rev-parse", "HEAD").stdout.strip()

    git(sibling_repo, env, "checkout", "-q", "--detach", "HEAD~2")
    git(sibling_repo, env, "clean", "-q", "-fd")
    (s_docs / "0005-x.md.stub").write_text("", encoding="utf-8")
    git(main_repo, env, "update-ref", "refs/remotes/origin/feature-remote", commit_0009)

    res = run_allocator(fscript, env, cwd=main_repo)
    if res.returncode == 0 and res.stdout.strip() == "0010":
        print("PASS: branch-only (0010)")
    else:
        msg = f"branch-only (expected 0010, got exit {res.returncode}, stdout: {res.stdout.strip()!r})"
        print(f"FAIL: {msg}")
        failures.append(msg)
    return failures


def test_claim_and_release(
    fscript: pathlib.Path,
    env: dict[str, str],
    tmp_path: pathlib.Path,
    main_repo: pathlib.Path,
    docs_adr: pathlib.Path,
) -> list[str]:
    failures: list[str] = []
    claim_res = run_allocator(fscript, env, "--claim", "my-feature", cwd=main_repo)
    stub_path = docs_adr / "0010-my-feature.md.stub"
    next_res = run_allocator(fscript, env, cwd=main_repo)
    if claim_res.returncode == 0 and claim_res.stdout.strip() == "0010" and stub_path.exists() and next_res.returncode == 0 and next_res.stdout.strip() == "0011":
        print("PASS: claim (0011)")
    else:
        msg = f"claim (expected claimed 0010, next 0011; got claim={claim_res.stdout.strip()!r}, next={next_res.stdout.strip()!r})"
        print(f"FAIL: {msg}")
        failures.append(msg)

    rel_unknown = run_allocator(fscript, env, "--release", "9999", cwd=main_repo)
    bad_slug = run_allocator(fscript, env, "--claim", "INVALID_SLUG!", cwd=main_repo)
    pdir = tmp_path / "prunable-wt"
    git(main_repo, env, "worktree", "add", "-q", "-b", "feat/prunable", str(pdir))
    shutil.rmtree(pdir)
    prunable_check = run_allocator(fscript, env, cwd=main_repo)
    rel_ok = run_allocator(fscript, env, "--release", "0010", cwd=main_repo)
    rel_again = run_allocator(fscript, env, "--release", "0010", cwd=main_repo)

    if rel_unknown.returncode == 1 and bad_slug.returncode != 0 and prunable_check.returncode == 0 and rel_ok.returncode == 0 and not stub_path.exists() and rel_again.returncode == 1:
        print("PASS: release-and-validation")
    else:
        msg = f"release-and-validation (unknown={rel_unknown.returncode}, bad_slug={bad_slug.returncode}, prunable={prunable_check.returncode}, rel_ok={rel_ok.returncode}, rel_again={rel_again.returncode})"
        print(f"FAIL: {msg}")
        failures.append(msg)
    return failures


def test_isolation(
    fscript: pathlib.Path,
    env: dict[str, str],
    tmp_path: pathlib.Path,
    main_repo: pathlib.Path,
    root_before: tuple[str, str, str] | None,
) -> list[str]:
    failures: list[str] = []
    invoking_repo = tmp_path / "invoking"
    invoking_repo.mkdir()
    git(invoking_repo, env, "init", "-q", "-b", "master")
    (invoking_repo / "file.txt").write_text("hello\n", encoding="utf-8")
    git(invoking_repo, env, "add", "file.txt")
    git(invoking_repo, env, "commit", "-q", "-m", "init")

    invoking_before = snapshot(invoking_repo, env)
    hook_env = dict(env)
    hook_env.update(
        {
            "GIT_DIR": str(invoking_repo / ".git"),
            "GIT_WORK_TREE": str(invoking_repo),
            "GIT_INDEX_FILE": str(invoking_repo / ".git" / "index"),
        }
    )
    run_allocator(fscript, hook_env, cwd=main_repo)
    run_allocator(fscript, hook_env, "--claim", "iso-test", cwd=main_repo)
    run_allocator(fscript, hook_env, "--release", "0010", cwd=main_repo)

    invoking_after = snapshot(invoking_repo, env)
    for label, b, a in zip(("config", "refs", "worktrees"), invoking_before, invoking_after):
        if b != a:
            msg = f"isolation: invoking repo {label} changed under hook variables"
            print(f"FAIL: {msg}")
            failures.append(msg)

    if root_before is not None:
        root_after = snapshot(ROOT, env)
        for label, b, a in zip(("config", "refs", "worktrees"), root_before, root_after):
            if b != a:
                msg = f"isolation: repository root {label} changed during test run"
                print(f"FAIL: {msg}")
                failures.append(msg)
    if not failures:
        print("PASS: isolation")
    return failures


def test_ref_cap(
    script_src: pathlib.Path, env: dict[str, str], tmp_path: pathlib.Path
) -> list[str]:
    failures: list[str] = []
    repo = tmp_path / "ref_cap_repo"
    fscript, docs_adr = init_fixture_repo(repo, env, script_src, ("0001-init.md",))

    head = git(repo, env, "rev-parse", "HEAD").stdout.strip()
    commands = "\n".join(f"create refs/heads/b{i:04d} {head}" for i in range(2001)) + "\n"
    subprocess.run(
        ["git", "-C", str(repo), "update-ref", "--stdin"],
        input=commands,
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )

    r_print = run_allocator(fscript, env, cwd=repo)
    if r_print.returncode != 0 and r_print.stdout.strip() == "":
        print("PASS: ref-cap print mode (exited non-zero, empty stdout)")
    else:
        msg = f"ref-cap print mode (expected exit != 0 and empty stdout, got exit {r_print.returncode}, stdout: {r_print.stdout.strip()!r})"
        print(f"FAIL: {msg}")
        failures.append(msg)

    r_claim = run_allocator(fscript, env, "--claim", "test-claim", cwd=repo)
    stubs = list(docs_adr.glob("*.stub"))
    if r_claim.returncode != 0 and len(stubs) == 0:
        print("PASS: ref-cap claim mode (exited non-zero, no stub created)")
    else:
        msg = f"ref-cap claim mode (expected exit != 0 and no stub, got exit {r_claim.returncode}, stubs: {stubs})"
        print(f"FAIL: {msg}")
        failures.append(msg)
    return failures


def test_git_failure(
    script_src: pathlib.Path, env: dict[str, str], tmp_path: pathlib.Path
) -> list[str]:
    failures: list[str] = []
    repo = tmp_path / "git_fail_repo"
    fscript, _ = init_fixture_repo(repo, env, script_src, ("0001-init.md",))

    bin_dir = tmp_path / "fake_bin"
    bin_dir.mkdir()
    fake_git = bin_dir / "git"
    real_git = shutil.which("git")
    fake_git.write_text(
        f'#!/usr/bin/env bash\nfor arg in "$@"; do\n    if [ "$arg" = "worktree" ]; then\n        exit 128\n    fi\ndone\nexec {real_git} "$@"\n'
    )
    fake_git.chmod(0o755)

    fail_env = dict(env)
    fail_env["PATH"] = f"{bin_dir}:{fail_env.get('PATH', '')}"

    r = run_allocator(fscript, fail_env, cwd=repo)
    if r.returncode != 0:
        print("PASS: git-failure (exited non-zero on git worktree failure)")
    else:
        msg = f"git-failure (expected exit != 0, got exit {r.returncode}, stdout: {r.stdout.strip()!r})"
        print(f"FAIL: {msg}")
        failures.append(msg)
    return failures


def run_tests() -> int:
    env = make_clean_env()
    failures: list[str] = []

    root_before: tuple[str, str, str] | None = None
    if (ROOT / ".git").exists():
        root_before = snapshot(ROOT, env)

    with tempfile.TemporaryDirectory(prefix="pelorus-adr-test-") as tmp:
        tmp_path = pathlib.Path(tmp)
        main_repo = tmp_path / "main"
        main_repo.mkdir()
        fscript, docs_adr = init_fixture_repo(main_repo, env, SCRIPT_UNDER_TEST)

        f_base, s_repo = test_base_and_sibling(fscript, env, tmp_path, main_repo)
        failures.extend(f_base)
        failures.extend(test_branch_only(fscript, env, main_repo, s_repo))
        failures.extend(test_claim_and_release(fscript, env, tmp_path, main_repo, docs_adr))
        failures.extend(test_isolation(fscript, env, tmp_path, main_repo, root_before))
        failures.extend(test_ref_cap(SCRIPT_UNDER_TEST, env, tmp_path))
        failures.extend(test_git_failure(SCRIPT_UNDER_TEST, env, tmp_path))

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(run_tests())
