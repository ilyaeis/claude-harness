#!/usr/bin/env bash
# Auto-commit the brain vault. Commits only when there are changes.
# Run by the brain-autocommit systemd user timer (daily 00:00, catch-up on resume).
set -euo pipefail

VAULT="${BRAIN_DIR:-$HOME/brain}"
cd "$VAULT" || exit 1

# Nothing to do if not a git repo or no changes.
git rev-parse --git-dir >/dev/null 2>&1 || exit 0
if [ -z "$(git status --porcelain)" ]; then
    exit 0
fi

# Debounce: skip if anything changed in the last 15 s (Obsidian or a skill may be mid-write).
if [ -n "$(find . -path ./.git -prune -o -type f -mmin -0.25 -print -quit)" ]; then
    exit 0
fi

git add -A
git commit -m "auto: vault snapshot $(date '+%Y-%m-%d %H:%M')" >/dev/null
