"""Sandbox test of ClaudeMail v4 on a Mac: a scratch ResilioSync tree and home folder, real mail.py subprocesses, and
fakes for everything outside ClaudeMail: the desktop app's session store, `open` (the claude:// links), session
processes with a real inbox socket and registry entry, the command-line Claude, and `osascript`.

    python3 tests/e2e_v4.py <scratch folder>
"""
import json, os, shutil, subprocess, sys, time, uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIVE = HERE.parent                                                  # Services/ClaudeMail
BASE = Path(sys.argv[1]).resolve() / "claudemail-v4"
SOCKETS = Path("/private/tmp/claude-" + str(os.getuid())) / "cmt"   # AF_UNIX paths must stay under ~104 bytes
shutil.rmtree(BASE, ignore_errors=True)
shutil.rmtree(SOCKETS, ignore_errors=True)
SOCKETS.mkdir(parents=True)
ROOT, HOME, BIN = BASE / "ResilioSync", BASE / "home", BASE / "bin"
SVC = ROOT / "Services" / "ClaudeMail"
APP = HOME / "Library" / "Application Support" / "Claude"
STORE = APP / "claude-code-sessions" / "acct" / "org"
for d in [SVC / "config", HOME / ".claude" / "sessions", HOME / ".claude" / "claudemail", STORE, BIN, HOME / "received"]:
    d.mkdir(parents=True)
(APP / "config.json").write_text(json.dumps({"lastKnownAccountUuid": "acct"}))
OTHER = APP / "claude-code-sessions" / "other-account" / "org"           # another account's store: not the sidebar
OTHER.mkdir(parents=True)
(OTHER / "local_zz.json").write_text(json.dumps({"sessionId": "local_zz", "cliSessionId": "zz", "cwd": "/x", "title": "Other account",
                                                 "isArchived": False, "lastActivityAt": int(time.time() * 1000)}))
# A hook that finds no dispatcher heartbeat restarts the machine's real LaunchAgent (launchctl is not faked): the marker
# of a kick just made keeps the sandbox from doing that (it did once, on 2026-10-26).
(HOME / ".claude" / "claudemail" / "kick.json").write_text(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S")
                                                                       + time.strftime("%z")[:3] + ":" + time.strftime("%z")[3:]}))
(HOME / ".claude" / "claudemail" / "logs").mkdir()
(HOME / ".claude" / "claudemail" / "logs" / "claudemail.pulvermaar.250101.log").write_text("old\n")    # pruned at start (TA2)
shutil.copy(LIVE / "mail.py", SVC / "mail.py")
shutil.copy(LIVE / "signed_mail.py", SVC / "signed_mail.py")
cfg = json.loads((LIVE / "config" / "defaults.json").read_text())
cfg.update(poll_seconds=0.5, open_minutes=0.1, retry_minutes=0.2)
(SVC / "config" / "defaults.json").write_text(json.dumps(cfg))
FOO, BAR = ROOT / "Projects" / "260101 Foo", ROOT / "Services" / "Bar"
FOO.mkdir(parents=True), BAR.mkdir(parents=True)
PY = sys.executable

# ---- fakes
(BIN / "fake_session.py").write_text('''import json, os, socket, sys, time
from pathlib import Path
sid, cwd, home = sys.argv[1], sys.argv[2], Path(sys.argv[3])
path = "''' + str(SOCKETS) + '''/" + sid[:8] + "-" + str(os.getpid()) + ".sock"
server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
server.bind(path)
server.listen(5)
entry = {"pid": os.getpid(), "sessionId": sid, "cwd": cwd, "startedAt": int(time.time() * 1000), "status": "idle",
         "messagingSocketPath": path, "hostSessionId": "local_" + sid}
token = "peer-" + str(os.getpid())
(home / ".claude" / "sessions" / (str(os.getpid()) + ".abc123.key")).write_text(json.dumps({"peerToken": token}))
(home / ".claude" / "sessions" / (str(os.getpid()) + ".json")).write_text(json.dumps(entry))
while True:
    conn, _ = server.accept()
    data = b""
    while True:
        chunk = conn.recv(65536)
        if not chunk:
            break
        data += chunk
    conn.close()
    lines = data.decode().splitlines()
    if not lines or json.loads(lines[0]) != {"type": "auth", "token": token}:     # what Windows enforces
        with open(home / "received" / (sid + ".refused"), "a") as fh:
            fh.write(data.decode() + chr(10))
        continue
    with open(home / "received" / (sid + ".jsonl"), "a") as fh:
        fh.write(chr(10).join(lines[1:]) + chr(10))
''')
(BIN / "open").write_text('''#!''' + PY + '''
import json, subprocess, sys, time, urllib.parse, uuid
from pathlib import Path
home = Path(''' + repr(str(HOME)) + ''')
store = Path(''' + repr(str(STORE)) + ''')
url = sys.argv[1]
with open(home / "opened.txt", "a") as fh:
    fh.write(url + "\\n")
if (home / "open_ignore").exists():
    sys.exit(0)
parsed = urllib.parse.urlparse(url)
query = dict(urllib.parse.parse_qsl(parsed.query))
known = [p for p in store.glob("local_*.json") if "cliSessionId" in json.loads(p.read_text())
         and json.loads(p.read_text())["cliSessionId"] == query.get("session")]
if parsed.netloc == "resume" and known:                      # a session the app has: restore it and start it
    record = dict(json.loads(known[0].read_text()), isArchived=False)
    known[0].write_text(json.dumps(record))
elif parsed.netloc == "resume":
    calls = [json.loads(l) for l in (home / "cli_calls.jsonl").read_text().splitlines()]
    call = [c for c in calls if c["session"] == query["session"]][-1]
    assert call["args"][-1].startswith("/rename "), call    # the app takes the transcript's custom title (live 2026-10-27)
    record = {"sessionId": "local_" + call["session"], "cliSessionId": call["session"], "cwd": call["cwd"],
              "title": call["args"][-1][len("/rename "):], "isArchived": False,
              "lastActivityAt": int(time.time() * 1000)}
    (store / (record["sessionId"] + ".json")).write_text(json.dumps(record))
else:
    sys.exit("fake open: unexpected " + url)
subprocess.Popen([''' + repr(PY) + ''', ''' + repr(str(BIN / "fake_session.py")) + ''', record["cliSessionId"], record["cwd"], str(home)],
                 start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
''')
CLI = APP / "claude-code" / "2.1.308" / "abc" / "claude.app" / "Contents" / "MacOS" / "claude"
CLI.parent.mkdir(parents=True)
CLI.write_text('''#!''' + PY + '''
import json, os, sys
from pathlib import Path
home = Path(''' + repr(str(HOME)) + ''')
if (home / "cli_fail").exists():
    print("Not logged in · Please run /login")
    sys.exit(1)
args = sys.argv[1:]
with open(home / "cli_calls.jsonl", "a") as fh:
    fh.write(json.dumps({"session": args[args.index("--session-id") + 1], "cwd": os.getcwd(), "args": args}) + "\\n")
''')
(BIN / "osascript").write_text("#!/bin/sh\necho \"$@\" >> " + str(HOME / "notified.txt") + "\n")
(BIN / "launchctl").write_text("#!/bin/sh\necho \"$@\" >> " + str(HOME / "launchctl.txt") + "\n")   # never the real LaunchAgent
for f in [BIN / "open", BIN / "osascript", BIN / "launchctl", CLI]:
    f.chmod(0o755)

