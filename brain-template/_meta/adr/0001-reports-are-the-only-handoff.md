# 0001. Session reports are the only handoff
Status: accepted

File name: `NNNN-<kebab-slug>.md`, numbered in order. Never renumber. Change an old decision with a new ADR that says "Supersedes 0001".

## Context
Claude sessions end, run out of context, or get `/clear`. Chat history does not carry over.
A fresh session must pick up the work with no history.

## Decision
Every session that ends or clears writes a report to `reports/<topic>/`, with the `session-report` skill.
The report is self-contained. `reports/INDEX.md` has one line per topic.

## Consequences
- A new session reads `INDEX.md`, then the newest report of the topic.
- Reports must say what is still open, or the next session cannot know it.
