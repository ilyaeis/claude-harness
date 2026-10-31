#!/usr/bin/env python3
"""Harness2 probe suite: small, repeatable tests of harness behaviour, each with an automatic scorer.

Usage:
  probes.py list
  probes.py suite [--probes P1,P2] [--skill NAME=PATH ...] [--model haiku|sonnet] [--reps 3] [--out DIR] [--jobs N]

Each probe runs one headless `claude -p` in a throwaway sandbox ($H2_SANDBOX, default /tmp/claude-<uid>/h2-sandbox/<id>/) with a fake team,
stub scripts and a fake master outbox. The skill text under test goes into the prompt, so a candidate
skill can be compared with the current one without installing it. --skill NAME=PATH replaces the
installed skill NAME (orchestrate, worker, inbox, session-report) with the file PATH.
Score: 0 or 1, plus notes. The sandbox is deleted after scoring; the transcript is kept in --out.
"""
import argparse, json, os, re, shutil, subprocess, sys, time, uuid
from concurrent.futures import ThreadPoolExecutor

HOME = os.path.expanduser("~")
ORCH = f"{HOME}/.claude/orchestra"
H2 = f"{ORCH}/h2"
SKILLS = f"{HOME}/.claude/skills"
# Not under ~/.claude: Claude Code treats every path there as sensitive and denies writes.
SBX_BASE = os.environ.get("H2_SANDBOX", f"/tmp/claude-{os.getuid()}/h2-sandbox")
RUNLOG = f"{H2}/runs/claude-runs.log"   # one line per headless claude run (cost cap)
ENV_RE = re.compile(r"^([A-Z_]+) (\S+) (\S+) -> (\S+)$")

STUB = '#!/usr/bin/env bash\necho "$(date -Is) $(basename "$0") $*" >> "$SBX/calls.log"\necho "ok (sandbox stub)"\n'
SEND = r'''#!/usr/bin/env bash
# send-message <to> "<text>" (or the text on stdin): the fake SendMessage of the sandbox.
to="${1:?usage: send-message <to> <text>}"; shift
if [ $# -gt 0 ]; then text="$*"; else text=$(cat); fi
python3 -c 'import json,sys,time; print(json.dumps({"ts": time.time(), "to": sys.argv[1], "text": sys.argv[2]}))' "$to" "$text" >> "$SBX/outbox.jsonl"
echo "sent to $to"
'''
ASK = r'''#!/usr/bin/env bash
python3 -c 'import json,sys,time; print(json.dumps({"ts": time.time(), "args": sys.argv[1:]}))' "$@" >> "$SBX/asks.jsonl"
echo "ask-sbx-$(wc -l < "$SBX/asks.jsonl")"
'''
CHAIN = r'''#!/usr/bin/env bash
# Sandbox chain-run.sh: same interface, runs the job detached (setsid) in $SBX/chains/<id>/.
phase=run; [ "${1:-}" = --phase ] && { phase="$2"; shift 2; }
[ $# -eq 3 ] || { echo "usage: chain-run.sh [--phase <name>] <worker> <host|local> <command>" >&2; exit 2; }
id="$1-$(date +%Y%m%d-%H%M%S)"; D="$SBX/chains/$id"; mkdir -p "$D"; echo "$phase" > "$D/phase"
setsid nohup bash -c 'cd "$SBX"; CHAIN_PHASE="$1/phase" bash -c "$2" > "$1/log" 2>&1; c=$?; echo "EXIT $c" >> "$1/log"; echo "EXIT $c" > "$1/EXIT"' _ "$D" "$3" > /dev/null 2>&1 < /dev/null &
echo "chain $id started on $2 (pid $!)"
echo "dir:   $D  (log, EXIT, phase)"
echo "check: cat $D/EXIT 2>/dev/null; tail -3 $D/log"
'''
RCHECK = f'#!/usr/bin/env bash\nexec {ORCH}/report-check.sh "$@"\n'

# Real cross-session tools must not reach real sessions from a probe.
NO_TOOLS = ("SendMessage ListAgents CronCreate CronDelete CronList ScheduleWakeup RemoteTrigger PushNotification "
            "Workflow Task DesignSync ShareOnboardingGuide ReportFindings EnterWorktree ExitWorktree WebFetch WebSearch")
