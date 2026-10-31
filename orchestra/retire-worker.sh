#!/usr/bin/env bash
# End a finished worker and mark it retired.
# Usage: retire-worker.sh [--force] <name> [report_path]
# Refuses if:
#   - no report file exists (--force does not skip this);
#   - report-check.sh --retire finds an error in the report;
#   - a chain of the worker (chain-run.sh) has no EXIT marker yet;
#   - report-link.sh fails.
# --force turns the last three into warnings, so a master is never stuck.
# Then it runs report-link.sh (reverse links, INDEX.md line, symlink in .orchestra/reports/).
# The transcript stays in ~/.claude/projects.
set -euo pipefail
. "$HOME/.claude/orchestra/lib.sh"

force=0; args=()
for a in "$@"; do [ "$a" = --force ] && force=1 || args+=("$a"); done
set -- "${args[@]+"${args[@]}"}"
name="${1:?usage: retire-worker.sh [--force] <name> [report_path]}"
[ -f "$ORCH_REG" ] || { echo "no registry at $ORCH_REG" >&2; exit 1; }
row=$(jq -c --arg n "$name" '.[] | select(.name==$n)' "$ORCH_REG")
[ -n "$row" ] || { echo "no worker '$name' in registry" >&2; exit 1; }

report="${2:-$(jq -r '.report_path // empty' <<<"$row")}"
[ -n "$report" ] && [ -f "$report" ] || { echo "report not found: '${report}'. Not retiring." >&2; exit 1; }

# Stop here on a failed check, unless --force. Args: what failed, how to fix.
refuse() {
  if [ "$force" = 1 ]; then
    echo "WARNING (--force): $1" >&2
  else
    echo "NOT RETIRING $name: $1" >&2
    echo "Fix: $2" >&2
    echo "Or, if the worker cannot fix it: retire-worker.sh --force $name $report" >&2
    exit 1
  fi
}

if ! out=$("$ORCH_HOME/report-check.sh" --retire "$report" 2>&1); then
  echo "$out" >&2
  refuse "the report fails the schema check (errors above)." \
    "ask $name to fix each 'error:' line in $report, then run retire-worker.sh $name again."
fi

# Every chain of the worker must have ended (EXIT marker on its host).
while IFS=$'\t' read -r cid chost cdir; do
  [ -n "$cid" ] || continue
  f="${cdir/#\~\//}/EXIT"
  if [ "$chost" = local ]; then mark=$(cat "$HOME/$f" 2>/dev/null || true)
  else mark=$(ssh -o GSSAPIAuthentication=no -o BatchMode=yes -o ConnectTimeout=10 "$chost" "cat $f" 2>/dev/null || true)
  fi
  if [ -n "$mark" ]; then
    reg_update --arg n "$name" --arg id "$cid" --arg m "$mark" \
      'map(if .name==$n then .chains |= map(if .id==$id then .exit=$m else . end) else . end)'
  else
    look="cat ${cdir}/heartbeat; tail -3 ${cdir}/log"; [ "$chost" = local ] || look="ssh $chost '$look'"
    refuse "chain $cid on $chost has no EXIT marker (still running, or the host did not answer)." \
      "wait for the chain to end, or stop it. Check: $look"
  fi
done < <(jq -r '.chains // [] | .[] | select(.exit==null) | [.id, .host, .dir] | @tsv' <<<"$row")

if ! "$ORCH_HOME/report-link.sh" "$report"; then
  refuse "report-link.sh failed (see above)." "fix the cause above, then run retire-worker.sh $name again."
fi

sid=$(session_id_for "$name" || jq -r '.session_id // empty' <<<"$row")

if w=$(worker_window "$name"); then
  # A tmux hook can leave the pane in view/copy mode; leave it so keys reach Claude.
  [ "$(tmux display -p -t "$w" '#{pane_in_mode}')" = 1 ] && tmux send-keys -t "$w" -X cancel
  tmux send-keys -t "$w" Escape
  tmux send-keys -t "$w" -l '/exit'
  tmux send-keys -t "$w" Enter
  for _ in $(seq 1 10); do
    worker_window "$name" >/dev/null || break
    sleep 1
  done
  tmux kill-window -t "$w" 2>/dev/null || true
fi

reg_update --arg n "$name" --arg r "$report" --arg s "$sid" --arg now "$(date -Is)" \
  'map(if .name==$n then .status="retired" | .retired_at=$now | .report_path=$r | .session_id=(if $s=="" then .session_id else $s end) else . end)'
rm -f "$ORCH_DIR/launch-$name.sh"
echo "retired $name (report: $report, session: ${sid:-unknown})"