ENV = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
ENV.update(HOME=str(HOME), PATH=str(BIN) + ":" + os.environ["PATH"], CLAUDE_MAILBOX_MACHINE="Pulvermaar")
fails, procs = [], []


def run(args, session=None, stdin="", check=True, env=None):
    e = dict(env or ENV)
    if session:
        e["CLAUDE_CODE_SESSION_ID"] = session
    p = subprocess.run([PY, "-B", str(SVC / "mail.py")] + args, input=stdin, capture_output=True, text=True, env=e)
    if check and p.returncode != 0:
        print("EXIT", p.returncode, args, p.stdout, p.stderr)
    return p


def expect(label, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + label + ("" if cond else "  " + str(detail)[:1500]))
    if not cond:
        fails.append(label)


def wait_for(cond, seconds=20):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.3)
    return bool(cond())


def sid(letter):
    return (letter * 8) + "-0000-4000-8000-" + letter * 12


def store(letter, folder, title, archived=False):
    record = {"sessionId": "local_" + sid(letter), "cliSessionId": sid(letter), "cwd": str(folder), "title": title,
              "isArchived": archived, "lastActivityAt": int(time.time() * 1000), "postTurnSummary": {"status_detail": title + " going"}}
    (STORE / (record["sessionId"] + ".json")).write_text(json.dumps(record))


