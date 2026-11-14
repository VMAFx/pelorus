# decimal history
% stdin qloop-valid.txt
$ pelorus_qloop --target 90.0 --tolerance 1.0 --min 10.0 --max 45.0 --start 20.0 --resolution 0.5 --max-iter 10 --direction 1
> probe 19.5000
[exit 0]

# indented history with blanks and tab
% stdin qloop-indented.txt
$ pelorus_qloop --target 90.0 --tolerance 1.0 --min 10.0 --max 45.0 --start 20.0 --resolution 0.5 --max-iter 10 --direction 1
> probe 19.5000
[exit 0]

# history exponents
% stdin qloop-exponent.txt
$ pelorus_qloop --target 90.0 --tolerance 1.0 --min 10.0 --max 45.0 --start 20.0 --resolution 0.5 --max-iter 10 --direction 1
> probe 19.5000
[exit 0]

# hex score refused
% stdin qloop-score-hex.txt
$ pelorus_qloop --target 90.0 --tolerance 1.0 --min 10.0 --max 45.0 --start 20.0 --resolution 0.5 --max-iter 10 --direction 1
! pelorus_qloop: line 1: invalid score value
[exit 1]

# plus knob refused
% stdin qloop-knob-plus.txt
$ pelorus_qloop --target 90.0 --tolerance 1.0 --min 10.0 --max 45.0 --start 20.0 --resolution 0.5 --max-iter 10 --direction 1
! pelorus_qloop: line 1: invalid knob value
[exit 1]

# score suffix refused
% stdin qloop-score-suffix.txt
$ pelorus_qloop --target 90.0 --tolerance 1.0 --min 10.0 --max 45.0 --start 20.0 --resolution 0.5 --max-iter 10 --direction 1
! pelorus_qloop: line 1: trailing garbage after score
[exit 1]

# --max-iter non-decimal option refused
$ pelorus_qloop --target 90.0 --tolerance 1.0 --min 10.0 --max 45.0 --start 20.0 --resolution 0.5 --max-iter 0x8 --direction 1
! pelorus_qloop: invalid value for '--max-iter': '0x8'
! Usage: pelorus_qloop --target T --tolerance D --min A --max B --start S --resolution R --max-iter N --direction -1|1
[exit 1]

# --direction non-decimal option refused
$ pelorus_qloop --target 90.0 --tolerance 1.0 --min 10.0 --max 45.0 --start 20.0 --resolution 0.5 --max-iter 10 --direction +1
! pelorus_qloop: invalid value for '--direction': '+1'
! Usage: pelorus_qloop --target T --tolerance D --min A --max B --start S --resolution R --max-iter N --direction -1|1
[exit 1]

