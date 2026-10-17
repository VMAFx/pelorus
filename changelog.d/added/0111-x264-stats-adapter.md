<!--
SPDX-FileCopyrightText: 2026 Lusoris
SPDX-License-Identifier: EUPL-1.2
-->
- `pel_tlm_parse_x264_stats()` reads x264 pass-1 stats into H.264 telemetry: display/decode indices, post-AQ mean QP, coded size and bit split, picture type and key/reference/shown flags. Bounded, locale-independent two-pass parsing rejects malformed records before writing output. Includes a real 30-frame x264 0.165 fixture (part of #111).
