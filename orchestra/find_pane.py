#!/usr/bin/env python3
"""Find the tmux pane of a live Claude session by its session name.

  name -> live pid (~/.claude*/sessions/*.json, kill -0)
       -> the tmux pane whose pane_pid is that pid or an ancestor of it.
Fallback (old rule): the single pane of window =<session>:=<name>, then =<session>-w:=<name>
(orch-* team; workers live in the hidden session <session>-w).
Used by watcher.py and inbox-server.py.

Usage: find_pane.py <session name> [<tmux session for the fallback>]
"""
import glob
import json
import os
import subprocess
import sys

SESSION_GLOB = "~/.claude*/sessions/*.json"


def tmux(*args):
    try:
        p = subprocess.run(["tmux", *args], capture_output=True, text=True, timeout=10)
        return p.returncode, p.stdout
    except (subprocess.TimeoutExpired, OSError) as e:
        return 255, str(e)


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def session_pid(name):
    """Pid of the live Claude session with this name, or None."""
    for f in glob.glob(os.path.expanduser(SESSION_GLOB)):
        try:
            s = json.load(open(f))
        except (OSError, ValueError):
            continue
        if s.get("name") == name and isinstance(s.get("pid"), int) and alive(s["pid"]):
            return s["pid"]
    return None


def parent(pid):
    try:
        stat = open(f"/proc/{pid}/stat").read()
        return int(stat.rsplit(")", 1)[1].split()[1])
    except (OSError, ValueError, IndexError):
        return 0


def find_pane(name, session=None):
    """Return (pane_id, "session:window.pane") or (None, reason)."""
    pid = session_pid(name)
    if pid:
        rc, out = tmux("list-panes", "-a", "-F", "#{pane_pid}\t#{pane_id}\t#{session_name}:#{window_name}.#{pane_index}")
        if rc == 0:
            panes = {}
            for line in out.splitlines():
                ppid, pane, label = line.split("\t", 2)
                panes[int(ppid)] = (pane, label)
            p = pid
            while p > 1:
                if p in panes:
                    return panes[p]
                p = parent(p)
    for sess in ([session, session + "-w"] if session else []):
        rc, out = tmux("list-panes", "-t", f"={sess}:={name}", "-F", "#{pane_id}\t#{session_name}:#{window_name}.#{pane_index}")
        rows = out.splitlines() if rc == 0 else []
        if len(rows) == 1:
            return tuple(rows[0].split("\t", 1))
        if rows:
            return None, f"{len(rows)} panes"
    return None, "no window"


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        sys.exit(__doc__.strip().splitlines()[-1])
    pane, label = find_pane(*sys.argv[1:])
    print(f"{pane} {label}" if pane else f"not found: {label}")
    sys.exit(0 if pane else 1)
