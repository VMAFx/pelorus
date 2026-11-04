# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Planted defect: a urlopen call outside the excluded file; never merged."""

from urllib.request import urlopen


def fetch(url: str) -> bytes:
    """Fetch a URL without a scheme check."""
    # planted probe: Codacy must still flag this line; never merged
    with urlopen(url, timeout=10) as response:  # noqa: S310
        return response.read(1024)
