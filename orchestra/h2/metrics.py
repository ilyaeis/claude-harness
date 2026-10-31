#!/usr/bin/env python3
"""Orchestra harness metrics (read-only).

Usage: metrics.py [--since YYYY-MM-DD] [--json | --md]   (default: both, JSON first)

Reads every team log (~/dev/*/.orchestra/log.md and */*/), the team registries,
~/.claude/orchestra/{events.jsonl,watcher.log,inbox/*} and the PO's away-mode decision log
(ORCH_AWAY_LOG, if set). Counts per day and per team.
Team = project dir relative to ~/dev (e.g. vision/depth). Times are local.

How each metric is measured (see MEASURES below; printed with --md):
"""
import collections
import datetime as dt
import glob
import json
import os
import re
import subprocess
import sys

HOME = os.path.expanduser("~")
DEV = f"{HOME}/dev"
ORCH = f"{HOME}/.claude/orchestra"
NIGHT = os.environ.get("ORCH_AWAY_LOG", "")   # the PO's away-mode decision log (optional)
WATCH_START = dt.datetime(2026, 10, 18, 22, 43)   # first watcher run: chain ends known after this
RETIRE_GATE = dt.datetime(2026, 10, 18, 21, 44)   # retire-worker.sh runs report-check from here

MEASURES = {
    "done_delay": "chain end (events.jsonl CHAIN_END; ts minus age when skipped_old) -> first "
                  "'<worker> done' line in the team log after it. Only the last chain before each done. "
                  "Resolution about 2 min (watcher cycle) + 1 min (log). Median min and count > 10 min.",
    "denied": "log lines (and night-log rows) with 'denied', 'permission-blocked' or 'permission block'.",
    "fix_rounds": "log lines where the master sends a fix back: 'fix sent', 'sent fix', 'asked (to) fix', "
                  "'assigned fix'. Lower bound: masters do not always log fixes.",
    "rc_fail": "retired workers (after the retire gate) whose report fails report-check.sh --retire now "
               "(retired with --force or edited later). Counted on the retire day.",
    "asks": "inbox asks (asks.jsonl, op=ask, tests skipped) and status-item answers (answers.jsonl). "
            "Wait = first answer minus ask time; asks only (status items have no start time).",
    "idle_gpu_h": "ESTIMATE. Workers with a GPU spawned after the watcher start: alive time minus the union "
                  "of its chains (started_at -> CHAIN_END event, else retire time). Counted on the spawn day.",
    "wake_fail": "watcher.log 'wake not sent' lines + INBOX_STUCK events + inbox queue items not 'sent'.",
}
__doc__ += "\n".join(f"  {k}: {v}" for k, v in MEASURES.items())


FIX_RE = re.compile(r"fix(es)? sent|sent (a |the )?fix|asked (to )?fix|assigned fix")


def ts(s):
    """Parse a log or ISO time to a naive local datetime."""
    s = s.strip()
    try:
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d.astimezone().replace(tzinfo=None) if d.tzinfo else d


def num(v):
    """Round; whole numbers as int."""
    if v is None:
        return None
    v = round(v, 2)
    return int(v) if float(v).is_integer() else v


def team_of(path):
    return os.path.relpath(os.path.dirname(os.path.dirname(path)), DEV)


def load_logs():
    """{team: [(time, actor, verb, text)]}"""
    out = {}
    for p in sorted(glob.glob(f"{DEV}/*/.orchestra/log.md") + glob.glob(f"{DEV}/*/*/.orchestra/log.md")):
        rows = []
        for line in open(p, encoding="utf-8", errors="replace"):
            m = re.match(r"(\d{4}-\d\d-\d\d \d\d:\d\d) (\S+) ?(\S*) ?(.*)", line.rstrip("\n"))
            if m:
                rows.append((ts(m.group(1)), m.group(2), m.group(3), line.rstrip("\n")))
        out[team_of(p)] = rows
    return out


def aliases(teams):
    """name -> team for master-x, orch-x, x (x from launch-master*.sh and the dir name)."""
    out = {}
    for team in teams:
        names = {os.path.basename(team)}
        for f in glob.glob(f"{DEV}/{team}/.orchestra/launch-master*.sh"):
            names |= {m for m in re.findall(r"master-([a-z0-9-]+)", open(f).read()) if not m.startswith("handoff")}
        for n in names:
            out[n] = out[f"master-{n}"] = out[f"orch-{n}"] = team
    return out


def load_registry():
    """{worker: (team, row)}"""
    out = {}
    for p in glob.glob(f"{DEV}/*/.orchestra/registry.json") + glob.glob(f"{DEV}/*/*/.orchestra/registry.json"):
        try:
            rows = json.load(open(p))
        except (OSError, ValueError):
            continue
        for r in rows if isinstance(rows, list) else []:
            rp = r.get("report_path")
            if rp and not os.path.isabs(os.path.expanduser(rp)):   # relative to the project dir
                r = {**r, "report_path": os.path.join(os.path.dirname(os.path.dirname(p)), rp)}
            out[r["name"]] = (team_of(p), r)
    return out


