#!/usr/bin/env bash
# Ask the user a question on the local inbox page, from any Claude session.
# Usage: inbox-ask.sh "<question>" [--yesno | --options "A|B|C" | --open] [--kind decision|verdict|user-action|delete|other] [--media <path>...] [--from <session name>]
#        inbox-ask.sh --done <id> [--from <session name>]        (withdraw the ask)
# Answer type: --yesno (default: Yes / No / Skip), --options "A|B|C" (2-6 short options, plus Skip), --open (text field, plus Skip).
# Explain, a note and Attach are always there.
# Prints the item id. The answer arrives as one typed line in this session's pane:
#   INBOX <id> user -> <session>: Q: ... | A: <yes|no|skip> <note>
# The session name comes from $CLAUDE_PID or the parent pids (~/.claude*/sessions/*.json). If none is found, pass --from.
set -euo pipefail
usage() { sed -n '3,6p' "$0" | sed 's/^# //' >&2; exit 2; }
q="" kind="" from="" done="" atype="yesno" options="" media=()
while [ $# -gt 0 ]; do
  case "$1" in
    --yesno) atype=yesno; shift ;;
    --open) atype=open; shift ;;
    --options) atype=options; options="${2-}"; shift $(( $# > 1 ? 2 : 1 )) ;;
    --kind) kind="${2:?}"; shift 2 ;;
    --from) from="${2:?}"; shift 2 ;;
    --done) done="${2:?}"; shift 2 ;;
    --media) shift; while [ $# -gt 0 ] && [ "${1#--}" = "$1" ]; do media+=("$(realpath -m "$1")"); shift; done ;;
    -h|--help) usage ;;
    *) [ -z "$q" ] || usage; q="$1"; shift ;;
  esac
done
[ -n "$q" ] || [ -n "$done" ] || usage

if [ -z "$from" ]; then   # this session's name: its pid is $CLAUDE_PID or an ancestor of this shell
  pid=${CLAUDE_PID:-$PPID}
  while [ -z "$from" ] && [ "${pid:-1}" -gt 1 ]; do
    from=$(jq -r --argjson p "$pid" 'select(.pid == $p) | .name // empty' ~/.claude*/sessions/*.json 2>/dev/null | head -1)
    pid=$(awk '{print $4}' "/proc/$pid/stat" 2>/dev/null || echo 1)
  done
  [ -n "$from" ] || { echo "inbox-ask: cannot find this session's name; pass --from <session name>" >&2; exit 2; }
fi

if [ -z "$done" ] && [ "$atype" = options ]; then
  IFS='|' read -ra opts <<<"$options"; n=${#opts[@]}
  [ "$n" -ge 2 ] && [ "$n" -le 6 ] || { echo "inbox-ask: --options needs 2 to 6 options split by |, got $n" >&2; exit 2; }
  for o in "${opts[@]}"; do
    o=${o//[[:space:]]/}
    [ -n "$o" ] || { echo "inbox-ask: --options has an empty option" >&2; exit 2; }
  done
fi

tok=$(cat ~/.claude/orchestra/inbox/token)
body=$(python3 -c 'import json,sys; a=sys.argv; print(json.dumps({"from":a[1],"text":a[2],"kind":a[3],"done":a[4],"atype":a[5],"options":a[6],"media":a[7:]}))' \
  "$from" "$q" "$kind" "$done" "$atype" "$options" "${media[@]}")
out=$(curl -sS -X POST -H "Cookie: orch_inbox=$tok" -H 'Content-Type: application/json' --data-binary "$body" \
  -w '\n%{http_code}' http://127.0.0.1:8377/ask)
code=${out##*$'\n'}; out=${out%$'\n'*}
[ "$code" = 200 ] || { echo "inbox-ask: HTTP $code: $out" >&2; exit 1; }
id=$(jq -r .id <<<"$out")
if [ -n "$done" ]; then echo "withdrawn $id"; else echo "$id"; fi
