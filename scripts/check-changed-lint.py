#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Lint committed changes with cppcheck deltas and Ruff formatting (ADR-0258).

Usage:
    python3 -I scripts/check-changed-lint.py [--base REF] [--head REF] [--jobs N]
    python3 -I scripts/check-changed-lint.py --self-test
"""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
import io
import os
from pathlib import Path
import re
import shutil
import subprocess  # nosec B404 - explicit argv, no shell, bounded tool calls
import sys
import tarfile
import tempfile

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from tracked_python import _git_executable, _run_git, repo_discovery_env  # noqa: E402


@dataclass(frozen=True)
class Repository:
    """A repository and its explicit discovery environment."""

    root: Path
    env: dict[str, str]

    def git(self, *args: str) -> bytes:
        """Reuse the shared Git runner and its deadline."""
        return _run_git(
            [_git_executable(), "-C", str(self.root), *args], env=self.env
        ).stdout


def run_tool(
    argv: list[str], root: Path, timeout: int = 60
) -> subprocess.CompletedProcess[str]:
    """Capture a bounded tool invocation; callers interpret its status."""
    return subprocess.run(  # nosec B603 - resolved tool, list argv, no shell, explicit deadline  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - resolved argv, no shell, explicit deadline
        argv,  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-tainted-env-args.dangerous-subprocess-use-tainted-env-args - argv[0] is the cppcheck or Ruff the operator names (--cppcheck, RUFF, PYTHON_LINT_ENV), resolved by shutil.which to an absolute executable; the rest are fixed flags and committed paths; list argv, no shell, explicit deadline
        cwd=root,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def ruff_path() -> str:
    """Mirror the shell lint entry point, including Windows virtualenvs."""
    if os.environ.get("RUFF"):
        return os.environ["RUFF"]
    directory = os.environ.get("PYTHON_LINT_BIN")
    if not directory and os.environ.get("PYTHON_LINT_ENV"):
        venv = Path(os.environ["PYTHON_LINT_ENV"])
        layout = "bin"
        if (venv / "Scripts").is_dir() and not (venv / "bin").is_dir():
            layout = "Scripts"
        directory = str(venv / layout)
    return str(Path(directory) / "ruff") if directory else "ruff"


def resolve_tools(root: Path, cppcheck: str) -> tuple[str, str, str]:
    """Resolve both tools, print versions once, and refuse missing tools."""
    candidates = (
        (cppcheck, "apt install cppcheck"),
        (ruff_path(), "make python-lint"),
    )
    resolved, versions = [], []
    for candidate, fix in candidates:
        executable = shutil.which(candidate)
        if executable is None:
            raise RuntimeError(f"{candidate} not found; fix: {fix}")
        proc = run_tool([executable, "--version"], root)
        if proc.returncode or not proc.stdout.strip():
            raise RuntimeError(f"{candidate} --version failed: {proc.stderr.strip()}")
        resolved.append(str(Path(executable).resolve()))
        versions.append(proc.stdout.strip().splitlines()[0])
        print(versions[-1])
    return resolved[0], resolved[1], versions[0].removeprefix("Cppcheck ")


def revisions(repo: Repository, base: str, head: str) -> tuple[str, str]:
    """Resolve commits before diffing; give an actionable missing-base error."""
    try:
        base_sha = repo.git("rev-parse", "--verify", f"{base}^0").decode().strip()
    except RuntimeError as exc:
        raise RuntimeError(f"base {base} not found; git fetch origin: {exc}") from exc
    head_sha = repo.git("rev-parse", "--verify", f"{head}^0").decode().strip()
    return repo.git("merge-base", base_sha, head_sha).decode().strip(), head_sha


def extract_tree(repo: Repository, rev: str, dest: Path) -> None:
    """Read committed content only, with tar's data extraction filter."""
    raw = repo.git("archive", "--format=tar", rev)
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        archive.extractall(dest, filter="data")


def changed_paths(repo: Repository, base: str, head: str) -> list[tuple[str, str]]:
    """Keep NUL-delimited paths intact, including whitespace and newlines."""
    parts = repo.git("diff", "--name-status", "-z", "--no-renames", base, head)
    fields = parts.decode("utf-8", "surrogateescape").split("\0")[:-1]
    return list(zip(fields[::2], fields[1::2], strict=True))


def ranges(repo: Repository, base: str, head: str, path: str):
    """Yield added line intervals with an exclusive end, ignoring pure deletions."""
    diff = repo.git("diff", "-U0", "--no-renames", base, head, "--", path)
    for match in re.finditer(rb"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", diff, re.M):
        start, count = int(match[1]), int(match[2] or b"1")
        if count:
            yield start, start + count


