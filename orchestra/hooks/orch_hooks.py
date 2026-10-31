#!/usr/bin/env python3
"""Orchestra hooks for Claude Code (task 28). One file, one subcommand per hook.

  orch_hooks.py stop-guard     Stop:                   worker with an ended chain and no DONE/BLOCKED since.
  orch_hooks.py send-contract  PreToolUse SendMessage: envelope + allowed type per contract.yaml.
  orch_hooks.py send-log       PostToolUse SendMessage: append the envelope to <team>/.orchestra/messages.jsonl.
  orch_hooks.py rm-explainer   PreToolUse Bash:        a delete bundled with other steps (shlex segments).
  orch_hooks.py recheck        summary for the enforce ask (hits per hook, rm hits now classified false).

Mode file ~/.claude/orchestra/hooks/mode: "log" (append to hooks.log, never block) or "enforce"
(exit 2 with the reason on stderr; Claude Code shows it to the model). Env ORCH_HOOKS_MODE overrides it.
Fail open: any internal error -> exit 0. No network. Stdlib only.
Test env: ORCH_HOOKS_LOG, ORCH_REGISTRIES (globs), ORCH_WATCHER_STATE, ORCH_CONTRACT.
"""
import glob
import json
import os
import re
import shlex
import sys
import time
from datetime import datetime

HOME = os.path.expanduser("~")
ORCH = os.path.join(HOME, ".claude", "orchestra")
HOOKS = os.path.join(ORCH, "hooks")
LOG = os.environ.get("ORCH_HOOKS_LOG") or os.path.join(HOOKS, "hooks.log")
CMDS = os.environ.get("ORCH_HOOKS_CMDS") or os.path.join(HOOKS, "hooks-cmds.jsonl")
CONTRACT = os.environ.get("ORCH_CONTRACT") or os.path.join(ORCH, "contract.yaml")
STATE = os.environ.get("ORCH_WATCHER_STATE") or os.path.join(ORCH, "watcher-state.json")
REGISTRIES = os.environ.get("ORCH_REGISTRIES") or \
    "~/dev/*/.orchestra/registry.json ~/dev/*/*/.orchestra/registry.json"
CLAUDE_DIR = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(HOME, ".claude")

ENVELOPE = re.compile(r"^([A-Z][A-Z_]*) (\S+) (\S+) -> ([^\s:]+)")   # "-> name: text" is allowed too
SUBAGENT_ID = re.compile(r"^a[0-9a-f]{16}$")


def mode():
    m = os.environ.get("ORCH_HOOKS_MODE")
    if not m:
        try:
            m = open(os.path.join(HOOKS, "mode")).read().strip()
        except OSError:
            m = "log"
    return "enforce" if m == "enforce" else "log"


def hit(hook, who, reason, cmd=None):
    """Log a hit (full command to hooks-cmds.jsonl for re-checks). In enforce mode, block: exit 2 + reason on stderr."""
    m = mode()
    if cmd is not None:
        with open(CMDS, "a") as f:
            f.write(json.dumps({"ts": datetime.now().astimezone().isoformat(timespec="seconds"), "mode": m,
                                "hook": hook, "who": who, "cmd": cmd}) + "\n")
    line = f"{datetime.now().astimezone().isoformat(timespec='seconds')} {m} {hook} {who or '-'}: {reason}\n"
    with open(LOG, "a") as f:
        f.write(line.replace("\n", " ").rstrip() + "\n")
    if m == "enforce":
        print(reason, file=sys.stderr)
        sys.exit(2)


def session_name(sid):
    for f in glob.glob(os.path.join(CLAUDE_DIR, "sessions", "*.json")):
        try:
            d = json.load(open(f))
        except (OSError, ValueError):
            continue
        if d.get("sessionId") == sid and d.get("name"):
            return d["name"]
    return None


def registry_rows():
    """Yield (registry path, row) for every row in every registry."""
    for pat in REGISTRIES.split():
        for reg in glob.glob(os.path.expanduser(pat)):
            try:
                rows = json.load(open(reg))
            except (OSError, ValueError):
                continue
            for row in rows:
                yield reg, row


