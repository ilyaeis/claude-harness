---
title: Where was I?
tags: [type/index]
---
# Where was I?

Open this note first. The queries run each time you open it (Obsidian Dataview plugin).

## Open report topics
```dataview
TABLE status, file.mtime AS "touched"
FROM "reports"
WHERE status = "open"
SORT file.mtime DESC
LIMIT 20
```

## Recently touched (last 3 days)
```dataview
LIST file.mtime
WHERE file.mtime >= date(today) - dur(3 days)
AND !contains(file.folder, "_meta")
SORT file.mtime DESC
LIMIT 25
```

## Today
- Daily note: `daily/YYYY-MM-DD.md`.
- Report index: [[reports/INDEX]].

## tmux: live sessions
The tmux hook (`tmux/tmux.conf.snippet` in the harness repo) rewrites [[tmux-now]] when tmux state changes.
![[tmux-now]]
