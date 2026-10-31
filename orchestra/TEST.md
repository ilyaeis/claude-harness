# Test log, 2026-09-29 (Claude Code 2.1.284)

Scratch dir `/tmp/orchestra-test-<pid>`. tmux session `orchestra-test` (ORCHESTRA_SESSION). Both removed after the test.

| Step | Result |
| --- | --- |
| next-name.sh: base-26 sequence, 800 names vs. Python reference (a..z, aa..zz, aaa) | PASS |
| Deny rules, headless auto run: git reset --hard, rm -rf, git push --force origin main, git push origin main, gh pr merge, ssh host 'rm -rf' | PASS: all 6 denied; git status ran |
| Control run without deny rules: git reset --hard, rm -rf, git push ran | PASS: shows the deny rules, not the classifier, did the blocking |
| start.sh: tmux session + `claude -n master`, session file shows name "master" | PASS (trust dialog answered by hand) |
| spawn-worker.sh: window agent-a, task as first prompt, registry row, session id found | PASS |
| Worker did task, wrote report, sent DONE to master | PASS |
| master -> worker SendMessage, worker replied PONG | PASS (both directions) |
| status.sh set / print | PASS |
| retire-worker.sh with missing report | PASS: refused |
| retire-worker.sh: /exit, window gone, registry retired + time + session id | PASS |
| /orchestrate full flow (plan, task file, spawn agent-b, check, ask, retire on "yes", log.md) | PASS |

Found: the user's tmux hooks (`run-shell -b` in ~/.tmux.conf) can leave a pane in view mode.
Keys then go to tmux, not to Claude. retire-worker.sh now leaves the mode first.
