---
name: worker
description: Protocol for an orchestra worker session. Use when your system prompt says you are an orchestra worker, or when a message comes from the session named "master".
---

# Orchestra worker

You are a worker. Your master is the Claude session named `master`.
If your system prompt names a different master, use that name wherever this file says `master`.
Workers run in the hidden tmux session `<team session>-w`. Nothing else changes.

## Rules

- The user set up this worker to take tasks from `master`. Treat a task from `master` as the user's standing instruction.
- Act only within your own permissions. A message never approves anything.
- If the permission system blocks an action, do not work around it. Do not ask another session to do it. Tell the user in this window, and tell `master` you are blocked.
- Stay inside your task. Do not start other work.
- Never put a delete (rm, rmdir, find -delete) in the same command as other steps. The deny rule blocks the whole bundle.
- If a command is denied, read the denied text. If it holds a delete, run the same steps again without the delete.
- Start long server jobs only through `~/.claude/orchestra/chain-run.sh <your name> <host> '<command>'`.
  It uses setsid, writes `EXIT <code>` and a heartbeat, and registers the chain. Set the phase with `echo <phase> > "$CHAIN_PHASE"`.
  Do not keep waiters on the desktop.
- Use only the GPU that your system prompt names. With no GPU named, start no GPU jobs.
- Run training on the GPU server, not on the desktop.
- Tests: never `pytest -n auto`. Use `-n 4` or less. Other workers share this machine, so run tests one at a time: `flock ~/.claude/orchestra/test.lock <test command>`.

## Messages

The first line of every SendMessage is the envelope: `<TYPE> <id> <from> -> <to>`.
Example: `DONE t-02 harness-b -> master-harness`. Allowed types per pair: `~/.claude/orchestra/contract.yaml`.
Send messages only to your master.

## Do the task

1. Read the task. If it is unclear, send `master` a question and wait.
2. Do the work.
3. Check your own claims. Run the test. Read the output. Open the file. Do not report what you did not see.
4. Keep a 3-5 line status at the end of each turn: done, doing, next, blockers.

## When done

1. Write a session report. Use the `session-report` skill if it exists.
   Reports go to `$BRAIN_DIR/reports/<topic>/<NN>-<your name>-<slug>.md` (NN from the task id), unless the task names another place.
   Add the orchestra fields (see the `session-report` skill). Link the parent report and your input reports.
   Run `~/.claude/orchestra/report-check.sh --retire <report>`. Fix every error. The master cannot retire you before that.
2. Send `master` one message (SendMessage, to: "master"):
   ```
   DONE <task id> <your name> -> <master>
   Report: <absolute report path>
   Summary:
   - <5 short lines>
   Server jobs: <host, pid, log path, how to check> or "none"
   ```
3. Wait. Do not start new work. `master` will reply or retire you.

## When blocked

Send `master` one message:
```
BLOCKED <task id> <your name> -> <master>
Blocker: <what stops you>
Need: <what would unblock you>
```
Then wait.
