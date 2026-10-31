#!/usr/bin/env bash
# Master reply to a user's EXPLAIN request on the local inbox.
# Usage: inbox-reply.sh <item-id> "<short explanation>"   (plain text, max 2000 chars)
set -euo pipefail
[ $# -eq 2 ] && [ -n "$2" ] || { echo 'usage: inbox-reply.sh <item-id> "<text>"' >&2; exit 2; }
tok=$(cat ~/.claude/orchestra/inbox/token)
python3 -c 'import json,sys;print(json.dumps({"id":sys.argv[1],"text":sys.argv[2]}))' "$1" "$2" |
  curl -sS -f -X POST -H "Cookie: orch_inbox=$tok" -H 'Content-Type: application/json' --data-binary @- http://127.0.0.1:8377/reply >/dev/null && echo "reply saved for $1"
