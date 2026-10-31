#!/usr/bin/env bash
# Start a team master in its own tmux session, with its first prompt as a launch argument.
# It never types into a tmux window.
# Usage: start-master.sh <team-dir> <team> [gpus] [approved]
#   team  short name, e.g. depth -> tmux session orch-depth, master master-depth, workers depth-*
#   gpus  the team's GPU set, comma-separated, e.g. gpu1:0,gpu1:1 (empty = no GPU)
# The master always runs on Opus 5.5 with bypass permissions.
set -euo pipefail
dir="$(cd "${1:?usage: start-master.sh <team-dir> <team> [gpus]}" && pwd)"
team="${2:?team name}"
gpus="${3:-}"
approved="${4:-}"   # "approved" = the user already approved the plan in the handoff
export ORCHESTRA_SESSION="orch-$team" ORCHESTRA_MASTER="master-$team" ORCHESTRA_PROJECT="$dir"
. "$HOME/.claude/orchestra/lib.sh"

tmux has-session -t "=$ORCH_TMUX" 2>/dev/null && { echo "tmux session '$ORCH_TMUX' exists. Do not start another." >&2; exit 1; }
session_id_for "$ORCH_MASTER" >/dev/null && { echo "A live Claude session is already named '$ORCH_MASTER'." >&2; exit 1; }
[ -f "$dir/.orchestra/master-handoff.md" ] || { echo "write $dir/.orchestra/master-handoff.md first" >&2; exit 1; }

prompt="/orchestrate Read .orchestra/master-handoff.md first and follow it. Show the user your plan in this window and wait for the user's OK. You work for the user directly. Do not report to the PO."
[ "$approved" = approved ] && prompt="/orchestrate Read .orchestra/master-handoff.md first and follow it. The user already approved the plan in the handoff (he asked the PO for exactly this work), so start it now without waiting. You work for the user directly. Do not report to the PO."

launcher="$dir/.orchestra/launch-master.sh"
{
  echo '#!/usr/bin/env bash'
  printf 'cd %q\n' "$dir"
  printf 'export ORCHESTRA_SESSION=%q ORCHESTRA_MASTER=%q ORCHESTRA_PROJECT=%q\n' "$ORCH_TMUX" "$ORCH_MASTER" "$dir"
  printf 'export ORCHESTRA_WORKER_PREFIX=%q ORCHESTRA_GPUS=%q\n' "$team-" "$gpus"
  launcher_env
  printf 'exec claude -n %q --model claude-opus-5-5 --dangerously-skip-permissions -- %q\n' "$ORCH_MASTER" "$prompt"
} > "$launcher"
chmod +x "$launcher"

tmux new-session -d -s "$ORCH_TMUX" -n "$ORCH_MASTER" -c "$dir" "$launcher"
echo "$ORCH_MASTER started in tmux $ORCH_TMUX:$ORCH_MASTER ($dir), GPUs: ${gpus:-none}"
