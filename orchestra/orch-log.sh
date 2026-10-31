#!/usr/bin/env bash
# Append one line with the real time to <project>/.orchestra/log.md.
# Usage: orch-log.sh <name> <event> <note...>
# Line: <YYYY-MM-DD HH:MM> <name> <event> <note>
# Project: ORCHESTRA_PROJECT or the current dir. Refuses if <project>/.orchestra does not exist.
set -euo pipefail
. "$HOME/.claude/orchestra/lib.sh"

[ $# -ge 3 ] || { echo "usage: orch-log.sh <name> <event> <note...>" >&2; exit 2; }
name="$1"; event="$2"; shift 2
[ -d "$ORCH_DIR" ] || { echo "no $ORCH_DIR. Run from the project root or set ORCHESTRA_PROJECT." >&2; exit 1; }
line="$(date '+%Y-%m-%d %H:%M') $name $event $*"
echo "$line" >> "$ORCH_DIR/log.md"
echo "$line"