def running_worker(name, sid=None):
    for reg, row in registry_rows():
        if row.get("status") == "running" and (row.get("name") == name or (sid and row.get("session_id") == sid)):
            return reg, row
    return None, None


def iso_ts(s):
    """ISO time ('Z' or +0300 forms) -> epoch seconds."""
    s = s.replace("Z", "+00:00")
    s = re.sub(r"([+-]\d\d)(\d\d)$", r"\1:\2", s)
    return datetime.fromisoformat(s).timestamp()


# ---------- stop-guard ----------

def chain_end_time(c, fired):
    """Epoch time the chain ended, or None. Local data only: watcher state, or a local EXIT file."""
    t = fired.get(f'{c["id"]}:CHAIN_END')
    if t:
        return iso_ts(t), watcher_exit(c["id"])
    if c.get("host") == "local":
        p = os.path.expanduser(os.path.join(c["dir"], "EXIT"))
        if os.path.exists(p):
            return os.path.getmtime(p), open(p).read().strip()
    return None, None


def watcher_exit(cid):
    """'EXIT <c>' from the CHAIN_END event in the tail of events.jsonl, or None."""
    try:
        with open(os.path.join(ORCH, "events.jsonl"), "rb") as f:
            f.seek(max(0, os.path.getsize(f.name) - 262144))
            tail = f.read().decode(errors="replace")
    except OSError:
        return None
    m = re.findall(r'"chain": "%s".*?"fact": "(EXIT -?\d+)' % re.escape(cid), tail)
    return m[-1] if m else None


def last_report_time(transcript):
    """Epoch time of the last SendMessage whose message starts with DONE or BLOCKED, or 0."""
    last = 0
    with open(transcript) as f:
        for line in f:
            if '"SendMessage"' not in line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("type") != "assistant":
                continue
            for c in d.get("message", {}).get("content") or []:
                if c.get("type") == "tool_use" and c.get("name") == "SendMessage":
                    msg = (c.get("input") or {}).get("message") or ""
                    if isinstance(msg, str) and re.match(r"(DONE|BLOCKED) ", msg):
                        last = max(last, iso_ts(d["timestamp"]))
    return last


def stop_guard(inp):
    if inp.get("stop_hook_active"):
        return
    sid = inp.get("session_id")
    name = session_name(sid)
    _, row = running_worker(name, sid)
    if not row or not row.get("chains"):
        return
    try:
        fired = json.load(open(STATE)).get("fired", {})
    except (OSError, ValueError):
        fired = {}
    ended = []
    for c in row["chains"]:
        t, code = chain_end_time(c, fired)
        if t:
            ended.append((t, c["id"], code or c.get("exit") or "?"))
    if not ended:
        return
    t, cid, code = max(ended)
    if last_report_time(inp["transcript_path"]) >= t:
        return
    hit("stop-guard", row["name"],
        f"your chain {cid} ended with {code}; send DONE or BLOCKED to your master")


# ---------- send-contract / send-log ----------

def load_channels():
    """Parse the flow-style channel lines of contract.yaml: - {from: a, to: b, types: [X, Y] | any}."""
    chans = []
    for line in open(CONTRACT):
        m = re.match(r"\s*-\s*\{from:\s*(\w+),\s*to:\s*(\w+),\s*types:\s*(any|\[[^\]]*\])\s*\}", line)
        if m:
            types = m.group(3)
            types = "any" if types == "any" else [t.strip() for t in types[1:-1].split(",") if t.strip()]
            chans.append((m.group(1), m.group(2), types))
    return chans


def role(name, sid=None):
    """(role, team) from a session name (or, for a worker, its session id)."""
    if not name and not sid:
        return None, None
    if name == "watcher":
        return "watcher", None
    if re.fullmatch(r"po\d*", name):
        return "po", None
    if name.startswith("master-"):
        return "master", name[len("master-"):]
    _, row = running_worker(name, sid)
    if row:
        return "worker", row["name"].rsplit("-", 1)[0]
    return None, None


