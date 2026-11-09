# usage: missing positional CSV argument
$ pelorus_qp_report
! pelorus_qp_report: <x265.csv> is required
! usage: pelorus_qp_report <x265.csv> [--requested-qp N]
[exit 1]

# usage: --requested-qp missing value
% copy x265-4.3-qp32.csv test.csv
$ pelorus_qp_report test.csv --requested-qp
! pelorus_qp_report: option '--requested-qp' requires a value
! usage: pelorus_qp_report <x265.csv> [--requested-qp N]
[exit 1]

# usage: --requested-qp non-numeric value
% copy x265-4.3-qp32.csv test.csv
$ pelorus_qp_report test.csv --requested-qp 1x
! pelorus_qp_report: invalid value for '--requested-qp': '1x'
! usage: pelorus_qp_report <x265.csv> [--requested-qp N]
[exit 1]

# usage: --requested-qp nan rejected
% copy x265-4.3-qp32.csv test.csv
$ pelorus_qp_report test.csv --requested-qp nan
! pelorus_qp_report: invalid value for '--requested-qp': 'nan'
! usage: pelorus_qp_report <x265.csv> [--requested-qp N]
[exit 1]

# usage: --requested-qp inf rejected
% copy x265-4.3-qp32.csv test.csv
$ pelorus_qp_report test.csv --requested-qp inf
! pelorus_qp_report: invalid value for '--requested-qp': 'inf'
! usage: pelorus_qp_report <x265.csv> [--requested-qp N]
[exit 1]

# usage: --requested-qp overflow value 1e309 rejected
% copy x265-4.3-qp32.csv test.csv
$ pelorus_qp_report test.csv --requested-qp 1e309
! pelorus_qp_report: invalid value for '--requested-qp': '1e309'
! usage: pelorus_qp_report <x265.csv> [--requested-qp N]
[exit 1]

# usage: unexpected positional argument
% copy x265-4.3-qp32.csv test.csv
$ pelorus_qp_report test.csv extra.csv
! pelorus_qp_report: unexpected argument 'extra.csv'
! usage: pelorus_qp_report <x265.csv> [--requested-qp N]
[exit 1]

# usage: unknown option flag
% copy x265-4.3-qp32.csv test.csv
$ pelorus_qp_report test.csv --bogus
! pelorus_qp_report: unknown option '--bogus'
! usage: pelorus_qp_report <x265.csv> [--requested-qp N]
[exit 1]

# usage: duplicate option flag
% copy x265-4.3-qp32.csv test.csv
$ pelorus_qp_report test.csv --requested-qp 32 --requested-qp 32
! pelorus_qp_report: duplicate option '--requested-qp'
! usage: pelorus_qp_report <x265.csv> [--requested-qp N]
[exit 1]

# usage: missing CSV file
$ pelorus_qp_report nonexistent.csv
! pelorus_qp_report: parse csv: data not present
[exit 1]

# usage: empty CSV file
% head x265-4.3-qp32.csv empty.csv 0
$ pelorus_qp_report empty.csv
! pelorus_qp_report: parse csv: data not present
[exit 1]

# usage: header-only CSV file with zero frame rows
% head x265-4.3-qp32.csv header_only.csv 1
$ pelorus_qp_report header_only.csv
! no frame rows parsed from header_only.csv
[exit 1]

# positive: default report without requested QP
% copy x265-4.3-qp32.csv test.csv
$ pelorus_qp_report test.csv
> PEL_SEC_QPREPORT (read back from a 144-byte Pelorus blob)
>   source            : x265 --csv (HEVC SW, PEL_QPSRC_NONE=0)
>   frames parsed     : 10
>   avg_qp (bit-wtd)  : 32.0000
>   total_bits        : 59928
>   psnr_y/u/v (dB)   : 0.000 / 0.000 / 0.000
>   qp_valid          : 0  (0 => frame-granular, no per-cell map)
>   honored_fraction  : 0.0000  (no requested map supplied)
[exit 0]

# positive: report with flat requested QP
% copy x265-4.3-qp32.csv test.csv
$ pelorus_qp_report test.csv --requested-qp 32.0
> PEL_SEC_QPREPORT (read back from a 144-byte Pelorus blob)
>   source            : x265 --csv (HEVC SW, PEL_QPSRC_NONE=0)
>   frames parsed     : 10
>   avg_qp (bit-wtd)  : 32.0000
>   total_bits        : 59928
>   psnr_y/u/v (dB)   : 0.000 / 0.000 / 0.000
>   qp_valid          : 0  (0 => frame-granular, no per-cell map)
>   requested QP      : 32.00 (flat, every frame)
>   honored_fraction  : 1.0000  (sign-agreement requested vs honored per-frame delta-QP)
[exit 0]

