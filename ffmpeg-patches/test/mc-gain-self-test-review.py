# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Exercise mc gain review regressions through stubs and the CLI dispatch."""

from __future__ import annotations

from contextlib import redirect_stdout, redirect_stderr
from functools import partial
import importlib
import io
import json
import math
import os
from pathlib import Path
import re
import sys
from types import ModuleType
from unittest.mock import patch

_tests = importlib.import_module("mc-gain-self-test")
expect, stub_fixture = _tests.expect, _tests.stub_fixture
STUB_FFMPEG = _tests.STUB_FFMPEG


def enumeration_case(runner: ModuleType, record: dict, folder: Path, mode: str) -> str:
    """Exercise explicit end markers, failed probes, and the enumeration cap."""
    entry = record["devices"][0]
    line = f"Device 0 selected: {entry['name']} (discrete) ({entry['device_id']})"
    knobs = {
        "failed": dict(probe_index=1, probe_rc=1, probe_output="initialization failed"),
        "blank": dict(probe_rc=0, probe_output="unrelated output"),
        "none": dict(probe_rc=1, probe_output="No devices found: unavailable!"),
        "none-later": dict(probe_index=1, probe_output="No devices found"),
        "failed-valid": dict(
            probe_index=1, probe_rc=1, probe_output=line.replace("Device 0", "Device 1")
        ),
        "wrong-index": dict(probe_index=1, probe_output=line),
    }
    names = [entry] * (7 if mode == "seven" else 8 if mode == "cap" else 2)
    _, env = stub_fixture(runner, folder, names, 3, probe=knobs.get(mode))
    if mode == "timeout":
        with patch.object(
            runner, "run_ffmpeg", return_value=runner.RunResult(124, "timed out", "")
        ):
            return str(runner.enumerate_devices(STUB_FFMPEG, env))
    return str(len(runner.enumerate_devices(STUB_FFMPEG, env)))


def make_link(kind: str, source: Path, target: Path, allow_skip: bool) -> str | None:
    """Create a real link or name an explicitly permitted unsupported operation."""
    try:
        if kind == "symlink":
            target.symlink_to(source)
        else:
            os.link(source, target)
    except OSError as exc:
        if allow_skip:
            return f"SKIP: {kind} creation not supported: {exc}"
        raise
    return None


def alias_case(runner: ModuleType, folder: Path, kind: str) -> tuple[int, str]:
    """Refuse each output alias before changing any source byte."""
    source, target = folder / (kind + ".glsl"), folder / (kind + "-out.glsl")
    original = (
        runner.ROOT / "ffmpeg-patches/files/vulkan/pelorus_mc.comp.glsl"
    ).read_bytes()
    source.write_bytes(original)
    if kind in ("symlink", "hardlink"):
        skip = make_link(kind, source, target, allow_skip=os.name == "nt")
        if skip is not None:
            return 0, skip
    else:
        target = source
    message = ""
    try:
        runner.tree_shader(source, target)
    except ValueError as exc:
        message = str(exc)
    return expect(
        message == "tree shader output aliases its input"
        and source.read_bytes() == original,
        "alias refused; bytes unchanged",
    )


def link_error_case(
    folder: Path, kind: str, allow_skip: bool, error: type[Exception]
) -> tuple[int, str]:
    """Inject link errors without changing platform identity or real link cases."""
    owner, name = (Path, "symlink_to") if kind == "symlink" else (os, "link")
    with patch.object(owner, name, side_effect=error("injected link failure")):
        message = make_link(kind, folder / "source", folder / "target", allow_skip)
    if allow_skip:
        return expect(
            message == f"SKIP: {kind} creation not supported: injected link failure",
            f"{kind} skip message returned",
        )
    return 0, "link created"


def skip_output_case(expected: int, rc: int) -> tuple[int, str]:
    """Accept a named skip only as a successful result the case expects."""
    output = io.StringIO()
    with redirect_stdout(output):
        passed = _tests.run_case(
            "canned-skip", partial(tuple, (rc, "SKIP: canned")), expected, "absent"
        )
    line = output.getvalue()
    skip = rc == 0 and expected == 0
    return expect(
        passed == skip
        and (
            line == "skip canned-skip: SKIP: canned\n"
            if skip
            else line.startswith("FAIL canned-skip:")
        ),
        "skip output and status",
    )


