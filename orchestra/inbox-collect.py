#!/usr/bin/env python3
"""Orchestra inbox collector (read-only).

Finds the live teams (tmux session orch-* with a master-* window, like watcher.py),
reads each team's .orchestra/status.md, and extracts the "Waits for the user"
items. Prints a JSON list of items to stdout:
  {id, team, master, text, kind, source, since, media}
media = [{path, kind, label}] for image/video files: paths named in the raw item text
(file or folder; relative to the project or ~), plus rules in inbox/media.json:
  [{"team": "dev", "match": "<text in the item>", "paths": ["<file or folder>", ...]}]
id = <team>-<8 hex of sha1(text)>, so it stays the same while the text stays the same.
Teams without a live tmux session are skipped (retired or idle).

Asks: any Claude session adds items with inbox-ask.sh (stored in inbox/asks.jsonl by the server).
An ask shows while its session is live, until it is withdrawn, and for 1 h after its last answer.
Its group is the team of that master, else the session name. master = the session name.
"""
import datetime
import glob
import hashlib
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from find_pane import session_pid  # noqa: E402

GLOBS = ["~/dev/*/.orchestra", "~/dev/*/*/.orchestra"]
# "Waits for the user: ...", "Waits for user (away; ...): ...", "Pending user: ..."
WAIT_RE = re.compile(r"(?:Waits for (?:the )?user|Pending user)[^:]*:\s*(.*)$", re.I)
NOTHING_RE = re.compile(r"^(nothing\b|none\b|away mode\b)", re.I)
SECRET_RE = re.compile(
    r"(sk-[A-Za-z0-9_-]{16,}|glpat-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}"
    r"|A[K]IA[0-9A-Z]{16}|xox[abposr]-[A-Za-z0-9-]{10,}|\b[A-Za-z0-9+/_-]{40,}\b)")
MEDIA_EXT = {".mp4": "video", ".mov": "video", ".mkv": "video", ".webm": "video", ".avi": "video",
             ".png": "image", ".jpg": "image", ".jpeg": "image", ".webp": "image", ".gif": "image"}
PATH_RE = re.compile(r"[~\w.-]*/[\w./{},-]*|[\w.-]+\.(?:" + "|".join(e[1:] for e in MEDIA_EXT) + r")\b", re.I)
MEDIA_RULES = os.path.expanduser("~/.claude/orchestra/inbox/media.json")
MAX_DIR_FILES = 12
ASKS = os.path.expanduser("~/.claude/orchestra/inbox/asks.jsonl")
ANSWERS = os.path.expanduser("~/.claude/orchestra/inbox/answers.jsonl")
ASK_KEEP_SEC = 3600   # an answered ask stays on the page this long, so its wake status shows
KINDS = [  # first match wins
    ("delete", re.compile(r"\b(delete|rm|remove|clean ?up)\b", re.I)),
    ("user-action", re.compile(r"\b(sudo|api key|key for|login|log in|chrome|extension|password)\b", re.I)),
    ("verdict", re.compile(r"\b(verdict|watch|look at|judge|see|videos?)\b", re.I)),
    ("decision", re.compile(r"(\?|\byes/no\b|\bvs\b|\breview\b|\bdecide|\boption\b|\bapprov)", re.I)),
]


