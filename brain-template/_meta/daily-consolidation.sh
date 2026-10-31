#!/usr/bin/env bash
# Run the vault's daily consolidation agent unattended (Claude Code print mode).
# Run by the brain-daily-consolidation systemd user timer (daily 06:00, catch-up if the PC was off).
# Prompt: the single fenced block in $BRAIN_DIR/_meta/routines/daily-consolidation.md.
set -euo pipefail

BRAIN_DIR="${BRAIN_DIR:-$HOME/brain}"
export BRAIN_DIR
PROMPT_FILE="$BRAIN_DIR/_meta/routines/daily-consolidation.md"
LOG_DIR="$BRAIN_DIR/log/daily-consolidation"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/$(date '+%Y-%m-%d').log"

PROMPT=$(awk '/^```$/{c++; if(c==1){f=1; next} if(c==2){f=0}} f' "$PROMPT_FILE")
if [ -z "$PROMPT" ]; then
    echo "$(date -Iseconds): ERROR - could not extract prompt from $PROMPT_FILE" >> "$LOG_FILE"
    exit 1
fi

cd "$BRAIN_DIR"
{
    echo "=== daily-consolidation run: $(date -Iseconds) ==="
    claude --print --permission-mode bypassPermissions "$PROMPT"
    echo "=== done: $(date -Iseconds) ==="
} >> "$LOG_FILE" 2>&1
