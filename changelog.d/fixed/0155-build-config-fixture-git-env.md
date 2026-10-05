- **`scripts/check-build-config.py --self-test` no longer runs its Git fixtures
  against the repository being pushed.** Git exports `GIT_DIR` and
  `GIT_WORK_TREE` to hooks, and the fixtures inherited them: a push hook that ran
  the fast suite committed `fixture` commits onto the pushed branch, created
  `base` and `n1.2.3` branches and wrote `core.hooksPath`, `commit.gpgsign` and
  `user.name` into the repository's config. The checker now drops the
  repository-selecting variables first, and a regression proves it.
