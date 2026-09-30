# Pelorus STATE — office box (Windows, Arc B580 + UHD 770)

> Local, git-ignored. The canonical PLAN/STATE/AUDIT live on the home
> workstation's `.workingdir/`; merge this session log into it there.

## Session 2026-09-30 (office box)

Start: master `f03badd` in sync with origin. vmafx clone fast-forwarded
(+5, to `822850396`). Praetor main is `25451d888c87` (PR #56 pins `846da590`,
162 commits behind). Local praetor clone left untouched (other sessions'
worktrees hang off it).

User decisions (2026-09-30): merge PRs when green + reviewed (squash, stack
order); #61 gets an MSYS2 UCRT64 Windows CI job; stop/restore LM Studio for GPU
timing runs; vmafx re-vendoring is handed to a vmafx session.

Queue:

- #59 renovate Go 1.27.x — checker hard-codes `go-version: '1.26.x'`.
- #56 praetor onboarding — merge master, re-pin engine to praetor main,
  re-baseline legacy #57 debt, get the standards gate green.
- #58 x265 fixture + replay hardening (stacked on #56) — covers #60/#62;
  SVT-AV1 `true` compile error on its stale base.
- #61 Windows UTF-8 paths — new PR + Windows CI job (ADR-0149).
- Intel HW validation on B580/UHD 770 — native Windows FFmpeg build, Vulkan
  filter matrix, QSV ROI proof (A380 low-power bug blocked it), QP-feedback QSV
  feasibility, perf rows (ADR-0150).

Environment: per-stream clones in `C:\tmp\pel\{s59,s56,s58,s61,shw}`; CI-parity
image `pelorus-ci:26.04`; MSYS2 UCRT64 toolchain installed (use `libx264`, not
the `x264` CLI package — it drags in MSYS2's own ffmpeg).

LM Studio state to restore after timing runs: app running, server ON port
1234, no model loaded.

### Log

- 10:55 Launched workflow `pelorus-office-queue` (run `wf_a5c6659f-f28`): streams
  s59, s56→s58, s61, shw (build → correctness → QSV → perf → docs/PR), each
  implement → review → fix. Merges stay with the orchestrator (order #59, #56,
  #58, #61, shw).
- 11:05 Host bugcheck 0x7F (arg1 0x8, double fault; dump
  `C:\Windows\Minidump\093026-7296-01.dmp`, unreadable without admin). Prior
  crash 2026-09-29 19:44 was 0x13A. At crash time: two Docker containers + file
  edits, no pelorus GPU work; Codex (vmafx) sessions also active. Nothing had
  completed or been pushed (s56 had a local-only merge commit `d514ea8`).
- 11:12 Relaunched the same workflow run with a crash-recovery note and
  resource caps (docker --cpus=8 --memory=6g, native -j8). The vmafx temporary
  `.wslconfig` (24 GB / 22 CPUs) is still in effect on a 32 GB host.
- 13:45 Stream results so far: #59 done (ADR-0151, checker mirrors the Renovate
  setup-go identity) → **merged `eb3b045`**. #56 refreshed + re-pinned to
  25451d88, audit now uses `--base` + `baseline --verify`, 7/7 green on
  `88c479f` (praetor #642 filed, #175 commented). #61 → PR #63 (ADR-0149,
  UTF-8 opener, Windows MSYS2 job) 5/5 green on `9e3b544`. shw-1 native
  Windows build OK (no pelorus Windows breaks); shw-2 found Intel Windows
  Vulkan driver multi-plane host-image-copy corruption (workaround modes
  `vk:0,disable_multiplane=1` / `vk:1,linear_images=1,disable_multiplane=1`),
  fixed 3 pelorus precision bugs locally (not pushed). Host instability
  evidence: WHEA fatal 28.09, bugchecks 0xA/0x13A/0x7F, nondeterministic cc1
  segfault.
- 13:50 User: adopt praetor agent-hooks (revert decline), make Standards a
  required check after #56 merges, merge #56 when green. Background agent
  finalising #56 (merge master eb3b045, hooks, ADR-0145 Accepted, ready).
- 14:25 Usage limit interrupted the finalize agent and 4 workflow agents
  (s58 impl, hw:3/4/5). Resumed both. Workflow re-ran s56 review/fix; the fix
  stage deferred to the finalize agent (same clone) and forwarded 16 findings
  — major: #59's checker adds 11 unbaselined fingerprints (75→76), needs a
  re-record with --allow-increase + docs/badge 76. Workflow will skip s58
  (s56 reports blocked); #58 is handled manually after #56 merges.
- 15:00 #56 finalised (merge eb3b045, agent hooks adopted in .claude/.codex/
  .gemini, ADR-0145 Accepted, baseline 75→76 for #59's checker function,
  7/7 green) → **merged `e5b9d70`**. Standards gate added to master's
  required checks. praetorctl (standardsctl@25451d88) installed at
  `%USERPROFILE%\go\bin\praetorctl.exe` (on user PATH); Windows hook test:
  allow=0, deny=2 under Git Bash/cmd/pwsh direct. #58 retargeted to master;
  background agent merging master + finishing #60/#62. Main checkout ff'd to
  e5b9d70.
