---
id: L-014
situation: Code or an agent must type a line into a Claude pane with tmux.
lesson: Agents never use send-keys. Only code sends one fixed wake line. Leave view-mode first, put a timeout on every tmux call, and check that the line left the input box.
evidence:
- internal reports — tmux hooks left panes in view-mode. Keys sent then went to tmux, not to Claude.
- internal reports — a lost Enter left an INBOX line unsent; 7 answers were stuck.
- internal reports — an input box held "yes, go ahead" that was never sent.
metric: null
status: accepted
tags:
- tmux
- watcher
- inbox
- messaging
merged:
- view-mode notes, the lost Enter, unsent box text
---