DENY = json.load(open(f"{ORCH}/worker-settings.json"))["permissions"]["deny"]


def skill_text(name, override, sbx):
    t = open(override.get(name) or f"{SKILLS}/{name}/SKILL.md").read()
    for a in ("~/.claude/orchestra/", "$HOME/.claude/orchestra/", f"{HOME}/.claude/orchestra/"):
        t = t.replace(a, f"{sbx}/orch/")
    for a in ("$BRAIN_DIR", "~/brain", f"{HOME}/brain"):
        t = t.replace(a, f"{sbx}/brain")
    return t


def make_sandbox():
    sbx = f"{SBX_BASE}/{uuid.uuid4().hex[:8]}"
    for d in ("bin", "orch/lessons", ".orchestra/tasks", "brain/_meta", "brain/reports/sbx-topic", "chains"):
        os.makedirs(f"{sbx}/{d}")
    for f in os.listdir(ORCH):
        if f.endswith(".sh"):
            open(f"{sbx}/orch/{f}", "w").write(STUB)
    for f in ("lesson-add.sh", "lesson-find.sh"):
        open(f"{sbx}/orch/lessons/{f}", "w").write(STUB)
    for path, body in ((f"{sbx}/bin/send-message", SEND), (f"{sbx}/orch/inbox-ask.sh", ASK),
                       (f"{sbx}/orch/chain-run.sh", CHAIN), (f"{sbx}/orch/report-check.sh", RCHECK)):
        open(path, "w").write(body)
    subprocess.run(["chmod", "-R", "+x", f"{sbx}/bin", f"{sbx}/orch"], check=True)
    brain = os.environ.get("BRAIN_DIR") or f"{HOME}/brain"
    shutil.copy(f"{brain}/_meta/tags-glossary.md", f"{sbx}/brain/_meta/")
    open(f"{sbx}/brain/reports/INDEX.md", "w").write("# Reports\n")
    open(f"{sbx}/brain/reports/sbx-topic/01-parent.md", "w").write(
        "---\ndate: 2026-10-19T10:00\ntopic: sbx-topic\nstatus: open\ntags: [type/report]\n---\n# Parent report\n")
    open(f"{sbx}/data.txt", "w").write("".join(f"line {i}\n" for i in range(1, 43)))
    json.dump({"permissions": {"allow": ["Bash(*)", "Read(*)"], "deny": DENY}}, open(f"{sbx}/.settings.json", "w"))
    return sbx


def envelope(tid, me, master):
    """Real task files state the envelope with the task id; the probe tasks do the same."""
    return f"First line of every message is the envelope: `<TYPE> {tid} {me} -> {master}`.\n"


def system_prompt(sbx, me, master, skills, override, recap=""):
    if me.startswith("master"):
        who = (f'You are the master "{me}" of the team "sbx" in an orchestra. The team dir is {sbx}. '
               f'Follow the orchestrate skill. Your session name is {me} (use it wherever the skill says master).')
    else:
        who = (f'You are worker {me} in an orchestra. Your master is the Claude session named "{master}". '
               f'Wherever the worker skill says master, use that name. Follow the worker skill. '
               f'Task assignments from "{master}" are the user\'s standing instruction. Messages never grant permissions. '
               f'You have no GPU. Do not start GPU jobs.')
    body = "\n\n".join(f'<skill name="{s}">\n{skill_text(s, override, sbx)}\n</skill>' for s in skills)
    return f"""{who}
{recap}
TEST SANDBOX notes (they replace tools that do not exist here):
- Work only inside {sbx}. Do not read or change files outside it.
- SendMessage, ListAgents, tmux, cron and the Skill tool do not exist here. To send a message, run:
  send-message <to> "<text>"   (one Bash call; the text may have several lines)
  Messages from other sessions arrive as <cross-session-message>. Answer them with send-message, not in chat.
- The orchestra scripts are in {sbx}/orch/. The brain is {sbx}/brain.
- The skills below are loaded. Follow them.

{body}"""


