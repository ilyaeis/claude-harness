#!/usr/bin/env python3
"""Teams tab and New-team form of the local inbox (loaded by inbox-server.py). Takes over the PO's team jobs.

teams()            read-only: every live team (tmux orch-* with a master-* window) and every other live Claude session.
                   Per team: goal, key results, open items (from .orchestra/status.md), live workers (registry.json
                   + live session names), GPU set (ORCHESTRA_GPUS in .orchestra/launch-master*.sh), master tokens
                   (ctx_meter.tokens: the number the pane shows), "usage_until" HH:MM if the master or a worker has a
                   USAGE_LIMIT event (events.jsonl) whose reset is still ahead.
gpus()             GPUs on the known hosts: nvidia-smi over ssh (read-only, BatchMode, 8 s), cached 60 s.
                   A host that does not answer is "unknown"; its GPUs come from the launch files.
                   Stale data is served while a background refresh runs.
launch(body, dry)  New team: checks, then writes .orchestra/master-handoff.md + contract.yaml and runs
                   ~/.claude/skills/po/start-master.sh <dir> <name> <gpus>. dry=True: checks only, prints the plan.
                   Real launches are logged to inbox/launches.jsonl.

Usage (test): inbox-teams.py            prints teams() and gpus() as JSON
"""
import datetime
import glob
import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time

ORCH = os.path.expanduser("~/.claude/orchestra")
HOME = os.path.expanduser("~")
DEV = os.path.realpath(HOME + "/dev")
START_MASTER = HOME + "/.claude/skills/po/start-master.sh"
TEMPLATE = ORCH + "/contract.team-template.yaml"
LAUNCHES = ORCH + "/inbox/launches.jsonl"
MAX_MASTERS = 8
GPU_CACHE_SEC = 60
SSH_TIMEOUT = 8


def _conf(key):
    """Value of `key = value` in watcher.conf, or ""."""
    try:
        for line in open(ORCH + "/watcher.conf"):
            k, _, v = line.partition("=")
            if k.strip() == key and not line.lstrip().startswith("#"):
                return v.strip()
    except OSError:
        pass
    return ""


DEFAULT_HOSTS = _conf("gpu_hosts").split()     # GPU servers to show even when no team uses them
SSH_USER = _conf("gpu_ssh_user")               # empty = your ssh default
NAME_RE = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
LABEL = r"[^:(]*(?:\([^)]*\))?[^:]*:\s*(.*)$"     # "Goal (new 15:14, via PO): ..." -> text after the label
KEY_RE = re.compile(r"^\W*Key results\b" + LABEL, re.I)
GOAL_RE = re.compile(r"^\W*Goal\b" + LABEL, re.I)
GPULINE_RE = re.compile(r"^\W*GPUs?\b" + LABEL, re.I)

_spec = importlib.util.spec_from_file_location("collect", ORCH + "/inbox-collect.py")
collector = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(collector)
from find_pane import alive  # noqa: E402  (collector put ORCH on sys.path)
from ctx_meter import tokens  # noqa: E402  (same number as the pane and the watcher)

_gpu_lock = threading.Lock()
_gpu_cache = {"at": 0, "data": None}
launch_lock = threading.Lock()


def clip(s, n):
    s = collector.SECRET_RE.sub("***", re.sub(r"\s+", " ", s).strip())
    return s if len(s) <= n else s[:n - 1] + "…"


def live_sessions():
    """name -> {pid, cwd, sessionId} of every live Claude session (all accounts)."""
    out = {}
    for f in glob.glob(os.path.expanduser("~/.claude*/sessions/*.json")):
        try:
            s = json.load(open(f))
        except (OSError, ValueError):
            continue
        if s.get("name") and isinstance(s.get("pid"), int) and alive(s["pid"]):
            out[s["name"]] = {"pid": s["pid"], "cwd": s.get("cwd", ""), "sid": s.get("sessionId", "")}
    return out


def launch_gpus(odir):
    """GPU ids in ORCHESTRA_GPUS of all launch-master*.sh in this .orchestra dir."""
    out = set()
    for f in glob.glob(odir + "/launch-master*.sh"):
        try:
            lines = open(f).read().splitlines()
        except OSError:
            continue
        for line in lines:
            if "ORCHESTRA_GPUS=" not in line:
                continue
            try:
                words = shlex.split(line)
            except ValueError:
                continue
            for w in words:
                if w.startswith("ORCHESTRA_GPUS="):
                    out.update(g.strip() for g in w.split("=", 1)[1].split(",") if g.strip())
    return out


