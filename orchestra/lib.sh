# Shared settings for the orchestra scripts. Source this file.
ORCH_HOME="$HOME/.claude/orchestra"
ORCH_TMUX="${ORCHESTRA_SESSION:-orchestra}"          # tmux session name (masters)
ORCH_WTMUX="$ORCH_TMUX-w"                            # hidden tmux session for workers
ORCH_MASTER="${ORCHESTRA_MASTER:-master}"            # Claude session name of the master
ORCH_PROJECT="${ORCHESTRA_PROJECT:-$PWD}"            # project root (master cwd)
ORCH_DIR="$ORCH_PROJECT/.orchestra"
ORCH_REG="$ORCH_DIR/registry.json"
ORCH_MAX_WORKERS="${ORCHESTRA_MAX_WORKERS:-4}"       # live workers allowed at once
# Claude account dir. claude-b sets CLAUDE_CONFIG_DIR=~/.claude-b; workers must use the same account.
ORCH_CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"

# Print the env lines every launcher needs: the Claude account and a pytest worker cap.
# pytest -n auto reads PYTEST_XDIST_AUTO_NUM_WORKERS. Without it, each worker starts 24 test processes.
launcher_env() {
  [ -n "${CLAUDE_CONFIG_DIR:-}" ] && printf 'export CLAUDE_CONFIG_DIR=%q\n' "$CLAUDE_CONFIG_DIR"
  echo 'export PYTEST_XDIST_AUTO_NUM_WORKERS=4'
}

reg_init() {
  mkdir -p "$ORCH_DIR"
  [ -f "$ORCH_REG" ] || echo '[]' > "$ORCH_REG"
}

# Apply a jq filter to the registry in place. Args: jq args...
# It holds flock on "$ORCH_REG.lock" so two callers do not lose a row. It fails after 10 s.
reg_update() {
  local tmp rc=0
  exec {REG_LOCK_FD}>>"$ORCH_REG.lock"
  if ! flock -w 10 "$REG_LOCK_FD"; then
    echo "reg_update: cannot lock $ORCH_REG.lock within 10 s" >&2
    exec {REG_LOCK_FD}>&-
    return 1
  fi
  tmp=$(mktemp "$ORCH_DIR/.reg.XXXXXX")
  { jq "$@" "$ORCH_REG" > "$tmp" && mv "$tmp" "$ORCH_REG"; } || { rc=$?; rm -f "$tmp"; }
  exec {REG_LOCK_FD}>&-
  return $rc
}

# Print "sessionId" of the live Claude session with this name, if any.
session_id_for() {
  local f pid
  for f in "$ORCH_CLAUDE_DIR"/sessions/*.json; do
    [ -f "$f" ] || continue
    [ "$(jq -r '.name // empty' "$f" 2>/dev/null)" = "$1" ] || continue
    pid=$(jq -r '.pid' "$f")
    kill -0 "$pid" 2>/dev/null && jq -r '.sessionId' "$f" && return 0
  done
  return 1
}

# Print names of all live Claude sessions, one per line.
live_session_names() {
  local f pid
  for f in "$ORCH_CLAUDE_DIR"/sessions/*.json; do
    [ -f "$f" ] || continue
    pid=$(jq -r '.pid' "$f" 2>/dev/null)
    kill -0 "$pid" 2>/dev/null && jq -r '.name // empty' "$f"
  done
}

# Print the tmux target "=<session>:<window>" of the worker window with this name.
# Looks in the hidden session first, then in the master session (older workers live there).
worker_window() {
  local t
  for t in "$ORCH_WTMUX" "$ORCH_TMUX"; do
    tmux list-windows -t "=$t" -F '#W' 2>/dev/null | grep -qxF "$1" && { echo "=$t:$1"; return 0; }
  done
  return 1
}

# Print the names of all windows in the master and worker sessions, one per line.
worker_windows() {
  tmux list-windows -t "=$ORCH_WTMUX" -F '#W' 2>/dev/null || true
  tmux list-windows -t "=$ORCH_TMUX" -F '#W' 2>/dev/null || true
}
