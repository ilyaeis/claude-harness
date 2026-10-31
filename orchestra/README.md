# Orchestra: one master, many workers

One Claude session is the master. It plans and checks. Worker sessions do the work.
Each session runs in its own tmux window in the tmux session `orchestra`.

Hybrid workers (2026-10-19): the user sees only masters.
- Short tasks (< ~1 h, no long server job) run as background subagents of the master (Agent tool). No window.
- Long tasks (GPU chains, anything that must outlive a master restart) are worker sessions. `spawn-worker.sh` puts them in the hidden tmux session `<ORCHESTRA_SESSION>-w` (created on first use). `retire-worker.sh`, `status.sh` and `find_pane.py` look there and in the master session. Watch one with `tmux attach -t orch-<team>-w`.
- Team names must not end in `-w`.

## Start

```bash
~/.claude/orchestra/start.sh ~/dev/<project>
```

Then, in the `master` window, type:

```
/orchestrate <your goal>
```

The master makes a plan. It shows you the plan. You approve it.
Then it spawns workers and starts a 20-minute check-in loop.

## Watch

- `Ctrl-b w` lists windows. `Ctrl-b n` / `Ctrl-b p` go to the next / previous window.
- Each worker window has the worker name: `agent-a`, `agent-b`, ...
- You can type in any worker window. The worker treats your text as normal user input.
- `~/.claude/orchestra/status.sh` (run in the project dir) prints the worker table.
- Files: `<project>/.orchestra/` holds `registry.json`, `log.md`, `status.md`, `tasks/`, `master-handoff.md`.

## Approve and retire

When a worker finishes, it writes a report and messages the master.
The master checks the work. It shows you a 5-line summary and the report path.
If the check passes, the master runs `retire-worker.sh <name>` without asking you (see the `orchestrate` skill). The window closes.
`retire-worker.sh` refuses a report that fails `report-check.sh --retire`, and a worker whose `chain-run.sh` chain has no EXIT marker. `--force` turns these into warnings.
The transcript stays in `~/.claude/projects/`. Resume it with `claude -r <session_id>` (the id is in the registry).

## What needs you

- Plan approval. (Retires do not need you. The master retires a worker after it checks the report.)
- Permission prompts in a worker window. A message from the master never approves anything.
- Blockers that the master sends to you.
- A trust dialog in a new, untrusted folder. Answer it once in the worker window.
- If a tmux hook leaves a pane in view mode (yellow text at the top), press `q`.

## What the deny list blocks (workers only)

File: `~/.claude/orchestra/worker-settings.json`. Workers run in `auto` mode plus these deny rules:

- `git push` to `main` or `master` (named in the command), and force pushes (`--force`, `-f`, `+ref`).
- `gh pr merge`.
- `git reset --hard`.
- `rm -rf`, `rm -fr`, `rm -Rf`, `rm -r -f`, `rm -f -r`.
- Over ssh: `rm`, `rmdir`, `unlink`, `find ... -delete`. Also `rsync --delete`.

## Limits

- Deny rules match the command text only. `bash -c '...'`, `/bin/rm`, `git -C . push`, or a plain `git push` while on `main` get past them.
- Your user settings allow `Bash(*)`. So in workers, Bash commands skip the auto-mode classifier. The deny list is the main guard for Bash.
- One orchestra at a time: the tmux session is always `orchestra`, and the master name is always `master`.
- `start.sh` refuses if another live session is already named `master`.
- The master can only check claims with reads and read-only commands. It does not re-run your jobs.
- The master checks in every 20 minutes. It does not see a worker crash sooner, unless it asked for an idle notice.

## Watcher (v1, alert only)

`watcher.py` runs every 2 min from the systemd user timer `orch-watcher.timer`.
It reads the registries of all teams: tmux sessions `orch-*` with a `master-*` window, plus the `registries` globs in `watcher.conf`.
For each `chain-run.sh` chain with `exit: null`, it reads the chain dir (local, or one `ssh -o BatchMode=yes` call per host).

