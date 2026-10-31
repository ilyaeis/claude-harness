---
name: session-report
description: Write a structured, self-contained report capturing everything useful from the current session, filed under a topic folder, then stop — it never exits or clears the session itself, that's left to the user. Trigger on "end session", "end the session", "save session", "close session", "clear", "checkpoint", "handoff", or when context is running low.
---

# Session Report

Replaces the old separate `end-session` and `compact-report` skills. One skill, no CLI
dependency — everything is a plain file write. The user never runs `/compact` — they always
`/clear` — so this is the handoff mechanism that survives a `/clear`, not a `/compact`.

## The rule: one topic, one folder — split into files freely within it

A report belongs to a **topic**, and a topic gets a **folder**. A short report is a single file
in that folder. A long report may be split into several files *inside the same topic folder* —
an entry-point file that summarizes and links to the parts. Don't scatter one topic's files
across different folders, and don't cram unrelated topics into one file just to avoid a split.

A report (the entry-point file, if split) must be **self-contained**: a fresh agent with zero
conversation history should be able to read it and pick up the work — context, what was
concluded, what's still open, what to do next — following links to part files or other topics
only for depth. Don't write a transcript summary; write what the next agent needs.

## Where reports live

```
$BRAIN_DIR/reports/<topic-slug>/
```

- Single-file report: `reports/<topic-slug>/<date>-<time>.md`
- Split report: `reports/<topic-slug>/00-<slug>.md` (entry point, summarizes + links below) plus
  `reports/<topic-slug>/01-<part-slug>.md`, `02-<part-slug>.md`, ... for the parts
- A topic folder accumulates over time — a later session on the same topic adds a new dated file
  or part, it doesn't have to rewrite everything into one file

`$BRAIN_DIR` is the brain vault (default `~/brain`).

Keep `$BRAIN_DIR/reports/INDEX.md` current — one line per **topic**:
```
- [<topic-slug>](<topic-slug>/) — one-line hook (under ~150 chars) — status: open|closed
```

## Step 1: Decide the trigger

- **Ending the session** ("end session", "close session", ...) → write the report, then confirm
  it's done and stop (Step 4a). Do not exit or terminate the session — that's the user's call.
- **About to `/clear` / low context / checkpoint** ("clear", "checkpoint", "handoff", context
  running low) → write the report, then tell the user to run `/clear` (Step 4b).

Either way the report itself is written the same way (Step 2-3), and either way the skill's job
ends with a message, never an action that exits or clears the session.

## Step 2: Mine the session for everything worth keeping

Go through the full conversation. Everything goes into the one report file — don't scatter
across separate memory/insight/marker files:

- **Context** — what this session was about, and for whom/what project
- **What was done** — key actions and changes, with exact file paths and commands
- **Conclusions / decisions** — what was concluded or decided, and why (include rejected
  alternatives if that saves the next agent from re-deriving them)
- **Open questions** — anything unresolved that the next agent or the user still needs to settle
- **Useful info to remember** — user preferences, how they like to work, corrections/feedback
  received, standing facts about the project (roles, constraints, deadlines — convert relative
  dates like "Thursday" to absolute)
- **Notable behaviour/working-style observations** — how the user works (decision style,
  guardrails, communication preferences), if anything stood out this session
- **Skill-improvement ideas** — if you noticed a skill (including this one) should work
  differently, note it with what happened and the proposed change

Skip anything trivial or already covered in an existing open report on the same topic (check
`reports/INDEX.md` first and update that report instead of duplicating).

## Step 3: Write the report

Frontmatter identifies the report and the agent that wrote it, and carries the vault's Obsidian
tags (see Step 3a):

```markdown
---
date: 2026-09-09T18:32
model: claude-sonnet-5
project: ~/brain
topic: vault-restructure
status: open
links: []
tags: [type/report, topic/vault-restructure, repo/brain, status/open]
---

# <Title>

## Context
...

## What was done
...

## Conclusions
...

## Open questions
...

## Useful info
...

## Next steps
...
```