def alias_skip_case(runner: ModuleType, folder: Path) -> tuple[int, str]:
    """Return a named link skip before attempting the shader transformation."""
    message = "SKIP: symlink creation not supported: injected link failure"
    with (
        patch.object(sys.modules[__name__], "make_link", return_value=message),
        patch.object(runner, "tree_shader") as shader,
    ):
        result = alias_case(runner, folder, "symlink")
    shader.assert_not_called()
    return expect(result == (0, message), "alias skip before shader")


def alias_link_error_case(
    runner: ModuleType, folder: Path, kind: str
) -> tuple[int, str]:
    """Check the real alias link failure against the host's exact skip policy."""
    owner, name = (Path, "symlink_to") if kind == "symlink" else (os, "link")
    message = "injected link failure"
    expected = (
        (0, f"SKIP: {kind} creation not supported: {message}")
        if os.name == "nt"
        else (1, message)
    )
    with (
        patch.object(owner, name, side_effect=OSError(message)),
        patch.object(runner, "tree_shader") as shader,
    ):
        try:
            result = alias_case(runner, folder, kind)
        except OSError as exc:
            result = 1, str(exc)
    shader.assert_not_called()
    return expect(result == expected, f"{kind} alias link error follows host policy")


def link_skip_cases(runner: ModuleType, folder: Path) -> list[tuple]:
    """Collect positive, negative, and exception boundary cases for named skips."""
    cases = []
    for kind in ("symlink", "hardlink"):
        for allow_skip in (True, False):
            call = partial(link_error_case, folder, kind, allow_skip, OSError)
            suffix = "skip" if allow_skip else "raise"
            message = kind + " skip message returned" if allow_skip else "injected"
            cases.append(
                (f"link-{kind}-{suffix}", call, 0 if allow_skip else 1, message)
            )
    call = partial(link_error_case, folder, "symlink", True, ValueError)
    cases.append(("link-value-error", call, 1, "injected link failure"))
    for expected, rc in ((0, 0), (1, 0), (77, 77)):
        call = partial(skip_output_case, expected, rc)
        cases.append((f"skip-output-{expected}", call, 0, "skip output and status"))
    call = partial(alias_skip_case, runner, folder)
    cases.append(("alias-skip-before-shader", call, 0, "alias skip before shader"))
    for kind in ("symlink", "hardlink"):
        call = partial(alias_link_error_case, runner, folder, kind)
        message = f"{kind} alias link error follows host policy"
        cases.append((f"alias-{kind}-link-error", call, 0, message))
    return cases


def driver_parse_case(runner: ModuleType) -> tuple[int, str]:
    """Retain different device names sharing a PCI ID and unknown metadata."""
    text = "deviceID = 0xffff\nGPU0:\n deviceName = first\n deviceID = 0xABCD\n driverName = mesa\n driverInfo = 1.2\nGPU1:\n deviceName = second\n deviceID = 0xabcd\n driverName = other\n driverInfo = 3.4\nGPU2:\n deviceName = unknown\n deviceID = 0x1234\nGPU3:\n deviceName = incomplete\n"
    expected = {
        ("first", "0xabcd"): "mesa 1.2",
        ("second", "0xabcd"): "other 3.4",
        ("unknown", "0x1234"): "unknown",
    }
    return expect(
        runner.parse_drivers(text) == expected and runner.parse_drivers("") == {},
        "driver identities retained",
    )


def driver_case(
    runner: ModuleType, record: dict, folder: Path, mode: str
) -> tuple[int, str]:
    """Measure matching or unknown drivers and skip stale drivers before arms."""
    entry = record["devices"][0]
    scenario, env = stub_fixture(runner, folder, [entry], entry["ratio_subgroup"])
    driver = {
        "matching": entry["driver"],
        "stale": "changed driver",
        "unknown": "unknown",
    }[mode]
    with patch.object(
        runner, "driver_map", return_value={(entry["name"], entry["device_id"]): driver}
    ):
        code, message = runner.check(STUB_FFMPEG, record, 5, env)
    calls = scenario.with_suffix(".calls").read_text().splitlines()
    stale = mode == "stale"
    fragment = (
        "stale record: driver changed driver differs from recorded" if stale else "PASS"
    )
    timed = not calls if stale else len(calls) == 18
    return expect(code == (77 if stale else 0) and fragment in message and timed, mode)