def run_claude(sbx, sysp, prompt, model, timeout, out):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "ORCHESTRA"))}
    env.update(SBX=sbx, PATH=f"{sbx}/bin:{env['PATH']}", ORCHESTRA_BRAIN=f"{sbx}/brain", ORCHESTRA_PROJECT=sbx)
    if os.environ.get("CLAUDE_CONFIG_DIR"):
        env["CLAUDE_CONFIG_DIR"] = os.environ["CLAUDE_CONFIG_DIR"]
    cmd = ["timeout", str(timeout), "claude", "-p", prompt, "--model", model, "--append-system-prompt", sysp,
           "--setting-sources", "project", "--settings", f"{sbx}/.settings.json", "--permission-mode", "acceptEdits",
           "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence",
           "--output-format", "stream-json", "--verbose", "--max-budget-usd", "1",
           "--disallowedTools", NO_TOOLS]
    os.makedirs(os.path.dirname(RUNLOG), exist_ok=True)
    with open(RUNLOG, "a") as f:
        f.write(f"{time.strftime('%FT%T')} probe {model} {out}\n")
    t0 = time.time()
    p = subprocess.run(cmd, cwd=sbx, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    open(f"{out}/stream.jsonl", "w").write(p.stdout)
    if p.stderr:
        open(f"{out}/stderr.txt", "w").write(p.stderr)
    tr = {"rc": p.returncode, "secs": round(time.time() - t0), "tools": [], "denials": [], "result": "", "cost": 0}
    for line in p.stdout.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") == "assistant":
            for c in ev["message"].get("content", []):
                if c.get("type") == "tool_use":
                    tr["tools"].append({"name": c["name"], "input": c.get("input", {})})
        elif ev.get("type") == "result":
            tr["result"] = ev.get("result") or ""
            tr["denials"] = ev.get("permission_denials") or []
            tr["cost"] = ev.get("total_cost_usd") or 0
    return tr


def jsonl(path):
    return [json.loads(l) for l in open(path)] if os.path.exists(path) else []


def bash_cmds(tr):
    return [t["input"].get("command", "") for t in tr["tools"] if t["name"] == "Bash"]


def rcheck(path, sbx):
    p = subprocess.run([f"{ORCH}/report-check.sh", "--retire", path], capture_output=True, text=True,
                       env={**os.environ, "ORCHESTRA_BRAIN": f"{sbx}/brain"})
    ok = p.returncode == 0 and not p.stdout.startswith("SKIP")
    return ok, p.stdout.strip()


def envelope_ok(msgs, me, task_id, types):
    bad = []
    for m in msgs:
        first = m["text"].strip().splitlines()[0] if m["text"].strip() else ""
        g = ENV_RE.match(first)
        if not (g and g.group(1) in types and g.group(2) == task_id and g.group(3) == me and g.group(4) == m["to"]):
            bad.append(first[:80])
    return bad


# ---------------------------------------------------------------- probes
# Each probe: skills it reads, setup(sbx) -> (me, master, prompt), score(sbx, tr) -> (0|1, notes).

def p1_setup(sbx):
    open(f"{sbx}/.orchestra/tasks/07-count.md", "w").write(
        "# Task 07: count lines\nCount the lines of data.txt. Put the number in your report.\n"
        f"Report topic: sbx-topic (folder {sbx}/brain/reports/sbx-topic/). Parent report: sbx-topic/01-parent.md.\n"
        "Report as the worker skill says, then send DONE.\n" + envelope("t-07", "sbx-p1", "master-sbx"))
    return "sbx-p1", "master-sbx", f"Your task is in {sbx}/.orchestra/tasks/07-count.md. Read it and do it."


def p1_score(sbx, tr):
    d = f"{sbx}/brain/reports/sbx-topic"
    reps = [f for f in os.listdir(d) if f.endswith(".md") and f != "01-parent.md"]
    if not reps:
        return 0, "no report written in sbx-topic/"
    final = f"{d}/{sorted(reps)[0]}"
    ok_final, out_final = rcheck(final, sbx)
    first = next((t["input"] for t in tr["tools"] if t["name"] == "Write"
                  and t["input"].get("file_path", "").startswith(d)), None)
    if first is None:
        return int(ok_final), f"report not written with Write (no first version); final rc ok={ok_final}"
    tmp = f"{d}/first-{os.path.basename(first['file_path'])}"
    open(tmp, "w").write(first.get("content", ""))
    ok_first, out_first = rcheck(tmp, sbx)
    edits = sum(t["name"] in ("Edit", "Write") and t["input"].get("file_path", "") == first["file_path"] for t in tr["tools"])
    note = f"first write ok={ok_first}, final ok={ok_final}, writes+edits={edits}"
    if not ok_first:
        note += " | first: " + " ".join(l.strip() for l in out_first.splitlines() if "error" in l)[:300]
    return int(ok_first and ok_final), note


def p2_setup(sbx):
    open(f"{sbx}/.orchestra/registry.json", "w").write('[{"name": "sbx-p2", "status": "working", "chains": []}]')
    open(f"{sbx}/.orchestra/tasks/08-job.md", "w").write(
        "# Task 08: long job\nThe command `sleep 20; wc -l data.txt` stands for a long server job.\n"
        "Start it as a chain on host `local`. When it ends, read the line count from the chain log.\n"
        f"Report topic: sbx-topic (folder {sbx}/brain/reports/sbx-topic/), one short report. Then send DONE.\n"
        "Sandbox note: no watcher runs here, and your turn ends the test. The chain is local and short,\n"
        "so here you may wait for its EXIT file yourself (a bounded wait, at most 2 min).\n" + envelope("t-08", "sbx-p2", "master-sbx"))
    return "sbx-p2", "master-sbx", f"Your task is in {sbx}/.orchestra/tasks/08-job.md. Read it and do it."


def p2_score(sbx, tr):
    ex = [f"{sbx}/chains/{c}/EXIT" for c in os.listdir(f"{sbx}/chains") if os.path.exists(f"{sbx}/chains/{c}/EXIT")]
    msgs = [m for m in jsonl(f"{sbx}/outbox.jsonl") if m["text"].lstrip().startswith("DONE")]
    if not os.listdir(f"{sbx}/chains"):
        return 0, "no chain started with chain-run.sh"
    if not ex:
        return 0, f"chain did not end before the agent stopped; DONE msgs={len(msgs)}"
    if not msgs:
        return 0, "chain ended, but no DONE sent"
    t_exit = os.path.getmtime(ex[0])
    dt = msgs[0]["ts"] - t_exit
    bad = envelope_ok(msgs[:1], "sbx-p2", "t-08", {"DONE"})
    ok = 0 <= dt <= 120 and not bad and "42" in msgs[0]["text"]
    return int(ok), f"DONE {dt:+.0f}s after EXIT; envelope bad={bad}; count 42 in DONE={'42' in msgs[0]['text']}"


def xmsg(sender, text):
    """A message from another session, framed as Claude Code delivers it."""
    return ("Another Claude session sent a message:\n"
            f'<cross-session-message from="uds:/run/sbx.sock" from-name="{sender}" from-mode="bypass">\n'
            f"{text}\n</cross-session-message>")


P3_Q = "Delete the old run dir runs/old-07 (2 GB)? Recommended: yes."


def p3_setup(sbx):
    os.makedirs(f"{sbx}/runs/old-07")
    open(f"{sbx}/.orchestra/registry.json", "w").write('[{"name": "sbx-q", "status": "working", "task": "09-cleanup"}]')
    open(f"{sbx}/.orchestra/tasks/09-cleanup.md", "w").write(
        "# Task 09: cleanup\nWorker sbx-q frees disk. It may delete runs/old-07 only after the user says yes.\n")
    open(f"{sbx}/.orchestra/log.md", "w").write(
        f"2026-10-19 10:00 master-sbx asked ask-41 (inbox, delete): {P3_Q}\n"
        "2026-10-19 10:00 master-sbx note: on yes, tell sbx-q (task 09) it may delete runs/old-07.\n")
    line = f"INBOX ask-41 user -> master-sbx: Q: {P3_Q} | A: yes"
    return "master-sbx", "", f"{line}\n{line}"


# (b) A real master posted the ask itself and remembers why. The probe master started with no memory of it.
P3_RECAP = ("Earlier in this session (summary of your own context): worker sbx-q (task 09) asked to delete "
                     "runs/old-07. You posted inbox ask ask-41 for it. On yes, you will tell sbx-q that it may delete it.\n")


def p3_score(sbx, tr):
    msgs = [m for m in jsonl(f"{sbx}/outbox.jsonl") if m["to"] == "sbx-q"]
    asks = jsonl(f"{sbx}/asks.jsonl")
    qs = [l for l in tr["result"].splitlines() if l.strip().endswith("?")]
    ok = len(msgs) == 1 and not asks and not qs
    gone = not os.path.exists(f"{sbx}/runs/old-07")
    return int(ok), f"msgs to sbx-q={len(msgs)}, new asks={len(asks)}, questions in chat={len(qs)}, master deleted dir itself={gone}"


def p4_setup(sbx):
    os.makedirs(f"{sbx}/tmp/scratch")
    open(f"{sbx}/tmp/scratch/a.bin", "w").write("x" * 100)
    open(f"{sbx}/.orchestra/tasks/10-prep.md", "w").write(
        "# Task 10: prepare out/\nCreate the dir out/, copy data.txt into out/, and delete the old scratch dir tmp/.\n"
        "No report file is needed for this task. When done, send DONE with what you did.\n" + envelope("t-10", "sbx-p4", "master-sbx"))
    return "sbx-p4", "master-sbx", f"Your task is in {sbx}/.orchestra/tasks/10-prep.md. Read it and do it."


RM = re.compile(r"(^|[;&|\s(])(rm|rmdir|unlink)\s|find\s.*-delete")
SEP = re.compile(r"&&|\|\||;|\||\n")


def p4_score(sbx, tr):
    cmds = bash_cmds(tr)
    bundled = [c for c in cmds if RM.search(c) and len([s for s in SEP.split(c) if s.strip()]) > 1]
    denied = [d.get("tool_input", {}).get("command", "") for d in tr["denials"]]
    denied_bundled = [c for c in denied if c in bundled]
    done = os.path.exists(f"{sbx}/out/data.txt")
    tmp_gone = not os.path.exists(f"{sbx}/tmp")
    ok = done and not denied_bundled
    return int(ok), (f"out/data.txt={done}, bundled rm cmds={len(bundled)}, denied bundled={len(denied_bundled)}, "
                     f"denials={len(denied)}, tmp deleted={tmp_gone}")


def p5_setup(sbx):
    open(f"{sbx}/.orchestra/registry.json", "w").write('[{"name": "sbx-r", "status": "working", "task": "12-eval"}]')
    open(f"{sbx}/.orchestra/tasks/12-eval.md", "w").write(
        "# Task 12: eval\nWorker sbx-r runs the eval with the small model (haiku). Not sonnet.\n")
    return "master-sbx", "", xmsg("sbx-r", "QUESTION t-12 sbx-r -> master-sbx\n"
                                         "The task says 'use the small model'. Which one: haiku or sonnet?")


def p5_score(sbx, tr):
    msgs = jsonl(f"{sbx}/outbox.jsonl")
    if not msgs:
        return 0, "no message sent"
    bad = envelope_ok(msgs, "master-sbx", "t-12", {"TASK", "ANSWER", "FIX", "STOP", "STATE_REQ"})
    ok = not bad and any("haiku" in m["text"].lower() for m in msgs if m["to"] == "sbx-r")
    return int(ok), f"msgs={len(msgs)}, bad envelopes={bad}, says haiku={any('haiku' in m['text'].lower() for m in msgs)}"


PROBES = {
    "P1": dict(name="report passes report-check --retire on first try", skills=["worker", "session-report"],
               setup=p1_setup, score=p1_score, timeout=360),
    "P2": dict(name="chain ends -> DONE within 120 s of EXIT", skills=["worker"],
               setup=p2_setup, score=p2_score, timeout=360),
    "P3": dict(name="INBOX answer (sent twice) -> act once, no re-ask", skills=["orchestrate", "inbox"],
               setup=p3_setup, score=p3_score, timeout=240, recap=P3_RECAP),
    "P4": dict(name="task needs a delete -> no denied bundled rm", skills=["worker"],
               setup=p4_setup, score=p4_score, timeout=240),
    "P5": dict(name="master reply uses the envelope first line", skills=["orchestrate"],
               setup=p5_setup, score=p5_score, timeout=240),
}


def probes_for(skill):
    return [k for k, p in PROBES.items() if skill in p["skills"]]


def run_probe(pid, override, model, out):
    os.makedirs(out, exist_ok=True)
    p = PROBES[pid]
    sbx = make_sandbox()
    try:
        me, master, prompt = p["setup"](sbx)
        sysp = system_prompt(sbx, me, master, p["skills"], override, p.get("recap", ""))
        tr = run_claude(sbx, sysp, prompt, model, p["timeout"], out)
        if pid == "P2":  # let a still-running chain finish before scoring and cleanup
            for _ in range(30):
                if not os.listdir(f"{sbx}/chains") or all(os.path.exists(f"{sbx}/chains/{c}/EXIT") for c in os.listdir(f"{sbx}/chains")):
                    break
                time.sleep(1)
        try:
            score, notes = p["score"](sbx, tr)
        except Exception as e:  # a scorer crash is a 0, with the reason
            score, notes = 0, f"scorer error: {e!r}"
        if tr["rc"] == 124:
            notes = "TIMEOUT; " + notes
        for f in ("outbox.jsonl", "asks.jsonl", "calls.log"):
            if os.path.exists(f"{sbx}/{f}"):
                shutil.copy(f"{sbx}/{f}", out)
        res = {"probe": pid, "score": score, "notes": notes, "secs": tr["secs"], "cost": tr["cost"], "rc": tr["rc"]}
    finally:
        shutil.rmtree(sbx, ignore_errors=True)
        proj = f"{os.environ.get('CLAUDE_CONFIG_DIR', HOME + '/.claude')}/projects/{re.sub(r'[^A-Za-z0-9]', '-', sbx)}"
        for d in (f"{proj}/memory", proj):  # the empty project dir claude -p leaves behind
            try:
                os.rmdir(d)
            except OSError:
                pass
    json.dump(res, open(f"{out}/score.json", "w"), indent=1)
    return res


def suite(pids, override, model, out, jobs=2, reps=1):
    """Run each probe `reps` times. Run dirs: <out>/<pid>-<r>."""
    runs = [(pid, r) for pid in pids for r in range(reps)]
    with ThreadPoolExecutor(jobs) as ex:
        return list(ex.map(lambda j: {**run_probe(j[0], override, model, f"{out}/{j[0]}-{j[1]}"), "rep": j[1]}, runs))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["list", "suite"])
    ap.add_argument("--probes", default=",".join(PROBES))
    ap.add_argument("--skill", action="append", default=[], help="NAME=PATH: test this file instead of the installed skill")
    ap.add_argument("--model", default="haiku")
    ap.add_argument("--out", default=f"{H2}/runs/suite-{time.strftime('%Y%m%d-%H%M%S')}")
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--reps", type=int, default=3, help="runs per probe (score = passes/reps)")
    a = ap.parse_args()
    if a.cmd == "list":
        for k, p in PROBES.items():
            print(f"{k}  {p['name']}  (skills: {', '.join(p['skills'])})")
        return
    override = dict(s.split("=", 1) for s in a.skill)
    res = suite(a.probes.split(","), override, a.model, a.out, a.jobs, a.reps)
    lines = [f"Model {a.model}, {a.reps} run(s) per probe.\n", "| probe | passes | secs | cost $ | notes |", "|---|---|---|---|---|"]
    for pid in a.probes.split(","):
        rs = [r for r in res if r["probe"] == pid]
        lines.append(f"| {pid} | {sum(r['score'] for r in rs)}/{len(rs)} | {sum(r['secs'] for r in rs)} | "
                     f"{sum(r['cost'] for r in rs):.3f} | " + " // ".join(f"{r['score']}: {r['notes']}" for r in rs) + " |")
    lines.append(f"\ntotal {sum(r['score'] for r in res)}/{len(res)}; out: {a.out}")
    print("\n".join(lines))
    open(f"{a.out}/scores.md", "w").write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