# positive: option placed before positional argument
% copy x265-4.3-qp32.csv test.csv
$ pelorus_qp_report --requested-qp 32.0 test.csv
> PEL_SEC_QPREPORT (read back from a 144-byte Pelorus blob)
>   source            : x265 --csv (HEVC SW, PEL_QPSRC_NONE=0)
>   frames parsed     : 10
>   avg_qp (bit-wtd)  : 32.0000
>   total_bits        : 59928
>   psnr_y/u/v (dB)   : 0.000 / 0.000 / 0.000
>   qp_valid          : 0  (0 => frame-granular, no per-cell map)
>   requested QP      : 32.00 (flat, every frame)
>   honored_fraction  : 1.0000  (sign-agreement requested vs honored per-frame delta-QP)
[exit 0]

# positive: report on CSV with PSNR columns
% copy x265-4.3-crf28-psnr.csv test_psnr.csv
$ pelorus_qp_report test_psnr.csv
> PEL_SEC_QPREPORT (read back from a 144-byte Pelorus blob)
>   source            : x265 --csv (HEVC SW, PEL_QPSRC_NONE=0)
>   frames parsed     : 24
>   avg_qp (bit-wtd)  : 31.0561
>   total_bits        : 118640
>   psnr_y/u/v (dB)   : 37.606 / 36.522 / 36.230
>   qp_valid          : 0  (0 => frame-granular, no per-cell map)
>   honored_fraction  : 0.0000  (no requested map supplied)
[exit 0]

# positive: report on CSV with PSNR columns and requested QP
% copy x265-4.3-crf28-psnr.csv test_psnr.csv
$ pelorus_qp_report test_psnr.csv --requested-qp 32
> PEL_SEC_QPREPORT (read back from a 144-byte Pelorus blob)
>   source            : x265 --csv (HEVC SW, PEL_QPSRC_NONE=0)
>   frames parsed     : 24
>   avg_qp (bit-wtd)  : 31.0561
>   total_bits        : 118640
>   psnr_y/u/v (dB)   : 37.606 / 36.522 / 36.230
>   qp_valid          : 0  (0 => frame-granular, no per-cell map)
>   requested QP      : 32.00 (flat, every frame)
>   honored_fraction  : 0.1250  (sign-agreement requested vs honored per-frame delta-QP)
[exit 0]

# positive: UTF-8 non-ASCII filename path
% copy x265-4.3-qp32.csv café_文件.csv
$ pelorus_qp_report café_文件.csv
> PEL_SEC_QPREPORT (read back from a 144-byte Pelorus blob)
>   source            : x265 --csv (HEVC SW, PEL_QPSRC_NONE=0)
>   frames parsed     : 10
>   avg_qp (bit-wtd)  : 32.0000
>   total_bits        : 59928
>   psnr_y/u/v (dB)   : 0.000 / 0.000 / 0.000
>   qp_valid          : 0  (0 => frame-granular, no per-cell map)
>   honored_fraction  : 0.0000  (no requested map supplied)
[exit 0]

# boundary: lower bound requested QP 0 accepted
% copy x265-4.3-qp32.csv test.csv
$ pelorus_qp_report test.csv --requested-qp 0
> PEL_SEC_QPREPORT (read back from a 144-byte Pelorus blob)
>   source            : x265 --csv (HEVC SW, PEL_QPSRC_NONE=0)
>   frames parsed     : 10
>   avg_qp (bit-wtd)  : 32.0000
>   total_bits        : 59928
>   psnr_y/u/v (dB)   : 0.000 / 0.000 / 0.000
>   qp_valid          : 0  (0 => frame-granular, no per-cell map)
>   requested QP      : 0.00 (flat, every frame)
>   honored_fraction  : 1.0000  (sign-agreement requested vs honored per-frame delta-QP)
[exit 0]

