#!/usr/bin/env bash
# Write the live tmux sessions and panes to $BRAIN_DIR/tmux-now.md (shown on 000-home.md).
# Called by the tmux hooks in tmux/tmux.conf.snippet. Read-only on tmux.
set -euo pipefail
out="${BRAIN_DIR:-$HOME/brain}/tmux-now.md"
{
  echo "# tmux now ($(date '+%Y-%m-%d %H:%M'))"
  echo
  echo "| session | window | pane | command | cwd |"
  echo "|---|---|---|---|---|"
  tmux list-panes -a -F '| #{session_name} | #{window_name} | #{pane_index} | #{pane_current_command} | #{pane_current_path} |' 2>/dev/null || true
} > "$out.tmp" && mv "$out.tmp" "$out"
