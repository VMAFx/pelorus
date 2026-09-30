<!-- markdownlint-disable MD013 -->
# Paperclip Operating Rules — VMAFx/pelorus

## Operating Contract

- Branch push != shipping. Open PR required. Work ships after merge.
- Rebase onto master immediately: run git fetch origin && git rebase origin/master before proposing.
- Rule 0 Terminal Disposition: every run ends with structured disposition: in_review or blocked.
- Timeout != failure. Re-check open PRs before retry; prevent duplicate PRs.
- Text register internal: `caveman` skill: fragments, no filler, verbatim code/paths/errors; facts, paths, commands, verdict.

## GitHub Push Protocol

```bash
git push --set-upstream origin HEAD
```

## High-Integrity Invariants

- HISS-01: Acyclic control flow; no recursion; legacy goto growth forbidden
- HISS-02: Scalar upper bounds on all loops; context timeout on all I/O
- HISS-04: McCabe Cyclomatic <= 10, Cognitive <= 15, Func LOC <= 60
- HISS-07: Public C errors use `pel_result`; every non-void result checked or explicitly discarded
- HISS-10: Zero-warning tolerance across compiler, linters, and formatters
- HISS-15: 3D testing mandatory (Positive, Negative, Boundary >= 2 checks/dim)
- HISS-16: Canonical AGENTS.md compiled to vendor harnesses