def read_status(path):
    """goal, key results, gpu line, open items from a status.md."""
    st = {"goal": "", "key": "", "gpu_line": "", "open": [], "status_at": ""}
    try:
        lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
        st["status_at"] = datetime.datetime.fromtimestamp(os.path.getmtime(path)).astimezone().isoformat(timespec="minutes")
    except OSError:
        return st
    for i, line in enumerate(lines):
        if not st["goal"] and (m := GOAL_RE.match(line)):
            st["goal"] = clip(m.group(1), 400)
        elif not st["key"] and (m := KEY_RE.match(line)):
            text = m.group(1)
            if not text.strip():     # header line, results follow as a list
                more = []
                for nxt in lines[i + 1:i + 8]:
                    if not nxt.strip() or nxt.startswith(("|", "#")):
                        break
                    more.append(nxt.strip())
                text = " ".join(more)
            st["key"] = clip(text, 700)
        elif not st["gpu_line"] and (m := GPULINE_RE.match(line)):
            st["gpu_line"] = clip(m.group(1), 200)
        if m := collector.WAIT_RE.search(line):
            st["open"] += [clip(s, 300) for s in collector.split_items(m.group(1)) if not collector.NOTHING_RE.match(s)]
    return st


def usage_limits():
    """name -> reset epoch of USAGE_LIMIT events in the tail of events.jsonl whose reset is still ahead."""
    try:
        with open(ORCH + "/events.jsonl", "rb") as f:
            f.seek(max(0, os.path.getsize(ORCH + "/events.jsonl") - (256 << 10)))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return {}
    out, now = {}, time.time()
    for line in lines:
        if '"USAGE_LIMIT"' not in line:
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if (e.get("reset_epoch") or 0) > now:
            out[e["master"]] = max(out.get(e["master"], 0), e["reset_epoch"])
    return out


def live_teams():
    """List of {team, session, master, dir} for tmux orch-* sessions with a master-* window."""
    return [{"team": s[len("orch-"):], "session": s, "master": m, "dir": d}
            for d, (s, m) in sorted(collector.tmux_teams().items(), key=lambda x: x[1][0])]


def held_gpus(teams_=None):
    """GPU id -> team, for the live teams."""
    held = {}
    for t in teams_ if teams_ is not None else live_teams():
        for g in launch_gpus(t["dir"] + "/.orchestra"):
            held.setdefault(g, t["team"])
    return held


def teams():
    sessions = live_sessions()
    live = live_teams()
    out, used = [], set()
    limits = usage_limits()
    for t in live:
        odir = t["dir"] + "/.orchestra"
        st = read_status(odir + "/status.md")
        try:
            reg = json.load(open(odir + "/registry.json"))
        except (OSError, ValueError):
            reg = []
        workers = []
        for r in reg if isinstance(reg, list) else []:
            n = r.get("name")
            if n in sessions and r.get("status") != "retired":
                workers.append({"name": n, "task": os.path.basename(r.get("task") or ""), "gpu": r.get("gpu"),
                                "tokens": tokens(sessions[n]["sid"])})
                used.add(n)
        for n in sessions:     # live workers the registry misses
            if n.startswith(t["team"] + "-") and n not in used:
                workers.append({"name": n, "task": "", "gpu": None, "tokens": tokens(sessions[n]["sid"])})
                used.add(n)
        m = sessions.get(t["master"])
        used.add(t["master"])
        until = max([limits.get(n, 0) for n in [t["master"]] + [w["name"] for w in workers]])
        out.append({**t, **st, "dir": t["dir"].replace(HOME, "~", 1), "gpus": sorted(launch_gpus(odir)),
                    "usage_until": time.strftime("%H:%M", time.localtime(until)) if until else None,
                    "master_live": bool(m), "master_tokens": tokens(m["sid"]) if m else None, "workers": workers})
    others = []
    for n, s in sorted(sessions.items()):
        if n in used:
            continue
        st = read_status(s["cwd"] + "/.orchestra/status.md") if s["cwd"] else {}
        others.append({"name": n, "dir": s["cwd"].replace(HOME, "~", 1), "tokens": tokens(s["sid"]),
                       "goal": st.get("goal", ""), "open": st.get("open", [])})
    masters = {t["master"] for t in live} | {n for n in sessions if n.startswith("master-")}
    return {"teams": out, "others": others, "masters": len(masters), "max_masters": MAX_MASTERS}


def known_hosts():
    hosts = set(DEFAULT_HOSTS)
    dirs = {os.path.realpath(d) for g in collector.GLOBS for d in glob.glob(os.path.expanduser(g))}
    for d in dirs:
        hosts.update(g.rsplit(":", 1)[0] for g in launch_gpus(d) if ":" in g)
    return sorted(hosts), dirs


