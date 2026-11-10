#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Open or close the scheduled changelog render pull request (ADR-0252).

Usage:
    open-render-pr.py --due <true|false> --app-slug <slug> --branch <name> [--repo <path>] [--repository <owner/name>]
    open-render-pr.py --check-settings
    open-render-pr.py --self-test
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import pathlib
import re
import subprocess  # nosec B404 - subprocess with fixed argv and explicit deadlines
import sys
import tempfile
import textwrap
import urllib.parse
from typing import Any, Callable, cast

SUBPROCESS_TIMEOUT_S = 120
SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")

GhRunner = Callable[[list[str], bytes | None], str]


def _load_dco_checker() -> Callable[[str], bool]:
    """Load has_dco_trailer from scripts/check-dco.py via importlib."""
    path = pathlib.Path(__file__).resolve().parent.parent / "check-dco.py"
    spec = importlib.util.spec_from_file_location("check_dco", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load check-dco: no loader for {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    checker = getattr(mod, "has_dco_trailer", None)
    if checker is None or not callable(checker):
        raise RuntimeError("cannot load check-dco: has_dco_trailer not found")
    return cast(Callable[[str], bool], checker)


def check_settings(env: dict[str, str]) -> int:
    """Validate that repository release-bot settings are present (ADR-0252)."""
    client_id = env.get("CLIENT_ID", "").strip()
    if not client_id:
        print(
            "::error::repository variable RELEASE_BOT_CLIENT_ID is not set (ADR-0252)",
            file=sys.stderr,
        )
        return 1
    has_key = env.get("HAS_KEY", "").strip()
    if has_key != "true":
        print(
            "::error::repository secret RELEASE_BOT_PRIVATE_KEY is not set (ADR-0252)",
            file=sys.stderr,
        )
        return 1
    return 0


def run_git(
    args: list[str],
    cwd: pathlib.Path,
    env: dict[str, str] | None = None,
    strip: bool = True,
) -> str:
    """Run a git command in cwd; return stdout or raise RuntimeError."""
    cmd = ["git", "-C", str(cwd), *args]
    try:
        proc = subprocess.run(  # nosec B603 - fixed git argv  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - fixed git argv
            cmd,
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_S,
            check=True,
            env=env,
        )
        return proc.stdout.strip() if strip else proc.stdout
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"git {' '.join(args)} failed in {cwd}: {exc.stderr.strip()}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"git {' '.join(args)} timed out in {cwd}") from exc


def _make_gh_runner(cwd: pathlib.Path) -> GhRunner:
    """Create default gh runner executing subprocess in cwd."""

    def _runner(args: list[str], stdin: bytes | None = None) -> str:
        cmd = ["gh", *args]
        try:
            proc = subprocess.run(  # nosec B603 - fixed gh argv  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - fixed gh argv
                cmd,
                input=stdin,
                capture_output=True,
                timeout=SUBPROCESS_TIMEOUT_S,
                cwd=str(cwd),
                check=True,
            )
            return proc.stdout.decode("utf-8")
        except subprocess.CalledProcessError as exc:
            err_msg = exc.stderr.decode("utf-8", "replace").strip()
            raise RuntimeError(
                f"gh {' '.join(args)} failed (exit {exc.returncode}): {err_msg}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"gh {' '.join(args)} timed out after {SUBPROCESS_TIMEOUT_S}s"
            ) from exc

    return _runner


def _find_own_pr(
    repository: str,
    branch: str,
    app_slug: str,
    runner: GhRunner,
) -> tuple[dict[str, Any] | None, int]:
    """Find open PR on branch; verify authorship or return (None, 1)."""
    owner = repository.split("/", 1)[0]
    endpoint = f"repos/{repository}/pulls?state=open&head={owner}:{branch}&per_page=10"
    raw_json = runner(["api", endpoint], None)
    pulls = json.loads(raw_json)
    bot_login = f"{app_slug}[bot]"
    own_pr: dict[str, Any] | None = None
    for pr in pulls:
        head = pr.get("head") or {}
        repo = head.get("repo") or {}
        head_repo = repo.get("full_name")
        if head_repo == repository:
            login = pr.get("user", {}).get("login", "")
            if login == bot_login:
                if own_pr is None:
                    own_pr = pr
            else:
                num = pr.get("number")
                print(
                    f"error: open pull request #{num} on {branch} authored by {login!r} (expected {bot_login})",
                    file=sys.stderr,
                )
                return None, 1
    return own_pr, 0


def _handle_due_false(
    repository: str,
    own_pr: dict[str, Any] | None,
    runner: GhRunner,
) -> int:
    """Close own PR when base renders current, else print message."""
    if own_pr is not None:
        pr_number = own_pr["number"]
        comment = (
            "base renders current; closed by the scheduled render workflow, ADR-0252"
        )
        runner(
            [
                "pr",
                "close",
                str(pr_number),
                "--comment",
                comment,
                "--delete-branch",
                "--repo",
                repository,
            ],
            None,
        )
        print(f"closed render pull request #{pr_number}")
        return 0
    print("no render pull request to close")
    return 0


def _render_and_validate_status(
    repo: pathlib.Path,
    env: dict[str, str] | None,
) -> int:
    """Run changelog render script and validate that only CHANGELOG.md changed."""
    render_script = repo / "scripts/release/concat-changelog-fragments.sh"
    if not render_script.is_file():
        raise RuntimeError(f"render script not found at {render_script}")
    try:
        subprocess.run(  # nosec B603 - fixed script argv  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - fixed script argv
            ["bash", str(render_script), "--write"],
            cwd=str(repo),
            timeout=SUBPROCESS_TIMEOUT_S,
            check=True,
            capture_output=True,
            env=env,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"render script failed: {exc.stderr.decode('utf-8', 'replace').strip()}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"render script timed out in {repo}") from exc

    status_raw = run_git(
        ["status", "--porcelain=v1", "-z", "--untracked-files=all"],
        repo,
        env=env,
        strip=False,
    )
    if not status_raw:
        print("error: render owed but the render changes nothing", file=sys.stderr)
        return 1
    entries = [e for e in status_raw.split("\0") if e]
    offending = [e[3:] for e in entries if e != " M CHANGELOG.md"]
    if offending:
        print(
            f"error: render touched unexpected paths: {', '.join(offending)}",
            file=sys.stderr,
        )
        return 1
    return 0


def _create_blob_and_tree(
    repo: pathlib.Path,
    repository: str,
    tip_tree: str,
    runner: GhRunner,
    env: dict[str, str] | None,
) -> tuple[str | None, int]:
    """Create git blob for CHANGELOG.md and tree based on tip_tree."""
    cl_bytes = (repo / "CHANGELOG.md").read_bytes()
    cl_b64 = base64.b64encode(cl_bytes).decode("ascii")
    blob_in = json.dumps({"content": cl_b64, "encoding": "base64"}).encode("utf-8")
    blob_out = runner(
        ["api", "--method", "POST", f"repos/{repository}/git/blobs", "--input", "-"],
        blob_in,
    )
    blob_sha = json.loads(blob_out)["sha"]

    local_sha = run_git(["hash-object", "CHANGELOG.md"], repo, env=env)
    if blob_sha != local_sha:
        print(
            f"error: blob sha {blob_sha} differs from git hash-object {local_sha}",
            file=sys.stderr,
        )
        return None, 1

    tree_in = json.dumps(
        {
            "base_tree": tip_tree,
            "tree": [
                {
                    "path": "CHANGELOG.md",
                    "mode": "100644",
                    "type": "blob",
                    "sha": blob_sha,
                }
            ],
        }
    ).encode("utf-8")
    tree_out = runner(
        ["api", "--method", "POST", f"repos/{repository}/git/trees", "--input", "-"],
        tree_in,
    )
    new_tree_sha = json.loads(tree_out)["sha"]
    return new_tree_sha, 0


def _check_branch_head(
    repository: str,
    branch: str,
    tip: str,
    new_tree_sha: str,
    runner: GhRunner,
) -> tuple[bool, bool, str | None]:
    """Check if branch exists and whether its head already has tip parent and new_tree."""
    refs_json = runner(
        ["api", f"repos/{repository}/git/matching-refs/heads/{branch}"], None
    )
    matching = json.loads(refs_json)
    exact_ref = f"refs/heads/{branch}"
    branch_entry = next((r for r in matching if r.get("ref") == exact_ref), None)
    if branch_entry is None:
        return False, False, None

    head_sha = branch_entry["object"]["sha"]
    commit_json = runner(["api", f"repos/{repository}/git/commits/{head_sha}"], None)
    head_commit = json.loads(commit_json)
    parents = [p["sha"] for p in head_commit.get("parents", [])]
    tree_sha = head_commit.get("tree", {}).get("sha", "")
    if parents == [tip] and tree_sha == new_tree_sha:
        return True, True, head_sha
    return True, False, head_sha


def _create_verified_commit(
    repository: str,
    tip: str,
    new_tree_sha: str,
    bot_user: str,
    bot_email: str,
    runner: GhRunner,
) -> tuple[str | None, int]:
    """Create signed commit through REST API with verification check."""
    tip_short = tip[:12]
    dco_checker = _load_dco_checker()
    subject = "docs(changelog): render unreleased fragments"
    raw_body = (
        "Render-only commit generated by the scheduled render workflow "
        f"(ADR-0252) with bash scripts/release/concat-changelog-fragments.sh "
        f"--write on {tip_short}."
    )
    wrapped_body = textwrap.fill(raw_body, width=72)
    trailer = f"Signed-off-by: {bot_user} <{bot_email}>"
    commit_msg = f"{subject}\n\n{wrapped_body}\n\n{trailer}\n"
    if not dco_checker(commit_msg):
        print("error: commit message rejected by DCO trailer check", file=sys.stderr)
        return None, 1

    commit_payload = {
        "message": commit_msg,
        "tree": new_tree_sha,
        "parents": [tip],
    }
    commit_in = json.dumps(commit_payload).encode("utf-8")
    commit_out = runner(
        ["api", "--method", "POST", f"repos/{repository}/git/commits", "--input", "-"],
        commit_in,
    )
    commit_resp = json.loads(commit_out)
    commit_sha = commit_resp["sha"]
    verification = commit_resp.get("verification", {})
    if verification.get("verified") is not True:
        reason = verification.get("reason", "unknown")
        print(
            f"error: render commit {commit_sha} is not verified ({reason}); ref not moved",
            file=sys.stderr,
        )
        return None, 1
    return commit_sha, 0


def _update_or_create_ref(
    repository: str,
    branch: str,
    commit_sha: str,
    branch_exists: bool,
    runner: GhRunner,
) -> None:
    """Update existing ref with force: true or create new ref."""
    if branch_exists:
        patch_in = json.dumps({"sha": commit_sha, "force": True}).encode("utf-8")
        runner(
            [
                "api",
                "--method",
                "PATCH",
                f"repos/{repository}/git/refs/heads/{branch}",
                "--input",
                "-",
            ],
            patch_in,
        )
    else:
        post_in = json.dumps({"ref": f"refs/heads/{branch}", "sha": commit_sha}).encode(
            "utf-8"
        )
        runner(
            ["api", "--method", "POST", f"repos/{repository}/git/refs", "--input", "-"],
            post_in,
        )


def _ensure_pull_request(
    repository: str,
    branch: str,
    default_branch: str,
    own_pr: dict[str, Any] | None,
    runner: GhRunner,
) -> int:
    """Create own PR if missing; return PR number."""
    if own_pr is not None:
        return own_pr["number"]

    pr_bluf = "Render-only update for unreleased changelog fragments in CHANGELOG.md."
    pr_cmd = "Reproducer: bash scripts/release/concat-changelog-fragments.sh --write"
    pr_desc = (
        "Opened by the scheduled changelog render workflow (ADR-0252). "
        "Lands automatically via squash auto-merge once required checks pass."
    )
    pr_body = f"{pr_bluf}\n\n{pr_cmd}\n\n{pr_desc}\n"
    payload = json.dumps(
        {
            "title": "docs(changelog): render unreleased fragments",
            "head": branch,
            "base": default_branch,
            "body": pr_body,
        }
    ).encode("utf-8")
    resp_out = runner(
        ["api", "--method", "POST", f"repos/{repository}/pulls", "--input", "-"],
        payload,
    )
    return json.loads(resp_out)["number"]


def _arm_squash_merge(
    repository: str,
    pr_number: int,
    bot_user: str,
    bot_email: str,
    runner: GhRunner,
) -> int:
    """Arm squash auto-merge on pull request."""
    dco_checker = _load_dco_checker()
    trailer = f"Signed-off-by: {bot_user} <{bot_email}>"
    squash_body = f"Render unreleased changelog fragments.\n\n{trailer}\n"
    if not dco_checker(squash_body):
        print("error: squash body rejected by DCO trailer check", file=sys.stderr)
        return 1

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as tmp_f:
        tmp_f.write(squash_body)
        tmp_path = tmp_f.name

    try:
        runner(
            [
                "pr",
                "merge",
                str(pr_number),
                "--auto",
                "--squash",
                "--repo",
                repository,
                "--subject",
                f"docs(changelog): render unreleased fragments (#{pr_number})",
                "--body-file",
                tmp_path,
            ],
            None,
        )
    finally:
        pathlib.Path(tmp_path).unlink(missing_ok=True)
    print(f"armed squash auto-merge on #{pr_number}")
    return 0


def _get_bot_credentials(app_slug: str, runner: GhRunner) -> tuple[str, str] | None:
    """Resolve bot username and noreply email from the forge."""
    quoted_bot = urllib.parse.quote(f"{app_slug}[bot]")
    bot_user = f"{app_slug}[bot]"
    bot_id = runner(["api", f"users/{quoted_bot}", "--jq", ".id"], None).strip()
    if re.fullmatch(r"[1-9][0-9]*", bot_id) is None:
        print(
            f"error: bot user id for {bot_user} is not numeric: {bot_id}",
            file=sys.stderr,
        )
        return None
    return bot_user, f"{bot_id}+{bot_user}@users.noreply.github.com"


def execute_open_render_pr(
    repo: pathlib.Path,
    repository: str,
    branch: str,
    app_slug: str,
    due: bool,
    runner: GhRunner,
    env: dict[str, str] | None = None,
) -> int:
    """Execute changelog render PR workflow logic (ADR-0252)."""
    default_branch = runner(
        ["api", f"repos/{repository}", "--jq", ".default_branch"], None
    ).strip()
    if branch == default_branch:
        print(
            f"error: branch {branch!r} cannot be the repository default branch",
            file=sys.stderr,
        )
        return 1

    identity = _get_bot_credentials(app_slug, runner)
    if identity is None:
        return 1
    bot_user, bot_email = identity
    own_pr, rc = _find_own_pr(repository, branch, app_slug, runner)
    if rc != 0:
        return rc
    if not due:
        return _handle_due_false(repository, own_pr, runner)

    rc_render = _render_and_validate_status(repo, env)
    if rc_render != 0:
        return rc_render

    tip = run_git(["rev-parse", "HEAD"], repo, env=env)
    tip_tree = run_git(["rev-parse", "HEAD^{tree}"], repo, env=env)
    new_tree_sha, rc_tree = _create_blob_and_tree(
        repo, repository, tip_tree, runner, env
    )
    if rc_tree != 0 or new_tree_sha is None:
        return rc_tree

    branch_exists, is_current, _ = _check_branch_head(
        repository, branch, tip, new_tree_sha, runner
    )
    if is_current:
        print("render branch already current")
    else:
        commit_sha, rc_commit = _create_verified_commit(
            repository, tip, new_tree_sha, bot_user, bot_email, runner
        )
        if rc_commit != 0 or commit_sha is None:
            return rc_commit
        _update_or_create_ref(repository, branch, commit_sha, branch_exists, runner)

    pr_number = _ensure_pull_request(repository, branch, default_branch, own_pr, runner)
    verb = "refreshed" if own_pr is not None else "opened"
    print(f"{verb} render pull request #{pr_number}")
    return _arm_squash_merge(repository, pr_number, bot_user, bot_email, runner)


# --- Self-test suite ---


def _load_staleness_fixture_helpers() -> tuple[
    Callable[[pathlib.Path], dict[str, str]],
    Callable[[pathlib.Path, dict[str, str], pathlib.Path, int], pathlib.Path],
    Callable[[pathlib.Path, dict[str, str], str, int], None],
]:
    """Load test fixtures from check-changelog-staleness.py via importlib (HISS-19)."""
    path = pathlib.Path(__file__).resolve().parent / "check-changelog-staleness.py"
    spec = importlib.util.spec_from_file_location("check_staleness", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load staleness checker: no loader for {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return (
        getattr(mod, "make_test_env"),
        getattr(mod, "init_fixture_repo"),
        getattr(mod, "commit_in_fixture"),
    )


class StubForge:
    """In-memory stub recording calls and answering requests."""

    def __init__(
        self,
        default_branch: str = "master",
        bot_id: str = "987654",
        verified: bool = True,
        verification_reason: str = "valid",
        blob_sha_override: str | None = None,
        existing_branch_sha: str | None = None,
        head_commit_parents: list[str] | None = None,
        head_commit_tree: str | None = None,
        open_pulls: list[dict[str, Any]] | None = None,
    ) -> None:
        self.default_branch = default_branch
        self.bot_id = bot_id
        self.verified = verified
        self.verification_reason = verification_reason
        self.blob_sha_override = blob_sha_override
        self.existing_branch_sha = existing_branch_sha
        self.head_commit_parents = head_commit_parents
        self.head_commit_tree = head_commit_tree
        self.open_pulls = open_pulls if open_pulls is not None else []
        self.calls: list[tuple[list[str], bytes | None]] = []
        self.merge_body = ""

    def _calc_blob_sha(self, stdin: bytes | None) -> str:
        """Compute sha1 of git blob payload for stub forge."""
        if self.blob_sha_override:
            return self.blob_sha_override
        data = json.loads(stdin) if stdin else {}
        content = base64.b64decode(data.get("content", ""))
        header = f"blob {len(content)}\0".encode("ascii")
        return hashlib.sha1(  # nosec B324 - sha1 for git object id
            header + content, usedforsecurity=False
        ).hexdigest()

    def __call__(self, args: list[str], stdin: bytes | None) -> str:
        self.calls.append((list(args), stdin))
        call_str = " ".join(args)

        if "--jq .default_branch" in call_str:
            return self.default_branch
        if "--jq .id" in call_str:
            return self.bot_id
        if "pulls?state=open" in call_str:
            return json.dumps(self.open_pulls)
        if args[:2] == ["pr", "merge"]:
            self.merge_body = pathlib.Path(
                args[args.index("--body-file") + 1]
            ).read_text(encoding="utf-8")
            return ""
        if args[:2] == ["pr", "close"]:
            return ""
        if "git/blobs" in call_str:
            return json.dumps({"sha": self._calc_blob_sha(stdin)})
        if "git/trees" in call_str:
            return json.dumps({"sha": "tree-sha-mock-12345678901234567890"})
        if "git/matching-refs/heads" in call_str:
            if self.existing_branch_sha is not None:
                branch = args[1].split("matching-refs/heads/", 1)[1]
                return json.dumps(
                    [
                        {
                            "ref": f"refs/heads/{branch}",
                            "object": {"sha": self.existing_branch_sha},
                        }
                    ]
                )
            return json.dumps([])
        if "git/commits/" in call_str and "--method POST" not in call_str:
            return json.dumps(
                {
                    "sha": self.existing_branch_sha,
                    "parents": [{"sha": p} for p in (self.head_commit_parents or [])],
                    "tree": {"sha": self.head_commit_tree or ""},
                }
            )
        if "git/commits" in call_str:
            return json.dumps(
                {
                    "sha": "commit-sha-mock-12345678901234567890",
                    "verification": {
                        "verified": self.verified,
                        "reason": self.verification_reason,
                    },
                }
            )
        if "git/refs" in call_str:
            return json.dumps({"ref": "refs/heads/chore/changelog-render-auto"})
        if "pulls" in call_str and "--method POST" in call_str:
            return json.dumps({"number": 42})
        raise RuntimeError(f"unhandled stub call: {call_str}")


def _test_s1_s2_s3() -> int:
    """Test release-bot settings check cases s1, s2, s3."""
    rc_s1 = check_settings({"CLIENT_ID": "12345", "HAS_KEY": "true"})
    if rc_s1 != 0:
        print(f"self-test FAIL: s1 failed: rc={rc_s1}, want 0", file=sys.stderr)
        return 1
    print("self-test ok: s1 both settings present -> rc 0")

    rc_s2 = check_settings({"CLIENT_ID": "", "HAS_KEY": "true"})
    if rc_s2 != 1:
        print(f"self-test FAIL: s2 failed: rc={rc_s2}, want 1", file=sys.stderr)
        return 1
    print("self-test ok: s2 client id empty -> rc 1")

    rc_s3 = check_settings({"CLIENT_ID": "12345", "HAS_KEY": "false"})
    if rc_s3 != 1:
        print(f"self-test FAIL: s3 failed: rc={rc_s3}, want 1", file=sys.stderr)
        return 1
    print("self-test ok: s3 key flag false -> rc 1")
    return 0


def _calls(
    stub: StubForge, method: str, path_fragment: str = ""
) -> list[tuple[list[str], bytes | None]]:
    """Match recorded API methods or PR commands against joined argv."""
    marker = method if method.startswith("pr ") else f"--method {method}"
    return [
        call
        for call in stub.calls
        if marker in " ".join(call[0]) and path_fragment in " ".join(call[0])
    ]


def _forge_write_calls(stub: StubForge) -> list[tuple[list[str], bytes | None]]:
    """Return recorded writes, excluding API reads."""
    return [
        call
        for method in ("POST", "PATCH", "pr close", "pr merge")
        for call in _calls(stub, method)
    ]


def _capture_execute(
    repo: pathlib.Path,
    env: dict[str, str],
    repo_name: str,
    stub: StubForge,
    due: bool = True,
    branch: str = "chore/changelog-render-auto",
) -> tuple[int, str, str]:
    """Capture both output channels for every opener case."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = execute_open_render_pr(
            repo, repo_name, branch, "vmafx-release-bot", due, stub, env=env
        )
    return rc, out.getvalue(), err.getvalue()


def _case_result(name: str, passed: bool, result: tuple[int, str, str]) -> int:
    """Report a case with captured diagnostics on failure."""
    if not passed:
        print(
            f"self-test FAIL: {name}: rc={result[0]}, stdout={result[1]!r}, stderr={result[2]!r}",
            file=sys.stderr,
        )
        return 1
    print(f"self-test ok: {name}")
    return 0


def _opened_contract(stub: StubForge, repo_name: str) -> bool:
    """Check every expected write and bot identity for a new render PR."""
    blobs = _calls(stub, "POST", "git/blobs")
    trees = _calls(stub, "POST", "git/trees")
    commits = _calls(stub, "POST", "git/commits")
    refs = _calls(stub, "POST", "git/refs")
    pulls = _calls(stub, "POST", "/pulls")
    merges = _calls(stub, "pr merge")
    if not all(
        len(calls) == 1 for calls in (blobs, trees, commits, refs, pulls, merges)
    ):
        return False
    if len(_forge_write_calls(stub)) != 6:
        return False
    commit_body = commits[0][1]
    ref_body = refs[0][1]
    pull_body = pulls[0][1]
    if commit_body is None or ref_body is None or pull_body is None:
        return False
    commit = json.loads(commit_body)
    pull = json.loads(pull_body)
    trailer = "Signed-off-by: vmafx-release-bot[bot] <987654+vmafx-release-bot[bot]@users.noreply.github.com>"
    return (
        set(commit) == {"message", "tree", "parents"}
        and _load_dco_checker()(commit["message"])
        and trailer in commit["message"]
        and trailer in stub.merge_body
        and json.loads(ref_body)
        == {
            "ref": "refs/heads/chore/changelog-render-auto",
            "sha": "commit-sha-mock-12345678901234567890",
        }
        and pull["title"] == "docs(changelog): render unreleased fragments"
        and pull["head"] == "chore/changelog-render-auto"
        and pull["base"] == "master"
        and "--auto --squash" in " ".join(merges[0][0])
        and f"--repo {repo_name}" in " ".join(merges[0][0])
        and "--subject docs(changelog): render unreleased fragments (#42)"
        in " ".join(merges[0][0])
    )


def _test_o1(repo: pathlib.Path, env: dict[str, str], repo_name: str) -> int:
    """New PR: all six writes, signed bot identity, and outcome messages."""
    stub = StubForge()
    result = _capture_execute(repo, env, repo_name, stub)
    return _case_result(
        "o1 fragments owed, no branch, no PR",
        (
            result[0] == 0
            and _opened_contract(stub, repo_name)
            and "opened render pull request #42" in result[1]
            and "armed squash auto-merge on #42" in result[1]
        ),
        result,
    )


def _test_o2(repo: pathlib.Path, env: dict[str, str], repo_name: str) -> int:
    """Current base: close the own PR, with no other write."""
    stub = StubForge(
        open_pulls=[
            {
                "number": 101,
                "user": {"login": "vmafx-release-bot[bot]"},
                "head": {"repo": {"full_name": repo_name}},
            }
        ]
    )
    result = _capture_execute(repo, env, repo_name, stub, due=False)
    closes = _calls(stub, "pr close")
    return _case_result(
        "o2 base current, own PR open",
        (
            result[0] == 0
            and len(closes) == 1
            and len(_forge_write_calls(stub)) == 1
            and "pr close 101" in " ".join(closes[0][0])
            and f"--delete-branch --repo {repo_name}" in " ".join(closes[0][0])
            and "closed render pull request #101" in result[1]
        ),
        result,
    )


def _test_o3(repo: pathlib.Path, env: dict[str, str], repo_name: str) -> int:
    """Current base, no own PR: no write."""
    stub = StubForge()
    result = _capture_execute(repo, env, repo_name, stub, due=False)
    return _case_result(
        "o3 base current, no PR",
        (
            result[0] == 0
            and not _forge_write_calls(stub)
            and "no render pull request to close" in result[1]
        ),
        result,
    )


def _test_o4(repo: pathlib.Path, env: dict[str, str], repo_name: str) -> int:
    """Current branch: skip commit, refs and pulls; re-arm auto-merge."""
    tip = run_git(["rev-parse", "HEAD"], repo, env=env)
    stub = StubForge(
        existing_branch_sha="branchhead123",
        head_commit_parents=[tip],
        head_commit_tree="tree-sha-mock-12345678901234567890",
        open_pulls=[
            {
                "number": 104,
                "user": {"login": "vmafx-release-bot[bot]"},
                "head": {"repo": {"full_name": repo_name}},
            }
        ],
    )
    result = _capture_execute(repo, env, repo_name, stub)
    merges = _calls(stub, "pr merge")
    return _case_result(
        "o4 branch head current",
        (
            result[0] == 0
            and not _calls(stub, "POST", "git/commits")
            and not _calls(stub, "POST", "git/refs")
            and not _calls(stub, "PATCH", "git/refs")
            and not _calls(stub, "POST", "/pulls")
            and len(merges) == 1
            and len(_forge_write_calls(stub)) == 3
            and "--auto --squash" in " ".join(merges[0][0])
            and "render branch already current" in result[1]
            and "refreshed render pull request #104" in result[1]
            and "armed squash auto-merge on #104" in result[1]
        ),
        result,
    )


def _test_o5(repo: pathlib.Path, env: dict[str, str], repo_name: str) -> int:
    """Unverified commit: no ref, pull or merge write."""
    stub = StubForge(verified=False, verification_reason="bad_signature")
    result = _capture_execute(repo, env, repo_name, stub)
    return _case_result(
        "o5 unverified commit",
        (
            result[0] == 1
            and len(_forge_write_calls(stub)) == 3
            and len(_calls(stub, "POST", "git/commits")) == 1
            and not _calls(stub, "POST", "git/refs")
            and not _calls(stub, "PATCH", "git/refs")
            and not _calls(stub, "POST", "/pulls")
            and not _calls(stub, "pr merge")
            and "is not verified (bad_signature); ref not moved" in result[2]
        ),
        result,
    )


def _test_o6(repo: pathlib.Path, env: dict[str, str], repo_name: str) -> int:
    """Unexpected path: fail by path name before forge writes."""
    stray = repo / "stray.txt"
    stray.write_text("untracked\n", encoding="utf-8")
    stub = StubForge()
    try:
        result = _capture_execute(repo, env, repo_name, stub)
    finally:
        stray.unlink(missing_ok=True)
    return _case_result(
        "o6 stray path rejected",
        (result[0] == 1 and not _forge_write_calls(stub) and "stray.txt" in result[2]),
        result,
    )


def _test_o7(repo: pathlib.Path, env: dict[str, str], repo_name: str) -> int:
    """Empty owed render: fail before forge writes."""
    # Render and commit so working tree is clean and current
    render_script = repo / "scripts/release/concat-changelog-fragments.sh"
    subprocess.run(  # nosec B603 - fixed script argv  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - fixed script argv
        ["bash", str(render_script), "--write"],
        cwd=str(repo),
        check=True,
        capture_output=True,
        env=env,
        timeout=SUBPROCESS_TIMEOUT_S,
    )
    run_git(["add", "CHANGELOG.md"], repo, env=env)
    run_git(
        [
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-q",
            "-m",
            "Render clean",
        ],
        repo,
        env=env,
    )
    stub = StubForge()
    result = _capture_execute(repo, env, repo_name, stub)
    return _case_result(
        "o7 render changes nothing",
        (
            result[0] == 1
            and not _forge_write_calls(stub)
            and "render owed but the render changes nothing" in result[2]
        ),
        result,
    )


def _test_o8(repo: pathlib.Path, env: dict[str, str], repo_name: str) -> int:
    """Foreign author in this repository: fail by login and PR number."""
    _add_unrendered_frag(repo, env, "frag-o8")
    stub = StubForge(
        open_pulls=[
            {
                "number": 77,
                "user": {"login": "attacker"},
                "head": {"repo": {"full_name": repo_name}},
            }
        ]
    )
    result = _capture_execute(repo, env, repo_name, stub)
    return _case_result(
        "o8 foreign pull request on branch",
        (
            result[0] == 1
            and not _forge_write_calls(stub)
            and "attacker" in result[2]
            and "#77" in result[2]
        ),
        result,
    )


def _test_o9(repo: pathlib.Path, env: dict[str, str], repo_name: str) -> int:
    """Reject default branch and non-chore CLI branch."""
    stub = StubForge(default_branch="master")
    result = _capture_execute(repo, env, repo_name, stub, branch="master")
    cli = subprocess.run(  # nosec B603 - fixed local CLI validation argv  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - fixed local CLI validation argv
        [
            sys.executable,
            "-I",
            str(pathlib.Path(__file__).resolve()),
            "--due",
            "true",
            "--app-slug",
            "vmafx-release-bot",
            "--branch",
            "main-fix",
            "--repository",
            repo_name,
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=SUBPROCESS_TIMEOUT_S,
        env=env,
    )
    return _case_result(
        "o9 branch validations",
        (
            result[0] == 1
            and not _forge_write_calls(stub)
            and "cannot be the repository default branch" in result[2]
            and cli.returncode == 2
            and "--branch must start with" in cli.stderr
        ),
        result,
    )


def _add_unrendered_frag(
    repo: pathlib.Path,
    env: dict[str, str],
    name: str,
) -> None:
    """Ensure working tree is clean and add an unrendered fragment commit."""
    run_git(["checkout", "-q", "--", "CHANGELOG.md"], repo, env=env)
    frag = repo / f"changelog.d/added/{name}.md"
    frag.write_text(f"- Fragment {name}.\n", encoding="utf-8")
    run_git(["add", str(frag)], repo, env=env)
    run_git(
        [
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-q",
            "-m",
            f"Add fragment {name}",
        ],
        repo,
        env=env,
    )


def _test_o10(repo: pathlib.Path, env: dict[str, str], repo_name: str) -> int:
    """Blob mismatch: stop after the blob write, before the tree."""
    _add_unrendered_frag(repo, env, "frag-o10")
    stub = StubForge(blob_sha_override="deadbeef00000000000000000000000000000000")
    result = _capture_execute(repo, env, repo_name, stub)
    return _case_result(
        "o10 mismatched blob sha",
        (
            result[0] == 1
            and len(_forge_write_calls(stub)) == 1
            and len(_calls(stub, "POST", "git/blobs")) == 1
            and not _calls(stub, "POST", "git/trees")
            and "differs from git hash-object" in result[2]
        ),
        result,
    )


def _test_o11(repo: pathlib.Path, env: dict[str, str], repo_name: str) -> int:
    """Stale branch: create a commit and force-update the ref."""
    _add_unrendered_frag(repo, env, "frag-o11")
    stub = StubForge(
        existing_branch_sha="stalebranchhead123",
        head_commit_parents=["stale-parent-0000000000000000000000000000"],
        head_commit_tree="old-tree-0000000000000000000000000000000000",
    )
    result = _capture_execute(repo, env, repo_name, stub)
    patches = _calls(stub, "PATCH", "git/refs/heads")
    payload = (
        json.loads(patches[0][1])
        if len(patches) == 1 and patches[0][1] is not None
        else {}
    )
    return _case_result(
        "o11 existing branch stale parent",
        (
            result[0] == 0
            and len(_forge_write_calls(stub)) == 6
            and len(_calls(stub, "POST", "git/commits")) == 1
            and payload.get("force") is True
            and "opened render pull request #42" in result[1]
        ),
        result,
    )


def _test_o12(repo: pathlib.Path, env: dict[str, str], repo_name: str) -> int:
    """Ignore fork and deleted-fork PRs, then open our own PR."""
    stub = StubForge(
        open_pulls=[
            {
                "number": 120,
                "user": {"login": "someone"},
                "head": {"repo": {"full_name": "someone/pelorus"}},
            },
            {"number": 121, "user": {"login": "someone"}, "head": {"repo": None}},
        ]
    )
    result = _capture_execute(repo, env, repo_name, stub)
    return _case_result(
        "o12 fork and deleted fork ignored",
        (
            result[0] == 0
            and _opened_contract(stub, repo_name)
            and "opened render pull request #42" in result[1]
        ),
        result,
    )


def _test_o13(repo: pathlib.Path, env: dict[str, str], repo_name: str) -> int:
    """Reject an invalid bot ID before any forge write."""
    stub = StubForge(bot_id="null")
    result = _capture_execute(repo, env, repo_name, stub)
    return _case_result(
        "o13 nonnumeric bot id",
        (
            result[0] == 1
            and not _forge_write_calls(stub)
            and "bot user id for vmafx-release-bot[bot] is not numeric: null"
            in result[2]
        ),
        result,
    )


def _run_open_pr_cases(
    repo: pathlib.Path,
    env: dict[str, str],
    repo_name: str,
) -> int:
    """Run opener self-test cases o1 through o13 in isolated fixture branches."""
    # Reset fixture with unrendered fragment for tests
    (repo / "changelog.d/added/test-frag.md").write_text(
        "- Fragment.\n", encoding="utf-8"
    )
    run_git(["add", "changelog.d/added/test-frag.md"], repo, env=env)
    run_git(
        [
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-q",
            "-m",
            "Add fragment",
        ],
        repo,
        env=env,
    )

    cases: list[Callable[[], int]] = [
        lambda: _test_o1(repo, env, repo_name),
        lambda: _test_o2(repo, env, repo_name),
        lambda: _test_o3(repo, env, repo_name),
        lambda: _test_o4(repo, env, repo_name),
        lambda: _test_o5(repo, env, repo_name),
        lambda: _test_o6(repo, env, repo_name),
        lambda: _test_o7(repo, env, repo_name),
        lambda: _test_o8(repo, env, repo_name),
        lambda: _test_o9(repo, env, repo_name),
        lambda: _test_o10(repo, env, repo_name),
        lambda: _test_o11(repo, env, repo_name),
        lambda: _test_o12(repo, env, repo_name),
        lambda: _test_o13(repo, env, repo_name),
    ]
    for case in cases:
        if case() != 0:
            return 1
    return 0


def run_self_test(repo_root: pathlib.Path) -> int:
    """Run full self-test suite for open-render-pr.py."""
    if _test_s1_s2_s3() != 0:
        return 1

    script_source = repo_root / "scripts/release/concat-changelog-fragments.sh"
    if not script_source.is_file():
        print(f"self-test: missing render script at {script_source}", file=sys.stderr)
        return 1

    make_env, init_repo, _ = _load_staleness_fixture_helpers()
    now = 1762758000
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = pathlib.Path(tmp_str)
        env = make_env(tmp)
        fixture_repo = tmp / "fixture"
        init_repo(fixture_repo, env, script_source, now)
        repo_name = "VMAFx/pelorus"

        if _run_open_pr_cases(fixture_repo, env, repo_name) != 0:
            return 1

    print("self-test passed")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for open-render-pr.py."""
    default_repo = pathlib.Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(
        description="Open or close scheduled changelog render pull request."
    )
    parser.add_argument(
        "--check-settings",
        action="store_true",
        help="verify that release-bot client id and private key settings are present",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="run self-test suite in an isolated fixture repository",
    )
    parser.add_argument(
        "--due",
        choices=["true", "false"],
        help="whether changelog render pull request is due (true or false)",
    )
    parser.add_argument(
        "--app-slug",
        help="GitHub App slug (e.g. vmafx-release-bot)",
    )
    parser.add_argument(
        "--branch",
        help="render pull request head branch (must start with chore/)",
    )
    parser.add_argument(
        "--repo",
        type=pathlib.Path,
        default=default_repo,
        help="path to local repository root",
    )
    parser.add_argument(
        "--repository",
        help="GitHub owner/name repository slug (default: $GITHUB_REPOSITORY)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point for open-render-pr.py."""
    args = parse_args(argv)
    if args.self_test:
        return run_self_test(args.repo)
    if args.check_settings:
        return check_settings(dict(os.environ))

    if args.due is None:
        print("error: --due is required (true or false)", file=sys.stderr)
        return 2
    if args.app_slug is None or not SLUG_PATTERN.match(args.app_slug):
        print(f"error: --app-slug must match {SLUG_PATTERN.pattern}", file=sys.stderr)
        return 2
    if args.branch is None or not args.branch.startswith("chore/"):
        print("error: --branch must start with 'chore/'", file=sys.stderr)
        return 2

    repository = args.repository or os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not repository:
        print(
            "error: --repository or GITHUB_REPOSITORY environment variable is required",
            file=sys.stderr,
        )
        return 1

    due = args.due == "true"
    runner = _make_gh_runner(args.repo)
    return execute_open_render_pr(
        args.repo,
        repository,
        args.branch,
        args.app_slug,
        due,
        runner,
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