# boundary: upper bound requested QP 69 accepted
% copy x265-4.3-qp32.csv test.csv
$ pelorus_qp_report test.csv --requested-qp 69
> PEL_SEC_QPREPORT (read back from a 144-byte Pelorus blob)
>   source            : x265 --csv (HEVC SW, PEL_QPSRC_NONE=0)
>   frames parsed     : 10
>   avg_qp (bit-wtd)  : 32.0000
>   total_bits        : 59928
>   psnr_y/u/v (dB)   : 0.000 / 0.000 / 0.000
>   qp_valid          : 0  (0 => frame-granular, no per-cell map)
>   requested QP      : 69.00 (flat, every frame)
>   honored_fraction  : 1.0000  (sign-agreement requested vs honored per-frame delta-QP)
[exit 0]

# boundary: requested QP -0 normalised to 0.00
% copy x265-4.3-qp32.csv test.csv
$ pelorus_qp_report test.csv --requested-qp -0
> PEL_SEC_QPREPORT (read back from a 144-byte Pelorus blob)
>   source            : x265 --csv (HEVC SW, PEL_QPSRC_NONE=0)
>   frames parsed     : 10
>   avg_qp (bit-wtd)  : 32.0000
>   total_bits        : 59928
>   psnr_y/u/v (dB)   : 0.000 / 0.000 / 0.000
>   qp_valid          : 0  (0 => frame-granular, no per-cell map)
>   requested QP      : 0.00 (flat, every frame)
>   honored_fraction  : 1.0000  (sign-agreement requested vs honored per-frame delta-QP)
[exit 0]

# boundary: requested QP below 0 rejected
% copy x265-4.3-qp32.csv test.csv
$ pelorus_qp_report test.csv --requested-qp -0.01
! pelorus_qp_report: --requested-qp -0.01 is outside 0..69 (the x265 QP range)
! usage: pelorus_qp_report <x265.csv> [--requested-qp N]
[exit 1]

# boundary: requested QP above 69 rejected
% copy x265-4.3-qp32.csv test.csv
$ pelorus_qp_report test.csv --requested-qp 69.01
! pelorus_qp_report: --requested-qp 69.01 is outside 0..69 (the x265 QP range)
! usage: pelorus_qp_report <x265.csv> [--requested-qp N]
[exit 1]

# boundary: requested QP sentinel -1 rejected
% copy x265-4.3-qp32.csv test.csv
$ pelorus_qp_report test.csv --requested-qp -1
! pelorus_qp_report: --requested-qp -1 is outside 0..69 (the x265 QP range)
! usage: pelorus_qp_report <x265.csv> [--requested-qp N]
[exit 1]

# boundary: requested QP large float 1e300 rejected
% copy x265-4.3-qp32.csv test.csv
$ pelorus_qp_report test.csv --requested-qp 1e300
! pelorus_qp_report: --requested-qp 1e+300 is outside 0..69 (the x265 QP range)
! usage: pelorus_qp_report <x265.csv> [--requested-qp N]
[exit 1]

# boundary: exact maximum 4096 frame rows parsed without truncation warning
% repeat x265-4.3-qp32.csv rep4096.csv 2 4096
$ pelorus_qp_report rep4096.csv
> PEL_SEC_QPREPORT (read back from a 144-byte Pelorus blob)
>   source            : x265 --csv (HEVC SW, PEL_QPSRC_NONE=0)
>   frames parsed     : 4096
>   avg_qp (bit-wtd)  : 32.0000
>   total_bits        : 61603840
>   psnr_y/u/v (dB)   : 0.000 / 0.000 / 0.000
>   qp_valid          : 0  (0 => frame-granular, no per-cell map)
>   honored_fraction  : 0.0000  (no requested map supplied)
[exit 0]

# boundary: 4097 frame rows triggers truncation warning on stderr
% repeat x265-4.3-qp32.csv rep4097.csv 2 4097
$ pelorus_qp_report rep4097.csv
> PEL_SEC_QPREPORT (read back from a 144-byte Pelorus blob)
>   source            : x265 --csv (HEVC SW, PEL_QPSRC_NONE=0)
>   frames parsed     : 4096
>   avg_qp (bit-wtd)  : 32.0000
>   total_bits        : 61603840
>   psnr_y/u/v (dB)   : 0.000 / 0.000 / 0.000
>   qp_valid          : 0  (0 => frame-granular, no per-cell map)
>   honored_fraction  : 0.0000  (no requested map supplied)
! pelorus_qp_report: warning: rep4097.csv has more than 4096 frame rows; the report covers the first 4096
[exit 0]
