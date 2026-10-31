#!/usr/bin/env python3
"""Orchestra watcher v1 (alert only). Run by the systemd user timer orch-watcher.timer.

Each run:
  1. Exit at once if ~/.claude/orchestra/watcher.off exists.
  2. Find the teams: tmux sessions "orch-*" with a window "master-*" (the master pane),
     plus the registries that match the config "registries" globs.
  3. Read each chain with "exit": null in each registry (chain-run.sh rows).
     One ssh call per host (BatchMode, short timeout). "local" runs on this machine.
  4. Detect CHAIN_END (EXIT file exists) and CHAIN_STALE (no EXIT, heartbeat not changed
     for stale_min minutes; log not changed if the chain has no heartbeat). Each event fires once per chain (state file).
  5. Append each event to events.jsonl.
  6. Teams in the wake allowlist: send one fixed WAKE line to the master pane by
     tmux send-keys. Other teams: dry run (event only).
     If the input box holds exactly one of OUR lines (an earlier watcher wake, or a line in
     inbox/queue.json), press Enter on it first (submit(), also used by inbox-server.py), then type the wake.
     Any other text in the box is a user draft: the wake waits.
  7. Token meter: for each live Claude session named master-*, read its context size (ctx_meter.py).
     At 300k/400k/500k fire CTX_300K/CTX_400K/CTX_500K once per session id (only the highest new one).
     Kinds in "wake_kinds_all" wake that master wherever it runs; other kinds: dry run.
     Test only: ORCH_CTX_THRESHOLDS="<n300> <n400> <n500>" overrides the thresholds.
  8. Usage limit / API stall: for each live idle Claude session (any name), read the last lines above its input box
     (capture-pane, read-only). "You've hit your ... limit · resets 3am (Europe/Riga)" -> USAGE_LIMIT;
     "API Error: ..." -> API_STALL. One event per session per idle period (statusUpdatedAt).
     Resume when still idle in the same period: USAGE_RESUME at reset + 2 min, API_RESUME after 3 min idle.
     Max 2 resumes per session per hour. Resumes wake only if the kind is in "wake_kinds_all".
     Test only: ORCH_USAGE_SESSIONS=<json file> {name: {sid, status, since, pane}} replaces the live sessions.
It never kills, restarts, retires, deletes or edits anything except its own
state, log and events files.

Usage: watcher.py [--config <file>] [--dry-run]   (--dry-run: print events, change nothing)
"""
import fcntl
import glob
import json
import os
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from find_pane import find_pane  # noqa: E402
from ctx_meter import live_masters, live_sessions, tokens  # noqa: E402

ORCH = os.path.expanduser("~/.claude/orchestra")
DEFAULTS = {
    "wake_sessions": "orch-harness",
    "wake_kinds_all": "",
    "wake_max_age_min": "60",
    "ctx_names": "",
    "stale_min": "20",
    "wake_gap_sec": "120",
    "ssh_timeout_sec": "8",
    "registries": "~/dev/*/.orchestra/registry.json ~/dev/*/*/.orchestra/registry.json",
    "off_file": ORCH + "/watcher.off",
    "state": ORCH + "/watcher-state.json",
    "events": ORCH + "/events.jsonl",
    "log": ORCH + "/watcher.log",
}


def load_config(path):
    cfg = dict(DEFAULTS)
    if os.path.exists(path):
        for line in open(path):
            line = line.split("#", 1)[0].strip()
            if "=" in line:
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip()
    for k in ("off_file", "state", "events", "log"):
        cfg[k] = os.path.expanduser(cfg[k])
    return cfg


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def log(cfg, msg):
    with open(cfg["log"], "a") as f:
        f.write(f"{now_iso()} {msg}\n")