- 15:06 Host bugcheck 0x1E (c0000005; dump 093026-13390-01.dmp), reboot
  15:17. ~1 min earlier hw:3-qsv started a VPL surface-reuse (lock vs sync)
  MBQP encode stress on the UHD 770 (IMPL=1) — first crash correlated with our
  GPU work. User chose to continue GPU work on both GPUs as planned.
  #58 agent had 5 unpushed local commits (merge e5b9d70, DACL refactor,
  baseline re-record) → resumed. Workflow resumed (hw:3 restarts).
- 15:22 Third crash today: bugcheck 0x3B (c0000005), reboot 15:28. Running at
  the time: #58 agent docker run (s58-cx) + s61 re-review audits; no GPU work.
  Bugchecks since 29.09: 0xA, 0x13A, 0x7F, 0x1E, 0x3B + WHEA fatal 28.09 →
  platform instability under load (RAM suspected). Switched to serial
  execution: one agent at a time (#58 first, docker --cpus=4 --memory=4g,
  -j4), workflow not resumed; hardware stages to follow alone.
- 15:50 Fourth crash today: bugcheck 0x20001 (HYPERVISOR_ERROR) with only
  light read-only review agents running. #58 reviews: c-reviewer approve
  (minors), ffmpeg-patch-reviewer approve (2 should-fix hermeticity gaps),
  pr-body-checker mergeable; security review cut off by the crash.
- 15:58 **Merged #58 as `6731314`** (closes #60, #62; baseline 62). Follow-up
  issue #65 for the review hardening items. Main checkout ff'd. Agent merging
  master into #63 under no-Docker/no-GPU rules. Hardware stages (QSV, perf,
  docs PR) on hold: host unstable even at light load.
- 16:30 #63 updated (merge 6731314, baseline re-record 62→62, 8/8 green) →
  **merged `174938e`** (closes #61). vmafx handoff issue VMAFx/vmafx#1640
  (re-vendor mirror 93bef12 → 174938e). PR #64 (workflow-opened Renovate
  matcher reimplementation, +555) closed by user decision; docs-only
  correction PR #66 (ADR-0152: checker validates the mirroring manager +
  literal only; commit a887f55) awaiting CI.
- 16:50 User: praetor was not adopted properly (no dev container; ad-hoc
  load killed the machine). Found `.standards.yaml` declines dev-container,
  git-hooks, branch-ruleset (dupes too), added by an agent in 11d1bdc on
  2026-09-20 with no user decision; I merged #56 asking only about
  agent-hooks. User decision: full adoption, reverse all three. One serial
  agent on chore/praetor-full-adoption (C:\tmp\pel\adopt): engine-rendered
  dev container + lefthook + ruleset file, gates run inside the dev
  container, no forge mutation (`sync --remote` waits for commit signing on
  this box), ADR-0153 amends 0145. Feedback memory saved.
- 17:20 PR #66 (ADR-0152, doc-reviewer fixes 3060b23) 9/9 green → **merged `f65b167`**. Main checkout ff'd. Full-adoption agent still running.
- 17:20 Sixth bugcheck today: 0x50 (PAGE_FAULT_IN_NONPAGED_AREA) during the adoption agent's local dev-container image build (docker/buildkit). Agent had d722241 (adopt dev-container/git-hooks/ruleset) + 578488c (devcontainer Node 24) local, ADR-0153 draft uncommitted. Resumed: push now, dev-container build + gates move to hosted CI, local work light only.
