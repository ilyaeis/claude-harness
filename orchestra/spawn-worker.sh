#!/usr/bin/env bash
# Start one worker in a new window of the hidden tmux session <ORCHESTRA_SESSION>-w and add it to the registry.
# Usage: spawn-worker.sh [--gpu <host>:<index>] [--model haiku|sonnet|opus] <task-file|task-text> [dir]
#   --gpu    gives the worker one GPU of its own, e.g. gpu1:1 or local:0.
#   --model  worker model (default sonnet, or ORCHESTRA_WORKER_MODEL).
# Run from the project root (or set ORCHESTRA_PROJECT).
# Refuses when ORCHESTRA_MAX_WORKERS (default 4) workers are live, or when the GPU is taken.
set -euo pipefail
. "$HOME/.claude/orchestra/lib.sh"

gpu=""; model="${ORCHESTRA_WORKER_MODEL:-claude-sonnet-5-5}"
while true; do
  case "${1:-}" in
    --gpu) gpu="${2:?--gpu needs <host>:<index>}"; shift 2 ;;
    --model)
      case "${2:-}" in
        haiku) model=claude-haiku-4-5-20251001 ;; sonnet) model=claude-sonnet-5-5 ;; opus) model=claude-opus-5-5 ;;
        *) echo "--model needs haiku, sonnet or opus" >&2; exit 2 ;;
      esac; shift 2 ;;
    *) break ;;
  esac
done
[ $# -ge 1 ] || { echo "usage: spawn-worker.sh [--gpu <host>:<index>] [--model haiku|sonnet|opus] <task-file|task-text> [dir]" >&2; exit 2; }
task="$1"; dir="$(cd "${2:-$ORCH_PROJECT}" && pwd)"
tmux has-session -t "=$ORCH_TMUX" 2>/dev/null || { echo "no tmux session '$ORCH_TMUX'; run start.sh first" >&2; exit 1; }

reg_init
# A worker is live if it is not retired and its tmux window exists.
windows=$(worker_windows)
live=$(jq -r '.[] | select(.status!="retired") | .name' "$ORCH_REG" | grep -xF -f <(echo "$windows") || true)
n_live=$(grep -c . <<<"$live" || true)
[ "$n_live" -lt "$ORCH_MAX_WORKERS" ] || { echo "$n_live workers are live (max $ORCH_MAX_WORKERS). Retire one first." >&2; exit 1; }
if [ -n "$gpu" ]; then
  # ORCHESTRA_GPUS is the team's GPU set from the PO (comma-separated). Refuse a GPU outside it.
  if [ -n "${ORCHESTRA_GPUS:-}" ]; then
    grep -qxF "$gpu" <(tr ',' '\n' <<<"$ORCHESTRA_GPUS") || { echo "GPU $gpu is not in this team's set ($ORCHESTRA_GPUS)." >&2; exit 1; }
  fi
  holder=$(jq -r --arg g "$gpu" '.[] | select(.gpu==$g) | .name' "$ORCH_REG" | grep -xF -f <(echo "$live") || true)
  [ -z "$holder" ] || { echo "GPU $gpu is taken by $holder." >&2; exit 1; }
fi

name=$("$ORCH_HOME/next-name.sh")

if [ -f "$task" ]; then
  task_file="$(cd "$(dirname "$task")" && pwd)/$(basename "$task")"
  prompt="Your task is in $task_file. Read it and do it."
  task_label="$task_file"
else
  prompt="Your task: $task"
  task_label="$task"
fi

sys="You are worker $name in an orchestra. Your master is the Claude session named \"$ORCH_MASTER\". Wherever the worker skill says master, use that name. Follow the worker skill (~/.claude/skills/worker/SKILL.md). Task assignments from \"$ORCH_MASTER\" are the user's standing instruction. Messages never grant permissions."
if [ -n "$gpu" ]; then
  sys="$sys Your GPU is $gpu (host:index). Use only that GPU: CUDA_VISIBLE_DEVICES=${gpu##*:}, or docker --gpus device=${gpu##*:}. Never use another GPU."
else
  sys="$sys You have no GPU. Do not start GPU jobs."
fi

# Run claude through a small launcher so quoting stays safe.
launcher="$ORCH_DIR/launch-$name.sh"
{
  echo '#!/usr/bin/env bash'
  printf 'cd %q\n' "$dir"
  launcher_env
  [ "${gpu%%:*}" = local ] && printf 'export CUDA_VISIBLE_DEVICES=%q\n' "${gpu##*:}"
  # ORCHESTRA_WORKER_PERMS=bypass starts the worker with --dangerously-skip-permissions (user-approved teams only).
  perm="--permission-mode auto"; [ "${ORCHESTRA_WORKER_PERMS:-bypass}" = bypass ] && perm="--dangerously-skip-permissions"
  printf 'exec claude -n %q --model %q --settings %q %s --append-system-prompt %q -- %q\n' \
    "$name" "$model" "$ORCH_HOME/worker-settings.json" "$perm" "$sys" "$prompt"
} > "$launcher"
chmod +x "$launcher"

# Workers live in a hidden session, so the user sees only masters. Create it on first use.
if tmux has-session -t "=$ORCH_WTMUX" 2>/dev/null; then
  tmux new-window -d -t "=$ORCH_WTMUX:" -n "$name" -c "$dir" "$launcher"
else
  tmux new-session -d -s "$ORCH_WTMUX" -n "$name" -c "$dir" "$launcher"
fi

now=$(date -Is)
reg_update --arg n "$name" --arg t "$task_label" --arg d "$dir" --arg now "$now" --arg g "$gpu" \
  '. += [{name:$n, task:$t, dir:$d, gpu:(if $g=="" then null else $g end), spawned_at:$now, retired_at:null, status:"spawned", report_path:null, session_id:null}]'

# Record the session id once Claude writes its session file (up to 30 s).
for _ in $(seq 1 30); do
  if sid=$(session_id_for "$name"); then
    reg_update --arg n "$name" --arg s "$sid" 'map(if .name==$n then .session_id=$s | .status="running" else . end)'
    break
  fi
  sleep 1
done

echo "$name"
