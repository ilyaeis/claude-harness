#!/usr/bin/env python3
"""Harness2 improve loop for one orchestra skill: Propose -> Probe -> Compose. Never applies the change.

Usage: improve.sh [--skill orchestrate|worker|inbox|session-report] [--budget N] [--model haiku|sonnet]
                  [--reps 3] [--max-candidates 2] [--current-from <suite dir>] [--from <session>] [--no-ask]
  Propose  one sonnet run: metrics (7 d), lessons INDEX and the skill -> up to 2 candidates
           (one "invariant" = process rule, one "domain" = team fact), each <= 15 changed lines.
  Probe    the probe suite (probes/probes.py) on current and each candidate, only probes that read this
           skill, --reps runs each (default 3). --budget = max probe runs (default 18); too small -> exit.
           --current-from reuses the current skill's runs from a `probes.py suite` dir (same model, same skill).
  Win      a candidate wins only if its total passes beat current by >= 2 and no probe drops by >= 2 passes.
  Compose  one sonnet run picks keep / A / B / merge among the winners. No winner -> keep, no compose run.
  Output   h2/runs/<time>/: current.md, cand-*.md, cand-*.diff, scores.md, rationale.md, next.md, proposal.diff.
           Then an inbox ask (Apply|Reject|Explain) and one EXPERIENCE note per supported change (lesson-add.sh).
On "Apply", the master backs up the skill and applies proposal.diff with `patch`. Nothing is applied here.
"""
import argparse, datetime, difflib, json, os, subprocess, sys, time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "probes"))
import probes  # noqa: E402

H2, ORCH, SKILLS = probes.H2, probes.ORCH, probes.SKILLS
MAX_LINES = 15
WIN_GAIN, MAX_DROP = 2, 2   # in passes (k runs per probe): one run of haiku noise must not decide


def wins(cur, cand):
    """cur, cand: {probe: passes}. True when cand is better by >= WIN_GAIN passes and no probe drops by >= MAX_DROP."""
    return (sum(cand.values()) - sum(cur.values()) >= WIN_GAIN
            and all(cur[p] - cand[p] < MAX_DROP for p in cur))

CAND_SCHEMA = {
    "type": "object", "required": ["candidates"],
    "properties": {"candidates": {"type": "array", "maxItems": 2, "items": {
        "type": "object", "required": ["kind", "title", "rationale", "lessons", "edits"],
        "properties": {
            "kind": {"enum": ["invariant", "domain"]},
            "title": {"type": "string"},
            "rationale": {"type": "string"},
            "lessons": {"type": "array", "items": {"type": "string"}},
            "edits": {"type": "array", "items": {"type": "object", "required": ["old", "new"],
                      "properties": {"old": {"type": "string"}, "new": {"type": "string"}}}}}}}}}
COMPOSE_SCHEMA = {"type": "object", "required": ["decision", "rationale"],
                  "properties": {"decision": {"enum": ["keep", "A", "B", "merge"]}, "rationale": {"type": "string"}}}