def empty_main_case(runner: ModuleType) -> tuple[int, str]:
    """Return a CLI failure when record generation emits no device entries."""
    errors = io.StringIO()
    argv = ["mc-gain-smoke.py", "--make-record", "--tree-ffmpeg", str(STUB_FFMPEG)]
    with (
        patch.object(sys, "argv", argv),
        patch.dict(os.environ, FFMPEG_BIN=str(STUB_FFMPEG)),
        patch.object(runner, "resolve_ffmpeg_bin", return_value=STUB_FFMPEG),
        patch.object(runner, "make_record", return_value=0),
        redirect_stderr(errors),
    ):
        code = runner.main()
    return expect(
        code == 1 and "no device entries emitted" in errors.getvalue(),
        "empty main refused",
    )


def record_main_case(
    runner: ModuleType, record: dict, folder: Path, mode: str
) -> tuple[int, str]:
    """Refresh stale workloads while preserving check and duplicate refusals."""
    stale = json.loads(json.dumps(record))
    stale["workload"]["frames"] += 1
    if mode == "duplicate":
        stale["devices"].append(stale["devices"][0])
    path = folder / ("record-main-" + mode + ".json")
    path.write_text(json.dumps(stale), encoding="utf-8")
    scenario, env = stub_fixture(runner, folder, [stale["devices"][0]], 3)
    env["FFMPEG_BIN"] = str(STUB_FFMPEG)
    argv = ["mc-gain-smoke.py", "--record", str(path), "--runs", "5"]
    if mode != "check":
        argv += ["--make-record", "--tree-ffmpeg", str(STUB_FFMPEG)]
    output, errors = io.StringIO(), io.StringIO()
    with (
        patch.object(sys, "argv", argv),
        patch.dict(os.environ, env, clear=True),
        patch.object(runner, "driver_map", return_value={}),
        redirect_stdout(output),
        redirect_stderr(errors),
    ):
        code = runner.main()
    fragment = {
        "make": "no device entries emitted",
        "check": "measured with another workload",
        "duplicate": "duplicate device",
    }[mode]
    calls = scenario.with_suffix(".calls").read_text().splitlines()
    return expect(
        code == 1
        and fragment in errors.getvalue()
        and not output.getvalue()
        and len(calls) == (30 if mode == "make" else 0),
        f"{mode}: rc={code}, stderr={errors.getvalue()!r}, calls={len(calls)}",
    )


def separation_case(runner: ModuleType, mode: str) -> tuple[int, str]:
    """Pin covariance signs, equality, and each prefix's own net denominator."""
    if mode == "all-terms":
        samples = {
            "passthrough": _tests.sample_triplet(10, 0.1),
            "sub8": _tests.sample_triplet(13, 0.2),
            "sub32": _tests.sample_triplet(12, 0.3),
            "tree8": _tests.sample_triplet(20, 0.4),
            "tree32": _tests.sample_triplet(15, 0.5),
        }
        gap, se_gap = runner.separation(samples)
        # (0.2/2)² = 0.01; (1.5·0.3/2)² = 0.050625;
        # (0.4/5)² = 0.0064; (2·0.5/5)² = 0.04;
        # ((2-1)/5 - (1.5-1)/2)²·0.1² = 0.000025.
        return expect(
            math.isclose(gap, 0.5, rel_tol=1e-12)
            and math.isclose(se_gap, math.sqrt(0.10705), rel_tol=1e-12)
            and not runner.separated(samples),
            f"{mode}: gap={gap}, se_gap={se_gap}, accepted={runner.separated(samples)}",
        )
    rows = {
        "shared-passthrough-edge": (0.5, 2.0, 0.5, 1, 1, 0.75, False),
        "shared-passthrough": (0.5, 2.5, 0.5, 2, 2, 0.5, True),
        "same-side": (3.0, 5.0, 0.4, 1, 1, 0.8, True),
        "net32-per-prefix": (3.0, 5.0, 0.5, 1, 2, 0.0, True),
    }
    sub, tree, sep, ns, nt, expected, accepted = rows[mode]
    samples = _tests.separation_samples(
        sub, 0, tree, 0, sep=sep, net_sub=ns, net_tree=nt
    )
    gap, se_gap = runner.separation(samples)
    return expect(
        gap == tree - sub
        and math.isclose(se_gap, expected, rel_tol=1e-12)
        and runner.separated(samples) == accepted,
        f"{mode}: gap={gap}, se_gap={se_gap}, accepted={runner.separated(samples)}",
    )


