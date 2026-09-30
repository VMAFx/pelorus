#!/usr/bin/env python3
"""shw-3 correlate.py -- relate wrongly-quantised frames to early slot reuse.

Usage: correlate.py <qp.csv from qpdump> <probe csv>.reuse.csv [row_mbs]

A frame is "mismatched" when more than two MB rows are off the expected QP
pattern (P_Skip QP-predictor artefacts are at most about one row).  A frame is
"at risk" when its input slot -- and with it the slot's MBQP map / ROI buffer --
was reused for a later frame after surface.Data.Locked dropped to zero but
before the frame's bitstream came out of SyncOperation.
"""
import csv
import sys

qp_csv, reuse_csv = sys.argv[1], sys.argv[2]
row = int(sys.argv[3]) if len(sys.argv) > 3 else 120
mism = set()
for r in csv.DictReader(open(qp_csv)):
    if int(r["n_off"]) > 2 * row:
        mism.add(int(r["frame"]))
risk = set()
for r in csv.DictReader(open(reuse_csv)):
    risk.add(int(r["previous_frame_not_yet_synced"]))
both = mism & risk
print(f"mismatched={len(mism)} at_risk(early_reuse)={len(risk)} mismatched_and_at_risk={len(both)} "
      f"mismatched_not_at_risk={len(mism - risk)}"
      + (f" e.g. not-at-risk {sorted(mism - risk)[:8]}" if mism - risk else ""))
