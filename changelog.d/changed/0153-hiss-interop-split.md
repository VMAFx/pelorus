- **The interop parser and the x265 CSV reader meet the HISS limits.**
  `pel_blob_pack()`, `pel_blob_find_section()`, `pel_qp_report_from_blocks()` and
  `pel_x265_csv_parse()` are split into helpers without a change in behaviour
  (the conformance fixture passes unchanged), and the field splitter of the CSV
  reader carries an explicit scalar bound. The HISS baseline loses 5
  infractions (99 to 93, with one more from `scripts/check-build-config.py`, whose Renovate regression table is split into helpers).
