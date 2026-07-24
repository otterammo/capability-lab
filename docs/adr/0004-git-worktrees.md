# ADR-0004: Git Worktrees

**Status:** Accepted

Check out the exact fixture commit into a detached Git worktree. This preserves the source repository, produces native patches, and avoids custom copy synchronization. Cleanup is always attempted and its result is persisted; Docker isolation remains a later phase.