def jsonl(path):
    if not os.path.exists(path):
        return []
    out = []
    for line in open(path, encoding="utf-8", errors="replace"):
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def main():
    args = sys.argv[1:]
    since = None
    if "--since" in args:
        since = dt.datetime.fromisoformat(args[args.index("--since") + 1])
    fmt = "json" if "--json" in args else "md" if "--md" in args else "both"

    logs, reg = load_logs(), load_registry()
    cell = collections.defaultdict(lambda: collections.defaultdict(float))  # (day, team) -> metric -> value
    delays = collections.defaultdict(list)                                 # (day, team) -> [min]
    waits = collections.defaultdict(list)

    def add(t, team, key, v=1):
        if t and (since is None or t >= since):
            cell[(t.date().isoformat(), team)][key] += v

    # Chain ends from the watcher.
    ends = {}  # chain id -> end time
    by_worker = collections.defaultdict(list)
    for e in jsonl(f"{ORCH}/events.jsonl"):
        if e.get("kind") != "CHAIN_END" or not e.get("worker") or e["worker"].startswith("zz-"):
            continue
        t = ts(e["ts"])
        m = re.search(r"event (\d+) min old", e.get("fact", ""))
        if m:
            t -= dt.timedelta(minutes=int(m.group(1)))
        ends[e["chain"]] = t
        by_worker[e["worker"]].append(t)

    done_re = re.compile(r"^done\b|^DONE\b", re.I)
    for team, rows in logs.items():
        dones = collections.defaultdict(list)
        for t, actor, verb, text in rows:
            low = text.lower()
            if re.search(r"denied|permission[- ]block", low):
                add(t, team, "denied")
            if FIX_RE.search(low):
                add(t, team, "fix_rounds")
            if done_re.match(verb):
                dones[actor].append(t)
        for w, ds in dones.items():
            prev = None
            for d in ds:
                cands = [c for c in by_worker.get(w, []) if c <= d + dt.timedelta(minutes=1) and (prev is None or c > prev)]
                if cands:
                    mins = max(0.0, (d - max(cands)).total_seconds() / 60)
                    if since is None or d >= since:
                        delays[(d.date().isoformat(), team)].append(mins)
                prev = d

    # Night log rows: | time | team | decision | reason |
    if os.path.exists(NIGHT):
        # Rows have only HH:MM. Start 2026-10-16 (header); a time earlier than the last one = next day.
        day, last = dt.date(2026, 10, 16), dt.time(23, 0)
        for line in open(NIGHT, encoding="utf-8"):
            m = re.match(r"\| (\d\d):(\d\d)", line)
            if m:
                t = dt.time(int(m.group(1)), int(m.group(2)))
                if t < last and (last.hour - t.hour) > 3:
                    day += dt.timedelta(days=1)
                last = t
            if re.search(r"denied|permission[- ]block", line, re.I) and line.startswith("|"):
                parts = [p.strip() for p in line.split("|")]
                add(dt.datetime.combine(day, last), "night-log:" + (parts[2] if len(parts) > 2 else "?"), "denied")

    # Report-check at retire.
    retired = [(team, r) for team, r in reg.values()
               if r.get("retired_at") and r.get("report_path") and ts(r["retired_at"]) and ts(r["retired_at"]) >= RETIRE_GATE]
    paths = [r["report_path"] for _, r in retired if os.path.exists(r["report_path"])]
    failed = set()
    if paths:
        out = subprocess.run([f"{ORCH}/report-check.sh", "--retire", "--quiet", *paths],
                             capture_output=True, text=True).stdout
        failed = {l.split(None, 1)[1].strip() for l in out.splitlines() if l.startswith("FAIL ")}
    for team, r in retired:
        add(ts(r["retired_at"]), team, "retired")
        if not os.path.exists(r["report_path"]) or r["report_path"] in failed:
            add(ts(r["retired_at"]), team, "rc_fail")

    # Questions to the user.
    master_team = aliases(logs)
    asks, first_answer = {}, {}
    for a in jsonl(f"{ORCH}/inbox/asks.jsonl"):
        if a.get("op") == "ask" and not re.search(r"\btest\b.*ignore|ignore.*\btest\b|^t-\d+ test", a.get("text", ""), re.I) \
                and not re.match(r"harness-[a-z]+$", a.get("from", "")):
            asks[a["id"]] = a
    for a in jsonl(f"{ORCH}/inbox/answers.jsonl"):
        first_answer.setdefault(a["id"], (ts(a["at"]), a))
    for i, a in asks.items():
        team = master_team.get(a["from"], a["from"])
        t = ts(a["at"])
        add(t, team, "asks")
        if i in first_answer:
            w = (first_answer[i][0] - t).total_seconds() / 60
            if since is None or t >= since:
                waits[(t.date().isoformat(), team)].append(w)
        else:
            add(t, team, "asks_open")
    for i, (t, a) in first_answer.items():
        if i.startswith("ask-") or re.search(r"\btest\b", a.get("text", ""), re.I):
            continue
        team = master_team.get(a.get("team"), a.get("team", "?"))
        add(t, team, "status_answers")

    # Idle GPU (estimate).
    now = dt.datetime.now()
    for w, (team, r) in reg.items():
        sp = ts(r.get("spawned_at", "") or "")
        if not r.get("gpu") or not sp or sp < WATCH_START:
            continue
        end = ts(r["retired_at"]) if r.get("retired_at") else now
        spans = []
        for c in r.get("chains") or []:
            s = ts(c.get("started_at", "") or "")
            if s:
                spans.append((max(s, sp), min(ends.get(c["id"], end), end)))
        busy, cur = 0.0, None
        for s, e in sorted(spans):
            if cur and s <= cur[1]:
                cur = (cur[0], max(cur[1], e))
            else:
                if cur:
                    busy += (cur[1] - cur[0]).total_seconds()
                cur = (s, e)
        if cur:
            busy += (cur[1] - cur[0]).total_seconds()
        add(sp, team, "idle_gpu_h", max(0.0, (end - sp).total_seconds() - busy) / 3600)

    # Wakes that failed.
    for line in open(f"{ORCH}/watcher.log", encoding="utf-8", errors="replace"):
        if " wake not sent" in line:
            m = re.search(r"-> (master-\S+):", line)
            add(ts(line.split()[0]), master_team.get(m.group(1).rstrip(":"), m.group(1)) if m else "?", "wake_fail")
    for e in jsonl(f"{ORCH}/events.jsonl"):
        if e.get("kind") == "INBOX_STUCK":
            add(ts(e["ts"]), master_team.get(re.sub(r"-[a-z]$", "", e.get("team") or "?"), e.get("team") or "?"), "wake_fail")
    try:
        q = json.load(open(f"{ORCH}/inbox/queue.json"))
    except (OSError, ValueError):
        q = {}
    for i, it in q.items():
        if it.get("status") != "sent":
            add(dt.datetime.fromtimestamp(it.get("at", 0)), master_team.get(it.get("master"), it.get("master", "?")), "wake_fail")

    for k, v in delays.items():
        cell[k]["done_n"] = len(v)
        cell[k]["done_delay_med_min"] = round(sorted(v)[len(v) // 2], 1)
        cell[k]["done_gt10"] = sum(x > 10 for x in v)
    for k, v in waits.items():
        cell[k]["ask_wait_med_min"] = round(sorted(v)[len(v) // 2], 1)

    rows = [{"day": d, "team": t, **{m: num(v) for m, v in vals.items()}}
            for (d, t), vals in sorted(cell.items())]
    totals = collections.defaultdict(float)
    for r in rows:
        for m in ("done_n", "done_gt10", "denied", "fix_rounds", "retired", "rc_fail", "asks", "asks_open",
                  "status_answers", "idle_gpu_h", "wake_fail"):
            totals[m] += r.get(m, 0)
    alld = [x for v in delays.values() for x in v]
    allw = [x for v in waits.values() for x in v]
    totals["done_delay_med_min"] = round(sorted(alld)[len(alld) // 2], 1) if alld else None
    totals["ask_wait_med_min"] = round(sorted(allw)[len(allw) // 2], 1) if allw else None
    totals = {k: num(v) for k, v in totals.items()}
    result = {"since": since.isoformat() if since else None, "generated": now.isoformat(timespec="seconds"),
              "measures": MEASURES, "totals": totals, "rows": rows}
    if fmt in ("json", "both"):
        print(json.dumps(result, indent=1))
    if fmt in ("md", "both"):
        print(markdown(result))


COLS = [("done_n", "DONE after chain"), ("done_delay_med_min", "delay med min"), ("done_gt10", "delay >10m"),
        ("denied", "denied"), ("fix_rounds", "fix rounds"), ("retired", "retired"), ("rc_fail", "rc fail"),
        ("asks", "asks"), ("ask_wait_med_min", "ask wait med min"), ("asks_open", "asks open"),
        ("status_answers", "status answers"), ("idle_gpu_h", "idle GPU h (est)"), ("wake_fail", "wake fail")]


def markdown(res):
    head = "| day | team | " + " | ".join(c[1] for c in COLS) + " |"
    lines = [head, "|" + "---|" * (len(COLS) + 2)]
    for r in res["rows"]:
        lines.append(f"| {r['day']} | {r['team']} | " + " | ".join(
            "" if r.get(k) is None else str(r[k]) for k, _ in COLS) + " |")
    t = res["totals"]
    lines.append("| **total** | | " + " | ".join("" if t.get(k) is None else str(t[k]) for k, _ in COLS) + " |")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