- `CHAIN_END`: the chain has an `EXIT` file.
- `CHAIN_STALE`: no `EXIT`, and the log did not change for `stale_min` minutes (20).
- Each event fires once per chain (`watcher-state.json`). A stale chain that ends later also gets `CHAIN_END`.

Each event is one JSON line in `events.jsonl`:

```json
{"ts": "2026-10-18T22:43:47+0300", "kind": "CHAIN_END", "team": "orch-depth", "session": "orch-depth",
 "master": "master-depth", "worker": "depth-b", "chain": "depth-b-20261018-221534",
 "host": "<user>@<host>", "fact": "EXIT 0 phase=build log=537B", "wake": "dry_run"}
```

`team` is the tmux session, or the project dir name if the team has no tmux session (then `session` and `master` are null).
`wake` is `queued` (team in the allowlist) or `dry_run`. The send result goes to `watcher.log`.
A `CHAIN_STALE` fact also has `heartbeat_age`: a fresh heartbeat means the job is alive but quiet.

Wake: for teams in `wake_sessions` (in `watcher.conf`), the watcher types one line into the master pane and presses Enter once:
`WAKE <KIND> <chain-id> watcher -> <master>: <worker> <host> <fact>`.
Before it types, it checks: the window `=<session>:=<master>` exists, has one pane, the pane runs `claude`, and the input box has no typed text (dim suggestion text is fine).
If tmux left the pane in `view-mode`, it leaves view-mode first (same as `q`). In copy-mode (a person scrolls) or with a draft in the box, it holds the wake to the next run.
If the box holds exactly one of our own lines (an earlier watcher wake, or an inbox-server line from `inbox/queue.json`), it presses Enter on it first and checks that it left the box, then types the wake. After each wake it checks the line left the box (`submitted=` in watcher.log). `submit()` lives in `watcher.py`; `inbox-server.py` uses it.
At most 1 wake per master per `wake_gap_sec` (120 s). It never kills, restarts, retires or deletes anything.

Enable wakes for more teams: edit the one line in `~/.claude/orchestra/watcher.conf`, e.g. `wake_sessions = orch-harness orch-depth`. Now `wake_sessions = *`: every team with a live master gets CHAIN_END / CHAIN_STALE wakes. An event older than `wake_max_age_min` (60) is marked fired without a wake (`wake=skipped_old`). `watcher.py --dry-run` prints the events and changes nothing.

Files: `watcher.py`, `watcher.conf`, `watcher-state.json`, `watcher.log`, `events.jsonl`, `~/.config/systemd/user/orch-watcher.{service,timer}`.

Stop and start:

```bash
touch ~/.claude/orchestra/watcher.off          # kill switch: each run exits at once
rm ~/.claude/orchestra/watcher.off             # on again
systemctl --user stop orch-watcher.timer        # pause until next login/boot
systemctl --user disable --now orch-watcher.timer   # stop for good
systemctl --user list-timers orch-watcher.timer     # check
journalctl --user -u orch-watcher.service -n 20     # errors
```

### Usage limit and API stall resume

Each watcher run reads the last lines above the input box of every idle Claude session (read-only). "You've hit your ... limit · resets 3am (<timezone>)" fires `USAGE_LIMIT` (with `reset`), "API Error: ..." fires `API_STALL`. One event per session per idle period (`statusUpdatedAt` in `sessions/*.json`).
If the session is still idle in that period, the watcher sends `WAKE USAGE_RESUME <sid8> watcher -> <name>: usage limit reset; continue your task.` at reset + 2 min, or `API_RESUME` after 3 min idle. Max 2 resumes per session per hour; only kinds in `wake_kinds_all`; usual input-box checks. Teams tab shows "usage limit until HH:MM".

### Token meter and master self-restart

