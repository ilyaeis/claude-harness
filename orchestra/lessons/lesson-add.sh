#!/usr/bin/env bash
# Add a lesson to the library, or add evidence to an existing one (same slug).
# Usage: lesson-add.sh --slug <kebab> --situation "<when>" --lesson "<1-3 lines>"
#                      --evidence "<path>[:line] <note>" [--evidence ...]
#                      [--tags a,b] [--metric "<name>: <before> -> <after>"] [--merged "<note>"]
# Evidence gate: refuses unless at least one evidence starts with a path that exists.
# Evidence with a missing path is refused too (fix or drop it).
# Status: candidate. accepted when a metric contrast is given, or when 2+ evidences name different files.
# Writes L-<nnn>-<slug>.md in this folder and rebuilds INDEX.md.
set -euo pipefail
[ $# -ge 1 ] || { sed -n "2,9p" "$0" | sed "s/^# \{0,1\}//"; exit 2; }
exec python3 - "$@" <<'PY'
import glob, os, re, sys
import yaml

LIB = os.path.expanduser(os.environ.get("ORCHESTRA_LESSONS", "~/.claude/orchestra/lessons"))
args, opt = sys.argv[1:], {"evidence": []}
i = 0
while i < len(args):
    k = args[i].lstrip("-")
    if k not in ("slug", "situation", "lesson", "evidence", "tags", "metric", "merged") or i + 1 >= len(args):
        sys.exit(f"bad argument: {args[i]}. See the usage in {sys.argv[0]}")
    if k == "evidence":
        opt["evidence"].append(args[i + 1].strip())
    else:
        opt[k] = args[i + 1].strip()
    i += 2


def ev_path(e):
    """The file named at the start of an evidence string, without a :line suffix."""
    p = os.path.expanduser(e.split()[0]) if e.split() else ""
    return re.sub(r":\d+(-\d+)?$", "", p)


def accepted(fm):
    files = {ev_path(e) for e in fm.get("evidence", [])}
    return bool(fm.get("metric")) or len(files) >= 2


if not opt["evidence"]:
    sys.exit("REFUSED: no --evidence. A lesson needs at least one evidence path that exists.")
bad = [e for e in opt["evidence"] if not os.path.exists(ev_path(e))]
if bad:
    sys.exit("REFUSED: evidence path not found: " + "; ".join(bad))
slug = opt.get("slug", "")
if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", slug):
    sys.exit("REFUSED: --slug must be kebab-case (a-z, 0-9, -).")

files = sorted(glob.glob(f"{LIB}/L-[0-9][0-9][0-9]-*.md"))
same = [f for f in files if re.match(rf"L-\d{{3}}-{re.escape(slug)}\.md$", os.path.basename(f))]


def read(f):
    m = re.match(r"---\n(.*?)\n---\n?(.*)", open(f, encoding="utf-8").read(), re.S)
    return yaml.safe_load(m.group(1)), m.group(2)


def write(f, fm, body):
    with open(f, "w", encoding="utf-8") as fh:
        fh.write("---\n" + yaml.safe_dump(fm, sort_keys=False, allow_unicode=True, width=200) + "---\n" + body)


if same:
    f = same[0]
    fm, body = read(f)
    new = [e for e in opt["evidence"] if e not in fm["evidence"]]
    fm["evidence"] += new
    if opt.get("metric"):
        fm["metric"] = opt["metric"]
    if opt.get("merged"):
        fm.setdefault("merged", []).append(opt["merged"])
    if fm.get("status") == "candidate" and accepted(fm):
        fm["status"] = "accepted"
    write(f, fm, body)
    print(f"UPDATED {f}: +{len(new)} evidence, status {fm['status']}")
else:
    for k in ("situation", "lesson"):
        if not opt.get(k):
            sys.exit(f"REFUSED: --{k} is missing.")
    if len(opt["lesson"].splitlines()) > 3:
        sys.exit("REFUSED: --lesson must be 1-3 lines.")
    n = max([int(os.path.basename(f)[2:5]) for f in files] + [0]) + 1
    fm = {"id": f"L-{n:03d}", "situation": opt["situation"], "lesson": opt["lesson"],
          "evidence": opt["evidence"], "metric": opt.get("metric") or None, "status": "candidate",
          "tags": [t.strip() for t in opt.get("tags", "").split(",") if t.strip()]}
    if opt.get("merged"):
        fm["merged"] = [opt["merged"]]
    if accepted(fm):
        fm["status"] = "accepted"
    f = f"{LIB}/L-{n:03d}-{slug}.md"
    write(f, fm, "")
    files.append(f)
    print(f"ADDED {f}: status {fm['status']}")

# Rebuild INDEX.md.
rows = []
for f in sorted(glob.glob(f"{LIB}/L-[0-9][0-9][0-9]-*.md")):
    fm, _ = read(f)
    first = str(fm["lesson"]).splitlines()[0]
    rows.append(f"| [{fm['id']}]({os.path.basename(f)}) | {fm['status']} | {len(fm['evidence'])} | "
                f"{', '.join(fm.get('tags') or [])} | {first} |")
with open(f"{LIB}/INDEX.md", "w", encoding="utf-8") as fh:
    fh.write("# Lessons library\n\nOne file per lesson. Add with `lesson-add.sh` (evidence gate). "
             "Find with `lesson-find.sh <words>` (accepted only).\n"
             "Status: candidate = 1 evidence; accepted = metric contrast or 2+ evidence files; retired = wrong or obsolete (edit by hand).\n\n"
             "| id | status | evidence | tags | lesson |\n|---|---|---|---|---|\n" + "\n".join(rows) + "\n")
PY
