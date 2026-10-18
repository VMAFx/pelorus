#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Validate .github/workflows/codeql.yml structure, pins, permissions and triggers."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "codeql.yml"

REQUIRED_LANGUAGES = ("c-cpp", "python", "actions")
PINNED_USES_RE = re.compile(
    r"^[ ]+(?:- )?uses:\s+[^@\s]+@[0-9a-f]{40}\s+#\s+v\S+"
)
USES_LINE_RE = re.compile(r"^[ ]+(?:- )?uses:\s*(\S+)")


def split_workflow_sections(text: str) -> tuple[str, str]:
    """Split workflow text into top-level header and jobs section."""
    marker = "\njobs:\n"
    idx = text.find(marker)
    if idx == -1:
        return text, ""
    return text[:idx], text[idx + len(marker):]


def check_triggers(header: str) -> list[str]:
    """Verify workflow triggers include pull_request and push to master."""
    errors: list[str] = []
    if not re.search(r"^\s+pull_request:", header, re.MULTILINE):
        errors.append("triggers must include pull_request")
    has_push = bool(re.search(r"^\s+push:", header, re.MULTILINE))
    has_master = bool(
        re.search(r"branches:\s*\[.*master.*\]", header)
        or re.search(r"-\s+master", header)
    )
    if not (has_push and has_master):
        errors.append("triggers must include push to master")
    return errors


def check_permissions(header: str, jobs: str) -> list[str]:
    """Verify security-events: write is only present at job level, never top-level."""
    errors: list[str] = []
    if "security-events:" in header:
        errors.append("security-events permission must not be declared at top-level")
    if not re.search(r"security-events:\s*write", jobs):
        errors.append("job level must declare security-events: write")
    if not re.search(r"contents:\s*read", header):
        errors.append("top-level permissions must declare contents: read")
    return errors


def check_languages(jobs: str) -> list[str]:
    """Verify all required languages (c-cpp, python, actions) are declared."""
    errors: list[str] = []
    for lang in REQUIRED_LANGUAGES:
        pattern = rf"language:\s*['\"]?{re.escape(lang)}['\"]?"
        if not re.search(pattern, jobs):
            errors.append(f"required CodeQL language {lang} missing from matrix")
    return errors


def check_pinned_uses(text: str) -> list[str]:
    """Verify every uses: line is pinned to a 40-hex SHA with a '# v' comment."""
    errors: list[str] = []
    lines = text.splitlines()
    uses_count = 0
    for line in lines:
        if USES_LINE_RE.search(line):
            uses_count += 1
            if not PINNED_USES_RE.match(line):
                errors.append(f"unpinned uses line: {line.strip()}")
    if uses_count == 0:
        errors.append("no uses: actions found in workflow")
    return errors


def check_step_order(jobs: str) -> list[str]:
    """Verify draft stop step is the first step and precedes checkout."""
    errors: list[str] = []
    steps_idx = jobs.find("\n    steps:\n")
    if steps_idx == -1:
        return ["missing steps: section in jobs"]
    steps_body = jobs[steps_idx:]
    draft_idx = steps_body.find("Stop on a draft pull request")
    if draft_idx == -1:
        errors.append("missing 'Stop on a draft pull request' step")
        return errors
    checkout_idx = steps_body.find("actions/checkout@")
    if checkout_idx == -1:
        errors.append("missing 'actions/checkout' step")
        return errors
    if draft_idx > checkout_idx:
        errors.append("'Stop on a draft pull request' step must precede checkout")

    step_matches = list(
        re.finditer(r"^[ ]{6}-[ ]+(name|uses):[ ]*(.*)$", steps_body, re.MULTILINE)
    )
    if not step_matches:
        errors.append("no steps found under steps:")
    else:
        first_step = step_matches[0].group(2).strip()
        if "Stop on a draft pull request" not in first_step:
            errors.append(
                f"first step under steps: must be 'Stop on a draft pull request', found '{first_step}'"
            )
    return errors


