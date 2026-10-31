#!/usr/bin/env bash
# Link a finished report into the brain and the team dir.
# Usage: report-link.sh [--dry-run] <report.md>
#   1. Adds the reverse link (this report) to the links: list of each report in its links:.
#   2. Refreshes the topic line in reports/INDEX.md: status, "newest: <file>", moves it to the top.
#      A new topic gets a new line with the report title as the hook.
#   3. Puts a symlink to the report in <project>/.orchestra/reports/ (only if <project>/.orchestra exists).
# --dry-run prints the diffs and writes nothing.
# Brain root: ORCHESTRA_BRAIN, else BRAIN_DIR (default ~/brain). Project: ORCHESTRA_PROJECT or the current dir.
set -euo pipefail
. "$HOME/.claude/orchestra/lib.sh"
[ $# -ge 1 ] || { echo "usage: report-link.sh [--dry-run] <report.md>" >&2; exit 2; }
brain="${ORCHESTRA_BRAIN:-${BRAIN_DIR:-$HOME/brain}}"
mkdir -p "$brain/reports"
# One writer at a time for INDEX.md and the parent reports.
exec 9>"$ORCH_HOME/report-link.lock"
flock -w 30 9 || { echo "report-link: INDEX.md lock busy for 30 s. Try again." >&2; exit 1; }
ORCH_DIR="$ORCH_DIR" exec python3 - "$brain" "$@" <<'PY'
import difflib, os, re, sys, tempfile
import yaml

brain = sys.argv[1]
args = sys.argv[2:]
dry = "--dry-run" in args
paths = [a for a in args if a != "--dry-run"]
if len(paths) != 1:
    sys.exit("usage: report-link.sh [--dry-run] <report.md>")
reports = os.path.join(brain, "reports")
report = os.path.abspath(paths[0])
if not os.path.isfile(report):
    sys.exit(f"report not found: {report}")
if not report.startswith(reports + "/"):
    sys.exit(f"report is not under {reports}/. Fix: write it to {reports}/<topic>/<NN>-<slug>.md")
rel = os.path.relpath(report, reports)            # <topic>/<file>.md
text = open(report, encoding="utf-8").read()
m = re.match(r"---\n(.*?)\n---\n", text, re.S)
fm = (yaml.safe_load(m.group(1)) if m else None) or {}
topic = str(fm.get("topic") or os.path.dirname(rel))
status = str(fm.get("status") or "open")


def write(path, old, new):
    if old == new:
        return
    diff = difflib.unified_diff(old.splitlines(True), new.splitlines(True), path, path + " (new)")
    if dry:
        sys.stdout.writelines(diff)
        return
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".rl.")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(new)
    os.chmod(tmp, os.stat(path).st_mode & 0o777 if os.path.exists(path) else 0o644)
    os.replace(tmp, path)
    print(f"updated {path}")


def add_link(path, link):
    """Add link to the links: list in the front-matter of path. Keeps the rest of the text."""
    old = open(path, encoding="utf-8").read()
    fmm = re.match(r"(---\n)(.*?\n)(---\n)", old, re.S)
    if not fmm:
        print(f"skip {path}: no front-matter")
        return
    body = fmm.group(2)
    lines = body.splitlines(True)
    for i, ln in enumerate(lines):
        if not ln.startswith("links:"):
            continue
        val = ln[len("links:"):].strip()
        if val.startswith("["):                       # inline list
            items = [x.strip() for x in val.strip("[]").split(",") if x.strip()]
            if link in items:
                return
            lines[i] = "links: [" + ", ".join(items + [link]) + "]\n"
        else:                                         # block list ("- x" lines below)
            j = i + 1
            while j < len(lines) and re.match(r"\s*- ", lines[j]):
                if lines[j].split("- ", 1)[1].strip() == link:
                    return
                j += 1
            lines.insert(j, f"  - {link}\n")
        break
    else:
        k = next((i for i, ln in enumerate(lines) if ln.startswith("tags:")), len(lines))
        lines.insert(k, f"links: [{link}]\n")
    write(path, old, old[:fmm.end(1)] + "".join(lines) + old[fmm.start(3):])


# 1. Reverse links.
for link in fm.get("links") or []:
    link = str(link)
    target = os.path.expanduser(link) if link.startswith(("/", "~")) else os.path.join(reports, link)
    if not os.path.isfile(target):
        print(f"skip link {link}: file not found")
        continue
    back = os.path.relpath(report, reports) if os.path.abspath(target).startswith(reports + "/") else report
    add_link(target, back)

# 2. INDEX.md topic line.
index = os.path.join(reports, "INDEX.md")
old = open(index, encoding="utf-8").read() if os.path.exists(index) else "# Reports index\n\nOne line per topic folder in `reports/`.\n\n"
lines = old.splitlines(True)
prefix = f"- [{topic}]({topic}/)"
pos = next((i for i, ln in enumerate(lines) if ln.startswith(prefix)), None)
newest = f"newest: {os.path.basename(report)}"
if pos is None:
    title = next((ln[2:].strip() for ln in text[m.end() if m else 0:].splitlines() if ln.startswith("# ")), os.path.basename(report))
    line = f"{prefix} — {title[:150]} · {newest} — status: {status}\n"
else:
    line = lines.pop(pos).rstrip("\n")
    line = re.sub(r" — status: [^—]*$", "", line)
    line = re.sub(r" · newest: \S+", "", line)
    line = f"{line} · {newest} — status: {status}\n"
first = next((i for i, ln in enumerate(lines) if ln.startswith("- [")), len(lines))
lines.insert(first, line)
write(index, old, "".join(lines))

# 3. Symlink in the team dir.
orch = os.environ.get("ORCH_DIR", "")
if orch and os.path.isdir(orch):
    d = os.path.join(orch, "reports")
    ln = os.path.join(d, os.path.basename(report))
    if os.path.islink(ln) and os.readlink(ln) == report:
        pass
    elif os.path.lexists(ln):
        print(f"skip symlink: {ln} exists and is not a link to {report}")
    elif dry:
        print(f"would link {ln} -> {report}")
    else:
        os.makedirs(d, exist_ok=True)
        os.symlink(report, ln)
        print(f"linked {ln} -> {report}")
PY