def start_session(letter, folder):
    procs.append(subprocess.Popen([PY, str(BIN / "fake_session.py"), sid(letter), str(folder), str(HOME)],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    wait_for(lambda: any(sid(letter) in p.read_text() for p in (HOME / ".claude" / "sessions").glob("*.json")))


def received(session):
    path = HOME / "received" / (session + ".jsonl")
    return [json.loads(l)["message"]["content"] for l in path.read_text().splitlines()] if path.is_file() else []


def body(text):
    f = BASE / ("body-" + str(uuid.uuid4())[:8] + ".md")
    f.write_text(text)
    return str(f)


def sent_path(out):
    return Path(out.split("sent ", 1)[1].splitlines()[0].strip())


def delivered_to(sent, session):
    return lambda: (sent.parent / "read" / sent.name).is_file() and any(sent.name in t for t in received(session))


def opened_sessions():
    calls = HOME / "cli_calls.jsonl"
    return [json.loads(l)["session"] for l in calls.read_text().splitlines()] if calls.is_file() else []


try:
    A, B, C, E, F, S0 = (sid(x) for x in "abcefe"[:5] + "9")
    S0 = sid("9")
    store("a", FOO, "Foo feature A"); store("b", FOO, "Foo feature B"); store("c", FOO, "Old Foo", archived=True)
    store("e", FOO, "Foo feature E"); store("f", FOO, "Foo feature F"); store("9", BAR, "Bar work")
    # a session the user cleared: the app dropped its cliSessionId and kept the old one as prior (Altiplano, 2026-10-28)
    K = sid("k")
    (STORE / ("local_" + K + ".json")).write_text(json.dumps({"sessionId": "local_" + K, "cwd": str(FOO), "title": "Cleared Foo",
        "isArchived": False, "lastActivityAt": int(time.time() * 1000) - 60000, "priorCliSessionIds": [sid("j")]}))
    start_session("a", FOO)

    # hook and whoami
    p = run(["hook"], stdin=json.dumps({"session_id": S0, "cwd": str(BAR), "transcript_path": ""}),
            env=dict(ENV, CLAUDE_PID="4242"))
    rec = json.loads((HOME / ".claude/claudemail/sessions" / (S0 + ".json")).read_text())
    expect("hook records desk and claude pid", rec["desk"] == "Bar" and rec["claude_pid"] == 4242, rec)
    expect("whoami --desk", run(["whoami", "--desk"], session=S0).stdout.strip() == "Pulvermaar:Bar")
    run(["hook"], stdin=json.dumps({"session_id": "headless", "cwd": str(BAR)}), env=dict(ENV, CLAUDE_CODE_SESSION_ATTENDED="0"))
    expect("headless run not recorded", not (HOME / ".claude/claudemail/sessions/headless.json").exists())

    # listing
    out = run(["sessions", "Pulvermaar:Foo"]).stdout
    expect("sessions lists running and closed, not archived", A in out and B in out and C not in out and "idle" in out
           and "closed" in out and "Foo feature A going" in out, out)
    expect("desk name matched without date stamp", "Pulvermaar:Foo" in out, out)
    expect("another account's sessions are not listed", "Other account" not in run(["sessions", "Pulvermaar"]).stdout)

    # two-stage: a session must choose
    p = run(["send", "--to", "Pulvermaar:Foo", "--subject", "x", "--body-file", body("x")], session=S0, check=False)
    expect("send without a choice refused with the list", p.returncode != 0 and "Pick the session" in p.stderr and A in p.stdout, p.stdout + p.stderr)

    dispatcher = subprocess.Popen([PY, "-B", str(SVC / "mail.py"), "dispatch"], env=ENV, stdout=subprocess.DEVNULL,
                                  stderr=open(BASE / "dispatch.err", "w"))
    procs.append(dispatcher)

    # running session: socket
    m4 = sent_path(run(["send", "--to", "Pulvermaar:Foo", "--for-session", A[:8], "--subject", "T4 running", "--body-file", body("hello A")], session=S0).stdout)
    expect("T4 delivered to the running session by socket", wait_for(delivered_to(m4, A)), received(A))
    text = received(A)[-1] if received(A) else ""
    expect("T4 delivery names reply command and body", "--reply-to" in text and "hello A" in text and "opened this session" not in text, text)

    # a session still starting: its registry entry has the socket but no status yet (ended the dispatcher, Stahlwerk 2026-10-27)
    starting = HOME / ".claude" / "sessions" / (str(os.getpid()) + ".json")
    starting.write_text(json.dumps({"pid": os.getpid(), "sessionId": E, "cwd": str(FOO), "startedAt": int(time.time() * 1000),
                                    "messagingSocketPath": str(SOCKETS / "starting.sock"), "hostSessionId": "local_" + E}))
    time.sleep(2)
    p = run(["sessions", "Pulvermaar:Foo"], check=False)
    expect("T4b a starting session is listed as closed, and the dispatcher keeps running", dispatcher.poll() is None
           and p.returncode == 0 and E in p.stdout, (BASE / "dispatch.err").read_text() + p.stdout + p.stderr)
    starting.unlink()

    # closed session: continue link, then socket
    m5 = sent_path(run(["send", "--to", "Pulvermaar:Foo", "--for-session", B[:8], "--subject", "T5 closed", "--body-file", body("hello B")], session=S0).stdout)
    expect("T5 closed session opened and delivered", wait_for(delivered_to(m5, B)), (HOME / "opened.txt").read_text() if (HOME / "opened.txt").exists() else "")
    expect("T5 used the import link with its CLI id", "claude://resume?session=" + B in (HOME / "opened.txt").read_text())

    # new session: command-line turn, import, socket
    m6 = sent_path(run(["send", "--to", "Pulvermaar:Foo", "--new", "--name", "Foo  T6\n work", "--subject", "T6 new", "--body-file", body("hello new")], session=S0).stdout)
    expect("T6 new session started by the CLI", wait_for(lambda: len(opened_sessions()) == 1))
    new6 = opened_sessions()[0]
    call = json.loads((HOME / "cli_calls.jsonl").read_text().splitlines()[0])
    expect("T6 CLI ran in the desk folder", call["cwd"] == str(FOO.resolve()) or call["cwd"] == str(FOO), call)
    expect("T6 delivered to the new session", wait_for(delivered_to(m6, new6)))
    expect("T6 text says ClaudeMail opened it", "opened this session" in (received(new6) or [""])[-1])
    expect("T6 the proposed name went to the CLI as /rename, whitespace collapsed", call["args"][-1] == "/rename Foo T6 work"
           and not any(a.startswith("--name") or a == "/cost" for a in call["args"]), call)
    expect("T6 the new session carries the proposed title", json.loads((STORE / ("local_" + new6 + ".json")).read_text())["title"] == "Foo T6 work")
    p = run(["send", "--to", "Pulvermaar:Foo", "--new", "--subject", "x", "--body-file", body("x")], session=S0, check=False)
    expect("T6b a session sending --new without --name is refused", p.returncode != 0 and "--name" in p.stderr, p.stdout + p.stderr)
    p = run(["send", "--to", "Pulvermaar:Foo", "--new", "--name", "  ", "--subject", "x", "--body-file", body("x")], session=S0, check=False)
    expect("T6b a blank name is no name", p.returncode != 0 and "--name" in p.stderr, p.stdout + p.stderr)
    p = run(["send", "--to", "Pulvermaar:Foo", "--for-session", A[:8], "--name", "x", "--subject", "x", "--body-file", body("x")], session=S0, check=False)
    expect("T6c --name for an existing session is refused", p.returncode != 0 and "keeps its title" in p.stderr, p.stdout + p.stderr)

    # archived session: restored and delivered to itself
    m7 = sent_path(run(["send", "--to", "Pulvermaar:Foo", "--for-session", C, "--subject", "T7 archived", "--body-file", body("hello C")], session=S0).stdout)
    expect("T7 archived session restored and delivered to itself", wait_for(delivered_to(m7, C)) and len(opened_sessions()) == 1)
    expect("T7 no longer archived", wait_for(lambda: not json.loads((STORE / ("local_" + C + ".json")).read_text())["isArchived"]))
    # a session that is not on this machine: a new one, with a note
    G = sid("7")
    m7b = sent_path(run(["send", "--to", "Pulvermaar:Foo", "--for-session", G, "--subject", "T7b gone", "--body-file", body("hello G")], session=S0).stdout)
    expect("T7b unknown session -> new session", wait_for(lambda: len(opened_sessions()) == 2))
    new7 = opened_sessions()[1]
    expect("T7b delivered with a note naming the missing session", wait_for(delivered_to(m7b, new7)) and G in received(new7)[-1])
    expect("T7b the new session is titled by the subject", json.loads((STORE / ("local_" + new7 + ".json")).read_text())["title"] == "T7b gone")
    expect("T7b the search by CLI id passed the cleared session's record", dispatcher.poll() is None, (BASE / "dispatch.err").read_text())
    # the cleared session, named by its desktop id: nothing to resume, so it waits for the user to open it
    m7c = sent_path(run(["send", "--to", "Pulvermaar:Foo", "--for-session", "local_" + K, "--subject", "T7c cleared", "--body-file", body("x")], session=S0).stdout)
    st7c = lambda: json.loads(m7c.with_name(m7c.name[:-3] + ".delivery.json").read_text()) if m7c.with_name(m7c.name[:-3] + ".delivery.json").exists() else {}
    expect("T7c a cleared session waits for the user", wait_for(lambda: st7c().get("state") == "waiting for the user")
           and "no conversation to resume" in st7c().get("reason", "") and len(opened_sessions()) == 2 and dispatcher.poll() is None, st7c())
    start_session("k", FOO)                                 # the user opens it: the app gives it a CLI id, here the same
    expect("T7c delivered once the user opened it", wait_for(delivered_to(m7c, K), 40))
    # a program without a session gets a new session without choosing
    m8 = sent_path(run(["send", "--to", "Pulvermaar:Foo", "--to-own-desk", "--subject", "T8 lease notice", "--body-file", body("job done")]).stdout)
    expect("T8 program mail -> new session", wait_for(lambda: len(opened_sessions()) == 3) and wait_for(delivered_to(m8, opened_sessions()[2])))
    call8 = json.loads((HOME / "cli_calls.jsonl").read_text().splitlines()[2])
    expect("T8 a program's mail without a name opens the session titled by its subject", call8["args"][-1] == "/rename T8 lease notice", call8)

    # command-line Claude not signed in: waits for the user, retried later
    (HOME / "cli_fail").touch()
    m9 = sent_path(run(["send", "--to", "Pulvermaar:Foo", "--new", "--name", "-T9 leading dash", "--subject", "T9 no login", "--body-file", body("x")], session=S0).stdout)
    status9 = lambda: json.loads(m9.with_name(m9.name[:-3] + ".delivery.json").read_text()) if m9.with_name(m9.name[:-3] + ".delivery.json").exists() else {}
    expect("T9 waits for the user with the CLI's words", wait_for(lambda: status9().get("state") == "waiting for the user")
           and "Not logged in" in status9().get("reason", ""), status9())
    expect("T9 one notification", wait_for(lambda: (HOME / "notified.txt").exists() and "T9 no login" in (HOME / "notified.txt").read_text()))
    (HOME / "cli_fail").unlink()
    expect("T9 retried and delivered after retry_minutes", wait_for(lambda: (m9.parent / "read" / m9.name).is_file(), 40))
    expect("T9 notified once only", (HOME / "notified.txt").read_text().count("T9 no login") == 1)
    expect("TB2 the sender gets a notice that its mail waits for the user", wait_for(lambda: any("ClaudeMail notice: T9 no login" in t
           and "waits for the user" in t and "D-261012-2" in t for t in received(S0)), 30), received(S0)[-1:])

    # the app does not open the session in time: waits for the user, retried later
    (HOME / "open_ignore").touch()
    m10 = sent_path(run(["send", "--to", "Pulvermaar:Foo", "--for-session", E[:8], "--subject", "T10 slow", "--body-file", body("x")], session=S0).stdout)
    st10 = lambda: json.loads(m10.with_name(m10.name[:-3] + ".delivery.json").read_text()) if m10.with_name(m10.name[:-3] + ".delivery.json").exists() else {}
    applog = HOME / "Library/Logs/Claude/main.log"
    applog.parent.mkdir(parents=True, exist_ok=True)
    applog.write_text("2026-10-30 00:41:59 [info] [CCD] Warming session local_" + E + "\n"
                      "2026-10-30 00:41:59 [info] [CliGovernor] at cap; yielding warm spawn\n")
    expect("T10 opening times out to the user", wait_for(lambda: st10().get("state") == "waiting for the user", 30), st10())
    expect("TB4 the reason says the app did not start it, with what its log says", "the app did not start the session" in st10()["reason"]
           and "yielding warm spawn" in st10()["reason"] and "of its sessions have a process" in st10()["reason"], st10())
    (HOME / "open_ignore").unlink()
    expect("T10 retried and delivered", wait_for(delivered_to(m10, E), 40))

    # a stale registry entry whose pid is alive but whose socket is gone
    (HOME / ".claude/sessions" / (str(os.getpid()) + ".x.key")).write_text(json.dumps({"peerToken": "t"}))
    (HOME / ".claude/sessions/1.json").write_text(json.dumps({"pid": os.getpid(), "sessionId": F, "cwd": str(FOO), "startedAt": 1,
                                                              "status": "idle", "messagingSocketPath": str(SOCKETS / "gone.sock")}))
    m18 = sent_path(run(["send", "--to", "Pulvermaar:Foo", "--for-session", F[:8], "--subject", "T18 stale", "--body-file", body("x")], session=S0).stdout)
    expect("T18 failed post falls through to opening, then delivered", wait_for(delivered_to(m18, F), 30))

    # reply goes back to the sending session (closed: opened)
    p = run(["send", "--reply-to", str(m4.parent / "read" / m4.name), "--body-file", body("answer from A")], session=A)
    m11 = sent_path(p.stdout)
    expect("T11 reply lands on the sender's desk for the sender", "desks/Bar/" in m11.as_posix() and S0 in m11.read_text(), p.stdout)
    expect("T11 reply delivered to the sender", wait_for(delivered_to(m11, S0), 30))
    expect("T11 subject Re:", "Re: T4 running" in m11.parent.joinpath("read", m11.name).read_text())
    p = run(["send", "--reply-to", str(m4.parent / "read" / m4.name), "--body-file", body("A again")], session=A,
            env=dict(ENV, CLAUDE_CODE_HOST_SESSION_ID="local_" + A))
    m12a = sent_path(p.stdout)
    expect("T12 a desktop session signs its mail with its desktop id", "from_session: local_" + A in m12a.read_text(), m12a.read_text())
    expect("T12 delivered to S0", wait_for(delivered_to(m12a, S0), 30))
    m12 = sent_path(run(["send", "--reply-to", str(m12a.parent / "read" / m12a.name), "--body-file", body("back to A")], session=S0).stdout)
    expect("T12 the reply names A's desktop id", "for_session: local_" + A in m12.read_text(), m12.read_text())
    expect("T12 mail to a desktop id reaches the running session", wait_for(delivered_to(m12, A), 30))

    # report
    reports = sorted((SVC / "mail/Pulvermaar/reports").glob("*.json"))
    rep = json.loads(reports[-1].read_text())
    expect("report lists sessions and desks", any(s["session"] == "local_" + A and s["state"] == "idle" for s in rep["sessions"])
           and "Foo" in rep["desks"] and "Home" in rep["desks"] and "count" in rep["undelivered"], rep)
    expect("at most five reports kept", len(reports) <= 5)
    expect("TA1 the report states its protocol and the peers it sees", rep["protocol"] == 4 and isinstance(rep["seen"], dict)
           and rep["undelivered"]["unseen"] == 0, rep)
    expect("TA2 a log older than log_keep_days was pruned at start", not (HOME / ".claude/claudemail/logs/claudemail.pulvermaar.250101.log").exists())
    newest = lambda: max(p.name for p in (SVC / "mail/Pulvermaar/reports").glob("*.json"))
    before = newest()
    time.sleep(3)
    expect("TA3 nothing changed, so no new report within report_minutes", newest() == before, before)
    store("m", BAR, "Report trigger")
    expect("TA3 a new session in the store writes a report at once", wait_for(lambda: newest() != before, 10), before)
    time.sleep(2)
    before = newest()
    for letter, bump in (("e", 5000), ("f", 9000), ("e", 12000)):        # two sessions trade places by activity
        rec = json.loads((STORE / ("local_" + sid(letter) + ".json")).read_text())
        rec["lastActivityAt"] = int(time.time() * 1000) + bump
        (STORE / ("local_" + sid(letter) + ".json")).write_text(json.dumps(rec))
        time.sleep(1.2)
    expect("TA3b sessions trading places by activity write no report", newest() == before, newest())

    # another machine, from its report
    D = sid("d")
    (SVC / "mail/Mistral/reports").mkdir(parents=True)
    (SVC / "mail/Mistral/reports/261026-120000-000.json").write_text(json.dumps({
        "machine": "Mistral", "updated": time.strftime("%Y-%m-%dT%H:%M:%S") + time.strftime("%z")[:3] + ":" + time.strftime("%z")[3:],
        "sessions": [{"session": D, "desk": "Sample Project", "folder": "C:/x", "title": "TA", "state": "idle",
                      "last_active": "2026-10-26T12:00:00-05:00", "doing": ""}],
        "desks": {"Sample Project": "C:/x", "Home": "C:/Users/goocy"}, "undelivered": {"count": 0}}))
    p = run(["send", "--to", "Mistral:sample project", "--for-session", D[:8], "--subject", "T13", "--body-file", body("x")], session=S0)
    expect("T13 to another machine's session", p.returncode == 0 and D in sent_path(p.stdout).read_text()
           and "desks/Sample Project/" in sent_path(p.stdout).as_posix(), p.stdout + p.stderr)
    p = run(["send", "--to", "Mistral:Nope", "--new", "--name", "x", "--subject", "x", "--body-file", body("x")], session=S0, check=False)
    expect("T14 unknown desk refused", p.returncode != 0 and "No desk" in p.stderr, p.stderr)
    p = run(["send", "--to", "Moosfels", "--new", "--name", "x", "--subject", "x", "--body-file", body("x")], session=S0, check=False)
    expect("T14b machine without a report still gets --new mail on Home", p.returncode == 0 and "Moosfels/desks/Home/" in p.stdout, p.stdout + p.stderr)
    p = run(["send", "--to", "Moosfels:Foo", "--new", "--name", "x", "--subject", "x", "--body-file", body("x")], session=S0, check=False)
    expect("T14c but no unknown desk there", p.returncode != 0 and "No desk" in p.stderr, p.stderr)
    (SVC / "mail/Teppich/reports").mkdir(parents=True)
    (SVC / "mail/Teppich/desks/Computers").mkdir(parents=True)
    (SVC / "mail/Teppich/reports/261026-120000-000.json").write_text(json.dumps({"listening": {}, "updated": "2026-10-26T12:00:00-05:00"}))
    p = run(["sessions", "Teppich"], session=S0, check=False)
    expect("T14d a machine still on a v3 report lists its desk folders", p.returncode == 0 and "Teppich:Computers" in p.stdout, p.stdout + p.stderr)
    p = run(["send", "--to", "Teppich:Computers", "--for-session", sid("8"), "--subject", "T14d", "--body-file", body("x")], session=S0, check=False)
    expect("T14d mail with a full id to such a machine is accepted", p.returncode == 0 and "desks/Computers/" in p.stdout, p.stdout + p.stderr)
    expect("TA4 send says that machine's dispatcher is silent instead of 'within seconds'",
           "silent since" in p.stdout and "within seconds" not in p.stdout, p.stdout)
    expect("TA4 sessions shows the same warning", "! Teppich's dispatcher is silent since" in run(["sessions", "Teppich"]).stdout)
    p = run(["send", "--reply-to", str(m8.parent / "read" / m8.name), "--body-file", body("x")], session=A, check=False)
    expect("T15 reply to a program refused", p.returncode != 0 and "is a program" in p.stderr, p.stderr)
    p = run(["send", "--to", "all", "--subject", "x", "--body-file", body("x")], session=S0, check=False)
    expect("T16 --to all needs --new", p.returncode != 0)
    p = run(["send", "--to", "all", "--new", "--name", "T16", "--subject", "T16 all", "--body-file", body("x")], session=S0)
    expect("T16 --to all --new reaches every other machine's Home", p.stdout.count("sent ") == 6 and "Pulvermaar/" not in p.stdout, p.stdout)
    p = run(["send", "--to", "Mistral:General", "--new", "--name", "x", "--subject", "x", "--body-file", body("x")], session=S0)
    expect("T16b General means Home", p.returncode == 0 and "desks/Home/" in p.stdout, p.stdout + p.stderr)

    # One id on three machines, as after Mistral's fan-out of 2026-10-30 (ids are unique per machine only): a path names
    # its own message, also after it moved to read/, and a bare id is refused. Altiplano sorts first, which is the
    # message the old lookup by bare id returned, so each check below asks for another machine's.
    same = "261030-004134-Mistral"
    fixture = [("Altiplano", "Home", "1", "altiplano-session"), ("Pulvermaar", "ClaudeMail", "2", "pulvermaar-session"),
               ("Stahlwerk", "Home", "3", "")]                     # Stahlwerk's is not delivered yet: no read/, no record
    for machine, desk, letter, delivered in fixture:
        folder = SVC / "mail" / machine / "desks" / "Home" / ("read" if delivered else "")
        folder.mkdir(parents=True, exist_ok=True)
        (folder / (same + ".md")).write_text("---\nfrom: Mistral:" + desk + "\nfrom_session: " + sid(letter) + "\nto: " + machine
                                             + ":Home\nsubject: T19 to " + machine + "\nsent: 2026-10-30T00:41:34-05:00\nid: " + same
                                             + "\nconversation: " + same + "\n---\n\nRetire the Post office on " + machine + ".\n")
        if delivered:
            (folder / (same + ".delivery.json")).write_text(json.dumps({"state": "delivered", "delivered_to": delivered}))
    p = run(["status", same], check=False)
    expect("T19 status of an id on three machines is refused with all three paths", p.returncode != 0 and "names 3 messages" in p.stderr
           and all("/" + m + "/desks/Home/" in p.stderr for m, _, _, _ in fixture), p.stdout + p.stderr)
    gone = SVC / "mail/Pulvermaar/desks/Home" / (same + ".md")      # where it was before the dispatcher delivered it
    p = run(["status", str(gone)], check=False)
    expect("T19b status of a delivered message's old path shows that machine's message and record", p.returncode == 0
           and p.stdout.startswith((gone.parent / "read" / gone.name).as_posix()) and "to Pulvermaar:Home" in p.stdout
           and "pulvermaar-session" in p.stdout and "altiplano-session" not in p.stdout, p.stdout + p.stderr)
    p = run(["status", "Pulvermaar/desks/Home/" + same + ".md"], check=False)
    expect("T19c a path relative to the mail folder names its machine's message", p.returncode == 0
           and "to Pulvermaar:Home" in p.stdout and "pulvermaar-session" in p.stdout, p.stdout + p.stderr)
    p = run(["status", "/Users/someone/ResilioSync/Services/ClaudeMail/mail/Stahlwerk/desks/Home/read/" + same + ".md"], check=False)
    expect("T19d another machine's path names the message on that machine, also when it is not in read/ yet", p.returncode == 0
           and "to Stahlwerk:Home" in p.stdout and "not yet seen by the dispatcher" in p.stdout, p.stdout + p.stderr)
    p = run(["status", "Moosfels/desks/Home/" + same + ".md"], check=False)
    expect("T19e a path to a machine without that message is not found elsewhere", p.returncode != 0
           and "No such message" in p.stderr, p.stdout + p.stderr)
    p = run(["send", "--reply-to", same, "--body-file", body("which one?")], session=A, check=False)
    expect("T19f reply to an id on three machines is refused, nothing sent", p.returncode != 0 and "names 3 messages" in p.stderr
           and "sent " not in p.stdout, p.stdout + p.stderr)
    p = run(["send", "--reply-to", str(gone), "--body-file", body("done on Pulvermaar")], session=A, check=False)
    reply = sent_path(p.stdout) if p.returncode == 0 else Path("none")
    expect("T19g reply to a delivered message's old path goes to that message's sender", p.returncode == 0
           and "desks/ClaudeMail/" in reply.as_posix() and "Mistral/" in reply.as_posix() and "for_session: " + sid("2") in reply.read_text()
           and "Re: T19 to Pulvermaar" in reply.read_text() and "in_reply_to: " + same in reply.read_text(), p.stdout + p.stderr)

    # install-hook drops the old Stop waiter and keeps other hooks
    (HOME / ".claude/settings.json").write_text(json.dumps({"hooks": {
        "Stop": [{"hooks": [{"type": "command", "command": "python ~/ResilioSync/Services/ClaudeMail/mail.py wait", "asyncRewake": True}]},
                 {"hooks": [{"type": "command", "command": "other stop"}]}],
        "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "python ~/ResilioSync/Services/ClaudeMail/mail.py hook"}]}]},
        "model": "x"}))
    run(["install-hook"])
    s = json.loads((HOME / ".claude/settings.json").read_text())
    expect("install-hook: Stop waiter gone, other kept, hook on two events",
           [h["command"] for e in s["hooks"]["Stop"] for h in e["hooks"]] == ["other stop"]
           and len(s["hooks"]["UserPromptSubmit"]) == 1 and "SessionStart" in s["hooks"] and s["model"] == "x", s)
    expect("old Stop hook command is a no-op", run(["wait"], stdin="{}").returncode == 0)

    # a settings file the app cannot parse (a UTF-8 BOM, Altiplano 2026-10-27 to 10-30)
    settings = HOME / ".claude/settings.json"
    plain = settings.read_bytes()
    settings.write_bytes(b"\xef\xbb\xbf" + plain)
    latest = lambda: json.loads(max((SVC / "mail/Pulvermaar/reports").glob("*.json")).read_text())
    expect("TA5 an unparsable settings file is in the report", wait_for(lambda: "BOM" in latest()["settings_problem"], 10), latest())
    expect("TA5 and notified once", wait_for(lambda: (HOME / "notified.txt").read_text().count("settings unreadable") == 1, 10))
    settings.write_bytes(plain)
    expect("TA5 cleared once it parses again", wait_for(lambda: latest()["settings_problem"] == "", 10), latest())

    # a peer that does not see this machine's reports: one-way sync (Sandbank, 2026-10-29)
    stamp = time.strftime("%y%m%d-%H%M%S", time.gmtime())
    (SVC / "mail/Mistral/reports" / (stamp + "-000Z.json")).write_text(json.dumps({
        "machine": "Mistral", "updated": time.strftime("%Y-%m-%dT%H:%M:%S") + time.strftime("%z")[:3] + ":" + time.strftime("%z")[3:],
        "sessions": [], "desks": {"Home": "C:/Users/goocy"}, "undelivered": {"count": 0}, "protocol": 4,
        "seen": {"Pulvermaar": "261001-000000-000Z"}}))
    expect("TA6 sessions warns that Mistral has not seen this machine's reports",
           "has not seen this machine's reports since 261001" in run(["sessions", "Mistral"]).stdout)
    ahead = time.time() + 3 * 3600
    (SVC / "mail/Stahlwerk/reports").mkdir(parents=True, exist_ok=True)
    (SVC / "mail/Stahlwerk/reports" / (time.strftime("%y%m%d-%H%M%S", time.gmtime(ahead)) + "-000Z.json")).write_text(json.dumps({
        "machine": "Stahlwerk", "updated": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ahead)) + time.strftime("%z")[:3] + ":" + time.strftime("%z")[3:],
        "sessions": [], "desks": {"Home": "C:/Users/goocy"}, "undelivered": {"count": 0}}))
    expect("TA6b a peer whose clock runs ahead is named as such", "clock runs 1" in run(["sessions", "Stahlwerk"]).stdout,
           run(["sessions", "Stahlwerk"]).stdout)
    future = SVC / "mail/Pulvermaar/reports" / (time.strftime("%y%m%d-%H%M%S", time.gmtime(ahead)) + "-000Z.json")
    future.write_text(json.dumps({"machine": "Pulvermaar", "updated": "x"}))
    store("p", BAR, "Report trigger 2")
    expect("TA6c a report named in the future is pruned, the new one kept", wait_for(lambda: not future.exists(), 10)
           and latest()["machine"] == "Pulvermaar" and "seen" in latest(), latest())

    # a desk of the synced tree starts its sessions in its folder, not in the folder of its newest session, which may
    # be gone (Moosfels' ClaudeMail desk pointed at the removed Steward folder, 2026-10-30)
    store("q", BAR / "gone", "Newest Bar session, folder removed")
    rec = json.loads((STORE / ("local_" + sid("q") + ".json")).read_text())
    rec["lastActivityAt"] = int(time.time() * 1000) + 60000
    (STORE / ("local_" + sid("q") + ".json")).write_text(json.dumps(rec))
    calls_before = len(opened_sessions())
    mq = sent_path(run(["send", "--to", "Pulvermaar:Bar", "--new", "--name", "TA10 tree folder", "--subject", "TA10", "--body-file", body("x")], session=S0).stdout)
    expect("TA10 a new session for a synced desk starts in its folder", wait_for(lambda: len(opened_sessions()) > calls_before)
           and json.loads((HOME / "cli_calls.jsonl").read_text().splitlines()[-1])["cwd"] in (str(BAR), str(BAR.resolve())),
           (HOME / "cli_calls.jsonl").read_text().splitlines()[-1])

    expect("dispatcher still running", dispatcher.poll() is None, (BASE / "dispatch.err").read_text())
    expect("no session refused a post for a bad auth line", not list((HOME / "received").glob("*.refused")))
    (SVC / "mail/Pulvermaar/desks/Foo/garbage.md").write_bytes(b"\xff\xfe no header")
    (SVC / "mail/Pulvermaar/desks/Foo/noheader.md").write_text("just text")
    expect("malformed files go to rejected/ and the dispatcher lives on", wait_for(lambda: (SVC / "mail/Pulvermaar/desks/Foo/rejected/garbage.md").is_file()
           and (SVC / "mail/Pulvermaar/desks/Foo/rejected/noheader.md").is_file()) and dispatcher.poll() is None)
    sent_now = time.strftime("%Y-%m-%dT%H:%M:%S") + time.strftime("%z")[:3] + ":" + time.strftime("%z")[3:]
    (SVC / "mail/Pulvermaar/desks/Foo/261030-000001-Pulvermaar.md").write_text("---\nfrom: Pulvermaar:Bar\nfrom_session: " + S0
        + "\nto: Pulvermaar:Foo\nsubject: \nsent: " + sent_now + "\nid: 261030-000001-Pulvermaar\n---\n\nno subject\n")
    expect("TB3 a rejected message with a sender sends that session a notice", wait_for(lambda: any("was rejected" in t and "lacks subject" in t
           for t in received(S0)), 30), received(S0)[-1:])
    calls = len(opened_sessions())
    (SVC / "mail/Pulvermaar/desks/Foo/261030-000002-Pulvermaar.md").write_text("---\nfrom: Pulvermaar:Bar\nfrom_session: " + S0
        + "\nto: Pulvermaar:Foo\nfor_session: local_zz\nsubject: TB5 other account\nsent: " + sent_now
        + "\nid: 261030-000002-Pulvermaar\n---\n\nfor the old account's session\n")
    expect("TB5 mail for a session under another account goes to a new session that is told so", wait_for(lambda: len(opened_sessions()) > calls, 20)
           and wait_for(lambda: any("another account on this machine (other-account)" in t for t in received(opened_sessions()[-1])), 20))
    calls = len(opened_sessions())
    stray = SVC / "mail/Pulvermaar/desks/Bar/261030-000004-Mistral.md"
    stray.write_text("---\nfrom: Mistral:dispatcher\nto: Pulvermaar:Bar\nfor_session: " + sid("y") + "\nsubject: ClaudeMail notice: x\nsent: "
                     + sent_now + "\nid: 261030-000004-Mistral\n---\n\nnotice for a session that is not here\n")
    expect("TB7 a notice for a session that is not on this machine is set aside, and opens no session",
           wait_for(lambda: (stray.parent / "read" / stray.name).is_file(), 20) and len(opened_sessions()) == calls
           and json.loads((stray.parent / "read" / (stray.name[:-3] + ".delivery.json")).read_text())["state"] == "set aside")

    def dispatch_once():
        d = subprocess.Popen([PY, "-B", str(SVC / "mail.py"), "dispatch"], env=ENV, stdout=subprocess.DEVNULL,
                             stderr=open(BASE / "dispatch.err", "w"))
        procs.append(d)
        return d
    status_of = lambda m: json.loads(m.with_name(m.name[:-3] + ".delivery.json").read_text()) if m.with_name(m.name[:-3] + ".delivery.json").exists() else {}
    notices = lambda words: [t for t in received(S0) if "ClaudeMail notice" in t and words in t]

    # one failing message fails alone (design R4e): a running session with two peer keys trips the assertion in inbox_payload
    pid_a = next(json.loads(p.read_text())["pid"] for p in (HOME / ".claude/sessions").glob("*.json") if A in p.read_text())
    extra = HOME / ".claude/sessions" / (str(pid_a) + ".def456.key")
    extra.write_text(json.dumps({"peerToken": "other"}))
    mb1 = sent_path(run(["send", "--to", "Pulvermaar:Foo", "--for-session", A[:8], "--subject", "TB1 fails", "--body-file", body("x")], session=S0).stdout)
    expect("TB1 the message is marked failed with the kind and place", wait_for(lambda: status_of(mb1).get("state") == "failed", 20)
           and status_of(mb1)["signature"].startswith("AssertionError at mail.py:") and "inbox_payload" in status_of(mb1)["signature"], status_of(mb1))
    calls = len(opened_sessions())
    mb2 = sent_path(run(["send", "--to", "Pulvermaar:Home", "--new", "--name", "TB1 after", "--subject", "TB1 later desk", "--body-file", body("x")], session=S0).stdout)
    expect("TB1 the dispatcher lives on and delivers a desk after it", wait_for(lambda: len(opened_sessions()) > calls, 20)
           and wait_for(delivered_to(mb2, opened_sessions()[-1]), 20) and dispatcher.poll() is None, (BASE / "dispatch.err").read_text())
    expect("TB1 the report counts it as failed", wait_for(lambda: latest()["undelivered"]["failed"] == 1, 10), latest()["undelivered"])
    expect("TB1 the user is notified once", (HOME / "notified.txt").read_text().count("Claude mail failed") == 1)
    expect("TB1 the sender gets a notice", wait_for(lambda: notices("TB1 fails") and notices("itself failed"), 30), received(S0)[-1:])
    first_failed = status_of(mb1)["failed"]
    time.sleep(2)
    expect("TB1 it is not tried again on the same code", status_of(mb1)["failed"] == first_failed and not (mb1.parent / "read" / mb1.name).exists())
    extra.unlink()

    # a change to signed_mail.py restarts the dispatcher too, and a failed message is tried once more on the new code
    with open(SVC / "signed_mail.py", "a") as fh:
        fh.write("\n")
    expect("TA7 a changed signed_mail.py ends the dispatcher for a restart", wait_for(lambda: dispatcher.poll() == 3, 15), dispatcher.poll())
    dispatcher = dispatch_once()
    expect("TB1 on the new code the failed message is tried again and delivered", wait_for(delivered_to(mb1, A), 20), status_of(mb1))

    # a crash loop outside delivery is visible: a delivery record without `arrived` ends the count of undelivered mail
    stamp_now = time.strftime("%Y-%m-%dT%H:%M:%S") + time.strftime("%z")[:3] + ":" + time.strftime("%z")[3:]
    bad = SVC / "mail/Pulvermaar/desks/Foo/261030-000003-Pulvermaar.md"
    bad.write_text("---\nfrom: Pulvermaar:Bar\nfrom_session: " + S0 + "\nto: Pulvermaar:Foo\nfor_session: " + sid("x")
                   + "\nsubject: TA8 bad record\nsent: " + stamp_now + "\nid: 261030-000003-Pulvermaar\n---\n\nx\n")
    bad_status = bad.with_name(bad.name[:-3] + ".delivery.json")
    bad_status.write_text(json.dumps({"state": "waiting for the user", "tried": stamp_now, "notified": stamp_now, "reason": "x"}))
    crash_file = HOME / ".claude/claudemail/crash.json"
    expect("TA8 the dispatcher ends on the bad record", dispatcher.wait(20) == 1)
    crash = json.loads(crash_file.read_text())
    expect("TA8 crash.json names the kind and place", crash["restarts"] == 1 and crash["signature"].startswith("KeyError at mail.py:")
           and "undelivered" in crash["signature"], crash)
    crashed = latest()
    expect("TA8 a report says it is crashing, with the sessions it had", crashed["crash"]["signature"] == crash["signature"]
           and any(s["session"] == "local_" + A for s in crashed["sessions"]), crashed)
    first_name = newest()
    expect("TA8 a second crash counts on", dispatch_once().wait(20) == 1 and json.loads(crash_file.read_text())["restarts"] == 2)
    expect("TA8 and writes no new report for the same crash", newest() == first_name, newest())
    out = run(["sessions", "Pulvermaar"]).stdout
    expect("TA8 sessions shows the crash", "! dispatcher crashing since" in out and "KeyError" in out, out)
    p = run(["send", "--to", "Pulvermaar:Foo", "--for-session", A[:8], "--subject", "TA8 while crashing", "--body-file", body("x")], session=S0)
    expect("TA8 send says it is crashing instead of 'within seconds'", "crashing since" in p.stdout and "within seconds" not in p.stdout, p.stdout)
    (HOME / ".claude/claudemail/kick.json").unlink()
    p = run(["hook"], stdin=json.dumps({"session_id": S0, "cwd": str(BAR), "transcript_path": ""}))
    expect("TA8 the hook tells the session instead of kicking", "crashing since" in p.stdout and "restarting its watcher" not in
           (HOME / ".claude/claudemail/logs" / ("claudemail.pulvermaar." + time.strftime("%y%m%d") + ".log")).read_text(), p.stdout)
    old = json.loads(crash_file.read_text())
    crash_file.write_text(json.dumps(dict(old, last="2026-10-01T00:00:00-05:00")))
    beat_file = HOME / ".claude/claudemail/dispatcher.json"
    beat_file.write_text(json.dumps(dict(json.loads(beat_file.read_text()), updated="2026-10-01T00:00:00-05:00")))
    (HOME / ".claude/claudemail/kick.json").unlink()
    p = run(["hook"], stdin=json.dumps({"session_id": S0, "cwd": str(BAR), "transcript_path": ""}))
    log_text = lambda: (HOME / ".claude/claudemail/logs" / ("claudemail.pulvermaar." + time.strftime("%y%m%d") + ".log")).read_text()
    expect("TA8b a stale crash.json with no dispatcher running is no crash loop: the hook kicks", "crashing" not in p.stdout
           and wait_for(lambda: (HOME / "launchctl.txt").exists() and "kickstart" in (HOME / "launchctl.txt").read_text(), 10), p.stdout)
    expect("TB6 the kick says why, in the right words", "has not reported for" in log_text() and "restarting its watcher" in log_text()
           and wait_for(lambda: "kick: restarted the watcher" in log_text(), 10) and "was not reporting" not in log_text())
    crash_file.write_text(json.dumps(old))
    bad_status.unlink()
    bad.unlink()
    dispatcher = dispatch_once()
    expect("TA8 crash.json gone and the report no longer says crashing", wait_for(lambda: not crash_file.exists() and "crash" not in latest(), 15),
           latest())
    dispatcher.kill()
    dispatcher.wait()

    # the watcher ends on a code change, for launchd to start it again on the new code
    watcher = subprocess.Popen([PY, "-B", str(SVC / "mail.py"), "watch"], env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    procs.append(watcher)
    time.sleep(4)
    with open(SVC / "mail.py", "a") as fh:
        fh.write("\n# changed\n")
    code = None
    try:
        code = watcher.wait(30)
    except subprocess.TimeoutExpired:
        pass
    expect("TA9 the watcher exits 0 after the code changed", code == 0, code)
    log = "".join(p.read_text() for p in (HOME / ".claude/claudemail/logs").glob("*.log"))
    print("--- log tail\n" + "\n".join(log.splitlines()[-12:]))
finally:
    for p in procs:
        p.kill()
    for path in (HOME / ".claude" / "sessions").glob("*.json"):
        pid = json.loads(path.read_text())["pid"]
        if pid != os.getpid():
            try:
                os.kill(pid, 9)
            except ProcessLookupError:
                pass
print("\n" + ("ALL PASSED" if not fails else str(len(fails)) + " FAILED: " + "; ".join(fails)))
sys.exit(1 if fails else 0)
