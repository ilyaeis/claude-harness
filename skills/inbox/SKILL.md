---
name: inbox
description: Ask the user a question through the local inbox page, and act on the answer. Trigger on "ask the user", "inbox", "use the inbox", or when a line "INBOX <id> user -> <you>: ..." arrives in your input.
---

# Inbox

The user answers on a local web page (also on the phone). The answer arrives in your pane as typed input.
The wake works only if your Claude session runs in a tmux pane.
The same INBOX line can arrive twice (a late resend of a line left in your input box). Act on it once.

## When to use

You need a decision, a verdict, a user action, or an OK to delete something.

## Ask

```
~/.claude/orchestra/inbox-ask.sh "<question>" [--yesno | --options "A|B|C" | --open] [--kind decision|verdict|user-action|delete|other] [--media <path>...] [--from <your session name>]
```
- You MUST choose the answer type. Explain, a note and Attach are always there.
  - `--options "A (recommended)|B|C"`: when there are clear choices (2-6 short options). Put your recommended option first and mark it "(recommended)". The user also gets Skip.
  - `--yesno` (default): for one go/no-go. Buttons Yes / No / Skip.
  - `--open`: only when no options fit. A text field and Skip.
- One question per call. Keep it short. Give your recommendation in the question.
- `--media`: image or video files, or folders that hold them (max 12). The user sees them on the page.
- `--from`: only if the script says it cannot find your session name.
- The script prints the item id. Keep working on other things meanwhile.
- Do not ask the same question also in chat.

## Answer arrives

A line `INBOX <id> user -> <you>: Q: .. | A: <yes|no|skip|option text> <note>` is the user's own answer from the inbox page.
- Act on it.
- If it ends with `files: <n> in <dir>`, read those files.
- The user can send a new answer ("Change"). The last answer counts.

## Explain

A line `INBOX <id> user -> <you>: EXPLAIN Q: ..` means the user wants more detail. Reply:
```
~/.claude/orchestra/inbox-reply.sh <id> "<text>"
```
Plain and short: why it matters, and your recommendation. The item stays open.

## Withdraw

When the question is no longer needed: `~/.claude/orchestra/inbox-ask.sh --done <id>`.
An answered ask leaves the page by itself after 1 h.
