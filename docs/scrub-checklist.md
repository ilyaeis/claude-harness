# Scrub checklist

I built this repo from my live setup. Before each push I run these checks.
Use them too if you publish your own harness.

## Never copy
- `~/.claude/projects/`, `history.jsonl`, `sessions/`, `.credentials.json`, `~/.claude.json`,
  `shell-snapshots/`, `file-history/`, `todos/`, `stats-cache.json`. They hold transcripts, prompts and tokens.
- Runtime state: `*.jsonl`, `*.log`, `*.lock`, `inbox/` (token, answers, uploads), `watcher-state.json`, backups.

## Replace
| Found | Replace with |
|---|---|
| employer, product and internal tool names | drop the sentence, or `my-project` |
| internal hosts, IPs, VPN or tailnet names | `<host>`, `gpu1`, `<user>@<host>` |
| `/home/<me>/...` | `$HOME` in scripts, `~` in docs |
| vault path | `$BRAIN_DIR` (default `~/brain`) |
| colleague names, people-named sessions, emails | remove |
| MR numbers, queue names, team names | generic words |
| evidence paths into private reports | "internal reports" |

## Check
```bash
grep -rniE '<employer>|<internal domains>|<private IP ranges>|/home/<me>|<my name>|<email regex>|<secret prefixes>|<32-hex ids>' . --exclude-dir=.git
git ls-files | grep -E '\.(jsonl|log|lock|bak)|token|backup-'
gitleaks detect --source . --no-git
```
Secret prefixes: HTTP auth header tokens, GitHub and Anthropic token prefixes, AWS key ids.
All three must print nothing (gitleaks: "no leaks found").
When unsure if a line is work or personal, remove it.