def claude_json(prompt, schema, cwd, label):
    cmd = ["claude", "-p", prompt, "--model", "sonnet", "--json-schema", json.dumps(schema),
           "--output-format", "json", "--setting-sources", "project", "--strict-mcp-config",
           "--disable-slash-commands", "--no-session-persistence", "--max-budget-usd", "2",
           "--disallowedTools", "Bash Edit Write NotebookEdit " + probes.NO_TOOLS]
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "ORCHESTRA"))}
    if os.environ.get("CLAUDE_CONFIG_DIR"):
        env["CLAUDE_CONFIG_DIR"] = os.environ["CLAUDE_CONFIG_DIR"]
    with open(probes.RUNLOG, "a") as f:
        f.write(f"{time.strftime('%FT%T')} {label} sonnet {cwd}\n")
    p = subprocess.run(["timeout", "600", *cmd], cwd=cwd, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    open(f"{cwd}/{label}.raw.json", "w").write(p.stdout)
    out = json.loads(p.stdout)
    if isinstance(out, list):  # --verbose style output: a list of events, the last "result" holds the answer
        out = [e for e in out if e.get("type") == "result"][-1]
    so = out.get("structured_output")
    if so is None:
        r = out.get("result", "")
        so = json.loads(r[r.find("{"):r.rfind("}") + 1])
    return so, out.get("total_cost_usd", 0)


def apply_edits(text, edits):
    for e in edits:
        if text.count(e["old"]) != 1:
            raise ValueError(f"'old' text found {text.count(e['old'])} times (need 1): {e['old'][:60]!r}")
        text = text.replace(e["old"], e["new"])
    return text


def udiff(a, b, name):
    return "".join(difflib.unified_diff(a.splitlines(True), b.splitlines(True), f"a/{name}", f"b/{name}"))


def changed_lines(diff):
    return sum(1 for l in diff.splitlines() if l[:1] in "+-" and not l.startswith(("+++", "---")))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skill", default="orchestrate", choices=["orchestrate", "worker", "inbox", "session-report"])
    ap.add_argument("--budget", type=int, default=18)
    ap.add_argument("--model", default="haiku", choices=["haiku", "sonnet"])
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--max-candidates", type=int, default=2, choices=[1, 2])
    ap.add_argument("--current-from", default="", help="suite dir with <probe>-<r>/score.json runs of the current skill")
    ap.add_argument("--from", dest="sender", default="")
    ap.add_argument("--no-ask", action="store_true")
    a = ap.parse_args()

    pids = probes.probes_for(a.skill)
    need = (a.max_candidates + (not a.current_from)) * len(pids) * a.reps
    if need > a.budget:
        sys.exit(f"budget {a.budget} < {need} probe runs needed (up to {a.max_candidates} candidates{' + current' * (not a.current_from)}"
                 f" x {len(pids)} probes x {a.reps} reps). Raise --budget, or use --current-from.")
    run = f"{H2}/runs/{time.strftime('%Y%m%d-%H%M%S')}-{a.skill}"
    os.makedirs(run)
    skill_path = f"{SKILLS}/{a.skill}/SKILL.md"
    cur = open(skill_path).read()
    open(f"{run}/current.md", "w").write(cur)
    cost = {"runs": 0, "usd": 0.0}

    # ---- Propose
    since = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()
    metrics = subprocess.run([sys.executable, f"{H2}/metrics.py", "--since", since, "--md"],
                             capture_output=True, text=True).stdout
    lessons = open(f"{ORCH}/lessons/INDEX.md").read()
    probe_list = "\n".join(f"- {k}: {probes.PROBES[k]['name']}" for k in pids)
    prompt = f"""You improve one skill of a Claude Code orchestra harness (masters spawn workers in tmux; workers run tasks and report).
Read the inputs below. Propose up to {a.max_candidates} small candidate change(s) to the skill `{a.skill}` (if only 1, pick the kind with the clearest gain):
- one "invariant": a process rule (how to act), and
- one "domain": a team-specific fact (a path, a name, a number, a known pitfall).
Base each candidate on accepted lessons that the skill does NOT reflect yet, or on a bad metric. Name the lesson ids.
Each candidate: a list of exact edits {{old, new}}. "old" must be a verbatim substring that occurs exactly once in the skill
(to insert, use an existing line as "old" and that line plus your new lines as "new"). At most {MAX_LINES} changed lines per candidate.
Write in plain English, ASD-STE100 style: short sentences, one idea each, active voice. Do not add options for future needs.
Behaviour probes that test this skill (the candidates are scored on them, against the current skill):
{probe_list}

<skill name="{a.skill}">
{cur}
</skill>

<metrics since="{since}">
{metrics}
</metrics>

<lessons>
{lessons}
</lessons>"""
    open(f"{run}/propose.prompt.md", "w").write(prompt)
    schema = json.loads(json.dumps(CAND_SCHEMA))
    schema["properties"]["candidates"]["maxItems"] = a.max_candidates
    prop, usd = claude_json(prompt, schema, run, "propose")
    cost["runs"] += 1; cost["usd"] += usd
    json.dump(prop, open(f"{run}/propose.json", "w"), indent=1)

    variants = {"current": {"text": cur, "path": f"{run}/current.md"}}
    rejected = []
    for letter, c in zip("AB", prop.get("candidates", [])[:a.max_candidates]):
        try:
            text = apply_edits(cur, c["edits"])
        except ValueError as e:
            rejected.append(f"{letter} ({c['kind']}): edit does not apply: {e}")
            continue
        diff = udiff(cur, text, f"{a.skill}/SKILL.md")
        n = changed_lines(diff)
        if n == 0 or n > MAX_LINES:
            rejected.append(f"{letter} ({c['kind']}): {n} changed lines (allowed 1..{MAX_LINES})")
            continue
        open(f"{run}/cand-{letter}.md", "w").write(text)
        open(f"{run}/cand-{letter}.diff", "w").write(diff)
        variants[letter] = {"text": text, "path": f"{run}/cand-{letter}.md", "cand": c, "lines": n}

    # ---- Probe
    reps = a.reps
    scores = {}
    for v, info in variants.items():
        if v == "current" and a.current_from:
            res = [json.load(open(f"{a.current_from}/{pid}-{r}/score.json")) for pid in pids for r in range(reps)]
            open(f"{run}/current-from.txt", "w").write(a.current_from + "\n")
        else:
            override = {} if v == "current" else {a.skill: info["path"]}
            res = probes.suite(pids, override, a.model, f"{run}/probe-{v}", jobs=3, reps=reps)
            cost["runs"] += len(res); cost["usd"] += sum(r["cost"] for r in res)
        scores[v] = {pid: [r for r in res if r["probe"] == pid] for pid in pids}
    passes = {v: {pid: sum(r["score"] for r in s[pid]) for pid in pids} for v, s in scores.items()}
    lines = [f"Probe model {a.model}, {reps} run(s) per probe and variant. Cell = passes/{reps}.",
             f"Win rule: total >= current + {WIN_GAIN} passes, and no probe drops by >= {MAX_DROP} passes.\n",
             "| variant | " + " | ".join(pids) + " | total | wins | notes |", "|---|" + "---|" * (len(pids) + 3)]
    for v in variants:
        notes = "; ".join(f"{pid}: {r['notes']}" for pid in pids for r in scores[v][pid])
        win = "-" if v == "current" else ("yes" if wins(passes["current"], passes[v]) else "no")
        lines.append(f"| {v} | " + " | ".join(f"{passes[v][p]}/{reps}" for p in pids) +
                     f" | {sum(passes[v].values())}/{reps * len(pids)} | {win} | {notes} |")
    table = "\n".join(lines)
    open(f"{run}/scores.md", "w").write(table + "\n")

    # ---- Compose (only winners)
    ok = [v for v in variants if v != "current" and wins(passes["current"], passes[v])]
    dropped = [v for v in variants if v != "current" and v not in ok]
    if not ok:
        dec = {"decision": "keep", "rationale": f"No candidate won (total +{WIN_GAIN} passes, no probe -{MAX_DROP})."}
    else:
        blocks = "\n\n".join(f"<candidate id=\"{v}\" kind=\"{variants[v]['cand']['kind']}\" title=\"{variants[v]['cand']['title']}\">\n"
                             f"Rationale: {variants[v]['cand']['rationale']}\nLessons: {', '.join(variants[v]['cand']['lessons'])}\n"
                             f"<diff>\n{open(run + f'/cand-{v}.diff').read()}</diff>\n</candidate>" for v in ok)
        cprompt = f"""You compose the next version of the orchestra skill `{a.skill}`.
Below: the current skill, candidate diffs, and probe scores (current vs candidates; a higher score is better).
Choose one: "keep" (current skill), "A" or "B" (adopt one candidate), or "merge" (both). Only the candidates below are allowed;
each one won: total passes >= current + {WIN_GAIN}, no probe down by >= {MAX_DROP}. Prefer changes that raised a score or that a lesson clearly supports. Reject changes that only add words.
Give a short rationale (3-6 sentences, plain English): which probe moved, and why the change should help in real teams.

<skill name="{a.skill}">
{cur}
</skill>

{blocks}

<scores>
{table}
</scores>"""
        dec, usd = claude_json(cprompt, COMPOSE_SCHEMA, run, "compose")
        cost["runs"] += 1; cost["usd"] += usd
        if dec["decision"] not in ["keep", "merge", *ok] or (dec["decision"] == "merge" and len(ok) < 2):
            dec = {"decision": "keep", "rationale": f"compose chose '{dec['decision']}', which is not allowed. " + dec["rationale"]}
    chosen = {"keep": [], "merge": ok}.get(dec["decision"], [dec["decision"]])
    nxt = cur
    try:
        for v in chosen:
            nxt = apply_edits(nxt, variants[v]["cand"]["edits"])
    except ValueError as e:  # the two candidates clash: take the first one only
        chosen = chosen[:1]; nxt = variants[chosen[0]]["text"]
        dec["rationale"] += f" (merge failed: {e}; kept {chosen[0]} only)"
    open(f"{run}/next.md", "w").write(nxt)
    diff = udiff(cur, nxt, f"{a.skill}/SKILL.md")
    open(f"{run}/proposal.diff", "w").write(diff)
    json.dump(cost, open(f"{run}/cost.json", "w"))

    rat = [f"# Harness2 improve: {a.skill} ({os.path.basename(run)})", "",
           f"Decision: **{dec['decision']}** ({', '.join(chosen) or 'no change'})", "", dec["rationale"], "",
           "## Candidates"]
    for v in variants:
        if v != "current":
            c = variants[v]["cand"]
            rat.append(f"- {v} ({c['kind']}, {variants[v]['lines']} lines, lessons {', '.join(c['lessons'])}): {c['title']}. "
                       f"{c['rationale']}{' NOT A WINNER.' if v in dropped else ''}")
    rat += [f"- rejected before probing: {r}" for r in rejected]
    rat += ["", "## Scores", table, "", f"Cost: {cost['runs']} headless claude runs, ${cost['usd']:.2f}.", "",
            f"Apply (master, after the user says Apply): `cp {skill_path} {run}/SKILL.md.bak && patch {skill_path} {run}/proposal.diff`"]
    open(f"{run}/rationale.md", "w").write("\n".join(rat) + "\n")
    print("\n".join(rat))

    if not chosen:
        print(f"\nNo change proposed. Run dir: {run}")
        return
    # ---- Library: one EXPERIENCE note per supported change (status candidate: one run is weak evidence)
    for v in chosen:
        c = variants[v]["cand"]
        sc = ", ".join(f"{p} {passes['current'][p]}/{reps}->{passes[v][p]}/{reps}" for p in pids)
        subprocess.run([f"{ORCH}/lessons/lesson-add.sh", "--slug", f"h2-{a.skill}-{c['kind']}-{os.path.basename(run)[:8]}",
                        "--situation", f"Harness2 improve run on the {a.skill} skill: candidate '{c['title']}'.",
                        "--lesson", f"{c['rationale'][:300]} Probe contrast: {sc}.",
                        "--evidence", f"{run}/scores.md Harness2 run {os.path.basename(run)}",
                        "--tags", f"experience,h2,{a.skill}"], check=False)
    # ---- Ask the user (never auto-apply)
    if not a.no_ask:
        sc = "; ".join(f"{v}: " + ", ".join(f"{p} {passes[v][p]}/{reps}" for p in pids) for v in variants)
        q = (f"Apply {a.skill} skill change? {dec['decision']}: " +
             " + ".join(variants[v]["cand"]["title"] for v in chosen) +
             f" ({changed_lines(diff)} lines). Scores {sc}. Diff: {run}/proposal.diff. Why: {run}/rationale.md")
        cmd = [f"{ORCH}/inbox-ask.sh", q[:990], "--options", "Apply|Reject|Explain", "--kind", "decision"]
        if a.sender:
            cmd += ["--from", a.sender]
        ask = subprocess.run(cmd, capture_output=True, text=True)
        print(f"\ninbox ask: {ask.stdout.strip()} {ask.stderr.strip()}")
        open(f"{run}/ask.txt", "w").write(ask.stdout + ask.stderr)
    print(f"Run dir: {run}")


if __name__ == "__main__":
    main()