def smi(host):
    try:
        p = subprocess.run(["ssh", "-o", "GSSAPIAuthentication=no", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={SSH_TIMEOUT}", f"{SSH_USER}@{host}" if SSH_USER else host,
                            "nvidia-smi --query-gpu=index,memory.used,memory.total,utilization.gpu --format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=SSH_TIMEOUT + 2, stdin=subprocess.DEVNULL)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if p.returncode != 0:
        return None
    rows = []
    for line in p.stdout.splitlines():
        parts = [x.strip() for x in line.split(",")]
        if len(parts) == 4 and all(x.isdigit() for x in parts):
            rows.append({"index": int(parts[0]), "mem_used": int(parts[1]), "mem_total": int(parts[2]), "util": int(parts[3])})
    return rows


def gpus():
    """{hosts: [{host, state: ok|unknown, gpus: [{id, index, held_by, mem_used, mem_total, util}]}], at}. Cached 60 s."""
    if _gpu_cache["data"] is None:
        refresh_gpus()
    elif time.time() - _gpu_cache["at"] >= GPU_CACHE_SEC and _gpu_lock.acquire(blocking=False):
        _gpu_lock.release()
        threading.Thread(target=refresh_gpus, daemon=True).start()   # serve the old data meanwhile
    data = _gpu_cache["data"]
    held = held_gpus()
    out = []
    for h in data:
        rows = []
        for g in h["gpus"]:
            gid = f"{h['host']}:{g['index']}"
            rows.append({**g, "id": gid, "held_by": held.get(gid)})
        out.append({**h, "gpus": rows})
    return {"hosts": out, "at": datetime.datetime.fromtimestamp(_gpu_cache["at"]).astimezone().isoformat(timespec="seconds")}


def refresh_gpus():
    with _gpu_lock:
        if _gpu_cache["data"] is None or time.time() - _gpu_cache["at"] >= GPU_CACHE_SEC:
            hosts, dirs = known_hosts()
            res = {}
            threads = [threading.Thread(target=lambda h=h: res.__setitem__(h, smi(h))) for h in hosts]
            for th in threads:
                th.start()
            for th in threads:
                th.join()
            seen = {g for d in dirs for g in launch_gpus(d)}
            data = []
            for h in hosts:
                rows = res.get(h)
                if rows is None:
                    idx = sorted({int(g.rsplit(":", 1)[1]) for g in seen if g.rsplit(":", 1)[0] == h and g.rsplit(":", 1)[1].isdigit()})
                    rows = [{"index": i} for i in idx]
                data.append({"host": h, "state": "unknown" if res.get(h) is None else "ok", "gpus": rows})
            _gpu_cache.update(at=time.time(), data=data)


def handoff_text(name, dirpath, goal, gpu_list):
    gl = ", ".join(gpu_list) if gpu_list else "none"
    return f"""# Handoff: master-{name} (written {time.strftime('%Y-%m-%d %H:%M')} by the inbox New-team form)
Session/tmux: master-{name} / orch-{name}. Workers: {name}-*. Project dir: {dirpath}. Model: Opus 5.5.

## Start
- Run `/orchestrate`. Read this file first. Make the plan with the user in your window. Wait for the user's OK.
- You work for the user directly. Do not report to the PO.
- To ask the user: use the `inbox` skill (`~/.claude/orchestra/inbox-ask.sh`). Also keep the "Waits for the user:" line in `.orchestra/status.md` current.
- Keep `.orchestra/status.md` with the lines `Goal:`, `Key results:`, `Waits for the user:` and `GPUs:`. The inbox Teams tab reads them.
- Channel contract: `.orchestra/contract.yaml`.

## Goal
{goal}

## GPU set
{gl}
Use only these GPUs. Never use a GPU of another team. Ask the user for more.

## Resource rules
- Run training and long jobs on the GPU servers, not on the desktop.
- Start long server jobs only through `~/.claude/orchestra/chain-run.sh`.
- Keep server data in one team dir per server. Write its size in status.md. Ask the user before you delete data outside a run's own tmp dir.
- Tests: never `pytest -n auto`. Use `-n 4` or less, wrapped in `flock ~/.claude/orchestra/test.lock`.
- No push to main, no merge, no paid API, no prod change without the user's OK.
"""


def contract_text(name, gpu_list):
    hosts = sorted({g.rsplit(":", 1)[0] for g in gpu_list})
    t = open(TEMPLATE).read()
    t = re.sub(r"^team: .*$", f"team: {name}", t, flags=re.M)
    t = re.sub(r"^master: .*$", f"master: master-{name}", t, flags=re.M)
    t = re.sub(r"^workers: .*$", f"workers: {name}-*", t, flags=re.M)
    t = re.sub(r"^gpus: \[\]", "gpus: [" + ", ".join(gpu_list) + "]", t, flags=re.M)
    t = re.sub(r"^allowed_hosts: \[\]", "allowed_hosts: [" + ", ".join(hosts) + "]", t, flags=re.M)
    return t


def launch(body, dry, requester):
    """Return (http code, result dict). result: {ok, errors, plan, output}."""
    try:
        name = str(body.get("name") or "").strip()
        raw_dir = str(body.get("dir") or "").strip()
        goal = str(body.get("goal") or "").strip()
        sel = [str(g).strip() for g in (body.get("gpus") or [])]
        confirm = str(body.get("confirm") or "").strip().lower()
    except AttributeError:
        return 400, {"ok": False, "errors": ["bad request"]}
    errors = []
    if not NAME_RE.fullmatch(name) or len(name) > 24:
        errors.append("name: use a short slug: a-z, 0-9 and single '-', start with a letter, max 24 chars")
    if name.endswith("-w"):
        errors.append("name: must not end in '-w' (tmux orch-<team>-w is the team's hidden worker session)")
    live = live_teams()
    sessions = live_sessions()
    if name and (any(t["team"] == name for t in live)
                 or subprocess.run(["tmux", "has-session", "-t", f"=orch-{name}"], capture_output=True).returncode == 0
                 or f"master-{name}" in sessions):
        errors.append(f"name in use: tmux orch-{name} or session master-{name} is live")
    d = os.path.realpath(os.path.expanduser(raw_dir)) if raw_dir else ""
    if not d or not os.path.isdir(d):
        errors.append(f"dir missing: {raw_dir or '(empty)'}")
    elif not (d.startswith(DEV + "/") and d.count("/") - DEV.count("/") <= 2):
        errors.append("dir must be ~/dev/<project> or ~/dev/<group>/<project> (the inbox reads only those)")
    elif any(os.path.realpath(t["dir"]) == d for t in live):
        errors.append("dir already has a live team")
    if not goal:
        errors.append("goal: write the goal")
    elif len(goal) > 4000:
        errors.append("goal: max 4000 chars")
    sel = list(dict.fromkeys(sel))
    known = {g["id"] for h in gpus()["hosts"] for g in h["gpus"]}
    held = held_gpus(live)
    for g in sel:
        if g not in known:
            errors.append(f"GPU {g}: not a known GPU")
        elif g in held:
            errors.append(f"GPU {g}: held by live team {held[g]}")
    masters = {t["master"] for t in live} | {n for n in sessions if n.startswith("master-")}
    if len(masters) >= MAX_MASTERS:
        errors.append(f"{len(masters)} live masters: the limit is {MAX_MASTERS}")
    if not dry and confirm != "yes":
        errors.append(f'confirm: answer "Start master-{name}? yes"')
    gcsv = ",".join(sel)
    odir = d + "/.orchestra" if d else ""
    cmd = [START_MASTER, d, name, gcsv]
    plan = {"write": [odir + "/master-handoff.md", odir + "/contract.yaml"],
            "backup": [p for p in (odir + "/master-handoff.md", odir + "/contract.yaml") if odir and os.path.exists(p)],
            "run": " ".join(shlex.quote(c) for c in cmd), "tmux": f"orch-{name}:master-{name}",
            "attach": f"tmux attach -t orch-{name}", "gpus": sel,
            "handoff": handoff_text(name, d, goal, sel) if not errors else "",
            "contract": contract_text(name, sel) if not errors else ""}
    if errors:
        return 409, {"ok": False, "dry": dry, "errors": errors, "plan": plan}
    if dry:
        return 200, {"ok": True, "dry": True, "errors": [], "plan": plan}
    with launch_lock:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        os.makedirs(odir, exist_ok=True)
        for p in plan["backup"]:
            os.replace(p, p[:-len(os.path.splitext(p)[1])] + f".{stamp}" + os.path.splitext(p)[1])
        with open(odir + "/master-handoff.md", "w") as f:
            f.write(plan["handoff"])
        with open(odir + "/contract.yaml", "w") as f:
            f.write(plan["contract"])
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
            rc, output = p.returncode, (p.stdout + p.stderr).strip()
        except (subprocess.TimeoutExpired, OSError) as e:
            rc, output = -1, str(e)
        if rc == 0:
            _gpu_cache["at"] = 0
        with open(LAUNCHES, "a") as f:
            f.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "name": name, "dir": d, "gpus": sel,
                                "requester": requester, "rc": rc, "output": output[-500:]}) + "\n")
    return (200 if rc == 0 else 500), {"ok": rc == 0, "dry": False, "errors": [] if rc == 0 else [output], "plan": plan,
                                       "output": output}


if __name__ == "__main__":
    print(json.dumps({"teams": teams(), "gpus": gpus()}, indent=1, ensure_ascii=False))