def allowed_types(frm, to):
    """Allowed types for the pair, or None if the pair is forbidden."""
    fr, ft = role(frm)
    tr, tt = role(to)
    for cf, ct, types in load_channels():
        ok_from = cf == fr or (cf == "own_worker" and fr == "worker" and tr == "master" and ft == tt)
        if not ok_from:
            continue
        if ct == tr or ct == "any" or (ct == "own_worker" and tr == "worker" and fr == "master" and ft == tt):
            return types
    return None


def send_contract(inp):
    ti = inp.get("tool_input") or {}
    msg = ti.get("message")
    to = ti.get("to") or ""
    if not isinstance(msg, str) or not msg.strip() or SUBAGENT_ID.match(to):
        return   # pure subscription, structured message, or a subagent of this session
    sid = inp.get("session_id")
    sender = session_name(sid)
    if sender and role(sender, sid)[0] is None:
        return   # plain session: no contract
    first = msg.splitlines()[0]
    m = ENVELOPE.match(first)
    if not m:
        hit("send-contract", sender, f"first line is not an envelope '<TYPE> <id> <from> -> <to>': {first[:80]!r}")
        return
    typ, _, frm, dst = m.groups()
    types = allowed_types(frm, dst)
    if types is None:
        hit("send-contract", sender, f"pair {frm} -> {dst} is not allowed by contract.yaml")
    elif types != "any" and typ not in types:
        hit("send-contract", sender, f"type {typ} is not allowed for {frm} -> {dst}; allowed: {', '.join(types)}")


def team_dir(cwd):
    d = os.path.realpath(cwd or ".")
    while d != "/":
        if os.path.isdir(os.path.join(d, ".orchestra")):
            return d
        d = os.path.dirname(d)
    return None


def send_log(inp):
    ti = inp.get("tool_input") or {}
    msg = ti.get("message")
    if not isinstance(msg, str) or not msg.strip():
        return
    first = msg.splitlines()[0]
    m = ENVELOPE.match(first)
    d = team_dir(inp.get("cwd"))
    if not m or not d or '"success": false' in json.dumps(inp.get("tool_response")):
        return   # no envelope, no team dir, or the send failed
    rec = {"ts": datetime.now().astimezone().isoformat(timespec="seconds"), "type": m.group(1), "id": m.group(2),
           "from": m.group(3), "to": m.group(4), "tool_to": ti.get("to"), "envelope": first[:300],
           "session": session_name(inp.get("session_id"))}
    with open(os.path.join(d, ".orchestra", "messages.jsonl"), "a") as f:
        f.write(json.dumps(rec) + "\n")
    if m.group(1) == "FINDING":
        with open(os.path.join(ORCH, "findings.jsonl"), "a") as f:
            f.write(json.dumps(rec) + "\n")


# ---------- rm-explainer ----------

HEREDOC = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?[^\n]*\n.*?\n\s*\1\s*(?=\n|$)", re.S)   # body is data, not commands
SEPARATORS = set("&|;()\n")   # a token made only of these splits segments
SKIP_WORDS = {"sudo", "nohup", "time", "env", "nice", "exec", "command", "xargs", "timeout",
              "do", "then", "else", "elif", "!", "{", "}"}
SSH_OPTS_WITH_ARG = set("bcDEeFIiJLlmOoPpQRSWw")


def drop_subst(cmd):
    """Replace each $( ... ) body (balanced parens) with SUBST: bash quotes nest there, shlex cannot follow."""
    out, i = [], 0
    while True:
        j = cmd.find("$(", i)
        if j < 0:
            return "".join(out) + cmd[i:]
        out.append(cmd[i:j] + "SUBST")
        depth, k = 0, j + 1
        while k < len(cmd):
            depth += {"(": 1, ")": -1}.get(cmd[k], 0)
            k += 1
            if depth == 0:
                break
        i = k


def tokens(cmd):
    """shlex tokens; separators (&& || ; | ( ) newline) come out as their own tokens."""
    lex = shlex.shlex(cmd, posix=True, punctuation_chars="();<>|&\n")
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    return list(lex)


