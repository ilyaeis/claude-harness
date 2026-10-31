#!/usr/bin/env bash
# Start a long job as a durable chain (setsid) on a server or on this machine.
# Usage: chain-run.sh [--phase <name>] <worker> <host|local> <command>
#   <worker>   the worker that owns the chain (its registry row gets the chain).
#   <host>     ssh host (e.g. gpu1 or user@host), or "local".
#   <command>  one string, run with bash -c in the home dir on the host. cd in it if needed.
# On the host, the chain dir ~/.orchestra-chains/<id>/ holds:
#   log        job output; the last line is "EXIT <code>"
#   EXIT       "EXIT <code>" when the job ends
#   phase      the phase name; the job changes it with: echo <phase> > "$CHAIN_PHASE"
#   heartbeat  "<time> <phase> <log bytes>", rewritten every 60 s
#   pid        the chain pid
# The registry row of <worker> gets a chains[] entry. retire-worker.sh checks it.
# Run from the project root (or set ORCHESTRA_PROJECT).
set -euo pipefail
. "$HOME/.claude/orchestra/lib.sh"

phase=run
[ "${1:-}" = --phase ] && { phase="${2:?--phase needs a name}"; shift 2; }
[ $# -eq 3 ] || { echo "usage: chain-run.sh [--phase <name>] <worker> <host|local> <command>" >&2; exit 2; }
worker="$1"; host="$2"; cmd="$3"
[ -f "$ORCH_REG" ] || { echo "no registry at $ORCH_REG. Run from the project root or set ORCHESTRA_PROJECT." >&2; exit 1; }
jq -e --arg n "$worker" 'any(.[]; .name==$n)' "$ORCH_REG" >/dev/null || { echo "no worker '$worker' in $ORCH_REG" >&2; exit 1; }

id="$worker-$(date +%Y%m%d-%H%M%S)-$(od -An -N2 -tx1 /dev/urandom | tr -d ' \n')"
rel=".orchestra-chains/$id"
runner=$(cat <<EOF
#!/usr/bin/env bash
D="\$HOME/$rel"
cd "\$HOME"
echo \$\$ > "\$D/pid"
printf '%s\n' $(printf %q "$phase") > "\$D/phase"
: > "\$D/log"
( while :; do echo "\$(date -Is) \$(cat "\$D/phase") \$(stat -c %s "\$D/log")" > "\$D/heartbeat"; sleep 60; done ) &
hb=\$!
CHAIN_DIR="\$D" CHAIN_PHASE="\$D/phase" bash -c $(printf %q "$cmd") >> "\$D/log" 2>&1 < /dev/null
code=\$?
kill \$hb 2>/dev/null
echo "EXIT \$code" >> "\$D/log"
echo "\$(date -Is) end \$code" > "\$D/heartbeat"
echo "EXIT \$code" > "\$D/EXIT"
EOF
)
start='D="$HOME/'"$rel"'"; mkdir -p "$D" && cat > "$D/run.sh" && chmod +x "$D/run.sh" && { setsid nohup "$D/run.sh" > /dev/null 2>&1 < /dev/null & echo $!; }'

if [ "$host" = local ]; then
  pid=$(bash -c "$start" <<<"$runner")
  check="cat ~/$rel/heartbeat ~/$rel/EXIT 2>/dev/null; tail -3 ~/$rel/log"
else
  pid=$(ssh -o GSSAPIAuthentication=no -o BatchMode=yes -o ConnectTimeout=10 "$host" "$start" <<<"$runner")
  check="ssh $host 'cat ~/$rel/heartbeat ~/$rel/EXIT 2>/dev/null; tail -3 ~/$rel/log'"
fi

reg_update --arg n "$worker" --arg id "$id" --arg h "$host" --arg d "~/$rel" --arg p "$pid" --arg c "$cmd" --arg now "$(date -Is)" \
  'map(if .name==$n then .chains = ((.chains // []) + [{id:$id, host:$h, dir:$d, pid:$p, cmd:$c, started_at:$now, exit:null}]) else . end)'
echo "chain $id started on $host (pid $pid)"
echo "dir:   ~/$rel  (log, EXIT, phase, heartbeat)"
echo "check: $check"
