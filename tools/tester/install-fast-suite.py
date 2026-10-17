#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Install native Meson fast tests and their arguments into a relocatable kit.

Usage: python3 -I install-fast-suite.py TESTS_JSON BUILD_DIR SOURCE_DIR DESTINATION
TESTS_JSON is the output of meson introspect --tests BUILD_DIR.
Run after ninja and meson install; DESTINATION is the installed prefix's tests/.
The prefix must use libdir=lib, as in the tester Containerfile. Repository
scripts and shader compiler checks remain build-host gates, outside this kit.
"""

import argparse
import json
import re
import shlex
import shutil
from pathlib import Path

MAX_METADATA_BYTES = 4 * 1024 * 1024


def relative_to(path, root):
    """Keep lexical paths: resolving symlinks would change fixture semantics."""
    try:
        return Path(path).relative_to(root)
    except ValueError:
        return None


def copy_argument(value, build, source, destination):
    """Copy an absolute binary/fixture; preserve flags and relative arguments."""
    path = Path(value)
    if not path.is_absolute():
        return shlex.quote(value)
    for root, directory in ((build, "bin"), (source, "fixtures")):
        relative = relative_to(path, root)
        if relative is not None:
            if ".." in relative.parts or not path.is_file():
                raise ValueError(f"missing or unsafe test input: {path}")
            target = destination / directory / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            return '"$suite_root"/' + shlex.quote(f"{directory}/{relative}")
    raise ValueError(f"test input outside build/source trees: {path}")


def test_environment(test, build):
    """Preserve Meson overrides; replace only its build-time library search path."""
    values = []
    for key, value in test["env"].items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ValueError(f"invalid environment key: {key}")
        if key == "LD_LIBRARY_PATH":
            if value != str(build / "libpelorus"):
                raise ValueError(f"unsupported library path: {value}")
            values.append('LD_LIBRARY_PATH="$LD_LIBRARY_PATH"')
        else:
            if str(build) in value:
                raise ValueError(f"unrelocated environment value: {key}={value}")
            values.append(shlex.quote(f"{key}={value}"))
    return values


def test_command(test, build, source, destination):
    """Run in a private writable Meson build workdir; bound every invocation."""
    if test["protocol"] != "exitcode" or test.get("extra_paths"):
        raise ValueError(f"unsupported test protocol/search path: {test['name']}")
    workdir = Path(test["workdir"]) if test["workdir"] else build
    relative = relative_to(workdir, build)
    if relative is None or ".." in relative.parts:
        raise ValueError(f"workdir outside build tree: {workdir}")
    timeout = test["timeout"]
    if not isinstance(timeout, (int, float)) or not 0 < timeout <= 300:
        raise ValueError(f"unbounded test timeout: {test['name']}")
    command = ["env", *test_environment(test, build), "timeout", "--kill-after=5s", f"{timeout}s"]
    command += [copy_argument(arg, build, source, destination) for arg in test["cmd"]]
    cwd = '"$scratch"/' + shlex.quote(str(relative))
    return [f"printf '%s\\n' {shlex.quote('=== ' + test['name'] + ' ===')}",
            f"(mkdir -p {cwd} && cd {cwd} && {' '.join(command)})"]


def install(tests, build, source, destination):
    selected = [test for test in tests
                if any(suite.split(":")[-1] == "fast" for suite in test["suite"])
                and relative_to(test["cmd"][0], build) is not None]
    if not selected:
        raise ValueError("no native fast tests: configure with -Dtests=true and build first")
    template = Path(__file__).with_name("run-fast-suite.sh.in").read_text(encoding="utf-8")
    lines = [template.rstrip()]
    for test in selected:
        lines.extend(test_command(test, build, source, destination))
    lines.append(f"printf '%s\\n' 'PASS: {len(selected)} native fast tests'")
    runner = destination / "run-fast-suite.sh"
    runner.write_text("\n".join(lines) + "\n", encoding="utf-8")
    runner.chmod(0o755)
    print(f"Installed {len(selected)} native fast tests: {runner}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("tests_json", "build_dir", "source_dir", "destination"):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    with args.tests_json.open("rb") as metadata:
        data = metadata.read(MAX_METADATA_BYTES + 1)
    if len(data) > MAX_METADATA_BYTES:
        raise ValueError(f"test metadata exceeds {MAX_METADATA_BYTES} bytes")
    install(json.loads(data), args.build_dir.resolve(), args.source_dir.resolve(),
            args.destination.resolve())


if __name__ == "__main__":
    main()
