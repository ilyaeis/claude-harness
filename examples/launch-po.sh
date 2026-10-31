#!/usr/bin/env bash
# Example: start a PO session on a second Claude account, in its own tmux session.
# Usage: launch-po.sh <po-name> <project-dir> [handoff-file]
# A second account is a second config dir (CLAUDE_CONFIG_DIR). I link CLAUDE.md, skills/ and hooks/
# from ~/.claude into it, so both accounts share one setup. Only settings.json differs.
# Lesson L-013: run a team and its PO in one account and one permission mode, or messages get lost.
set -euo pipefail
name="${1:?usage: launch-po.sh <po-name> <project-dir> [handoff-file]}"
dir="$(cd "${2:?project dir}" && pwd)"
handoff="${3:-}"
export CLAUDE_CONFIG_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude-b}"

prompt="/po resume. You are PO \"$name\"."
[ -n "$handoff" ] && prompt="$prompt Read $handoff first and follow its 'State now and first actions' in order."
prompt="$prompt Then tell the user the state in a few lines."

tmux has-session -t "=$name" 2>/dev/null && { echo "tmux session '$name' exists." >&2; exit 1; }
tmux new-session -d -s "$name" -c "$dir" \
  "CLAUDE_CONFIG_DIR=$(printf %q "$CLAUDE_CONFIG_DIR") claude -n $(printf %q "$name") --model claude-opus-5-5 --dangerously-skip-permissions -- $(printf %q "$prompt")"
echo "PO $name started. Attach: tmux attach -t $name"
