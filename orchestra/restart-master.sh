#!/usr/bin/env bash
# A team master restarts ITSELF (watcher wake CTX_500K). Run it from the master's own Bash tool:
#   ~/.claude/orchestra/restart-master.sh [<team-dir>]       (default: the current dir)
# Refuses when:
#   - <dir>/.orchestra/launch-master.sh is missing (plain sessions are out of scope);
#   - <dir>/.orchestra/master-handoff.md is missing or older than 30 min;
#   - the caller is not the master named in launch-master.sh (it must be an ancestor of this script).
# Steps:
#   1. Logs "restart" to .orchestra/log.md (orch-log.sh). Copies the handoff to master-handoff.<time>.md.
#   2. Writes a master retire report to $BRAIN_DIR/reports/<topic>/ (links the previous report of
#      this master, if any), runs report-check.sh --retire and report-link.sh.
#   3. Writes .orchestra/launch-master-restart.sh: launch-master.sh with a new first prompt.
#   4. Starts a detached helper (setsid). It waits until this script ends and the old master's
#      transcript has not changed for RESTART_QUIET s (its last turn ended; max 120 s), sends SIGTERM to the old
#      Claude pid, waits until it exits (max 120 s), then starts the new master in a new tmux window
#      of the same tmux session (or a new tmux session if the old one is gone).
# Workers keep running. The new master finds them by registry.json and status.sh.
# Env: RESTART_QUIET (default 10). Test only: ORCHESTRA_BRAIN (brain root).
set -euo pipefail
dir="$(cd "${1:-$PWD}" && pwd)"
odir="$dir/.orchestra"
launch="$odir/launch-master.sh"
handoff="$odir/master-handoff.md"
brain="${ORCHESTRA_BRAIN:-${BRAIN_DIR:-$HOME/brain}}"
ORCH="$HOME/.claude/orchestra"
log() { ORCHESTRA_PROJECT="$dir" "$ORCH/orch-log.sh" "$master" "$@" >/dev/null; }

[ -f "$launch" ] || { echo "refused: no $launch. Only team masters (started by start-master.sh) can restart." >&2; exit 1; }
[ -f "$handoff" ] || { echo "refused: no $handoff. Write it first (goal, plan, tasks + status, live workers, open questions, next step)." >&2; exit 1; }
age=$(( $(date +%s) - $(stat -c %Y "$handoff") ))
[ "$age" -le 1800 ] || { echo "refused: $handoff is $((age / 60)) min old (max 30). Update it first." >&2; exit 1; }

master=$(sed -n 's/.*ORCHESTRA_MASTER=\([^ ]*\).*/\1/p' "$launch" | head -1)
tsess=$(sed -n 's/.*ORCHESTRA_SESSION=\([^ ]*\).*/\1/p' "$launch" | head -1)
[ -n "$master" ] && [ -n "$tsess" ] || { echo "refused: no ORCHESTRA_MASTER or ORCHESTRA_SESSION in $launch" >&2; exit 1; }
grep -q '^exec claude .* -- ' "$launch" || { echo "refused: no 'exec claude ... -- <prompt>' line in $launch" >&2; exit 1; }