- `date:` — the real time from `date +%FT%H:%M`. Do not guess it.
- Orchestra sessions (worker, master, PO, verifier) add these fields after `status:`:
  `agent:` (session name), `role:` (worker|master|po|verifier), `team:`, `task:` (task file),
  `parent:` (who spawned you), `result:` (done|blocked|partial).
  Name the file `<NN>-<agent>-<slug>.md`, NN from the task id. Then run
  `~/.claude/orchestra/report-check.sh --retire <report>` and fix every error.
- `links:` — relative paths (`<topic-slug>/<file>.md`) of other reports/parts this one relates
  to, continues from, or splits into. Add the reverse link in the other file too when it makes
  sense.
- `status: open` while work continues across sessions; set to `closed` (edit in place, don't
  create a new report) once the topic is fully resolved.
- Omit any section that's genuinely empty for this session — don't pad.
- Never save secrets or credentials.

If the report is growing past what one file should hold, stop and split: turn the current file
into the entry point (trim it to a summary + links) and move the overflow into `01-<part>.md`,
`02-<part>.md`, etc. in the same topic folder. Tag only the entry-point file; part files don't
need their own `tags:` (they're reached via the entry point's links).

## Step 3a: Tag it

The vault uses a controlled tag vocabulary so everything stays findable in Obsidian. Read
`$BRAIN_DIR/_meta/tags-glossary.md` first — it lists every tag in use, grouped by family,
with a one-line meaning. Reuse an existing tag whenever one fits; only add a new one when nothing
in the glossary covers it, and if you do, append it to the glossary with its one-line meaning (in
the matching family section). If the glossary file doesn't exist yet, create it with the four
family headers below before using it.

Every report's entry-point file gets, in its `tags:` list:
- **Exactly one** `type/*` tag — always `type/report`.
- **0–3** `topic/*` tags — the subject(s), matching the report's `topic-slug` (the folder name)
  when there's a clean 1:1 mapping; reuse the glossary's existing `topic/*` vocabulary rather than
  inventing near-duplicates (e.g. don't add `topic/gpu-issues` if `topic/gpu` already exists).
- **0–1** `repo/*` tag — only if the session was about a specific dev repo, normalized to the base
  repo name (not a worktree/branch variant — e.g. `repo/my-project`, not
  `repo/my-project-feature-x`).
- **0–1** `status/*` tag — mirror the frontmatter `status:` field (`status/open` / `status/closed`)
  when set.

Then add/update the topic's line in `reports/INDEX.md`.

## Step 4a: Confirm and stop (end-session trigger)

1. Output a brief summary: the report filename(s) and a one-line description of what it covers.
2. Output: "Session saved." and stop there.
3. Do not run any command to exit, kill, or otherwise terminate the session or the Claude Code
   process. The skill's job is done once the report is written and confirmed — ending the actual
   chat session (closing the terminal, running `/clear`, etc.) is entirely the user's decision to
   make and act on themselves.

## Step 4b: Hand off to /clear (checkpoint trigger)

1. Output the report path(s) written (entry point first, then any parts).
2. Tell the user to run `/clear` now. `/clear` wipes context entirely (unlike `/compact`, it
   carries nothing forward) — so the report is the *only* thing that survives. Make sure the
   entry-point file is genuinely self-contained before telling the user to clear.
3. If context is about to overflow on its own, do this proactively, before that happens.

## Important rules

- Be selective about *content* (don't pad with trivia) but not about *coverage* — a report that
  omits an open question or a conclusion has failed its job, since nothing else will remember it.
- One topic, one folder. Split into files within it when long; don't merge unrelated topics to
  avoid a split, and don't scatter one topic's files across folders.
- If there's truly nothing worth saving (trivial session), say so — for end-session, still confirm
  and stop; for checkpoint, still tell the user to run `/clear`.
- Never exit, kill, or clear the session as part of this skill, for either trigger — only write
  the report and, for the checkpoint trigger, tell the user to run `/clear` themselves.
