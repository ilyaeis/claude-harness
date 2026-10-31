# Tags glossary

Controlled vocabulary for the `tags:` frontmatter field in the vault.
Every tag in use must be in this file. Use an existing tag before you add a new one.
`report-check.sh` (orchestra) reads this file. A tag with a `/` that is not here is an error.

Rules per file: exactly one `type/*`, 0-3 `topic/*`, 0-1 `repo/*`, 0-1 `status/*`.
Format: an inline YAML list in frontmatter, e.g. `tags: [type/report, topic/my-topic, status/open]`.
To add a tag: append it to its family below, with a one-line meaning.

## type/
- `type/decision` — a recorded decision (`decisions/`)
- `type/log` — a dated session or activity log (`log/`)
- `type/knowledge` — reference knowledge: learn notes, research write-ups (`knowledge/`)
- `type/report` — a dated analysis, audit, or session-checkpoint report (`reports/*`)
- `type/note` — standing documentation that is kept current
- `type/index` — a hub note that mainly links to other notes (`000-home.md`)

## topic/
Add your own topics here, e.g. `topic/my-topic` — what it covers.

## repo/
Add one line per code repo, e.g. `repo/my-project` — what the repo is.

## status/
Only where the file tracks a status in its frontmatter.
- `status/open` — report or decision: work continues across sessions
- `status/closed` — report or decision: the topic is fully resolved