def budget_margin_case(runner: ModuleType) -> tuple[int, str]:
    """Read only the mc-gain test block and retain a sixty-second margin."""
    text = (runner.ROOT / "meson.build").read_text(encoding="utf-8")
    block = re.search(r"test\('mc-gain',(?:(?!\btest\().)*", text, re.S)
    assert block is not None, "mc-gain test block missing"
    timeout = re.search(r"\btimeout:\s*(\d+)\b", block[0])
    assert timeout is not None, "mc-gain timeout missing"
    return expect(
        runner.RUN_BUDGET_S + 60 <= int(timeout[1]), "budget below Meson timeout"
    )


def budget_case(runner: ModuleType, record: dict, mode: str) -> tuple[int, str]:
    """Pin exhausted boundaries and distinguish clamped from stage timeouts."""
    device = runner.Device(0, "canned", "0xabcd")
    arms = runner.measurement_arms(STUB_FFMPEG, record["workload"])
    remaining = {
        "exhausted": -1.0,
        "boundary": 0.0,
        "clamps-timeout": 3.0,
        "unclamped-timeout": 500.0,
    }[mode]
    calls: list[float] = []
    output = "Device 0 selected: canned (discrete) (0xabcd)\ntimed out"

    def run(argv: list[str], timeout: float, env: dict | None = None) -> object:
        """Record the allowed timeout without starting a subprocess."""
        calls.append(timeout)
        return runner.RunResult(124, output, "")

    with (
        patch.object(runner.time, "monotonic", return_value=1000.0),
        patch.object(runner, "run_ffmpeg", side_effect=run),
    ):
        message = ""
        try:
            runner.measure(
                device, record["workload"], arms, 5, deadline=1000 + remaining
            )
        except ValueError as exc:
            message = str(exc)
    elapsed = runner.RUN_BUDGET_S - remaining
    prefix = (
        f"mc-gain budget {runner.RUN_BUDGET_S} s exhausted after {elapsed:.3f} s at "
    )
    expected = prefix + "device 0 canned 0xabcd arm sub32"
    timeouts = [] if remaining <= 0 else [min(runner.RUN_TIMEOUT_S, remaining)]
    if mode == "unclamped-timeout":
        expected = f"sub32: failed or missing bench line: {output}"
    return expect(
        message == expected and calls == timeouts,
        f"{mode}: {message!r}, timeouts={calls}",
    )


def budget_check_case(runner: ModuleType, record: dict, mode: str) -> tuple[int, str]:
    """Pin check's explicit deadlines and its default budget through every stage."""
    clock = [1000.0]
    calls: list[list[str]] = []
    entry = record["devices"][0]
    line = f"Device 0 selected: {entry['name']} (discrete) ({entry['device_id']})"

    def run(argv: list[str], timeout: float, env: dict | None = None) -> object:
        """Answer enumeration and advance the local clock before any arm."""
        calls.append(argv)
        if "vulkan=vk:0" in argv:
            return runner.RunResult(0, line, "")
        clock[0] = 1000.0 + runner.RUN_BUDGET_S + 1
        return runner.RunResult(1, "Unable to find device with index 1!", "")

    default = mode == "check-default-budget"
    kwargs = {} if default else {"deadline": 999.0}
    drivers = None if mode == "check-metadata" else {}
    with (
        patch.object(runner.time, "monotonic", side_effect=lambda: clock[0]),
        patch.object(runner, "run_ffmpeg", side_effect=run),
        patch.object(runner.shutil, "which", return_value="/canned/vulkaninfo"),
    ):
        message = ""
        try:
            runner.check(STUB_FFMPEG, record, 5, drivers=drivers, **kwargs)
        except ValueError as exc:
            message = str(exc)
    context = {
        "check-metadata": "driver metadata vulkaninfo",
        "check-enumeration": "device 0 enumeration",
        "check-default-budget": (
            f"device 0 {entry['name']} {entry['device_id'].lower()} arm sub32"
        ),
    }[mode]
    expected = (
        f"mc-gain budget {runner.RUN_BUDGET_S} s exhausted after "
        f"{runner.RUN_BUDGET_S + 1:.3f} s at {context}"
    )
    return expect(
        message == expected and len(calls) == (2 if default else 0),
        f"{mode}: {message!r}, calls={len(calls)}",
    )


