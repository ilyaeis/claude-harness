#!/usr/bin/env bash
# Print the worker registry as a table.
# Usage: status.sh                               print the table
#        status.sh set <name> <status> [report]  set status (and report path)
set -euo pipefail
. "$HOME/.claude/orchestra/lib.sh"
[ -f "$ORCH_REG" ] || { echo "no registry at $ORCH_REG"; exit 0; }

if [ "${1:-}" = set ]; then
  name="${2:?name}"; st="${3:?status}"; rep="${4:-}"
  case "$st" in spawned|running|done|retired) ;; *) echo "bad status: $st" >&2; exit 2;; esac
  reg_update --arg n "$name" --arg s "$st" --arg r "$rep" \
    'map(if .name==$n then .status=$s | (if $r=="" then . else .report_path=$r end) else . end)'
fi

windows=$(worker_windows)
{
  echo -e "NAME\tSTATUS\tWINDOW\tSPAWNED\tRETIRED\tREPORT\tTASK"
  jq -r '.[] | [.name, .status, .spawned_at[0:16], (.retired_at // "-")[0:16], (.report_path // "-"), (.task|.[0:50])] | @tsv' "$ORCH_REG" |
  while IFS=$'\t' read -r n s sp rt rp t; do
    w=no; grep -qxF "$n" <<<"$windows" && w=yes
    echo -e "$n\t$s\t$w\t$sp\t$rt\t$rp\t$t"
  done
} | column -t -s $'\t'