def format_span(tree, path, span, ruff) -> int:
    """Check one file or interval, preserving Ruff's tool-error distinction."""
    flags = "format --check --no-cache --force-exclude --config".split()
    argv = [ruff, *flags, str(tree / "ruff.toml")]
    if span is not None:
        argv += ["--range", f"{span[0]}-{span[1]}"]
    proc = run_tool([*argv, "--", path], tree)
    if proc.returncode not in (0, 1):
        raise RuntimeError(f"ruff format {path}: {proc.stderr.strip()}")
    if proc.returncode == 1:
        location = f":{span[0]}-{span[1] - 1}" if span else ""
        fix = f"--range {span[0]}-{span[1]} " if span else ""
        print(
            f"{path}{location}: changed lines are not ruff-formatted "
            f"(fix: ruff format {fix}{path})"
        )
    return proc.returncode


def ruff_format(repo, base, head, tree, paths, ruff) -> int:
    """Check added files or changed ranges in existing Python files."""
    files = intervals = unformatted = 0
    for status, path in paths:
        if Path(path).suffix not in {".py", ".pyi"} or not (tree / path).is_file():
            continue
        files += 1
        spans = [None] if status == "A" else list(ranges(repo, base, head, path))
        for span in spans:
            intervals += int(span is not None)
            unformatted += format_span(tree, path, span, ruff)
    print(
        f"check-changed-lint: ruff format: {files} files, {intervals} ranges, "
        f"{unformatted} unformatted"
    )
    return unformatted


def cppcheck(tree: Path, executable: str, jobs: int) -> list[tuple[str, ...]]:
    """Scan the tree's declared translation units and reject unparsed output."""
    lane = tree / ".config/clang-tidy/lane-files.txt"
    if not lane.is_file():
        return []
    units = [line.split("#", 1)[0].strip() for line in lane.read_text().splitlines()]
    units = [line for line in units if line]
    if not units:
        return []
    file_list = tree.parent / f"{tree.name}-units.txt"
    file_list.write_text("\n".join(units) + "\n", encoding="utf-8")
    flags = "--enable=style --std=c11 --language=c --quiet".split()
    includes = "-I libpelorus/include -I libpelorus/src -I tools".split()
    template = "--template={file}\t{line}\t{id}\t{severity}\t{message}"
    argv = [executable, *flags, "-j", str(jobs), *includes, template]
    argv.append(f"--file-list={file_list}")
    proc = run_tool(argv, tree, 300)
    if proc.returncode:
        raise RuntimeError(f"cppcheck exit {proc.returncode}: {proc.stderr.strip()}")
    return parse_cppcheck(proc.stderr)


def parse_cppcheck(output: str) -> list[tuple[str, ...]]:
    """Refuse any nonempty diagnostic outside the five-field template."""
    findings = []
    for line in output.splitlines():
        if not line:
            continue
        fields = tuple(line.split("\t"))
        if len(fields) != 5 or not fields[1].isdigit():
            raise RuntimeError(f"cppcheck unexpected output: {line}")
        findings.append(fields)
    return findings


def finding_key(tree: Path, finding: tuple[str, ...]) -> tuple[str, str, str]:
    """Source text survives line moves; counts still distinguish duplicates."""
    path, number, ident, _, _ = finding
    source = tree / path
    lines = []
    if source.is_file():
        lines = source.read_text(encoding="utf-8", errors="replace").splitlines()
    index = int(number) - 1
    text = " ".join(lines[index].split()) if 0 <= index < len(lines) else ""
    return path, ident, text


def cppcheck_delta(repo, base, headtree, scratch, tool, jobs, version) -> int:
    """Compare finding multisets, reporting only new occurrences."""
    basetree = scratch / "base"
    extract_tree(repo, base, basetree)
    before = cppcheck(basetree, tool, jobs)
    after = cppcheck(headtree, tool, jobs)
    new = Counter(finding_key(headtree, item) for item in after)
    new.subtract(Counter(finding_key(basetree, item) for item in before))
    count = sum(max(0, value) for value in new.values())
    for item in after:
        key = finding_key(headtree, item)
        if new[key] > 0:
            path, line, ident, severity, message = item
            print(f"{path}:{line}: {severity} {ident}: {message}")
            new[key] -= 1
    print(
        f"check-changed-lint: cppcheck {version}: base {len(before)}, "
        f"head {len(after)}, new {count}"
    )
    return count


def check(repo: Repository, base: str, head: str, jobs: int, tools) -> int:
    """Return 0 clean, 1 findings, 2 tool/Git/input failure, without tracebacks."""
    try:
        base, head = revisions(repo, base, head)
        paths = changed_paths(repo, base, head)
        with tempfile.TemporaryDirectory(prefix="changed-lint-") as directory:
            scratch = Path(directory)
            tree = scratch / "head"
            extract_tree(repo, head, tree)
            findings = ruff_format(repo, base, head, tree, paths, tools[1])
            needs_c = any(
                Path(path).suffix in {".c", ".h"}
                or path == ".config/clang-tidy/lane-files.txt"
                for _, path in paths
            )
            if needs_c:
                findings += cppcheck_delta(
                    repo, base, tree, scratch, tools[0], jobs, tools[2]
                )
            else:
                print("cppcheck: no C change, skipped")
        return int(findings > 0)
    except (
        RuntimeError,
        OSError,
        ValueError,
        tarfile.TarError,
        subprocess.TimeoutExpired,
    ) as exc:
        print(f"check-changed-lint: {exc}", file=sys.stderr)
        return 2