def segments(cmd, depth=0):
    """Command segments as token lists. The command run by ssh or bash -c is split into its own segments."""
    out, seg = [], []
    for t in tokens(drop_subst(HEREDOC.sub("<<heredoc", cmd))) + [";"]:
        if t and set(t) <= SEPARATORS:
            if seg:
                out.extend(nested(seg, depth) or [seg])
            seg = []
        else:
            seg.append(t)
    return out


def command_word(seg):
    """Index of the command word: skip VAR=x, wrappers (sudo, xargs, timeout N ...) and shell keywords."""
    i = 0
    while i < len(seg):
        w = seg[i]
        if re.match(r"^\w+=", w) or w in SKIP_WORDS or (w.startswith("-") and i > 0 and seg[i - 1] in SKIP_WORDS):
            i += 1
        elif i > 0 and seg[i - 1] == "timeout" and re.match(r"^[\d.]+[smhd]?$", w):
            i += 1
        else:
            return i
    return None


def nested(seg, depth):
    """Segments of the remote/inner command of 'ssh host cmd' or 'bash -c cmd', else None."""
    i = command_word(seg)
    if i is None or depth > 3:
        return None
    w, rest = os.path.basename(seg[i]), seg[i + 1:]
    if w == "ssh":
        j = 0
        while j < len(rest) and rest[j].startswith("-"):
            j += 2 if rest[j][1:] in SSH_OPTS_WITH_ARG else 1
        remote = rest[j + 1:]
        return segments(" ".join(remote), depth + 1) if remote else None
    if w in ("bash", "sh") and "-c" in rest[:2]:
        k = rest.index("-c") + 1
        return segments(rest[k], depth + 1) if k < len(rest) else None
    return None


def is_delete(seg):
    i = command_word(seg)
    if i is None:
        return False
    w = os.path.basename(seg[i])
    if w in ("rm", "rmdir", "unlink"):
        return True
    if w == "find":
        return "-delete" in seg or any(seg[k] == "-exec" and k + 1 < len(seg) and
                                       os.path.basename(seg[k + 1]) in ("rm", "rmdir", "unlink")
                                       for k in range(i, len(seg)))
    return False


def bundled_delete(cmd):
    """True if a delete segment runs together with other segments. Unparsable -> False (fail open)."""
    try:
        segs = segments(cmd)
    except ValueError:
        return False
    return len(segs) > 1 and any(is_delete(s) for s in segs)


def rm_explainer(inp):
    cmd = (inp.get("tool_input") or {}).get("command") or ""
    if bundled_delete(cmd):
        hit("rm-explainer", session_name(inp.get("session_id")),
            "split the delete out into its own command; if a lone delete is denied too, "
            f"list the path and size in your report. Command: {cmd[:160]!r}", cmd=cmd)


def recheck():
    """Summary for the enforce ask: hits per hook since install, and rm hits the current classifier calls false."""
    per, false, total_rm = {}, 0, 0
    for line in open(LOG):
        parts = line.split(" ", 3)
        if len(parts) > 2 and parts[1] in ("log", "enforce"):
            per[parts[2]] = per.get(parts[2], 0) + 1
    try:
        for line in open(CMDS):
            rec = json.loads(line)
            if rec.get("hook") == "rm-explainer":
                total_rm += 1
                false += not bundled_delete(rec["cmd"])
    except OSError:
        pass
    by = ", ".join(f"{k} {v}" for k, v in sorted(per.items())) or "none"
    print(f"{sum(per.values())} hits ({by}); re-check: {false} of {total_rm} logged rm-explainer commands are false hits")


HANDLERS = {"stop-guard": stop_guard, "send-contract": send_contract, "send-log": send_log,
            "rm-explainer": rm_explainer}


def main():
    if sys.argv[1:] == ["recheck"]:
        return recheck()
    try:
        HANDLERS[sys.argv[1]](json.load(sys.stdin))
    except SystemExit:
        raise
    except Exception as e:   # fail open
        try:
            with open(LOG, "a") as f:
                f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} error {sys.argv[1:2]}: {type(e).__name__}: {e}\n")
        except Exception:
            pass
    sys.exit(0)


if __name__ == "__main__":
    main()
