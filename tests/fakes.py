"""Tiny in-process IMAP, SMTP and Claude-API servers (stdlib only) for testing responder.py end to end."""
import base64
import datetime as dt
import json
import re
import socketserver
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


def tokens(s):
    return [a or b for a, b in re.findall(r'"([^"]*)"|(\S+)', s)]


class ImapState:
    def __init__(self):
        self.folders = {"INBOX": [], "Sent Messages": []}
        self.next_uid = 1
        self.password = "app-pass"
        self.users = None          # None = any username; or a set of accepted usernames
        self.fail_store = False
        self.lock = threading.Lock()

    def add(self, folder, raw, when=None, flags=()):
        raw = raw.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")   # real servers store CRLF
        with self.lock:
            m = {"uid": self.next_uid, "raw": raw, "flags": set(flags),
                 "date": when or dt.datetime.now(dt.timezone.utc)}
            self.next_uid += 1
            self.folders[folder].append(m)
            return m


class ImapHandler(socketserver.StreamRequestHandler):
    def send(self, s):
        self.wfile.write(s if isinstance(s, bytes) else s.encode())

    def handle(self):
        st = self.server.state
        self.folder = None
        self.send("* OK IMAP4rev1 fake ready\r\n")
        while True:
            line = self.rfile.readline()
            if not line:
                return
            line = line.decode().rstrip("\r\n")
            tag, _, rest = line.partition(" ")
            cmd, _, args = rest.partition(" ")
            cmd = cmd.upper()
            if cmd == "CAPABILITY":
                self.send("* CAPABILITY IMAP4rev1\r\n")
            elif cmd == "LOGIN":
                t = tokens(args)
                if len(t) == 2 and t[1] == st.password and (st.users is None or t[0] in st.users):
                    self.send(f"{tag} OK LOGIN done\r\n")
                else:
                    self.send(f"{tag} NO [AUTHENTICATIONFAILED] bad credentials\r\n")
                continue
            elif cmd == "LIST":
                self.send('* LIST (\\HasNoChildren) "/" "INBOX"\r\n')
                self.send('* LIST (\\HasNoChildren \\Sent) "/" "Sent Messages"\r\n')
            elif cmd in ("SELECT", "EXAMINE"):
                name = tokens(args)[0]
                if name not in st.folders:
                    self.send(f"{tag} NO no such mailbox\r\n")
                    continue
                self.folder = name
                self.send(f"* {len(st.folders[name])} EXISTS\r\n")
                self.send(f"{tag} OK [{'READ-ONLY' if cmd == 'EXAMINE' else 'READ-WRITE'}] done\r\n")
                continue
            elif cmd == "NOOP":
                pass
            elif cmd == "LOGOUT":
                self.send("* BYE\r\n")
                self.send(f"{tag} OK bye\r\n")
                return
            elif cmd == "APPEND":
                m = re.match(r'("[^"]*"|\S+)\s*(\([^)]*\))?\s*("[^"]*")?\s*\{(\d+)\}$', args)
                name, flags, size = m.group(1).strip('"'), m.group(2), int(m.group(4))
                self.send("+ Ready\r\n")
                raw = self.rfile.read(size)
                self.rfile.readline()
                st.add(name, raw, flags=set((flags or "").strip("()").split()))
            elif cmd == "UID":
                sub, _, a2 = args.partition(" ")
                sub = sub.upper()
                msgs = st.folders[self.folder]
                if sub == "SEARCH":
                    t = [x.upper() for x in tokens(a2)]
                    res = []
                    for m in msgs:
                        ok = True
                        i = 0
                        while i < len(t):
                            if t[i] == "UNANSWERED" and "\\Answered" in m["flags"]:
                                ok = False
                            if t[i] == "UNDELETED" and "\\Deleted" in m["flags"]:
                                ok = False
                            if t[i] == "SINCE":
                                since = dt.datetime.strptime(t[i + 1].title(), "%d-%b-%Y").date()
                                if m["date"].date() < since:
                                    ok = False
                                i += 1
                            i += 1
                        if ok:
                            res.append(str(m["uid"]))
                    self.send("* SEARCH " + " ".join(res) + "\r\n")
                elif sub == "FETCH":
                    seqset, _, items = a2.partition(" ")
                    want = set()
                    for part in seqset.split(","):
                        want.add(int(part))
                    for m in msgs:
                        if m["uid"] not in want:
                            continue
                        idate = m["date"].strftime("%d-%b-%Y %H:%M:%S +0000")
                        if "HEADER.FIELDS" in items.upper():
                            fields = re.search(r"HEADER\.FIELDS \(([^)]*)\)", items, re.I).group(1).upper().split()
                            head = m["raw"].split(b"\r\n\r\n")[0].decode("utf-8", "replace")
                            keep = [ln for ln in head.split("\r\n") if ln.split(":")[0].upper() in fields]
                            payload = ("\r\n".join(keep) + "\r\n\r\n").encode()
                            label = f"BODY[HEADER.FIELDS ({' '.join(fields)})]"
                        else:
                            payload, label = m["raw"], "BODY[]"
                        self.send(f'* {msgs.index(m) + 1} FETCH (UID {m["uid"]} INTERNALDATE "{idate}" {label} {{{len(payload)}}}\r\n')
                        self.send(payload)
                        self.send(")\r\n")
                elif sub == "STORE":
                    if st.fail_store:
                        self.send(f"{tag} NO store failed\r\n")
                        continue
                    uid = int(a2.split(" ")[0])
                    for m in msgs:
                        if m["uid"] == uid:
                            m["flags"].add("\\Answered")
            else:
                self.send(f"{tag} BAD unknown command\r\n")
                continue
            self.send(f"{tag} OK done\r\n")


