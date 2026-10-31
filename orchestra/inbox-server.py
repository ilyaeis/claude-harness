#!/usr/bin/env python3
"""Orchestra local inbox server. Run by the systemd user service orch-inbox.service.

Binds 127.0.0.1 only. Remote access goes through `tailscale serve` (HTTPS, tailnet only).
  GET  /?t=<token>       sets the auth cookie, then redirects to /
  GET  /                 the page (inbox-page.html)
  GET  /api/items        live items from inbox-collect.py + last answer + wake status per item
  POST /answer           {id, choice, note, files}: append to inbox/answers.jsonl, wake the team master
  POST /explain          {id}: the user asks the master to explain the item; wake line EXPLAIN, item stays open
  POST /reply            {id, text}: the master's explanation (tool: inbox-reply.sh); stored in inbox/replies.jsonl
  POST /ask              {from, text, kind?, media?}: any Claude session asks the user (tool: inbox-ask.sh);
                         stored in inbox/asks.jsonl; answers wake that session. {from, done: <id>} withdraws it.
  POST /upload?id=&name= raw file body (video/image, 500 MB max): stream to inbox/uploads/<item>/<time>-<name>
  GET  /u/<item>/<n>     file n attached to the item's last answer, HTTP Range
  GET  /m/<item>/<n>     media file n of the item (allowlist = the item's media list), HTTP Range
  GET  /api/teams        Teams tab: live teams, other live sessions, GPUs (inbox-teams.py), harness metrics (h2/metrics.py)
  POST /team[?dry=1]     New team {name, dir, goal, gpus, confirm: "yes"}: checks, handoff, start-master.sh.
                         Needs Origin/Referer = this page's host (CSRF). dry=1: checks + plan, starts nothing.
Wake: one line typed into the pane of the item's session (find_pane.py; pane checks from watcher.py).
Busy pane (user types, copy-mode): queued, retried every 15 s. Queue lives in inbox/queue.json.
After Enter the line must leave the input box (checked ~3 s, one more Enter), else the wake is "failed".
Our own unsent line in the box is not user typing: it is submitted first. Queued > 10 min: "stuck"
(INBOX_STUCK event in events.jsonl), retried every 60 s. Statuses: queued, stuck, sent, failed, delivered-by-hand.
Every request without the token cookie gets 403.

Usage: inbox-server.py [--port N]
"""
import hashlib
import hmac
import http.server
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.parse

ORCH = os.path.expanduser("~/.claude/orchestra")
BOX = ORCH + "/inbox"
TOKEN_FILE = BOX + "/token"
ANSWERS = BOX + "/answers.jsonl"
QUEUE = BOX + "/queue.json"
REPLIES = BOX + "/replies.jsonl"
ASKS = BOX + "/asks.jsonl"
ASK_KINDS = ("", "decision", "verdict", "user-action", "delete", "other")
CACHE = BOX + "/cache"
UPLOADS = BOX + "/uploads"
LOG = BOX + "/server.log"
EVENTS = ORCH + "/events.jsonl"
PAGE = ORCH + "/inbox-page.html"
RETRY_SEC = 15
STUCK_SEC = 600          # a wake queued this long is "stuck": red on the page, INBOX_STUCK event
STUCK_RETRY_SEC = 60     # stuck wakes are retried this often
FAIL_AFTER = 20          # tries with a hard error (no window, wrong pane) before "failed"
MAX_LINE = 400
COOKIE = "orch_inbox"
MAX_FILE = 500 << 20     # per uploaded file
MAX_ITEM = 2 << 30       # per item (all its uploads)
MIN_FREE = 10 << 30      # refuse uploads below this much free disk
UP_EXT = (".mp4", ".mov", ".m4v", ".3gp", ".mkv", ".webm", ".avi", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".heic", ".heif")

_spec = importlib.util.spec_from_file_location("watcher", ORCH + "/watcher.py")
watcher = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(watcher)

_spec = importlib.util.spec_from_file_location("collect", ORCH + "/inbox-collect.py")
collector = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(collector)

_spec = importlib.util.spec_from_file_location("teams", ORCH + "/inbox-teams.py")
teams = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(teams)

_h2 = {"at": 0, "data": None}


