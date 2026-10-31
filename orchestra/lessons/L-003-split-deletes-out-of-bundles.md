---
id: L-003
situation: A setup command over ssh contains rm, rm -rf or another delete.
lesson: Do not bundle a delete with setup steps; the deny rule blocks the whole bundle. On a denial, read the denied text. If it holds a delete, rerun the same steps without the delete. Queue the delete
  for the user.
evidence:
- internal reports — 6 cases, 2026-10-15 to 2026-10-18. A setup command with rm -rf was denied; the worker dropped the rm and it ran (correct).
- internal reports — a failure table of one week. Bundled rm denials were the most common block (10 rows).
metric: null
status: accepted
tags:
- permissions
- deletes
- worker
- ssh
merged:
- bundled-rm denials from the PO away log, a harness research table, a master report and a PO handoff
---
