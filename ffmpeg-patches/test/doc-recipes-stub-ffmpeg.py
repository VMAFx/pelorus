# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Stub ffmpeg for doc-recipes-smoke.py --self-test.

STUB_PLANT selects the defect a run plants; unset or "clean" prints every
needle a recipe needs. The smoke runner starts this file through the Python
interpreter, so it needs no executable bit and runs on Windows too.
"""

import os
import sys
import time
from pathlib import Path

NEEDLES = (
    "pixfmt:cuda",
    "Transferred CUDA image to Vulkan!",
    "Transferred Vulkan image to CUDA!",
    "Using input frames context (format cuda) with hevc_nvenc encoder",
)
CLEAN_EXTRA = (
    "Using input frames context (format vulkan) with hevc_vulkan encoder",
    "tiling=drm",
    "Using input frames context (format vaapi) with h264_vaapi encoder",
    "Using input frames context (format qsv) with hevc_qsv encoder",
)
SW_CONVERSION = (
    "[auto_scale_0 @ 0x1] w:1280 h:720 fmt:yuv420p csp:unknown range:tv sar:1/1 "
    "-> w:1280 h:720 fmt:nv12 csp:unknown range:tv sar:1/1 flags:0x00000004"
)
PLANT_LINES = {
    "hwdownload": ("Parsed_hwdownload_1 @ 0x5555: hwdownload",),
    "autoscale": (
        (
            "[auto_scale_0 @ 0x1] w:1280 h:720 fmt:vulkan csp:unknown range:tv sar:1/1 "
            "-> w:1280 h:720 fmt:nv12 csp:unknown range:tv sar:1/1 flags:0x00000004"
        ),
    ),
    "autoscale_unparsed": (
        "[Parsed_format_0 @ 0x1] auto-inserting filter 'auto_scale_0'",
    ),
    # a parsed software conversion next to a second insert without formats
    "autoscale_mixed": (
        "[Parsed_format_0 @ 0x1] auto-inserting filter 'auto_scale_0'",
        SW_CONVERSION,
        "[Parsed_hwupload_1 @ 0x1] auto-inserting filter 'auto_scale_1'",
    ),
}


def plant():
    return os.environ.get("STUB_PLANT", "")


def decode_check(argv):
    if not Path(argv[argv.index("-i") + 1]).is_file():
        sys.stderr.write("Error: No such file or directory\n")
        return 1
    frames = 119 if plant() == "119frames" else 120
    sys.stderr.write(
        f"frame=  {frames} fps=0.0 q=0.0 size=N/A time=00:00:05.00 bitrate=N/A\n"
    )
    return 0


def stdin_is_devnull():
    """True when fd 0 is the null device; ffmpeg must never wait on a prompt."""
    if os.name == "nt":
        return True
    st, null = os.fstat(0), os.stat(os.devnull)
    return (st.st_dev, st.st_ino) == (null.st_dev, null.st_ino)


def recipe_run(argv):
    if not stdin_is_devnull():
        sys.stderr.write("Error: stdin is not the null device\n")
        return 1
    if plant() == "rc":
        sys.stderr.write("Error: simulated ffmpeg failure\n")
        return 1
    if plant() == "hang":
        time.sleep(30)
    if plant() != "no_output":
        Path(argv[-1]).touch()
    if plant() == "missing_needle":
        print(NEEDLES[0])
        return 0
    if plant() in PLANT_LINES:
        print("\n".join(NEEDLES + PLANT_LINES[plant()]))
        return 0
    print("\n".join((SW_CONVERSION,) + NEEDLES + CLEAN_EXTRA))
    return 0


def main(argv):
    joined = " ".join(argv)
    if not argv:
        return 0
    if "-encoders" in argv:
        if plant() == "encoders_rc":
            sys.stderr.write("Error: simulated broken binary\n")
            return 1
        print(" V..... libx265              libx265 H.265 / HEVC (codec hevc)")
        print(" V..... hevc_nvenc           NVIDIA NVENC hevc encoder (codec hevc)")
        return 0
    probe = "-frames:v" in argv and argv[argv.index("-frames:v") + 1] == "1"
    if "null" in argv and probe:
        return 0
    if "testsrc2=size=1280x720:rate=24" in joined:
        if plant() == "no10bit" and argv[-1].endswith("input-10bit.mkv"):
            sys.stderr.write("Error: 10-bit encode unavailable\n")
            return 1
        Path(argv[-1]).touch()
        return 0
    if argv[-3:] == ["-f", "null", "-"]:
        return decode_check(argv)
    return recipe_run(argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