def harness_metrics():
    """Totals of h2/metrics.py for the last 7 days. Cached 10 min. None on error."""
    if time.time() - _h2["at"] > 600:
        since = time.strftime("%Y-%m-%d", time.localtime(time.time() - 7 * 86400))
        try:
            out = subprocess.run([sys.executable, ORCH + "/h2/metrics.py", "--since", since, "--json"],
                                 capture_output=True, text=True, timeout=60).stdout
            _h2["data"] = {"since": since, **json.loads(out)["totals"]}
        except (OSError, ValueError, KeyError, subprocess.SubprocessError):
            _h2["data"] = None
        _h2["at"] = time.time()
    return _h2["data"]


lock = threading.Lock()      # guards queue + answers files
kick = threading.Event()     # wakes the sender thread at once
converting = set()


def log(msg):
    with open(LOG, "a") as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {msg}\n")


def token():
    if not os.path.exists(TOKEN_FILE):
        old = os.umask(0o077)
        with open(TOKEN_FILE, "w") as f:
            f.write(os.urandom(24).hex() + "\n")
        os.umask(old)
    os.chmod(TOKEN_FILE, 0o600)
    return open(TOKEN_FILE).read().strip()


def collect():
    out = subprocess.run([sys.executable, ORCH + "/inbox-collect.py"], capture_output=True, text=True, timeout=30)
    return {it["id"]: it for it in json.loads(out.stdout or "[]")}


def load_queue():
    try:
        return json.load(open(QUEUE))
    except (OSError, ValueError):
        return {}


def save_queue(q):
    with open(QUEUE + ".tmp", "w") as f:
        json.dump(q, f, indent=1)
    os.replace(QUEUE + ".tmp", QUEUE)


def last_answers():
    out = {}
    if os.path.exists(ANSWERS):
        for line in open(ANSWERS):
            try:
                a = json.loads(line)
                out[a["id"]] = a
            except (ValueError, KeyError):
                pass
    return out


def safe(s, n):
    """One line: control chars and newlines become spaces, runs of spaces collapse, length capped."""
    s = "".join(" " if unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp") else c for c in str(s))
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= n else s[:n - 1] + "…"


def wake_line(it, choice, note, nfiles=0, fdir=""):
    head = f"INBOX {it['id']} user -> {it['master']}: Q: {safe(it['text'], 140)} | A: "
    tail = f" | files: {nfiles} in {fdir}" if nfiles else ""
    return safe(head + safe(f"{choice} {note}", MAX_LINE), MAX_LINE - len(tail)) + tail


def explain_line(it):
    tail = f' | reply with: ~/.claude/orchestra/inbox-reply.sh {it["id"]} "<short explanation>"'
    head = f"INBOX {it['id']} user -> {it['master']}: EXPLAIN Q: "
    return head + safe(it["text"], max(20, MAX_LINE - len(head) - len(tail))) + tail


def load_replies():
    out = {}
    if os.path.exists(REPLIES):
        for line in open(REPLIES):
            try:
                r = json.loads(line)
                out.setdefault(r["id"], []).append({k: r.get(k) for k in ("at", "kind", "text")})
            except (ValueError, KeyError):
                pass
    return out


def upload_name(name):
    """Safe file name: no path parts, ascii word chars only, allowed extension, or None."""
    base = os.path.basename(str(name).replace("\\", "/"))
    base = unicodedata.normalize("NFKD", base).encode("ascii", "ignore").decode()
    base = re.sub(r"[^\w.-]", "_", base).lstrip(".")[-80:]
    return base if base.lower().endswith(UP_EXT) else None


def sniff_ok(head):
    """Content check of the first bytes: image or video container."""
    return (head.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"\x1a\x45\xdf\xa3"))
            or head[4:8] == b"ftyp"
            or (head[:4] == b"RIFF" and head[8:12] in (b"WEBP", b"AVI ")))


def try_send(job, own):
    """Return (result, stale). result: 'sent', 'queued: <why>', 'not submitted' or 'error: <why>'.
    stale: our own earlier line that was still in the input box and is now submitted, else None."""
    # No watcher lock: the watcher holds it during slow ssh calls. Both check the input box right before typing.
    pane, stale = watcher.master_pane(job["session"], job["master"], own)
    if pane is None:
        why = stale
        return ("queued: " + why if why in ("input box not empty or not found", "pane in copy mode") else "error: " + why), None
    if stale:
        # Our own earlier line sits unsent in the box (its Enter was lost). Submit it; never type on top of it.
        if not watcher.submit(pane, stale):
            return "queued: own line stuck in input box", None
        return "queued: own earlier line submitted first", stale
    rc1, _ = watcher.run(["tmux", "send-keys", "-t", pane, "-l", job["line"]])
    if rc1 != 0:
        return "error: send-keys text rc=%d" % rc1, None
    return ("sent" if watcher.submit(pane, job["line"]) else "not submitted"), None