def run(cmd, timeout=10, stdin=None):
    try:
        p = subprocess.run(cmd, input=stdin, stdin=None if stdin else subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout
    except (subprocess.TimeoutExpired, OSError) as e:
        return 255, str(e)


def tmux_teams():
    """Map project dir -> {session, master, pane} for each tmux session orch-*."""
    rc, out = run(["tmux", "list-panes", "-a", "-F",
                   "#{session_name}\t#{window_name}\t#{pane_id}\t#{pane_current_path}"])
    teams = {}
    if rc != 0:
        return teams
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 4:
            continue
        sess, win, pane, path = parts
        if sess.startswith("orch-") and win.startswith("master-"):
            teams[os.path.realpath(path)] = {"session": sess, "master": win}
    return teams


def find_registries(cfg, teams):
    regs = set()
    for pat in cfg["registries"].split():
        regs.update(os.path.realpath(p) for p in glob.glob(os.path.expanduser(pat)))
    for proj in teams:
        p = os.path.join(proj, ".orchestra", "registry.json")
        if os.path.exists(p):
            regs.add(os.path.realpath(p))
    return sorted(regs)


def read_chains(host, chains, timeout):
    """Return (host_now, {chain id: (exit, log_size, log_mtime, heartbeat_mtime, phase)}) or (None, error)."""
    lines = ['echo "NOW $(date +%s)"']
    for c in chains:
        d = c["dir"]
        d = '"$HOME"/' + shlex.quote(d[2:]) if d.startswith("~/") else shlex.quote(d)
        lines.append(
            f'd={d}; e=$(cat "$d/EXIT" 2>/dev/null || echo -); '
            f's=$(stat -c "%s %Y" "$d/log" 2>/dev/null || echo "- -"); '
            f'h=$(stat -c %Y "$d/heartbeat" 2>/dev/null || echo -); '
            f'p=$(cat "$d/phase" 2>/dev/null || echo -); '
            f'echo "C|{c["id"]}|$e|$s|$h|$p"')
    script = "\n".join(lines) + "\n"
    if host == "local":
        rc, out = run(["bash", "-s"], timeout=timeout, stdin=script)
    else:
        rc, out = run(["ssh", "-o", "GSSAPIAuthentication=no", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={timeout}",
                       host, "bash -s"], timeout=timeout + 5, stdin=script)
    if rc != 0:
        return None, f"rc={rc} {out.strip()[:200]}"
    host_now, res = None, {}
    for line in out.splitlines():
        if line.startswith("NOW "):
            host_now = int(line.split()[1])
        elif line.startswith("C|"):
            _, cid, e, sm, hb, phase = line.split("|", 5)
            size, mtime = sm.split(" ", 1)
            res[cid] = (e, size, mtime, hb, phase)
    return host_now, res


CTX = [("CTX_300K", 300000, "keep your handoff current."),
       ("CTX_400K", 400000, "write .orchestra/master-handoff.md now (goal, plan, tasks + status, live workers,"
                            " open questions, next step)."),
       ("CTX_500K", 500000, "write the handoff, then run ~/.claude/orchestra/restart-master.sh")]


def ctx_events(cfg, state):
    """CTX_* events for live masters. Marks every crossed kind fired; returns one event per master."""
    limits = os.environ.get("ORCH_CTX_THRESHOLDS", "").split()
    kinds = [(k, int(limits[i]) if len(limits) == 3 else n, why) for i, (k, n, why) in enumerate(CTX)]
    only = set(cfg["ctx_names"].split())
    out = []
    for name, s in sorted(live_masters().items()):
        if only and name not in only:
            continue
        n = tokens(s["sid"])
        if n is None:
            continue
        new = [(k, why) for k, lim, why in kinds if n >= lim and f'{s["sid"]}:{k}' not in state["fired"]]
        for k, _ in new:
            state["fired"][f'{s["sid"]}:{k}'] = now_iso()
        if new:
            out.append((name, s["sid"], n) + new[-1])
    return out


LIMIT_RE = re.compile(r"^\s*(?:⎿\s*)?You've hit your (\w+) limit(?:\s*·\s*resets\s+(.+?))?\s*$")
APIERR_RE = re.compile(r"^\s*(?:⎿\s*)?API Error\b")
RESET_RE = re.compile(r"(?:\b([A-Z][a-z]{2}) (\d{1,2}),? (?:at )?)?\b(\d{1,2})(?::(\d{2}))?\s*([ap]m)\b\s*(?:\(([^)]+)\))?", re.I)
RESUME_GAP = {"USAGE_LIMIT": 120, "API_STALL": 180}     # seconds after reset / after the start of the idle period
RESUME_MAX_PER_HOUR = 2


def parse_reset(text, since):
    """Epoch of "3am (Europe/Riga)", "5:30pm", "Oct 21, 5pm (Europe/Riga)": the first such time after `since`. None if unparsed."""
    m = RESET_RE.search(text or "")
    if not m:
        return None
    mon, day, h, mi, ap, tzname = m.groups()
    try:
        tz = ZoneInfo(tzname) if tzname else None
    except (ValueError, KeyError):
        tz = None
    base = datetime.fromtimestamp(since, tz) if tz else datetime.fromtimestamp(since).astimezone()
    t = base.replace(hour=int(h) % 12 + (12 if ap.lower() == "pm" else 0), minute=int(mi or 0), second=0, microsecond=0)
    if mon:
        try:
            t = t.replace(month=datetime.strptime(mon, "%b").month, day=int(day))
        except ValueError:
            return None
        if t.timestamp() < since - 86400:
            t = t.replace(year=t.year + 1)
    elif t.timestamp() <= since:
        t += timedelta(days=1)
    return int(t.timestamp())


def pane_tail(pane, n=15):
    """Last n non-blank lines above the Claude input box (plain text), or [] if the capture fails."""
    rc, out = run(["tmux", "capture-pane", "-p", "-t", pane])
    if rc != 0:
        return []
    lines = out.splitlines()
    rules = [i for i, l in enumerate(lines) if l.strip().startswith("─" * 10)]
    if len(rules) >= 2:
        lines = lines[:rules[-2]]
    return [l for l in lines if l.strip()][-n:]


def detect_stall(tail):
    """(kind, limit name, reset text, line) if one of the last 6 lines is a limit or API error message, else None."""
    for line in reversed(tail[-6:]):
        if m := LIMIT_RE.match(line):
            return "USAGE_LIMIT", m.group(1), m.group(2) or "", line.strip()
        if APIERR_RE.match(line):
            return "API_STALL", None, "", line.strip()
    return None


def usage_sessions():
    """name -> {sid, status, since, pane?} of the sessions to check (live ones, or the test file)."""
    if os.environ.get("ORCH_USAGE_SESSIONS"):
        return json.load(open(os.environ["ORCH_USAGE_SESSIONS"]))
    return live_sessions()


def usage_events(cfg, state, sessions, now):
    """USAGE_LIMIT / API_STALL events, and USAGE_RESUME / API_RESUME when due. Queues resumes in state["pending"]."""
    wake_all = set(cfg["wake_kinds_all"].split())
    usage, resumes = state.setdefault("usage", {}), state.setdefault("resumes", {})
    events = []
    for name in list(usage):
        if name not in sessions:
            del usage[name]
    for name, s in sorted(sessions.items()):
        if s.get("status") != "idle":
            usage.pop(name, None)
            continue
        u = usage.get(name)
        if u and u["since"] == s["since"]:
            if u["due"] is None or u["resumed"] or now < u["due"]:
                continue
            u["resumed"] = True
            kind = "USAGE_RESUME" if u["kind"] == "USAGE_LIMIT" else "API_RESUME"
            recent = [t for t in resumes.get(name, []) if now - t < 3600]
            wake = "dry_run"
            if len(recent) >= RESUME_MAX_PER_HOUR:
                wake = "skipped_cap"
            elif kind in wake_all:
                wake = "queued"
                recent.append(now)
                why = "usage limit reset" if kind == "USAGE_RESUME" else "API error stall"
                state["pending"].append({"session": None, "master": name, "pane": s.get("pane"), "idle_since": s["since"],
                                         "line": clean(f"WAKE {kind} {u['sid'][:8]} watcher -> {name}: {why}; continue your task.", 300)})
            resumes[name] = recent
            events.append({"ts": now_iso(), "kind": kind, "master": name, "session_id": u["sid"], "wake": wake})
            continue
        pane = s.get("pane") or find_pane(name)[0]
        hit = detect_stall(pane_tail(pane)) if pane else None
        if not hit:
            continue
        kind, limit, reset_text, line = hit
        reset = parse_reset(reset_text, s["since"]) if kind == "USAGE_LIMIT" else None
        start = reset if kind == "USAGE_LIMIT" else s["since"]
        due = start + RESUME_GAP[kind] if start else None
        usage[name] = {"kind": kind, "sid": s.get("sid", ""), "since": s["since"], "due": due, "resumed": False}
        ev = {"ts": now_iso(), "kind": kind, "master": name, "session_id": s.get("sid", ""), "fact": clean(line, 200),
              "wake": "none"}
        if kind == "USAGE_LIMIT":
            ev.update(limit=limit, reset_epoch=reset,
                      reset=datetime.fromtimestamp(reset).astimezone().isoformat(timespec="minutes") if reset else None)
        events.append(ev)
    return events


def clean(s, n=160):
    return re.sub(r"[^\x20-\x7e]", "?", str(s))[:n]


ANSI = re.compile(r"\x1b\[[0-9;]*m")
DIM = re.compile(r"\x1b\[2m.*?(\x1b\[0m|\x1b\[22m|$)")


def box_text(pane):
    """Typed text in the Claude input box (between the last two rule lines), or None if no box is found.
    Dim text is a prompt suggestion, not a draft."""
    rc, out = run(["tmux", "capture-pane", "-e", "-p", "-t", pane])
    if rc != 0:
        return None
    lines = out.splitlines()
    rules = [i for i, l in enumerate(lines) if ANSI.sub("", l).strip().startswith("─" * 10)]
    if len(rules) < 2:
        return None
    box = lines[rules[-2] + 1:rules[-1]]
    text = "".join(ANSI.sub("", DIM.sub("", l)) for l in box)
    return text.replace("❯", "").replace("\xa0", "").strip()


def squash(s):
    """Text without any whitespace, to compare a line with its wrapped copy in the input box."""
    return re.sub(r"\s", "", s)


def submit(pane, line):
    """Press Enter until `line` has left the input box. True if it left; False if it is still there after 2 Enters.
    A box we cannot find (a dialog is open) counts as left: we never type again on top of an unknown state."""
    for _ in range(2):
        run(["tmux", "send-keys", "-t", pane, "Enter"])
        for _ in range(6):
            time.sleep(0.5)
            text = box_text(pane)
            if text is None or squash(line) not in squash(text):
                return True
    return False


def inbox_lines():
    """master -> wake lines the inbox server typed for it (inbox/queue.json). They count as our own lines."""
    try:
        q = json.load(open(ORCH + "/inbox/queue.json"))
    except (OSError, ValueError):
        return {}
    out = {}
    for j in q.values() if isinstance(q, dict) else []:
        if isinstance(j, dict) and j.get("master") and j.get("line"):
            out.setdefault(j["master"], []).append(j["line"])
    return out


def master_pane(session, master, own=(), pane=None):
    """Return the pane id of the Claude session `master` if it is safe to type into, else (None, reason).
    find_pane: session name -> pid -> pane; fallback: the single pane of =session:=master.
    If the input box holds exactly one of the lines in `own` (our own unsent wake line), return (pane, <that line>).
    `pane`: use this pane id instead of the lookup (test fake panes)."""
    pane, label = (pane, "") if pane else find_pane(master, session)
    if pane is None:
        return None, label
    for attempt in (1, 2):
        rc, out = run(["tmux", "display-message", "-p", "-t", pane,
                       "#{pane_current_command}\t#{pane_in_mode}\t#{pane_mode}"])
        if rc != 0:
            return None, "no window"
        cmd, in_mode, mode = out.rstrip("\n").split("\t")
        if cmd != "claude":
            return None, f"pane runs {cmd}"
        if in_mode == "0":
            text = box_text(pane)
            if text == "":
                return pane, ""
            mine = [l for l in own if text is not None and squash(text) == squash(l)]
            return (pane, mine[0]) if mine else (None, "input box not empty or not found")
        # view-mode is left by tmux run-shell hooks; leaving it is what "q" does.
        # copy-mode means a person scrolls: do not touch it, try again next run.
        if mode != "view-mode" or attempt == 2:
            return None, "pane in copy mode"
        run(["tmux", "send-keys", "-t", pane, "-X", "cancel"])


def main():
    cfg_path = ORCH + "/watcher.conf"
    if len(sys.argv) == 3 and sys.argv[1] == "--config":
        cfg_path = sys.argv[2]
    cfg = load_config(cfg_path)
    if os.path.exists(cfg["off_file"]):
        return 0
    lock = open(cfg["state"] + ".lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return 0

    state = {"fired": {}, "last_wake": {}, "last_line": {}, "pending": []}
    if os.path.exists(cfg["state"]):
        state.update(json.load(open(cfg["state"])))
    wake_sessions = set(cfg["wake_sessions"].split())   # "*" = every team with a live master
    max_age_sec = float(cfg["wake_max_age_min"]) * 60
    dry = "--dry-run" in sys.argv
    stale_sec = float(cfg["stale_min"]) * 60
    timeout = int(cfg["ssh_timeout_sec"])

    teams = tmux_teams()
    by_host = {}   # host -> list of (team info, worker, chain)
    counts = {}
    for reg in find_registries(cfg, teams):
        proj = os.path.dirname(os.path.dirname(reg))
        team = teams.get(proj, {"session": None, "master": None})
        tname = team["session"] or os.path.basename(proj)
        try:
            rows = json.load(open(reg))
        except (OSError, ValueError) as e:
            log(cfg, f"skip {reg}: {e}")
            continue
        n = 0
        for row in rows:
            for c in row.get("chains") or []:
                if c.get("exit") is not None or f'{c["id"]}:CHAIN_END' in state["fired"]:
                    continue
                by_host.setdefault(c["host"], []).append((team, tname, row["name"], c))
                n += 1
        counts[tname] = n

    events = []
    for host, items in by_host.items():
        host_now, res = read_chains(host, [i[3] for i in items], timeout)
        if host_now is None:
            log(cfg, f"host {host} unreachable: {res}")
            continue
        for team, tname, worker, c in items:
            r = res.get(c["id"])
            if r is None or r[1] == "-":
                log(cfg, f"chain {c['id']} on {host}: no log file")
                continue
            e, size, mtime, hb, phase = r
            hb_age = f"{host_now - int(hb)}s" if hb != "-" else "none"
            if e != "-":
                kind, fact = "CHAIN_END", f"{e} phase={phase} log={size}B"
            elif host_now - int(hb if hb != "-" else mtime) > stale_sec:
                # With a heartbeat, only the heartbeat age counts. Without one, the log age counts.
                kind = "CHAIN_STALE"
                what = "no heartbeat" if hb != "-" else "no log growth"
                fact = (f"{what} {int((host_now - int(hb if hb != '-' else mtime)) // 60)} min"
                        f" phase={phase} log={size}B heartbeat_age={hb_age}")
            else:
                continue
            key = f'{c["id"]}:{kind}'
            if key in state["fired"]:
                continue
            state["fired"][key] = now_iso()
            wake = "dry_run"
            # Age of the event: since the last log write (END) or the last sign of life (STALE).
            age = host_now - int(hb if kind == "CHAIN_STALE" and hb != "-" else mtime)
            if team["master"] and ("*" in wake_sessions or team["session"] in wake_sessions):
                wake = "queued"
                if age > max_age_sec:
                    wake = "skipped_old"
            if wake == "queued":
                line = clean(f'WAKE {kind} {c["id"]} watcher -> {team["master"]}: {worker} {host} {fact}', 300)
                state["pending"].append({"session": team["session"], "master": team["master"], "line": line})
            if wake == "skipped_old":
                fact += f" (skipped, event {age // 60} min old)"
            events.append({"ts": now_iso(), "kind": kind, "team": tname, "session": team["session"],
                           "master": team["master"], "worker": worker, "chain": c["id"], "host": host,
                           "fact": fact, "wake": wake})

    wake_all = set(cfg["wake_kinds_all"].split())
    for name, sid, n, kind, why in ctx_events(cfg, state):
        wake = "dry_run"
        if kind in wake_all:
            wake = "queued"
            line = clean(f"WAKE {kind} {sid[:8]} watcher -> {name}: context {n} tokens. {why}", 300)
            state["pending"].append({"session": None, "master": name, "line": line})
        events.append({"ts": now_iso(), "kind": kind, "master": name, "session_id": sid,
                       "tokens": n, "wake": wake})

    sessions = usage_sessions()
    events += usage_events(cfg, state, sessions, time.time())

    if dry:
        for ev in events:
            print(json.dumps(ev))
        return 0
    with open(cfg["events"], "a") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")

    # Send at most one wake per master per run, and not within wake_gap_sec of the last one.
    # Our own unsent line in the input box (an earlier watcher wake, or an inbox-server line) is submitted first.
    inbox = inbox_lines()
    keep, sent_to = [], set()
    for p in state["pending"]:
        m = p["master"]
        cur = sessions.get(m, {})
        if "idle_since" in p and (cur.get("status") != "idle" or cur.get("since") != p["idle_since"]):
            log(cfg, f"resume dropped ({m} is no longer idle in the same period): {p['line']}")
            continue
        if m in sent_to or time.time() - state["last_wake"].get(m, 0) < float(cfg["wake_gap_sec"]):
            keep.append(p)
            continue
        own = [q["line"] for q in state["pending"] if q["master"] == m] + inbox.get(m, [])
        own += [state["last_line"][m]] if m in state["last_line"] else []
        pane, why = master_pane(p["session"], m, own, p.get("pane"))
        if pane is None:
            if why != "no window":
                keep.append(p)
            log(cfg, f"wake not sent ({why}): {p['line']}")
            continue
        if why:
            ok = submit(pane, why)
            log(cfg, f"own line left in input box of {m} {pane} submitted={ok}: {why}")
            if not ok:
                keep.append(p)
                continue
            if why == p["line"]:
                state["last_wake"][m] = time.time()
                sent_to.add(m)
                continue
            pane, why = master_pane(p["session"], m, pane=p.get("pane"))
            if pane is None or why:
                keep.append(p)
                log(cfg, f"wake not sent ({why or 'input box not empty'}): {p['line']}")
                continue
        rc1, _ = run(["tmux", "send-keys", "-t", pane, "-l", p["line"]])
        ok = submit(pane, p["line"]) if rc1 == 0 else False
        state["last_wake"][m] = time.time()
        state["last_line"][m] = p["line"]
        sent_to.add(m)
        log(cfg, f"wake sent to {p['session']}:{m} {pane} rc={rc1} submitted={ok}: {p['line']}")
    state["pending"] = keep

    tmp = cfg["state"] + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=1)
    os.replace(tmp, cfg["state"])
    log(cfg, "run: chains " + " ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        + f" events={len(events)} pending={len(keep)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
