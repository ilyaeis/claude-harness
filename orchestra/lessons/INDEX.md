# Lessons library

One file per lesson. Add with `lesson-add.sh` (evidence gate). Find with `lesson-find.sh <words>` (accepted only).
This repo holds the generic lessons only. Three lesson files (L-003, L-004, L-014) are here as examples; the others stay in my private setup, and their evidence points to internal reports.
IDs have gaps: I removed the lessons about one work project.

Status: candidate = 1 evidence; accepted = metric contrast or 2+ evidence files; retired = wrong or obsolete (edit by hand).

| id | status | evidence | tags | lesson |
|---|---|---|---|---|
| L-001 | accepted | 7 | chain, watcher, worker, gpu | Make every chain write an EXIT marker and a heartbeat. Let the watcher wake the worker. Send DONE or BLOCKED within 10 min of chain exit. Masters check chain EXIT at each check-in. |
| L-002 | accepted | 5 | worker, waits, context, template | Wait with Monitor or a background command. Never run a foreground ssh 'until ... sleep' loop. Put a ready wait snippet in the task template. |
| [L-003](L-003-split-deletes-out-of-bundles.md) | accepted | 6 | permissions, deletes, worker, ssh | Do not bundle a delete with setup steps; the deny rule blocks the whole bundle. On a denial, read the denied text. If it holds a delete, rerun the same steps without the delete. Queue the delete for the user. |
| [L-004](L-004-never-route-around-permission-block.md) | accepted | 6 | permissions, po, worker, approval | Do not split, rephrase or relay to get around a real block. A relayed 'user approved' does not count. The user acts in that window (or runs the command with '!'), or the worker is killed and respawned. |
| L-005 | accepted | 8 | deletes, po, permissions, disk | Keep a delete list with exact paths and sizes. The user approves it in one batch; then the PO deletes. Check what still uses a dir before you delete it. Leave ambiguous targets alone. |
| L-006 | accepted | 8 | context, master, restart, po | Keep the master state file current at every event. At 400-500k write the state file and restart the master window only; workers keep running. Do it at a quiet point. |
| L-007 | accepted | 6 | context, worker, handoff | Ask for 'context left' in DONE lines. Roll the worker to a fresh one via a handoff file before it hits the limit. Detect 'Prompt is too long' at check-in. |
| L-008 | accepted | 6 | quota, watcher, po, master | Detect the limit text by code, not at the next check-in. Resume at the reset time by waking workers and checking their chains. Cap heavy parallel agents such as review agents. |
| L-011 | accepted | 9 | verification, reports, po, master | Re-read the number in the output files before you relay it. Let a different agent check headline claims. Label each claim measured, inferred or retracted, and state seeds. |
| L-012 | accepted | 4 | verification, reports, po | Do not relay a one-seed gain as a headline. Require 2+ seeds and a noise bar measured with different seeds. Say 'one seed' when that is all you have. |
| L-013 | accepted | 6 | permissions, messaging, setup, po | Run all sessions of a team and its PO in one permission mode and one Claude config dir (account). Otherwise messages are held, expire, or never arrive. |
| [L-014](L-014-no-agent-send-keys-verify-submit.md) | accepted | 5 | tmux, watcher, inbox, messaging | Agents never use send-keys. Only code sends one fixed wake line. Leave view-mode first, put a timeout on every tmux call, and check that the line left the input box. |
| L-015 | accepted | 3 | tmux, master, po | A dim '❯ ...' line is a Claude Code prompt suggestion, not a user order or typed text. Use capture-pane -e to tell them apart. Never press Enter on it. |
| L-016 | accepted | 3 | inbox, master, watcher | Do not rely on the wake line alone. At each check-in, read the inbox answers file for your team. Act once on an answer even if it arrives twice. |
| L-017 | accepted | 6 | resources, desktop, worker | Run heavy work on GPU servers, never on the desktop. No 'pytest -n auto'; cap threads and wrap tests in the shared flock. Long desktop jobs run under systemd-run with MemoryMax. |
| L-018 | accepted | 6 | disk, resources, po | Check df before launch and keep a free-space floor per host. Set disk caps per team. Delete a run's own tmp when it ends. |
| L-020 | accepted | 4 | git, mr, master, worker | Check MR/PR state (merged, closed, draft) before any push. Show the diff stat first. Do exactly the remote writes the task file names: one branch, one draft MR/PR. |
| L-022 | accepted | 5 | scope, template, hooks, po | Write each hard limit into every agent prompt template, not only the skill. Report each scope slip to the user. Prefer a hook that checks hosts over skill text. |
| L-028 | accepted | 4 | chain, server, worker | Start it detached and durable (setsid nohup, systemd-run). It must survive ssh logout and session restarts. A worker must not restart a job someone killed without asking. |
| L-029 | accepted | 4 | po, messaging, teams | Route the finding to the other master at once and name it in its task. Check for duplicate plans across teams before spawning. |
| L-030 | accepted | 5 | po, away-mode, user, planning | Ask one short question, or test the literal reading first. Never replace the user's named recipe with your own. Visual verdicts, product direction and merges stay with the user, also in away mode. |
| L-031 | accepted | 3 | testing, harness, permissions | Run test agents in a dir outside every real project, and point the brain to a scratch copy. A bypass-mode test agent will find and edit real ledgers. |
| L-033 | accepted | 3 | h2, worker, permissions, deletes, haiku | Haiku bundles 'mkdir && cp && rm -rf' (3/3 runs), the deny rule blocks the bundle, and it then sends BLOCKED asking for mkdir/cp permission. Sonnet 3/3 used rm -r and passed. Put the delete rule (L-003) in the task file of short haiku workers that delete. |
| L-036 | accepted | 2 | harness2, probes | A checker hint helps only after the first check. It cannot lift a first-write metric. Fix the input side: skill text or task template. Also check that the agent runs the checker at all. |