def tmux_teams():
    """Map project dir -> (session, master window)."""
    try:
        out = subprocess.run(["tmux", "list-panes", "-a", "-F",
                              "#{session_name}\t#{window_name}\t#{pane_current_path}"],
                             capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return {}
    teams = {}
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[0].startswith("orch-") and parts[1].startswith("master-"):
            teams[os.path.realpath(parts[2])] = (parts[0], parts[1])
    return teams


def split_items(text):
    """Split on ';' and on '. ' or '? ' before a capital or digit, outside brackets."""
    items, buf, depth, i = [], "", 0, 0
    while i < len(text):
        c = text[i]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth = max(0, depth - 1)
        if depth == 0 and (c == ";" or (c in ".?" and re.match(r"[.?]\s+[A-Z0-9]", text[i:]))):
            items.append(buf)
            buf = ""
            if c == "?":
                items[-1] += "?"
        else:
            buf += c
        i += 1
    items.append(buf)
    return [s.strip(" .") for s in items if s.strip(" .")]


def kind_of(text):
    for kind, rx in KINDS:
        if rx.search(text):
            return kind
    return "other"


def media_files(path):
    """Media files for one existing path: the file itself, or the media files directly in a folder."""
    if os.path.isfile(path):
        names = [path]
    elif os.path.isdir(path):
        names = sorted(os.path.join(path, n) for n in os.listdir(path))[:200]
    else:
        return []
    out = [n for n in names if os.path.isfile(n) and os.path.splitext(n)[1].lower() in MEDIA_EXT]
    return out[:MAX_DIR_FILES]


def find_media(raw, proj, team, rules):
    cands = [m.group(0).rstrip(".,") for m in PATH_RE.finditer(raw)]
    cands += [p for r in rules if r.get("team") == team and r.get("match", "\0") in raw for p in r.get("paths", [])]
    found = []
    for c in cands:
        c = os.path.expanduser(c)
        bases = [""] if os.path.isabs(c) else [proj, os.path.expanduser("~")]
        for b in bases:
            files = media_files(os.path.realpath(os.path.join(b, c)))
            if files:
                found += [f for f in files if f not in found]
                break
    def label(f):
        rel = os.path.relpath(f, proj) if f.startswith(proj + "/") else f.replace(os.path.expanduser("~"), "~", 1)
        return "/".join(SECRET_RE.sub("***", part) for part in rel.split("/"))
    return [{"path": f, "kind": MEDIA_EXT[os.path.splitext(f)[1].lower()], "label": label(f)} for f in found]


def jsonl(path):
    try:
        lines = open(path, encoding="utf-8").readlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def ask_items(teams):
    team_of = {master: session[len("orch-"):] for session, master in teams.values()}
    asks, done = {}, set()
    for r in jsonl(ASKS):
        if r.get("op") == "done":
            done.add(r.get("id"))
        elif r.get("id"):
            asks[r["id"]] = r
    answered = {a.get("id"): a.get("at") for a in jsonl(ANSWERS)}
    now = datetime.datetime.now().astimezone()
    items = []
    for iid, a in asks.items():
        if iid in done or not session_pid(a.get("from")):
            continue
        at = answered.get(iid)
        if at and (now - datetime.datetime.strptime(at, "%Y-%m-%dT%H:%M:%S%z")).total_seconds() > ASK_KEEP_SEC:
            continue
        files = [f for p in a.get("media") or [] for f in media_files(p)]
        text = SECRET_RE.sub("***", a.get("text", ""))
        items.append({"id": iid, "team": team_of.get(a["from"], a["from"]), "master": a["from"], "text": text,
                      "kind": a.get("kind") or kind_of(text), "atype": a.get("atype") or "yesno",
                      "options": a.get("options") or [], "source": ASKS, "since": a.get("at", ""),
                      "media": [{"path": f, "kind": MEDIA_EXT[os.path.splitext(f)[1].lower()],
                                 "label": "/".join(SECRET_RE.sub("***", x) for x in
                                                   f.replace(os.path.expanduser("~"), "~", 1).split("/"))}
                                for f in dict.fromkeys(files)]})
    return items


def main():
    teams = tmux_teams()
    try:
        rules = json.load(open(MEDIA_RULES))
    except (OSError, ValueError):
        rules = []
    items = []
    dirs = sorted({os.path.realpath(d) for g in GLOBS for d in glob.glob(os.path.expanduser(g))})
    for odir in dirs:
        proj = os.path.dirname(odir)
        status = os.path.join(odir, "status.md")
        if proj not in teams or not os.path.isfile(status):
            continue
        session, master = teams[proj]
        team = session[len("orch-"):]
        since = datetime.datetime.fromtimestamp(os.path.getmtime(status)).astimezone().isoformat(timespec="minutes")
        with open(status, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = WAIT_RE.search(line)
                if not m:
                    continue
                for raw in split_items(m.group(1)):
                    if NOTHING_RE.match(raw):
                        continue
                    text = SECRET_RE.sub("***", raw)
                    h = hashlib.sha1(text.encode()).hexdigest()[:8]
                    items.append({"id": f"{team}-{h}", "team": team, "master": master, "text": text,
                                  "kind": kind_of(text), "source": status, "since": since,
                                  "media": find_media(raw, proj, team, rules)})
    items += ask_items(teams)
    print(json.dumps(items, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
