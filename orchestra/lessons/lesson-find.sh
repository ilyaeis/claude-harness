#!/usr/bin/env bash
# Print the top 3 accepted lessons that match the words.
# Usage: lesson-find.sh <words...>
# Score: per query word, +2 if in slug or tags, +1 if in situation, lesson or evidence (word prefix match).
set -euo pipefail
[ $# -ge 1 ] || { echo "usage: lesson-find.sh <words...>" >&2; exit 2; }
exec python3 - "$@" <<'PY'
import glob, os, re, sys
import yaml

LIB = os.path.expanduser(os.environ.get("ORCHESTRA_LESSONS", "~/.claude/orchestra/lessons"))
STOP = {"a", "an", "the", "to", "of", "in", "on", "and", "or", "for", "is", "it", "with", "by", "at"}
query = [w for w in re.findall(r"[a-z0-9]+", " ".join(sys.argv[1:]).lower()) if w not in STOP]


def words(s):
    return set(re.findall(r"[a-z0-9]+", s.lower()))


hits = []
for f in sorted(glob.glob(f"{LIB}/L-[0-9][0-9][0-9]-*.md")):
    m = re.match(r"---\n(.*?)\n---", open(f, encoding="utf-8").read(), re.S)
    fm = yaml.safe_load(m.group(1))
    if fm.get("status") != "accepted":
        continue
    key = words(os.path.basename(f)[6:-3].replace("-", " ") + " " + " ".join(fm.get("tags") or []))
    text = words(f"{fm['situation']} {fm['lesson']} {' '.join(fm['evidence'])}")
    score = 0
    for q in query:
        if any(k.startswith(q) or q.startswith(k) and len(k) > 3 for k in key):
            score += 2
        if any(t.startswith(q) for t in text):
            score += 1
    if score:
        hits.append((-score, fm["id"], f, fm))
for s, i, f, fm in sorted(hits)[:3]:
    print(f"- {i} (score {-s}): {' '.join(str(fm['lesson']).split())}  [{f}]")
PY