def validate_workflow_text(text: str) -> list[str]:
    """Validate full workflow text against all rules."""
    header, jobs = split_workflow_sections(text)
    if not jobs:
        return ["missing jobs: section"]
    errors: list[str] = []
    errors.extend(check_triggers(header))
    errors.extend(check_permissions(header, jobs))
    errors.extend(check_languages(jobs))
    errors.extend(check_step_order(jobs))
    errors.extend(check_pinned_uses(text))
    return errors


def validate_workflow_file(path: Path) -> list[str]:
    """Validate workflow file on disk."""
    if not path.is_file():
        return [f"workflow file not found: {path}"]
    return validate_workflow_text(path.read_text(encoding="utf-8"))


def build_self_test_mutations(real_text: str) -> dict[str, tuple[str, str]]:
    """Build in-memory mutations per rule with their expected error substrings."""
    draft_step = (
        "      - name: Stop on a draft pull request\n"
        "        if: github.event.pull_request.draft == true\n"
        "        shell: bash\n"
        "        env:\n"
        "          TITLE: Gate not run on a draft\n"
        "          MESSAGE: >-\n"
        "            The gate did not run because the pull request is a draft.\n"
        "            It runs when the pull request is marked ready for review.\n"
        "        run: |\n"
        "          echo \"::error title=$TITLE::$MESSAGE\"\n"
        "          echo \"$MESSAGE\" >> \"$GITHUB_STEP_SUMMARY\"\n"
        "          exit 1"
    )
    checkout_step = (
        "      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1\n"
        "        if: github.event.pull_request.draft != true\n"
        "        with:\n"
        "          persist-credentials: false"
    )
    return {
        "drop a language": (
            real_text.replace("- language: c-cpp", "# dropped c-cpp"),
            "required CodeQL language c-cpp missing from matrix",
        ),
        "unpin init to @v4": (
            re.sub(
                r"github/codeql-action/init@[0-9a-f]{40}\s+#\s+v\S+",
                "github/codeql-action/init@v4",
                real_text,
            ),
            "unpinned uses line",
        ),
        "move draft step after checkout": (
            real_text.replace(
                f"{draft_step}\n{checkout_step}",
                f"{checkout_step}\n{draft_step}",
            ),
            "'Stop on a draft pull request' step must precede checkout",
        ),
        "top-level security-events write": (
            real_text.replace(
                "permissions:\n  contents: read\n",
                "permissions:\n  contents: read\n  security-events: write\n",
            ),
            "security-events permission must not be declared at top-level",
        ),
        "drop push trigger": (
            real_text.replace("  push:\n    branches: [master]\n", ""),
            "triggers must include push to master",
        ),
    }


def run_self_test(path: Path) -> int:
    """Run self-test mutation battery against real file."""
    if not path.is_file():
        sys.stderr.write(f"self-test failed: {path} not found\n")
        return 1
    real_text = path.read_text(encoding="utf-8")
    real_errors = validate_workflow_text(real_text)
    if real_errors:
        sys.stderr.write(
            "self-test failed: real file has errors:\n  "
            + "\n  ".join(real_errors)
            + "\n"
        )
        return 1

    mutations = build_self_test_mutations(real_text)
    failures: list[str] = []
    for name, (mutated_text, expected_err) in mutations.items():
        if mutated_text == real_text:
            failures.append(f"{name}: mutation did not change file text")
            continue
        errs = validate_workflow_text(mutated_text)
        if not any(expected_err in err for err in errs):
            failures.append(f"{name}: expected error '{expected_err}', got: {errs}")

    if failures:
        sys.stderr.write("self-test failures:\n  " + "\n  ".join(failures) + "\n")
        return 1
    print(f"self-test passed ({len(mutations)} mutations verified)")
    return 0


def main() -> int:
    """Entry point for checking CodeQL workflow."""
    if "--self-test" in sys.argv:
        return run_self_test(WORKFLOW_PATH)
    errors = validate_workflow_file(WORKFLOW_PATH)
    if errors:
        sys.stderr.write(
            "CodeQL workflow validation failed:\n  " + "\n  ".join(errors) + "\n"
        )
        return 1
    print("CodeQL workflow validation passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
