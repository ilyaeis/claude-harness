---
name: resurect
description: Hand the current work over to a fresh Claude chat and close this one. Writes a session report, starts a second Claude session in tmux with a prompt that points to the report and this transcript, then closes this session. Trigger on "/resurect", "resurrect", "resurrect yourself", "restart in a new chat", "hand over to a new session", or when context is too long to go on.
---

# Resurect

Move the work to a new chat. The new chat continues the work. This chat closes.

## Steps

1. Write the handoff report. Follow the `session-report` skill (Steps 2-3 and the INDEX update). Do not do its Step 4.
   The report must have a "Running when this report was written" section and a numbered "Next steps" section.
   Put the full path of this transcript in the report:
   `~/.claude/projects/<cwd-slug>/<session-id>.jsonl`.
   Find this session id: `grep -l "<a unique word from this chat>" ~/.claude/projects/*/*.jsonl`, or read
   `~/.claude/sessions/<pid>.json` (field `sessionId`).
2. Pick the work folder (`DIR`) of the new chat: the project the work is about, not always this cwd.
   Pick a short tmux session name (`NAME`), for example `<topic>-v2`. Check that `tmux ls` does not have it.
3. Make sure Claude trusts `DIR`, so the new chat does not stop at the trust dialog:
   ```bash
   cp ~/.claude.json ~/.claude.json.bak-trust && python3 - "$DIR" <<'PY'
   import json, os, sys
   p = os.path.expanduser("~/.claude.json"); d = json.load(open(p))
   d.setdefault("projects", {}).setdefault(sys.argv[1], {})["hasTrustDialogAccepted"] = True
   json.dump(d, open(p, "w"), indent=2)
   PY
   ```
4. Write the first prompt (`P`) of the new chat. Use this form:
   > Continue the <topic> work from the previous session. First read the handoff report <report path> fully.
   > You may read the full previous conversation transcript for any detail: <transcript path> (it is very long;
   > grep it or read parts, do not load it all). Then do Next steps <N> from the report: <one-sentence summary of
   > each step, with the user's own words for any open request>.

   Use only double quotes inside `P` with care: escape them, or use single quotes in the text.
5. Start the new chat:
   ```bash
   cd "$DIR" && tmux new-session -d -s "$NAME" -x 220 -y 50 -c "$DIR" \
     "claude --permission-mode bypassPermissions -n $NAME \"$P\""
   sleep 12; tmux capture-pane -t "$NAME" -p | grep -v '^\s*$' | tail -15
   ```
   Check in the pane output that the new chat has started to read the report. If it waits at a dialog, fix that first.
6. Tell the user in 3-6 short lines: the report path, the tmux session name (`tmux attach -t $NAME`), the next steps
   the new chat does, and that this chat closes in 60 s.
7. Close this chat. Find its pid: `grep -l "<session-id>" ~/.claude/sessions/*.json`, then field `pid`.
   Check it with `ps -o pid=,args= -p <pid>`. Then:
   ```bash
   setsid nohup sh -c 'sleep 60; kill <pid>' >/dev/null 2>&1 < /dev/null &
   ```
   Send step 6 before this kill fires.

## Rules

- Background jobs started with `setsid`/`nohup` survive. Subagents of this chat die with it. List them in the report
  as "restart if needed".
- Never use a `pkill` pattern that can match your own shell command. Kill by pid only.
- Do not close this chat when step 5 failed. Tell the user instead.
