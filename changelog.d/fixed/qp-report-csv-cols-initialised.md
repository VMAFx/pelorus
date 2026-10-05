- **The x265 CSV reader initialises its column indices before the header row
  sets them.** `x265_csv_read_rows()` left the column-index struct
  uninitialised until the header was read, and gcc 16 at `-O2` reported seven
  `-Wmaybe-uninitialized` warnings (`type`, `poc`, `qp`, `bits`, `psnr_y/u/v`).
  Every index now starts at -1 ("absent"), which the readers already treat as a
  missing column, so behaviour is unchanged. CI gains an optimised gcc build
  with warnings as errors; the default debug build never ran the optimiser and
  could not see this class of warning.