class SmtpState:
    def __init__(self):
        self.messages = []
        self.temp_failures = 0   # answer the next N DATA commands with 451
        self.reject_auth = False
        self.connections = 0


class SmtpHandler(socketserver.StreamRequestHandler):
    def send(self, s):
        self.wfile.write((s + "\r\n").encode())

    def handle(self):
        st = self.server.state
        st.connections += 1
        self.send("220 fake smtp")
        sender, rcpts = None, []
        while True:
            line = self.rfile.readline()
            if not line:
                return
            line = line.decode().rstrip("\r\n")
            u = line.upper()
            if u.startswith("EHLO") or u.startswith("HELO"):
                self.send("250-fake")
                self.send("250 AUTH PLAIN")
            elif u.startswith("AUTH"):
                self.send("535 5.7.8 authentication failed" if st.reject_auth else "235 ok")
            elif u.startswith("MAIL FROM"):
                sender = line
                self.send("250 ok")
            elif u.startswith("RCPT TO"):
                rcpts.append(re.search(r"<([^>]*)>", line).group(1))
                self.send("250 ok")
            elif u == "DATA":
                if st.temp_failures > 0:
                    st.temp_failures -= 1
                    self.send("451 try later")
                    continue
                self.send("354 go")
                data = b""
                while True:
                    ln = self.rfile.readline()
                    if ln == b".\r\n":
                        break
                    data += ln
                st.messages.append({"from": sender, "to": list(rcpts), "raw": data})
                rcpts = []
                self.send("250 queued")
            elif u == "QUIT":
                self.send("221 bye")
                return
            else:
                self.send("250 ok")


class ClaudeState:
    def __init__(self):
        self.requests = []
        self.script = []      # list of (http_code, body_or_None); consumed first, then default behaviour
        self.default_reply = "You ask about it.\n\nGod love you,\n+ Fulton J. Sheen"


class ClaudeHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        st = self.server.state
        n = int(self.headers.get("content-length", 0))
        req = json.loads(self.rfile.read(n))
        st.requests.append(req)
        if st.script:
            code, body = st.script.pop(0)
        else:
            text = req["messages"][0]["content"]
            if "NEWSLETTERISH" in text:
                inp = {"reply": False, "urgent": False, "reason": "promo", "body": ""}
            else:
                inp = {"reply": True, "urgent": "SUICIDE" in text, "reason": "sincere", "body": st.default_reply}
            code, body = 200, {"content": [{"type": "tool_use", "name": "decide", "input": inp}]}
        raw = (body if isinstance(body, str) else json.dumps(body)).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class Servers:
    def __enter__(self):
        class TS(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True
        self.imap, self.smtp = TS(("127.0.0.1", 0), ImapHandler), TS(("127.0.0.1", 0), SmtpHandler)
        self.claude = HTTPServer(("127.0.0.1", 0), ClaudeHandler)
        self.imap.state, self.smtp.state, self.claude.state = ImapState(), SmtpState(), ClaudeState()
        for s in (self.imap, self.smtp, self.claude):
            threading.Thread(target=s.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True).start()
        return self

    def __exit__(self, *a):
        for s in (self.imap, self.smtp, self.claude):
            s.shutdown()
            s.server_close()
