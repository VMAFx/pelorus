# decimal CRF fields, header and line indentation
% stdin zones-valid.csv
$ pelorus_zones --format zonefile
> 0 --crf 32.00
> 30 --crf 26.50
[exit 0]

# blank before CRF refused
% stdin zones-crf-blank.csv
$ pelorus_zones --format zonefile
! pelorus_zones: line 1: malformed crf: 0,29, 32.0000
! pelorus_zones: no valid shots read on stdin
[exit 1]

# hex first frame refused
% stdin zones-first-hex.csv
$ pelorus_zones --format zonefile
! pelorus_zones: line 1: malformed first_frame: 0x1,29,32.0
! pelorus_zones: no valid shots read on stdin
[exit 1]

# plus first frame refused
% stdin zones-first-plus.csv
$ pelorus_zones --format zonefile
! pelorus_zones: line 1: non-numeric line: +0,29,32.0
! pelorus_zones: no valid shots read on stdin
[exit 1]

# plus last frame refused
% stdin zones-last-plus.csv
$ pelorus_zones --format zonefile
! pelorus_zones: line 1: non-numeric last_frame: 0,+29,32.0
! pelorus_zones: no valid shots read on stdin
[exit 1]

# hex CRF refused
% stdin zones-crf-hex.csv
$ pelorus_zones --format zonefile
! pelorus_zones: line 1: malformed crf: 0,29,0x20
! pelorus_zones: no valid shots read on stdin
[exit 1]

# CRF above 63 refused
% stdin zones-crf-over.csv
$ pelorus_zones --format zonefile
! pelorus_zones: line 1: malformed crf: 0,29,63.01
! pelorus_zones: no valid shots read on stdin
[exit 1]

# 129 character CRF refused
% stdin zones-crf-129.csv
$ pelorus_zones --format zonefile
! pelorus_zones: line 1: malformed crf: 0,29,1.0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000
! pelorus_zones: no valid shots read on stdin
[exit 1]

# CRF exponent accepted
% stdin zones-crf-exponent.csv
$ pelorus_zones --format zonefile
> 0 --crf 10.00
[exit 0]

# CRF 63 accepted
% stdin zones-crf-max.csv
$ pelorus_zones --format zonefile
> 0 --crf 63.00
[exit 0]

# 128 character CRF accepted
% stdin zones-crf-128.csv
$ pelorus_zones --format zonefile
> 0 --crf 1.00
[exit 0]

# hex base CRF option refused
$ pelorus_zones --format zonefile --base-crf 0x20
! pelorus_zones: invalid value for '--base-crf': '0x20'
! Usage: pelorus_zones --format <zonefile|zones|segment-plan> [--base-crf <float>]
[exit 1]