# The old master: the live session with this name. It must be our ancestor (the master runs this script itself).
read -r pid sid < <(python3 - "$master" <<'PY'
import sys
sys.path.insert(0, __import__("os").path.expanduser("~/.claude/orchestra"))
from ctx_meter import live_sessions
s = live_sessions().get(sys.argv[1])
print(s["pid"], s["sid"]) if s else print("- -")
PY
)
[ "$pid" != "-" ] || { echo "refused: no live Claude session named $master" >&2; exit 1; }
p=$$
while [ "$p" -gt 1 ] && [ "$p" != "$pid" ]; do p=$(sed 's/.*) //' "/proc/$p/stat" | cut -d" " -f2); done
[ "$p" = "$pid" ] || { echo "refused: $master (pid $pid) is not the caller. A master restarts only itself." >&2; exit 1; }
ctx=$(python3 -c "import sys; sys.path.insert(0, '$ORCH'); from ctx_meter import tokens; print(tokens('$sid'))")
transcript=$(ls "$HOME"/.claude*/projects/*/"$sid".jsonl 2>/dev/null | head -1)

# 1. Log and keep a copy of the handoff.
stamp=$(date +%Y%m%d-%H%M)
cp -p "$handoff" "$odir/master-handoff.$stamp.md"
log restart "self-restart at $ctx tokens; old pid $pid session $sid; handoff copy master-handoff.$stamp.md"

# 2. Master retire report. Previous report: newest brain report with "agent: <master>".
prev=$(grep -l -s "^agent: $master\$" "$brain"/reports/*/*.md | xargs -r ls -t | head -1 || true)
if [ -n "$prev" ]; then topic=$(basename "$(dirname "$prev")"); links="[$topic/$(basename "$prev")]"
else topic=$(basename "$dir"); links="[]"; fi
mkdir -p "$brain/reports/$topic"
nn=$(ls "$brain/reports/$topic" | sed -n 's/^\([0-9][0-9]*\)-.*/\1/p' | sort -n | tail -1)
nn=$(printf '%02d' $(( 10#${nn:-0} + 1 )))
report="$brain/reports/$topic/$nn-$master-restart-$stamp.md"
{
  cat <<EOF
---
date: $(date +%FT%H:%M)
model: claude-opus-5-5
project: $dir
topic: $topic
status: open
agent: $master
role: master
team: ${master#master-}
task: $handoff
session_id: $sid
parent: user
result: partial
links: $links
tags: [type/report, topic/agent-orchestration, status/open]
---

# $master self-restart at $ctx tokens

## Result (5 lines)
- $master restarted itself at $ctx tokens (watcher CTX_500K rule).
- Old Claude session: $sid (pid $pid). The new session has the same name.
- The work goes on from the handoff below (copy: \`$odir/master-handoff.$stamp.md\`).
- Workers kept running. The new master finds them by registry.json and status.sh.

## Server jobs
See the handoff below.

## Handoff at restart
EOF
  sed 's/^#/###/' "$handoff"
} > "$report"
"$ORCH/report-check.sh" --retire "$report"
ORCHESTRA_PROJECT="$dir" ORCHESTRA_BRAIN="$brain" "$ORCH/report-link.sh" "$report"

# 3. Launcher with the restart prompt.
prompt="/orchestrate Read .orchestra/master-handoff.md first and follow it. You restarted at 500k; continue without asking the user."
relaunch="$odir/launch-master-restart.sh"
{ grep -v '^exec claude ' "$launch"; printf '%s -- %q\n' "$(sed -n '/^exec claude /s/ -- .*$//p' "$launch")" "$prompt"; } > "$relaunch"
chmod +x "$relaunch"

# 4. Detached helper: wait for this script to end, stop the old master, start the new one.
quiet="${RESTART_QUIET:-10}"
setsid nohup bash -c '
  me=$1 pid=$2 quiet=$3 tsess=$4 master=$5 dir=$6 relaunch=$7 ORCH=$8 tr=$9
  log() { ORCHESTRA_PROJECT="$dir" "$ORCH/orch-log.sh" "$master" "$@" >/dev/null; }
  while kill -0 "$me" 2>/dev/null; do sleep 1; done
  for _ in $(seq 120); do
    [ -f "$tr" ] && [ $(( $(date +%s) - $(stat -c %Y "$tr") )) -lt "$quiet" ] || break
    sleep 1
  done
  [ -f "$tr" ] || sleep "$quiet"
  kill -TERM "$pid" 2>/dev/null || true
  for _ in $(seq 120); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
  if kill -0 "$pid" 2>/dev/null; then log restart-failed "old pid $pid still alive after 120 s; new master not started"; exit 1; fi
  if tmux has-session -t "=$tsess" 2>/dev/null; then
    tmux new-window -d -t "=$tsess:" -n "$master" -c "$dir" "$relaunch"
  else
    tmux new-session -d -s "$tsess" -n "$master" -c "$dir" "$relaunch"
  fi && log restart-done "new $master started in tmux $tsess" || log restart-failed "tmux start failed"
' restart-helper "$$" "$pid" "$quiet" "$tsess" "$master" "$dir" "$relaunch" "$ORCH" "$transcript" \
  > "$odir/restart-master.log" 2>&1 < /dev/null &

echo "OK: report $report"
echo "When your turn ends (transcript quiet ${quiet}s), the helper stops $master (pid $pid) and starts a new $master in tmux $tsess."
echo "End your turn now. Do not start new work."