`ctx_meter.py` reads the context size of each live `master-*` session from its transcript:
input + cache read + cache creation + output tokens of the last main-chain answer. This equals the "<n> tokens" line in the pane.
`python3 ctx_meter.py` prints all masters. The Teams tab uses the same function.

Each watcher run fires `CTX_300K`, `CTX_400K`, `CTX_500K` once per session id (only the highest new one) into `events.jsonl`:
`{"kind": "CTX_300K", "master": ..., "session_id": ..., "tokens": ..., "wake": ...}`.
Kinds in `wake_kinds_all` (watcher.conf) wake that master in any tmux session:
`WAKE CTX_500K <sid8> watcher -> <master>: context <n> tokens. <what to do>`. Empty `wake_kinds_all` = dry run.

`restart-master.sh [<team-dir>]`: the master runs it on itself at `CTX_500K` (orchestrate skill). It refuses without
`.orchestra/launch-master.sh`, with a handoff missing or older than 30 min, or when the caller is not that master.
It logs to `log.md`, copies the handoff, writes a master report to the brain (links the previous one) and runs
`report-check.sh --retire` and `report-link.sh`. A setsid helper waits until the master's transcript is quiet for 10 s,
sends SIGTERM to the old Claude, waits for its exit (max 120 s), then starts `.orchestra/launch-master-restart.sh`
(launch-master.sh with the restart prompt) in a new window of the team's tmux session. Helper log: `.orchestra/restart-master.log`, `log.md`.

## Local inbox

A local page lists what waits for the user across all live teams. An answer wakes the team master at once (tmux). No polling, no claude.ai.

