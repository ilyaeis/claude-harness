#!/usr/bin/env bash
# Switch the orchestra hooks from "log" to "enforce", with the user's Yes first.
# Usage: mode-switch.sh ask      post the inbox ask (timer orch-hooks-ask, 2026-10-21 10:00)
#        mode-switch.sh switch   write "enforce" to the mode file only if the ask got Yes (timer orch-hooks-enforce, 2026-10-22 00:00)
set -euo pipefail
H="$HOME/.claude/orchestra/hooks"
case "${1:-}" in
  ask)
    summary=$(python3 "$H/orch_hooks.py" recheck)
    sample=$(grep -E ' (log|enforce) ' "$H/hooks.log" 2>/dev/null | tail -3 | cut -c1-160 | tr '\n' ' ' || true)
    "$HOME/.claude/orchestra/inbox-ask.sh" --from master-harness --kind decision --yesno \
      "Enforce orchestra hooks tomorrow (2026-10-22 00:00)? hooks.log since install: $summary. Sample: $sample Full log: $H/hooks.log" \
      > "$H/ask-id"
    echo "asked $(cat "$H/ask-id")" ;;
  switch)
    id=$(cat "$H/ask-id" 2>/dev/null || true)
    choice=$(jq -r --arg id "$id" 'select(.id==$id) | .choice' "$HOME/.claude/orchestra/inbox/answers.jsonl" 2>/dev/null | tail -1)
    if [ -n "$id" ] && [ "${choice,,}" = yes ]; then
      echo enforce > "$H/mode"
      echo "$(date -Is) switch: $id answered yes -> mode=enforce" >> "$H/hooks.log"
    else
      echo "$(date -Is) switch: ${id:-no ask} answered '${choice:-none}' -> mode stays $(cat "$H/mode")" >> "$H/hooks.log"
    fi ;;
  *) sed -n '3,4p' "$0" | sed 's/^# //' >&2; exit 2 ;;
esac