def budget_main_case(runner: ModuleType) -> tuple[int, str]:
    """Classify budget exhaustion as a CLI failure rather than a skip."""
    errors = io.StringIO()
    with (
        patch.object(sys, "argv", ["mc-gain-smoke.py"]),
        patch.dict(os.environ, FFMPEG_BIN=str(STUB_FFMPEG)),
        patch.object(
            runner, "check", side_effect=ValueError("mc-gain budget exhausted")
        ),
        redirect_stderr(errors),
    ):
        code = runner.main()
    return expect(
        code == 1 and errors.getvalue() == "mc-gain budget exhausted\n",
        "budget main returns 1",
    )


def review_cases(runner: ModuleType, record: dict, folder: Path) -> list[tuple]:
    """Collect review regressions with named positive and boundary cases."""
    cases = []
    enumeration = partial(enumeration_case, runner, record, folder)
    for mode, rc, msg in (
        ("failed", 1, "device enumeration failed"),
        ("blank", 1, "device enumeration failed"),
        ("timeout", 1, "device enumeration failed"),
        ("none", 0, "0"),
        ("none-later", 1, "device enumeration failed"),
        ("failed-valid", 1, "device enumeration failed"),
        ("wrong-index", 1, "device enumeration failed"),
        ("seven", 0, "7"),
        ("cap", 1, "cap 8"),
    ):
        cases.append(("enumeration-" + mode, partial(enumeration, mode), rc, msg))
    for kind in ("same", "symlink", "hardlink"):
        call = partial(alias_case, runner, folder, kind)
        cases.append(("alias-" + kind, call, 0, "bytes unchanged"))
    for mode in ("matching", "stale", "unknown"):
        call = partial(driver_case, runner, record, folder, mode)
        cases.append(("driver-" + mode, call, 0, mode))
    call = partial(driver_parse_case, runner)
    cases.append(("driver-parser", call, 0, "driver identities retained"))
    call = partial(empty_main_case, runner)
    cases.append(("record-empty-main", call, 0, "empty main refused"))
    shipped = partial(runner.validate_record, record, require_workload_match=True)
    cases.append(("shipped-record", shipped, 0, ""))
    for mode in ("make", "check", "duplicate"):
        call = partial(record_main_case, runner, record, folder, mode)
        cases.append(("record-main-" + mode, call, 0, mode))
    cases += link_skip_cases(runner, folder)
    for mode in (
        "shared-passthrough-edge",
        "shared-passthrough",
        "same-side",
        "net32-per-prefix",
        "all-terms",
    ):
        cases.append(
            ("separation-" + mode, partial(separation_case, runner, mode), 0, mode)
        )
    call = partial(budget_margin_case, runner)
    cases.append(("budget-below-meson-timeout", call, 0, "budget below Meson timeout"))
    for mode in ("exhausted", "clamps-timeout", "boundary", "unclamped-timeout"):
        call = partial(budget_case, runner, record, mode)
        cases.append(("budget-" + mode, call, 0, mode))
    for mode in ("check-metadata", "check-enumeration", "check-default-budget"):
        call = partial(budget_check_case, runner, record, mode)
        cases.append(("budget-" + mode, call, 0, mode))
    cases.append(
        ("budget-main", partial(budget_main_case, runner), 0, "budget main returns 1")
    )
    return cases