- Server: `inbox-server.py` (Python stdlib), systemd user service `orch-inbox.service`, listens on **127.0.0.1:8377 only**.
- Page: `inbox-page.html`. Items come live from `inbox-collect.py` on each load (and every 20 s while the page is open).
- Local URL (this desktop): `http://127.0.0.1:8377/?t=<token>`. Remote URL (after the user's Tailscale steps below): `https://<your-tailnet-host>/?t=<token>`.
- Token: `~/.claude/orchestra/inbox/token` (chmod 600). `?t=<token>` once sets a cookie (HttpOnly, SameSite=Strict, 1 year); then plain `/` works. No token or cookie: 403.

```
systemctl --user status orch-inbox          # check
systemctl --user restart orch-inbox         # restart (queue and answers survive)
systemctl --user disable --now orch-inbox   # stop for good
tail ~/.claude/orchestra/inbox/server.log   # requests, answers, wakes (Tailscale-User-Login is logged when present)
```

Token rotate: `rm ~/.claude/orchestra/inbox/token && systemctl --user restart orch-inbox`. Old cookies stop working. Open the new `?t=` link once on each device.

Remote access (the user's step, needs sudo; workers never run it):
```
sudo tailscale up                    # only if Tailscale is stopped
tailscale serve --bg 8377            # HTTPS inside the tailnet only. Never "funnel".
tailscale serve status               # shows the HTTPS URL inside your tailnet
```
If `tailscale serve` says "Access denied" / needs root: run `sudo tailscale set --operator=$USER` once, then `tailscale serve --bg 8377` again (or `sudo tailscale serve --bg 8377`).
Off: `tailscale serve --https=443 off` (or `tailscale serve reset`).

Answers: `~/.claude/orchestra/inbox/answers.jsonl`, one JSON per answer: `{at, id, team, master, text, choice, note, line, by}`. The last answer per item counts. "Change" on the page sends a new answer and a new wake.

How a master sees an answer: one line arrives as typed input in its pane:
```
INBOX <item-id> user -> <master>: Q: <item text, short> | A: <yes|no|later> <note>
```
Act on it, then remove the item from the "Waits for the user" line in your `status.md`. The item leaves the page. Until then the page shows it as answered.
Wake rules: control chars and newlines are stripped, max 400 chars, exactly one Enter. Pane checks come from `watcher.py` (one pane, runs claude, input box empty, not copy-mode).
Busy pane: the wake is queued in `inbox/queue.json` and retried every 15 s. The page shows per answered item: sent / queued / failed (failed = 20 hard errors, e.g. no master window).

Media: a verdict item shows videos and images. The list comes from `inbox-collect.py`: paths in the item text (file or folder, relative to the project or `~`; max 12 files per folder), plus rules in `inbox/media.json`:
```
[{"team": "dev", "match": "<text in the item>", "paths": ["<file or folder, same rules>"]}]
```
The server serves only `/m/<item-id>/<n>` from that list (HTTP Range for video seek). Other paths: 404. A video that is not H.264/yuv420p mp4 gets a cached H.264 copy in `inbox/cache/` (CPU ffmpeg, 8 threads); the original is served until the copy is ready.

### Ask the user (any session)

`~/.claude/orchestra/inbox-ask.sh "<question>" [--yesno | --options "A|B|C" | --open] [--kind decision|verdict|user-action|delete|other] [--media <path>...] [--from <name>]` puts one question on the inbox page and prints its id.
Answer type (stored as `atype` and `options` in `asks.jsonl`): `--yesno` (default) shows Yes / No / Skip; `--options` (2-6 options) shows one button per option plus Skip; `--open` shows a text field plus Skip. Explain, a note and Attach are always shown. Bad input is rejected. status.md items keep Yes / No / Skip / Explain.
The answer arrives in your pane as `INBOX <id> user -> <you>: Q: .. | A: <yes|no|skip|option text> <note>`. An `EXPLAIN` line asks you to reply with `inbox-reply.sh <id> "<text>"`.
Withdraw with `inbox-ask.sh --done <id>`. An answered ask leaves the page after 1 h. Stored in `inbox/asks.jsonl`.
Wakes find the pane by Claude session name (`find_pane.py <name>`: sessions/*.json pid -> tmux pane), fallback orch-*/master-*. The watcher uses the same lookup.
Skill: `~/.claude/skills/inbox/SKILL.md` ("use the inbox").

### Teams tab and New team (replaces the PO's team jobs)

The page has two tabs: Inbox and Teams. Code: `inbox-teams.py` (loaded by `inbox-server.py`). Test: `python3 inbox-teams.py` prints the JSON.

- Teams (read-only, `GET /api/teams`, refresh 60 s): each live team (tmux `orch-*` with a `master-*` window): goal, key results, GPU use, open items (from `.orchestra/status.md`), live workers (`registry.json` + live session names), GPU set (`ORCHESTRA_GPUS` in `.orchestra/launch-master*.sh`), master tokens (last usage in its transcript). Other live Claude sessions are listed below.
- GPUs: hosts from all `launch-master*.sh` plus `gpu_hosts` in `watcher.conf`. `ssh -o BatchMode=yes -o ConnectTimeout=8 <user>@<host> nvidia-smi --query-gpu=...` (read-only), cached 60 s, refreshed in the background. A host that does not answer shows "host unknown".
- New team (form at the bottom of Teams, `POST /team`): name, project dir, goal, GPU set (free GPUs only). Model is always Opus 5.5.
  "Check (dry run)" (`POST /team?dry=1`) runs every check and shows the handoff, contract and command. It starts nothing.
  "Start…" asks "Start master-<name>? Yes". Then the server writes `<dir>/.orchestra/master-handoff.md` and `contract.yaml` (old ones are kept as `*.<time>.md/.yaml`) and runs `~/.claude/skills/po/start-master.sh <dir> <name> <gpus>`.
  Refused: bad name, name in use (tmux `orch-<name>` or session `master-<name>` live), dir missing or not under `~/dev/` (1 or 2 levels), dir of a live team, empty goal, unknown GPU, GPU held by a live team, 8 or more live masters, no confirm.
  CSRF: POST needs `Origin` (or `Referer`) = the page's own host (`Host` or `X-Forwarded-Host`). JSON only. Token cookie as for all requests.
  Every real launch is logged to `inbox/launches.jsonl`: `{at, name, dir, gpus, requester (Tailscale-User-Login or "local"), rc, output}`. Dry runs and refusals go to `server.log`.

## GPU leases

My setup also had a GPU lease script that took server GPUs out of a shared job queue while a team used them. It is tied to my work servers, so it is not in this repo.

## Harness2 improve loop

- `h2/probes/probes.py suite`: 5 behaviour probes (report-check, DONE after chain, INBOX act once, no bundled rm, envelope). Each runs one headless haiku `claude -p` in a sandbox under `/tmp/claude-<uid>/h2-sandbox/`, scored 0/1 by code.
- `h2/improve.sh --skill <name> [--budget 18] [--max-candidates 1|2] [--current-from <suite dir>]`: Propose (sonnet, metrics + lessons -> 1-2 candidate diffs, <= 15 lines) -> Probe (each probe 3 times per variant, `--model haiku|sonnet`) -> Compose (sonnet; only winners).
- Win rule (in improve.py): a candidate wins only if its total passes beat current by >= 2 and no probe drops by >= 2 passes. `probes.py suite --reps 3` makes a reusable current baseline. Output: `h2/runs/<time>-<skill>/` (proposal.diff, scores.md, rationale.md).
- It never applies a change. It asks the user in the inbox (Apply|Reject|Explain). On Apply the master backs up the skill and runs the `patch` line from rationale.md.
- Each supported change adds an EXPERIENCE note (status candidate) with `lessons/lesson-add.sh`. Every headless run is logged in `h2/runs/claude-runs.log`.
- `h2-weekly.timer` (systemd user) asks "Run harness improve?" on Mondays 09:00 for master-harness. No auto-run.

## Hooks
`hooks/orch_hooks.py`, set in `~/.claude/settings.json` (all sessions, merged with the old hooks). Mode file `hooks/mode`: `log` (append to `hooks/hooks.log`, never block) or `enforce` (exit 2, the reason goes to the model).
- `stop-guard` (Stop): a running registry worker whose chain ended (watcher state `<id>:CHAIN_END`, or a local EXIT file) and that sent no DONE/BLOCKED since. Respects `stop_hook_active`. Masters, PO and plain sessions are never touched.
- `send-contract` (PreToolUse SendMessage): envelope on line 1 and the type allowed in `contract.yaml` for the pair. Skips empty messages, subagent ids and plain sessions.
- `send-log` (PostToolUse SendMessage): appends the envelope to `<team>/.orchestra/messages.jsonl` (FINDING also to `findings.jsonl`). Failed sends are not logged.
- `rm-explainer` (PreToolUse Bash): shlex splits the command into segments (on `&&`, `||`, `;`, `|`, `(`, `)`, newline; the command of `ssh host '...'` and `bash -c '...'` is split too). A hit = a segment whose command word (after `sudo`, `xargs`, `VAR=x`, `then` ...) is `rm`/`rmdir`/`unlink`, or `find ... -delete`/`-exec rm`, plus at least one other segment. Quoted text, heredoc bodies and `$( )` bodies are not commands. A lone delete is never touched. The full command goes to `hooks/hooks-cmds.jsonl`.
- Tests: `cd hooks && python3 -m unittest test_orch_hooks` (cases, plus the logged hits in `rm_cases.json` when that file exists). `orch_hooks.py recheck` prints hits per hook and how many logged rm commands the current classifier calls false; the enforce ask uses it.
Fail open: any error -> exit 0 (an `error` line in hooks.log). Switch: `hooks/mode-switch.sh ask` posts an inbox question to the user; `mode-switch.sh switch` writes `enforce` only if the answer was Yes. I ran `log` mode for two days, checked the hits, then switched.
