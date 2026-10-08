- Enforced the commit message policy. A `commit-msg` Lefthook job runs
  `forge check-message` and a `Signed-off-by` (DCO) check, and the `Standards`
  workflow runs `forge check-commits` plus a DCO check over every pull request
  commit, so a breaking change without a `Migration:` footer is refused. The
  agent context lists all 21 HISS invariants and states which ones are review
  only.
