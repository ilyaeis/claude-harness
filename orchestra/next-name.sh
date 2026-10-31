#!/usr/bin/env bash
# Print the next free worker name: agent-a .. agent-z, agent-aa, agent-ab ...
# A name is taken if it is in the registry or used by a live Claude session.
# Test mode: next-name.sh --after <suffix>  prints the suffix after <suffix>.
set -euo pipefail
. "$HOME/.claude/orchestra/lib.sh"

# Base-26 increment with digits a..z and no zero (a, .., z, aa, ab, ..).
next_suffix() {
  local s="$1" tail="" i c
  for (( i=${#s}-1; i>=0; i-- )); do
    c=${s:i:1}
    if [ "$c" != z ]; then
      c=$(printf "\\$(printf '%03o' $(( $(printf '%d' "'$c") + 1 )))")
      echo "${s:0:i}${c}${tail}"; return
    fi
    tail="a$tail"                 # z rolls over to a, carry left
  done
  echo "a$tail"                   # all digits were z (or empty): grow by one
}

if [ "${1:-}" = "--after" ]; then next_suffix "${2:-}"; exit; fi

taken=$( { [ -f "$ORCH_REG" ] && jq -r '.[].name' "$ORCH_REG"; live_session_names || true; } 2>/dev/null | sort -u)
s=""
while :; do
  s=$(next_suffix "$s")
  pre="${ORCHESTRA_WORKER_PREFIX:-agent-}"
  grep -qxF "$pre$s" <<<"$taken" || { echo "$pre$s"; exit 0; }
done
