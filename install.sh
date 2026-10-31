#!/usr/bin/env bash
# Install the harness into ~/.claude. Opt-in: without --link or --copy it only prints what it would do.
# Usage: install.sh [--dry-run] [--link | --copy] [--systemd] [--brain]
#   --dry-run   print the plan, change nothing (the default)
#   --link      symlink the repo files (a git pull updates the setup; new lessons and h2 runs land in the repo)
#   --copy      copy the repo files
#   --systemd   also install the systemd user units (watcher, inbox, weekly ask, brain routines)
#   --brain     also copy brain-template/ to $BRAIN_DIR (default ~/brain), only if that dir does not exist
# It never overwrites an existing file. It never touches ~/.claude/settings.json: merge the hooks
# from claude/settings.json by hand.
set -euo pipefail
REPO="$(cd "$(dirname "$0")" && pwd)"
CLAUDE_DIR="$HOME/.claude"   # the scripts use this path. A second config dir links CLAUDE.md, skills/ and hooks/ to it.
BRAIN_DIR="${BRAIN_DIR:-$HOME/brain}"
UNIT_DIR="$HOME/.config/systemd/user"
method=dry; dry=1; systemd=0; brain=0
for a in "$@"; do
  case "$a" in
    --dry-run) dry=1 ;;
    --link) method=link ;;
    --copy) method=copy ;;
    --systemd) systemd=1 ;;
    --brain) brain=1 ;;
    -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $a" >&2; exit 2 ;;
  esac
done
# --link/--copy act, unless --dry-run was also given.
[ "$method" != dry ] && [[ " $* " != *" --dry-run "* ]] && dry=0
[ "$method" = dry ] && method=link

run() { if [ "$dry" = 1 ]; then echo "  would: $*"; else "$@"; fi; }

place() {   # place <repo file or dir> <target>
  local src="$REPO/$1" dst="$2"
  if [ -e "$dst" ] || [ -L "$dst" ]; then echo "  skip (exists): $dst"; return; fi
  [ -d "$(dirname "$dst")" ] || run mkdir -p "$(dirname "$dst")"
  if [ "$method" = link ]; then run ln -s "$src" "$dst"; else run cp -r "$src" "$dst"; fi
}

echo "Harness install: method=$method dry_run=$dry claude_dir=$CLAUDE_DIR"
for cmd in claude tmux jq python3 flock; do command -v "$cmd" >/dev/null || echo "  warning: '$cmd' not found"; done
python3 -c 'import yaml' 2>/dev/null || echo "  warning: python3 module 'yaml' not found (pip install pyyaml)"

echo "Global CLAUDE.md and skills:"
place claude/CLAUDE.md "$CLAUDE_DIR/CLAUDE.md"
for s in "$REPO"/skills/*/; do s=$(basename "$s"); place "skills/$s" "$CLAUDE_DIR/skills/$s"; done

echo "Orchestra:"
for f in "$REPO"/orchestra/*; do f=$(basename "$f"); place "orchestra/$f" "$CLAUDE_DIR/orchestra/$f"; done
place claude/worker-settings.json "$CLAUDE_DIR/orchestra/worker-settings.json"

if [ "$systemd" = 1 ]; then
  echo "systemd user units:"
  for u in "$REPO"/systemd/*; do u=$(basename "$u"); place "systemd/$u" "$UNIT_DIR/$u"; done
  run systemctl --user daemon-reload
  echo "  enable what you want, e.g.: systemctl --user enable --now orch-watcher.timer orch-inbox.service"
fi

if [ "$brain" = 1 ]; then
  echo "Brain vault:"
  if [ -e "$BRAIN_DIR" ]; then echo "  skip (exists): $BRAIN_DIR"; else run cp -r "$REPO/brain-template" "$BRAIN_DIR"; fi
fi

cat <<MSG
Manual steps (the script never does these):
  1. Merge the "hooks" block of $REPO/claude/settings.json into $CLAUDE_DIR/settings.json.
  2. Optional: add $REPO/tmux/tmux.conf.snippet to ~/.tmux.conf.
  3. Optional: copy $REPO/project-template/ into a project for the quality gate and commit hook.
  4. Test: cd $CLAUDE_DIR/orchestra/hooks && python3 -m unittest test_orch_hooks
MSG