def event(kind, iid, job, fact):
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "kind": kind, "team": job["session"], "session": job["session"],
           "master": job["master"], "item": iid, "fact": fact}
    with open(EVENTS, "a") as f:
        f.write(json.dumps(rec) + "\n")


def sender():
    while True:
        kick.wait(RETRY_SEC)
        kick.clear()
        with lock:
            q = load_queue()
        for iid, job in q.items():
            if job["status"] not in ("queued", "stuck"):     # sent, failed, delivered-by-hand
                continue
            if job["status"] == "stuck" and time.time() - job.get("last_try", 0) < STUCK_RETRY_SEC:
                continue
            own = [j["line"] for j in q.values() if j["master"] == job["master"]]
            res, stale = try_send(job, own)
            job["tries"] = job.get("tries", 0) + 1
            job["last_try"] = time.time()
            if res == "sent":
                job["status"], job["sent_at"] = "sent", time.time()
                job.pop("why", None)
            elif res == "not submitted":
                job["status"], job["why"] = "failed", "not submitted: the line is still in the input box"
            elif res.startswith("queued"):
                job["why"] = res[len("queued: "):]
            else:
                job["errors"] = job.get("errors", 0) + 1
                job["why"] = res
                if job["errors"] >= FAIL_AFTER:
                    job["status"] = "failed"
            if job["status"] == "queued" and time.time() - job.get("at", time.time()) > STUCK_SEC:
                job["status"] = "stuck"
                event("INBOX_STUCK", iid, job, "wake not delivered for %d min: %s" % (STUCK_SEC // 60, job.get("why", "")))
            log(f"wake {iid} try {job['tries']}: {res}")
            with lock:
                cur = load_queue()
                if cur.get(iid, {}).get("line") == job["line"]:   # not replaced by a newer answer meanwhile
                    cur[iid] = job
                for oid, o in cur.items():
                    if stale and o["line"] == stale and o["status"] != "sent":
                        o["status"], o["sent_at"], o["why"] = "sent", time.time(), "submitted late (was left in input box)"
                        log(f"wake {oid}: stale line in input box submitted")
                save_queue(cur)
            if stale:
                kick.set()      # the box is free now: try again at once


def playable(path):
    """Path to serve for a video: the original if a phone can play it, else a cached H.264 copy (made in the background)."""
    if not path.lower().endswith((".mp4", ".mov", ".mkv", ".webm", ".avi")):
        return path
    st = os.stat(path)
    key = hashlib.sha1(f"{path}|{st.st_mtime}|{st.st_size}".encode()).hexdigest()[:16]
    cached = f"{CACHE}/{key}.mp4"
    if os.path.exists(cached):
        return cached
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=codec_name,pix_fmt", "-of", "csv=p=0", path], capture_output=True, text=True)
    if probe.stdout.strip() == "h264,yuv420p" and path.lower().endswith((".mp4", ".mov")):
        return path
    with lock:
        if key not in converting:
            converting.add(key)
            threading.Thread(target=convert, args=(path, cached, key), daemon=True).start()
    return path


def convert(src, dst, key):
    tmp = dst + ".part.mp4"
    rc = subprocess.run(["nice", "ffmpeg", "-y", "-v", "error", "-i", src, "-threads", "8", "-c:v", "libx264",
                         "-pix_fmt", "yuv420p", "-crf", "23", "-preset", "veryfast", "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                         "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", tmp]).returncode
    if rc == 0:
        os.replace(tmp, dst)
    log(f"convert {src} -> {dst} rc={rc}")
    converting.discard(key)


CTYPES = {".mp4": "video/mp4", ".mov": "video/quicktime", ".mkv": "video/x-matroska", ".webm": "video/webm",
          ".avi": "video/x-msvideo", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
          ".webp": "image/webp", ".gif": "image/gif", ".m4v": "video/mp4", ".3gp": "video/3gpp",
          ".heic": "image/heic", ".heif": "image/heic"}


class H(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "orch-inbox"

    def log_message(self, fmt, *args):
        who = self.headers.get("Tailscale-User-Login") if self.headers else None
        path = re.sub(r"([?&]t=)[^&]*", r"\1***", self.path)   # never log the token
        log(f"{self.address_string()} {'ts=' + who + ' ' if who else ''}{self.command} {path} "
            + (fmt % args).split('" ', 1)[-1])

    def send(self, code, body=b"", ctype="text/plain; charset=utf-8", extra=()):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def authed(self):
        url = urllib.parse.urlsplit(self.path)
        t = urllib.parse.parse_qs(url.query).get("t", [""])[0]
        if t and hmac.compare_digest(t, TOKEN):
            secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
            self.send(303, "", extra=[("Location", url.path or "/"), ("Set-Cookie",
                      f"{COOKIE}={TOKEN}; Path=/; Max-Age=31536000; HttpOnly; SameSite=Lax{secure}")])
            return None
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE and hmac.compare_digest(v, TOKEN):
                return True
        self.send(403, "Forbidden. Open the link with ?t=<token> once.\n")
        return None

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if not self.authed():
            return
        path = urllib.parse.urlsplit(self.path).path
        if path == "/":
            return self.send(200, open(PAGE, "rb").read(), "text/html; charset=utf-8")
        if path == "/api/items":
            items = collect()
            answers, q, replies = last_answers(), load_queue(), load_replies()
            for iid, it in items.items():
                it["thread"] = replies.get(iid, [])
                it["media"] = [{"url": f"/m/{iid}/{n}", "kind": m["kind"], "label": m["label"]}
                               for n, m in enumerate(it.get("media", []))]
                it.pop("source", None)
                a = answers.get(iid)
                if a:
                    it["answer"] = {k: a.get(k) for k in ("choice", "note", "at")}
                    it["answer"]["files"] = [
                        {"url": f"/u/{iid}/{n}", "label": os.path.basename(f),
                         "kind": "video" if CTYPES.get(os.path.splitext(f)[1].lower(), "").startswith("video") else "image"}
                        for n, f in enumerate(a.get("files") or [])]
                    job = q.get(iid, {})
                    it["answer"]["wake"] = job.get("status", "?")
                    it["answer"]["wake_why"] = job.get("why", "")
            return self.send(200, json.dumps(list(items.values())), "application/json")
        if path == "/api/teams":
            return self.send(200, json.dumps({**teams.teams(), "gpus": teams.gpus(), "harness": harness_metrics()}), "application/json")
        m = re.fullmatch(r"/m/([\w.-]+)/(\d+)", path)
        if m:
            it = collect().get(m.group(1))
            media = it.get("media", []) if it else []
            n = int(m.group(2))
            if n >= len(media):
                return self.send(404, "not found\n")
            return self.file(media[n]["path"])
        m = re.fullmatch(r"/u/([\w][\w.-]*)/(\d+)", path)
        if m:
            files = (last_answers().get(m.group(1)) or {}).get("files") or []
            n = int(m.group(2))
            if n >= len(files) or os.path.dirname(os.path.realpath(files[n])) != os.path.realpath(f"{UPLOADS}/{m.group(1)}"):
                return self.send(404, "not found\n")
            return self.file(files[n])
        self.send(404, "not found\n")

    def file(self, path):
        try:
            real = playable(path)
            size = os.path.getsize(real)
        except OSError:
            return self.send(404, "not found\n")
        ctype = CTYPES.get(os.path.splitext(real)[1].lower(), "application/octet-stream")
        start, end, code = 0, size - 1, 200
        rng = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", "").strip())
        if rng and (rng.group(1) or rng.group(2)):
            if rng.group(1):
                start = int(rng.group(1))
                end = min(int(rng.group(2)), size - 1) if rng.group(2) else size - 1
            else:
                start = max(0, size - int(rng.group(2)))
            if start > end or start >= size:
                return self.send(416, "", extra=[("Content-Range", f"bytes */{size}")])
            code = 206
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "private, max-age=300")
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(real, "rb") as f:
            f.seek(start)
            left = end - start + 1
            try:
                while left > 0:
                    chunk = f.read(min(1 << 20, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def reject(self, code, msg):
        """Answer before the body is read: close the connection so the unread body cannot confuse the next request."""
        self.close_connection = True
        self.send(code, msg + "\n", extra=[("Connection", "close")])

    def upload(self):
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        iid, name = q.get("id", [""])[0], upload_name(q.get("name", [""])[0])
        try:
            size = int(self.headers.get("Content-Length") or "")
        except ValueError:
            return self.reject(411, "Content-Length needed")
        if not re.fullmatch(r"[\w][\w.-]*", iid) or iid not in collect():
            return self.reject(404, "item not found")
        if not name:
            return self.reject(415, "only video and image files are allowed")
        if size <= 0:
            return self.reject(400, "empty file")
        if size > MAX_FILE:
            return self.reject(413, "file is over 500 MB")
        d = f"{UPLOADS}/{iid}"
        os.makedirs(d, mode=0o700, exist_ok=True)
        used = sum(os.path.getsize(f"{d}/{f}") for f in os.listdir(d) if os.path.isfile(f"{d}/{f}"))
        if used + size > MAX_ITEM:
            return self.reject(413, "over 2 GB of files for this item")
        if shutil.disk_usage(os.path.expanduser("~")).free - size < MIN_FREE:
            return self.reject(507, "not enough free disk space on the desktop (need 10 GB free)")
        final = f"{d}/{time.strftime('%Y%m%d-%H%M%S')}-{name}"
        n = 1
        while os.path.exists(final):
            final = f"{d}/{time.strftime('%Y%m%d-%H%M%S')}-{n}-{name}"
            n += 1
        part, left, first = final + ".part", size, True
        try:
            with open(part, "wb") as f:
                while left > 0:
                    chunk = self.rfile.read(min(1 << 20, left))
                    if not chunk:
                        raise ConnectionError("upload cut off")
                    if first:
                        first = False
                        if not sniff_ok(chunk[:16]):
                            os.unlink(part)
                            self.close_connection = True
                            return self.send(415, "the content is not a video or image\n", extra=[("Connection", "close")])
                    f.write(chunk)
                    left -= len(chunk)
            os.replace(part, final)
        except (OSError, ConnectionError) as e:
            self.close_connection = True
            if os.path.exists(part):
                os.unlink(part)
            log(f"upload {iid} failed: {e}")
            return
        log(f"upload {iid} {os.path.basename(final)} {size} bytes")
        self.send(200, json.dumps({"name": os.path.basename(final)}), "application/json")

    def explain_or_reply(self, path):
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self.reject(415, "json only")
        try:
            body = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 32768)))
            iid = str(body["id"])
            text = str(body.get("text") or "").strip()[:2000] if path == "/reply" else ""
        except (ValueError, KeyError, TypeError):
            return self.send(400, "bad request\n")
        it = collect().get(iid)
        if not it:
            return self.send(404, "item not found (left the status file?)\n")
        if path == "/reply" and not text:
            return self.send(400, "empty reply\n")
        rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "id": iid, "kind": "reply" if text else "explain", "text": text}
        with lock:
            with open(REPLIES, "a") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if not text:
                q = load_queue()
                q[iid] = {"session": "orch-" + it["team"], "master": it["master"], "line": explain_line(it),
                          "status": "queued", "tries": 0, "errors": 0, "at": time.time()}
                save_queue(q)
        log(f"{rec['kind']} {iid} by {self.headers.get('Tailscale-User-Login') or 'local'}")
        if text:
            return self.send(200, "{}", "application/json")
        kick.set()
        for _ in range(30):
            time.sleep(0.1)
            st = load_queue().get(iid, {})
            if st.get("tries"):
                break
        self.send(200, json.dumps({"wake": st.get("status", "queued")}), "application/json")

    def ask(self):
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self.reject(415, "json only")
        try:
            body = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 32768)))
            who, done = str(body["from"]), str(body.get("done") or "")
            text, kind = safe(body.get("text") or "", 1000), str(body.get("kind") or "")
            atype, raw_opts = str(body.get("atype") or "yesno"), str(body.get("options") or "")
            media = [os.path.realpath(os.path.expanduser(str(m))) for m in (body.get("media") or [])]
        except (ValueError, KeyError, TypeError):
            return self.send(400, "bad request\n")
        if not re.fullmatch(r"[\w][\w.-]*", who) or not collector.session_pid(who):
            return self.send(400, f"no live Claude session named {who!r}\n")
        rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "from": who}
        if done:
            if not any(a.get("id") == done and a.get("from") == who for a in collector.jsonl(ASKS)):
                return self.send(404, f"no ask {done} from {who}\n")
            rec.update(op="done", id=done)
        else:
            if not text:
                return self.send(400, "empty question\n")
            if kind not in ASK_KINDS:
                return self.send(400, "kind must be decision, verdict, user-action, delete or other\n")
            if atype not in ("yesno", "options", "open"):
                return self.send(400, "answer type must be yesno, options or open\n")
            options = [safe(o, 60) for o in raw_opts.split("|")] if atype == "options" else []
            if atype == "options" and (not 2 <= len(options) <= 6 or not all(options)):
                return self.send(400, "options needs 2 to 6 non-empty options split by |\n")
            bad = [m for m in media if not collector.media_files(m)]
            if bad or len(media) > 12:
                return self.send(400, f"media must be up to 12 image/video files or folders; not usable: {bad}\n")
            iid = "ask-" + hashlib.sha1(f"{who}|{text}|{time.time_ns()}".encode()).hexdigest()[:8]
            rec.update(op="ask", id=iid, text=text, kind=kind, atype=atype, options=options, media=media)
        with lock:
            with open(ASKS, "a") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        log(f"ask {rec['op']} {rec['id']} from {who}")
        self.send(200, json.dumps({"id": rec["id"]}), "application/json")

    def same_origin(self):
        """CSRF check: Origin (or Referer) must name this page's own host."""
        src = self.headers.get("Origin") or self.headers.get("Referer") or ""
        host = urllib.parse.urlsplit(src).netloc.lower()
        own = {h.strip().lower() for h in (self.headers.get("Host"), self.headers.get("X-Forwarded-Host")) if h}
        return bool(host) and host in own

    def new_team(self):
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self.reject(415, "json only")
        if not self.same_origin():
            return self.reject(403, "cross-origin request refused")
        dry = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("dry", [""])[0] == "1"
        try:
            body = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 32768)))
            if not isinstance(body, dict):
                raise ValueError
        except (ValueError, TypeError):
            return self.send(400, "bad request\n")
        who = self.headers.get("Tailscale-User-Login") or "local"
        code, res = teams.launch(body, dry, who)
        log(f"team {'dry ' if dry else ''}{safe(body.get('name') or '', 40)!r} by {who}: {code} {'; '.join(res.get('errors') or []) or 'ok'}")
        self.send(code, json.dumps(res), "application/json")

    def do_POST(self):
        if not self.authed():
            return
        path = urllib.parse.urlsplit(self.path).path
        if path == "/team":
            return self.new_team()
        if path == "/ask":
            return self.ask()
        if path == "/upload":
            return self.upload()
        if path in ("/explain", "/reply"):
            return self.explain_or_reply(path)
        if path != "/answer":
            return self.send(404, "not found\n")
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self.send(415, "json only\n")
        try:
            body = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 16384)))
            iid, choice, note = str(body["id"]), str(body.get("choice") or ""), safe(body.get("note") or "", 300)
            names = [str(n) for n in (body.get("files") or [])][:20]
        except (ValueError, KeyError, TypeError):
            return self.send(400, "bad request\n")
        it = collect().get(iid)
        if not it:
            return self.send(404, "item not found (left the status file?)\n")
        if choice not in ("", "yes", "no", "skip", "later") + tuple(it.get("options") or ()) or not (choice or note or names):
            return self.send(400, "choice must be one of the offered answers, a note or a file\n")
        fdir = f"{UPLOADS}/{iid}"
        files = [f"{fdir}/{n}" for n in names]
        if any(os.path.basename(n) != n or n.startswith(".") or not os.path.isfile(f) for n, f in zip(names, files)):
            return self.send(400, "unknown attached file\n")
        line = wake_line(it, choice, note, len(files), fdir)
        rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "id": iid, "team": it["team"], "master": it["master"],
               "text": it["text"], "choice": choice, "note": note, "files": files, "line": line,
               "by": self.headers.get("Tailscale-User-Login") or "local"}
        with lock:
            with open(ANSWERS, "a") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            q = load_queue()
            q[iid] = {"session": "orch-" + it["team"], "master": it["master"], "line": line, "status": "queued",
                      "tries": 0, "errors": 0, "at": time.time()}
            save_queue(q)
        log(f"answer {iid} by {rec['by']}: {choice} {note}")
        kick.set()
        for _ in range(30):   # wait up to 3 s for the first try
            time.sleep(0.1)
            st = load_queue().get(iid, {})
            if st.get("tries"):
                break
        self.send(200, json.dumps({"wake": st.get("status", "queued")}), "application/json")


def main():
    global TOKEN
    port = 8377
    if len(sys.argv) == 3 and sys.argv[1] == "--port":
        port = int(sys.argv[2])
    os.makedirs(CACHE, exist_ok=True)
    os.makedirs(UPLOADS, mode=0o700, exist_ok=True)
    os.chmod(BOX, 0o700)
    TOKEN = token()
    threading.Thread(target=sender, daemon=True).start()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), H)
    log(f"listening on 127.0.0.1:{port}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
