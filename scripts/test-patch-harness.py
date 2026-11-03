#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Check the status contract and the deadlines of scripts/patch_harness.py.

build_and_run() backs every C patch-harness test of the fast suite. A harness
or compiler that stalls must end as a failure inside its deadline (HISS-02),
not hang the suite. Each case plants one outcome: pass, fail, compile error,
a harness that never exits, and a compiler that never exits.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

# python3 -I drops the script directory from sys.path: load the sibling by path.
_SPEC = importlib.util.spec_from_file_location("patch_harness", pathlib.Path(__file__).with_name("patch_harness.py"))
if _SPEC is None or _SPEC.loader is None:
    raise ImportError("scripts/patch_harness.py not found next to this test")
harness = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(harness)

DESCRIPTION = "Check the status contract and the deadlines of scripts/patch_harness.py."

OK_C = "int main(void) { return 0; }\n"
FAIL_C = "int main(void) { return 3; }\n"
BROKEN_C = "int main(void) { return }\n"
# volatile keeps the loop: an empty infinite loop without side effects is
# undefined behaviour and may be removed by the optimiser.
HANG_C = "int main(void) { volatile int spin = 1; while (spin) { } return 0; }\n"
# A stand-in compiler that never finishes inside the deadline.
STALL_CC = (sys.executable, "-I", "-c", "import time; time.sleep(30)")


def main() -> int:
    parser = harness.make_parser(DESCRIPTION)
    args = parser.parse_args()
    cases = (
        ("pass", OK_C, args.cc, {}, 0, ""),
        ("fail", FAIL_C, args.cc, {}, 1, ""),
        ("compile error", BROKEN_C, args.cc, {}, 2, "failed to compile"),
        ("run deadline", HANG_C, args.cc, {"run_timeout": 1.0}, 1, "run exceeded 1 s"),
        ("build deadline", OK_C, STALL_CC, {"build_timeout": 1.0}, 2, "build exceeded 1 s"),
    )
    failures = 0
    for name, source, cc, deadlines, want, needle in cases:
        status, output = harness.build_and_run(source, cc, prefix="pelorus-harness-self-", **deadlines)
        if status != want or needle not in output:
            print(f"FAIL {name}: status {status}, want {want}; output: {output.strip()!r}")
            failures += 1
    if failures:
        return 1
    print(f"patch-harness: {len(cases)} cases ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
