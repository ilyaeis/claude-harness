#!/usr/bin/env python3
"""Context size of live Claude sessions, read from their transcripts.

tokens(sid): input_tokens + cache_read_input_tokens + cache_creation_input_tokens + output_tokens
of the last main-chain assistant message with a non-zero usage in ~/.claude*/projects/*/<sid>.jsonl.
This is the number Claude Code shows as "<n> tokens" above the input box (checked exactly on 5 masters, report 18).
live_masters(): every live Claude session named master-* -> {pid, sid, cwd}.
Used by watcher.py (CTX_* thresholds) and inbox-teams.py (Teams tab).

Usage: ctx_meter.py      prints "<name> <tokens>" for each live master
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from find_pane import alive  # noqa: E402

TAIL = 2 << 20     # read the last 2 MB of a transcript


def tokens(sid):
    """Context size of the session's last answer, or None."""
    files = glob.glob(os.path.expanduser(f"~/.claude*/projects/*/{sid}.jsonl")) if sid else []
    if not files:
        return None
    try:
        with open(files[0], "rb") as f:
            f.seek(max(0, os.path.getsize(files[0]) - TAIL))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        if '"usage"' not in line:
            continue
        try:
            row = json.loads(line)
            if row.get("isSidechain"):
                continue
            u = row["message"]["usage"]
            n = (u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
                 + u.get("output_tokens", 0))
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
        if n:      # synthetic messages (e.g. errors) have a zero usage
            return n
    return None


def live_sessions():
    """name -> {pid, cwd, sid, status, since} of every live named Claude session (all accounts)."""
    out = {}
    for f in glob.glob(os.path.expanduser("~/.claude*/sessions/*.json")):
        try:
            s = json.load(open(f))
        except (OSError, ValueError):
            continue
        if s.get("name") and isinstance(s.get("pid"), int) and alive(s["pid"]):
            out[s["name"]] = {"pid": s["pid"], "cwd": s.get("cwd", ""), "sid": s.get("sessionId", ""),
                              "status": s.get("status", ""), "since": (s.get("statusUpdatedAt") or 0) / 1000}
    return out


def live_masters():
    return {n: s for n, s in live_sessions().items() if n.startswith("master-")}


if __name__ == "__main__":
    for n, s in sorted(live_masters().items()):
        print(n, tokens(s["sid"]))
