#!/usr/bin/env bash
# Print the name of the Claude session that runs this script.
# It walks up the process tree to the first pid with a session file.
set -euo pipefail
pid=$$
while [ "$pid" -gt 1 ]; do
  f="$HOME/.claude/sessions/$pid.json"
  if [ -f "$f" ]; then jq -r '.name // empty' "$f"; exit 0; fi
  pid=$(ps -o ppid= -p "$pid" | tr -d ' ')
done
echo "no Claude session found" >&2; exit 1
