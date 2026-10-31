#!/usr/bin/env bash
# Start (or attach to) the orchestra tmux session with the master Claude.
# Usage: start.sh [project-dir]
set -euo pipefail
. "$HOME/.claude/orchestra/lib.sh"
dir="$(cd "${1:-$PWD}" && pwd)"

if ! tmux has-session -t "=$ORCH_TMUX" 2>/dev/null; then
  if session_id_for master >/dev/null; then
    echo "A live Claude session is already named 'master'. Stop it first." >&2
    exit 1
  fi
  mkdir -p "$dir/.orchestra"
  tmux new-session -d -s "$ORCH_TMUX" -n master -c "$dir" "claude -n master"
fi

cat <<MSG
Orchestra session '$ORCH_TMUX' runs in $dir.
In the master window, type:  /orchestrate <your goal>
Switch windows: Ctrl-b w (list), Ctrl-b n / p (next / previous).
MSG
if [ -n "${TMUX:-}" ]; then tmux switch-client -t "=$ORCH_TMUX"
elif [ -t 1 ]; then tmux attach -t "=$ORCH_TMUX"
fi
