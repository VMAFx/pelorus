# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Self-test of the zero-copy recipe smoke: plant every defect, require refusal.

Run through doc-recipes-smoke.py --self-test, which passes itself as the
runner module. Doc plants go through --check-doc; execution plants go
through the stub ffmpeg doc-recipes-stub-ffmpeg.py.
"""

import contextlib
import os
import tempfile
from pathlib import Path

R = None  # the runner module, set by self_test()

STUB_FFMPEG = Path(__file__).resolve().with_name("doc-recipes-stub-ffmpeg.py")


def _quiet(fn, *args, **kwargs):
    """Call fn with stdout discarded; return its result."""
    with (
        open(os.devnull, "w", encoding="utf-8") as devnull,
        contextlib.redirect_stdout(devnull),
    ):
        return fn(*args, **kwargs)


def _quiet_deadline(fn, *args, **kwargs):
    """_quiet with a 3 s recipe deadline, so the hang plant ends fast."""
    saved, R.RUN_TIMEOUT_S = R.RUN_TIMEOUT_S, 3
    try:
        return _quiet(fn, *args, **kwargs)
    finally:
        R.RUN_TIMEOUT_S = saved


def _with_pipe_stdin(fn, *args, **kwargs):
    """_quiet with a pipe on fd 0, so a child that inherits stdin is seen."""
    read_fd, write_fd = os.pipe()
    saved = os.dup(0)
    os.dup2(read_fd, 0)
    try:
        return _quiet(fn, *args, **kwargs)
    finally:
        os.dup2(saved, 0)
        for fd in (saved, read_fd, write_fd):
            os.close(fd)


def _self_test_doc(clean_doc):
    """Doc plants for --check-doc. Return the failure count."""
    failures = 0
    print("--- self-test: clean doc check ---")
    if R.check_doc(str(R.DOC_PATH), clean_doc) != 0:
        print("self-test FAIL: clean doc failed check-doc")
        failures += 1
    print("--- self-test: doc plants ---")
    derived = "<!-- recipe-run: nvenc-nvdec-derived -->"
    vulkan = "<!-- recipe-run: vulkan-hevc -->"
    fence = "```sh\nffmpeg -i input.mkv out.mkv\n```\n"
    plants_doc = [
        ("fence without marker", clean_doc.replace(derived + "\n\n", "")),
        ("marker without fence", clean_doc + f"\n{derived}\n\nNo fence here.\n"),
        (
            "id without SPEC",
            clean_doc.replace(derived, "<!-- recipe-run: unknown-recipe -->"),
        ),
        (
            "duplicate id",
            clean_doc.replace(derived, f"{derived}\n\n{fence}\n{derived}", 1),
        ),
        (
            "id outside its section",
            clean_doc.replace(vulkan + "\n\n", "<!-- recipe-run: none: moved -->\n\n")
            + f"\n## Notes on Vulkan\n\n{vulkan}\n\n{fence}",
        ),
        (
            "SPEC without marker",
            clean_doc.replace(
                "<!-- recipe-run: vulkan-hevc -->\n\n",
                "<!-- recipe-run: none: skip -->\n\n",
            ),
        ),
    ]
    for name, ptext in plants_doc:
        rc = _quiet(R.check_doc, "test.md", ptext)
        if rc != 1:
            print(
                f"self-test FAIL: doc plant '{name}' was not refused by check-doc (rc {rc}, want 1)"
            )
            failures += 1
        else:
            print(f"self-test: doc plant '{name}' correctly refused (rc {rc})")
    return failures


def _self_test_exec_runs(stub_path, stub_dir):
    """Runs through the stub that must pass. Return the failure count."""
    failures = 0
    print("--- self-test: 8-bit recipe without a 10-bit clip ---")
    os.environ["STUB_PLANT"] = "no10bit"
    rc = _quiet(
        R.run_smoke,
        stub_path,
        output_root=Path(stub_dir) / "out-no10bit",
        recipes_filter="nvenc-nvdec-derived",
    )
    if rc != 0:
        print(
            f"self-test FAIL: 8-bit recipe did not run without a 10-bit clip (rc {rc})"
        )
        failures += 1
    else:
        print("self-test: 8-bit recipe ran without a 10-bit clip")
    if os.name != "nt":
        print("--- self-test: recipe run with a pipe on stdin ---")
        os.environ["STUB_PLANT"] = "clean"
        rc = _with_pipe_stdin(
            R.run_smoke,
            stub_path,
            output_root=Path(stub_dir) / "out-stdin",
            recipes_filter="nvenc-nvdec-derived",
        )
        if rc != 0:
            print(f"self-test FAIL: recipe run inherited stdin (rc {rc})")
            failures += 1
        else:
            print("self-test: recipe run got the null device on stdin")
    print("--- self-test: clean stub run ---")
    os.environ["STUB_PLANT"] = "clean"
    rc = _quiet(R.run_smoke, stub_path, output_root=Path(stub_dir) / "out-clean")
    if rc != 0:
        print(f"self-test FAIL: clean stub run failed with rc {rc}")
        failures += 1
    else:
        print("self-test: clean stub run passed")
    return failures


def _self_test_exec():
    """Execution plants through a stub ffmpeg. Return the failure count."""
    failures = 0
    print("--- self-test: execution plants ---")
    with tempfile.TemporaryDirectory(prefix="pelorus-stub-bin.") as stub_dir:
        stub_path = STUB_FFMPEG
        exec_plants = [
            ("rc!=0", "rc"),
            ("hwdownload in log", "hwdownload"),
            ("auto scale", "autoscale"),
            ("auto scale without formats", "autoscale_unparsed"),
            ("second auto scale without formats", "autoscale_mixed"),
            ("hung recipe run", "hang"),
            ("missing needle", "missing_needle"),
            ("119 frames", "119frames"),
            ("failed encoder probe", "encoders_rc"),
            ("stale output clip", "no_output"),
        ]
        for name, plant_env in exec_plants:
            os.environ["STUB_PLANT"] = plant_env
            out = Path(stub_dir) / f"out-{plant_env}"
            # a clip left by an earlier run must not pass for this run's output
            out.mkdir()
            (out / "nvenc-nvdec-derived.mkv").touch()
            rc = _quiet_deadline(
                R.run_smoke,
                stub_path,
                output_root=out,
                recipes_filter="nvenc-nvdec-derived",
            )
            # a refusal is rc 1; a 77 means every variant skipped and the
            # plant never reached the check it targets
            if rc != 1:
                print(
                    f"self-test FAIL: execution plant '{name}' was not refused (rc {rc}, want 1)"
                )
                failures += 1
            else:
                print(
                    f"self-test: execution plant '{name}' correctly refused (rc {rc})"
                )
        failures += _self_test_exec_runs(stub_path, stub_dir)
    return failures


def _self_test_resolve():
    """FFMPEG_BIN resolution: path, bare name on PATH, missing name."""
    print("--- self-test: FFMPEG_BIN resolution ---")
    failures = 0
    with tempfile.TemporaryDirectory(prefix="pelorus-bin.") as bin_dir:
        exe = Path(bin_dir) / "pelorus-probe-ffmpeg"
        exe.write_text("#!/bin/sh\n", encoding="utf-8")
        exe.chmod(0o500)
        old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = bin_dir
        try:
            cases = [
                ("absolute path", str(exe), exe),
                ("missing absolute path", str(exe) + "-gone", None),
                ("missing bare name", "pelorus-no-such-ffmpeg", None),
            ]
            if os.name != "nt":  # Windows which() needs a PATHEXT suffix
                cases.append(("bare name on PATH", exe.name, exe))
            for name, value, want in cases:
                got = R.resolve_ffmpeg_bin(value)
                if (got and got.resolve()) != (want and want.resolve()):
                    print(f"self-test FAIL: resolve '{name}' gave {got}, want {want}")
                    failures += 1
                else:
                    print(f"self-test: resolve '{name}' ok")
        finally:
            os.environ["PATH"] = old_path
    return failures


def self_test(runner):
    """Run every plant against the runner module. Return the exit code."""
    global R
    R = runner
    failures = _self_test_doc(R.DOC_PATH.read_text(encoding="utf-8"))
    failures += _self_test_exec()
    failures += _self_test_resolve()
    if failures:
        print(f"doc-recipes-smoke self-test: {failures} failure(s)")
        return 1
    print("doc-recipes-smoke self-test: ok")
    return 0