def fixture_env() -> dict[str, str]:
    """Strip every inherited Git variable; isolate fixture config and identity."""
    env = {
        key: value
        for key, value in repo_discovery_env().items()
        if not key.startswith("GIT_")
    }
    env.update(
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        GIT_AUTHOR_NAME="Self Test",
        GIT_AUTHOR_EMAIL="test@example.invalid",
        GIT_COMMITTER_NAME="Self Test",
        GIT_COMMITTER_EMAIL="test@example.invalid",
        GIT_AUTHOR_DATE="2026-11-14T00:00:00+0000",
        GIT_COMMITTER_DATE="2026-11-14T00:00:00+0000",
    )
    return env


def fixture_commit(repo: Repository, files: dict[str, str]) -> str:
    """Commit fixture files with signing disabled only for that invocation."""
    for name, text in files.items():
        path = repo.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    repo.git("add", ".")
    repo.git("-c", "commit.gpgsign=false", "commit", "-qm", "fixture")
    return repo.git("rev-parse", "HEAD").decode().strip()


def fixture_files(root: Path) -> dict[str, str]:
    """Include old lint debt so fixtures detect accidental whole-tree checks."""
    return {
        ".config/clang-tidy/lane-files.txt": "src/a.c\nsrc/b.c\n",
        "src/a.c": "int planted(void)\n{\n    return 0;\n}\n",
        "src/b.c": (
            "int existing(void)\n{\n    int unused = 0;\n"
            "    unused = 1;\n    return 0;\n}\n"
        ),
        "pkg/old.py": "X =(1,2)\nZ = 1\n\n\ndef f():\n    return 1\n",
        "ruff.toml": (root / "ruff.toml").read_text(),
    }


def fixture_cases(initial):
    """Each case changes a fresh copy of the same baseline."""
    clean, unused, old = (initial[p] for p in ("src/a.c", "src/b.c", "pkg/old.py"))
    edits = {
        "src/a.c": clean.replace("0", "1"),
        "pkg/old.py": old.replace("return 1", "return 2"),
    }
    yield "a clean C and Python", edits, 0, ""
    yield "b planted unreadVariable", {"src/a.c": unused}, 1, "unreadVariable"
    yield "c planted Python", {"pkg/new.py": "Y =(1,2)\n"}, 1, "pkg/new.py"
    yield "d adjacent Python", {"pkg/old.py": old.replace("Z = 1", "Z = 2")}, 0, ""
    modified = {"pkg/old.py": old.replace("Z = 1", "Z =(1,2)")}
    yield "e modified Python range", modified, 1, "pkg/old.py:2-2"
    extra = "\nint other(void)\n{\n    return 2;\n}\n"
    yield "f unchanged finding", {"src/b.c": unused + extra}, 0, ""
    yield "g moved finding", {"src/b.c": "\n\n" + unused}, 0, ""
    yield "h unknown base", {"pkg/new.py": "Y = 2\n"}, 2, "base unknown not found"


def self_test(root: Path, tools, jobs: int) -> int:
    """Exercise real tools against independent committed base/head fixtures."""
    initial = fixture_files(root)
    failures = 0
    for name, edits, expected, needle in fixture_cases(initial):
        with tempfile.TemporaryDirectory(prefix="changed-lint-test-") as directory:
            repo = Repository(Path(directory), fixture_env())
            repo.git("init", "-q")
            base = fixture_commit(repo, initial)
            head = fixture_commit(repo, edits)
            output = io.StringIO()
            with redirect_stdout(output), redirect_stderr(output):
                rc = check(
                    repo, "unknown" if expected == 2 else base, head, jobs, tools
                )
            if rc == expected and needle in output.getvalue():
                print(f"ok: {name}")
            else:
                failures += 1
                print(f"FAIL: {name} (rc {rc}, want {expected})\n{output.getvalue()}")
    return int(failures > 0)


def job_count(value: str) -> int:
    """Accept plain decimal digits only; reject unreasonable fan-out as a usage error."""
    if not re.fullmatch(r"[0-9]{1,2}", value):
        raise argparse.ArgumentTypeError("jobs must be decimal digits in 1..32")
    number = int(value)
    if not 1 <= number <= 32:
        raise argparse.ArgumentTypeError("jobs must be in 1..32")
    return number


def main() -> int:
    """Parse the public CLI, resolve tools once, and dispatch the check."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="origin/master")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--jobs", default=4, type=job_count)
    parser.add_argument(
        "--cppcheck",
        default="cppcheck",
        help="cppcheck program name on PATH or path (default: cppcheck)",
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        env = repo_discovery_env()
        repo = Repository(Path.cwd(), env)
        root = Path(repo.git("rev-parse", "--show-toplevel").decode().strip())
        tools = resolve_tools(root, args.cppcheck)
        if args.self_test:
            return self_test(root, tools, args.jobs)
        return check(Repository(root, env), args.base, args.head, args.jobs, tools)
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"check-changed-lint: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
