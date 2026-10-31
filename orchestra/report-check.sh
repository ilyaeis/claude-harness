#!/usr/bin/env bash
# Check brain reports against the report schema. Read-only.
# Usage: report-check.sh [--retire] [--quiet] <report.md>...
#   --retire  also require the orchestra fields: agent, role, parent, result, task.
#   --quiet   print only files with errors or warnings.
# Exit 0: no errors (warnings are allowed). Exit 1: at least one error. Exit 2: usage.
# Brain root: ORCHESTRA_BRAIN, else BRAIN_DIR (default ~/brain).
set -euo pipefail
[ $# -ge 1 ] || { echo "usage: report-check.sh [--retire] [--quiet] <report.md>..." >&2; exit 2; }
exec python3 - "$@" <<'PY'
import datetime, difflib, os, re, sys
import yaml

brain = os.environ.get("ORCHESTRA_BRAIN") or os.environ.get("BRAIN_DIR") or os.path.expanduser("~/brain")
reports = os.path.join(brain, "reports")
glossary_path = os.path.join(brain, "_meta", "tags-glossary.md")

args = sys.argv[1:]
retire = "--retire" in args
quiet = "--quiet" in args
files = [a for a in args if a not in ("--retire", "--quiet")]
if not files:
    print("usage: report-check.sh [--retire] [--quiet] <report.md>...", file=sys.stderr)
    sys.exit(2)

glossary = set()
if os.path.isfile(glossary_path):
    for line in open(glossary_path, encoding="utf-8"):
        m = re.match(r"\s*-\s*`([^`]+)`", line)
        if m:
            glossary.add(m.group(1))

VALUES = {
    "status": {"open", "closed"},
    "role": {"worker", "master", "po", "verifier"},
    "result": {"done", "blocked", "partial"},
}
BASE = ["date", "topic", "status", "tags"]
RETIRE = ["agent", "role", "parent", "result", "task"]


def as_list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def check(path):
    err, warn = [], []
    if not os.path.isfile(path):
        return [f"file not found. Fix: give the full path of the report."], warn
    text = open(path, encoding="utf-8").read()
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    if not m:
        return ["no front-matter. Fix: start the file with a '---' block (see the session-report skill)."], warn
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        return [f"front-matter is not valid YAML ({str(e).splitlines()[0]}). Fix: quote values with ':' or '#'."], warn
    if not isinstance(fm, dict):
        return ["front-matter is not a key: value block."], warn

    for k in BASE + (RETIRE if retire else []):
        if fm.get(k) in (None, "", []):
            err.append(f"field '{k}' is missing. Fix: add '{k}:' to the front-matter.")
    for k, ok in VALUES.items():
        v = fm.get(k)
        if v is not None and str(v) not in ok:
            (err if retire or k != "status" else warn).append(
                f"{k}: '{v}' is not one of {sorted(ok)}. Fix: use one of them.")

    d = str(fm.get("date", ""))
    dm = re.match(r"(\d{4}-\d\d-\d\d)[T ](\d\d:\d\d)", d)
    if d and not dm and retire:
        warn.append(f"date '{d}' has no time. Fix: use YYYY-MM-DDTHH:MM from `date +%FT%H:%M`.")
    if dm:
        t = datetime.datetime.fromisoformat(f"{dm.group(1)}T{dm.group(2)}")
        mt = datetime.datetime.fromtimestamp(os.path.getmtime(path))
        if t > mt + datetime.timedelta(minutes=15):
            warn.append(f"date {d} is later than the file time {mt:%Y-%m-%dT%H:%M}. "
                        "Fix: stamp the real time with `date +%FT%H:%M`; do not guess it.")

    folder = os.path.basename(os.path.dirname(os.path.abspath(path)))
    if fm.get("topic") and str(fm["topic"]) != folder:
        warn.append(f"topic '{fm['topic']}' differs from the folder '{folder}'.")

    for link in as_list(fm.get("links")):
        link = str(link)
        here = os.path.dirname(os.path.abspath(path))
        cands = [os.path.expanduser(link)] if link.startswith(("/", "~")) else [
            os.path.join(reports, link), os.path.join(here, link)]
        if not any(os.path.exists(c) for c in cands):
            err.append(f"link '{link}' does not resolve. Fix: use a path relative to {reports}/ (e.g. <topic>/<file>.md).")

    tags = [str(t) for t in as_list(fm.get("tags"))]
    for t in tags:
        if t in glossary:
            continue
        if "/" in t:  # plain tags (e.g. private) are allowed by the glossary
            near = difflib.get_close_matches(t, sorted(glossary), n=3, cutoff=0.0)
            err.append(f"tag '{t}' is not in _meta/tags-glossary.md. Fix: use a glossary tag (closest: {', '.join(near)}), or add it to the glossary with a one-line meaning.")
    if tags:
        n = {f: sum(t.startswith(f + "/") for t in tags) for f in ("type", "topic", "repo", "status")}
        if n["type"] != 1:
            err.append(f"{n['type']} type/* tags. Fix: use exactly one (type/report).")
        if n["topic"] > 3:
            warn.append(f"{n['topic']} topic/* tags (max 3).")
        if n["repo"] > 1 or n["status"] > 1:
            warn.append("more than one repo/* or status/* tag.")
        st = fm.get("status")
        if st in ("open", "closed") and f"status/{st}" not in tags and n["status"]:
            warn.append(f"status tag does not match 'status: {st}'.")
    return err, warn


bad = 0
for f in files:
    # Files nested below reports/<topic>/ are research data, backups and assets, not reports.
    if os.path.relpath(os.path.abspath(f), reports).count(os.sep) > 1:
        if not quiet:
            print(f"SKIP {f}  (nested below reports/<topic>/)")
        continue
    err, warn = check(f)
    bad += bool(err)
    if quiet and not err and not warn:
        continue
    print(f"{'FAIL' if err else 'WARN' if warn else 'OK  '} {f}")
    for e in err:
        print(f"  error: {e}")
    for w in warn:
        print(f"  warn:  {w}")
sys.exit(1 if bad else 0)
PY
