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
known = [p for p in store.glob("local_*.json") if json.loads(p.read_text())["cliSessionId"] == query.get("session")]
if parsed.netloc == "resume" and known:                      # a session the app has: restore it and start it
    record = dict(json.loads(known[0].read_text()), isArchived=False)
    known[0].write_text(json.dumps(record))
elif parsed.netloc == "resume":
    calls = [json.loads(l) for l in (home / "cli_calls.jsonl").read_text().splitlines()]
    call = [c for c in calls if c["session"] == query["session"]][-1]
    record = {"sessionId": "local_" + call["session"], "cliSessionId": call["session"], "cwd": call["cwd"],
              "title": "Imported", "isArchived": False, "lastActivityAt": int(time.time() * 1000)}
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
for f in [BIN / "open", BIN / "osascript", CLI]:
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

    # closed session: continue link, then socket
    m5 = sent_path(run(["send", "--to", "Pulvermaar:Foo", "--for-session", B[:8], "--subject", "T5 closed", "--body-file", body("hello B")], session=S0).stdout)
    expect("T5 closed session opened and delivered", wait_for(delivered_to(m5, B)), (HOME / "opened.txt").read_text() if (HOME / "opened.txt").exists() else "")
    expect("T5 used the import link with its CLI id", "claude://resume?session=" + B in (HOME / "opened.txt").read_text())

    # new session: command-line turn, import, socket
    m6 = sent_path(run(["send", "--to", "Pulvermaar:Foo", "--new", "--subject", "T6 new", "--body-file", body("hello new")], session=S0).stdout)
    expect("T6 new session started by the CLI", wait_for(lambda: len(opened_sessions()) == 1))
    new6 = opened_sessions()[0]
    call = json.loads((HOME / "cli_calls.jsonl").read_text().splitlines()[0])
    expect("T6 CLI ran in the desk folder", call["cwd"] == str(FOO.resolve()) or call["cwd"] == str(FOO), call)
    expect("T6 delivered to the new session", wait_for(delivered_to(m6, new6)))
    expect("T6 text says ClaudeMail opened it", "opened this session" in (received(new6) or [""])[-1])

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
    # a program without a session gets a new session without choosing
    m8 = sent_path(run(["send", "--to", "Pulvermaar:Foo", "--to-own-desk", "--subject", "T8 lease notice", "--body-file", body("job done")]).stdout)
    expect("T8 program mail -> new session", wait_for(lambda: len(opened_sessions()) == 3) and wait_for(delivered_to(m8, opened_sessions()[2])))

    # command-line Claude not signed in: waits for the user, retried later
    (HOME / "cli_fail").touch()
    m9 = sent_path(run(["send", "--to", "Pulvermaar:Foo", "--new", "--subject", "T9 no login", "--body-file", body("x")], session=S0).stdout)
    status9 = lambda: json.loads(m9.with_name(m9.name[:-3] + ".delivery.json").read_text()) if m9.with_name(m9.name[:-3] + ".delivery.json").exists() else {}
    expect("T9 waits for the user with the CLI's words", wait_for(lambda: status9().get("state") == "waiting for the user")
           and "Not logged in" in status9().get("reason", ""), status9())
    expect("T9 one notification", wait_for(lambda: (HOME / "notified.txt").exists() and "T9 no login" in (HOME / "notified.txt").read_text()))
    (HOME / "cli_fail").unlink()
    expect("T9 retried and delivered after retry_minutes", wait_for(lambda: (m9.parent / "read" / m9.name).is_file(), 40))
    expect("T9 notified once only", (HOME / "notified.txt").read_text().count("T9 no login") == 1)

    # the app does not open the session in time: waits for the user, retried later
    (HOME / "open_ignore").touch()
    m10 = sent_path(run(["send", "--to", "Pulvermaar:Foo", "--for-session", E[:8], "--subject", "T10 slow", "--body-file", body("x")], session=S0).stdout)
    st10 = lambda: json.loads(m10.with_name(m10.name[:-3] + ".delivery.json").read_text()) if m10.with_name(m10.name[:-3] + ".delivery.json").exists() else {}
    expect("T10 opening times out to the user", wait_for(lambda: st10().get("state") == "waiting for the user", 30), st10())
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
    p = run(["send", "--to", "Mistral:Nope", "--new", "--subject", "x", "--body-file", body("x")], session=S0, check=False)
    expect("T14 unknown desk refused", p.returncode != 0 and "No desk" in p.stderr, p.stderr)
    p = run(["send", "--to", "Moosfels", "--new", "--subject", "x", "--body-file", body("x")], session=S0, check=False)
    expect("T14b machine without a report still gets --new mail on Home", p.returncode == 0 and "Moosfels/desks/Home/" in p.stdout, p.stdout + p.stderr)
    p = run(["send", "--to", "Moosfels:Foo", "--new", "--subject", "x", "--body-file", body("x")], session=S0, check=False)
    expect("T14c but no unknown desk there", p.returncode != 0 and "No desk" in p.stderr, p.stderr)
    (SVC / "mail/Teppich/reports").mkdir(parents=True)
    (SVC / "mail/Teppich/desks/Computers").mkdir(parents=True)
    (SVC / "mail/Teppich/reports/261026-120000-000.json").write_text(json.dumps({"listening": {}, "updated": "2026-10-26T12:00:00-05:00"}))
    p = run(["sessions", "Teppich"], session=S0, check=False)
    expect("T14d a machine still on a v3 report lists its desk folders", p.returncode == 0 and "Teppich:Computers" in p.stdout, p.stdout + p.stderr)
    p = run(["send", "--to", "Teppich:Computers", "--for-session", sid("8"), "--subject", "T14d", "--body-file", body("x")], session=S0, check=False)
    expect("T14d mail with a full id to such a machine is accepted", p.returncode == 0 and "desks/Computers/" in p.stdout, p.stdout + p.stderr)
    p = run(["send", "--reply-to", str(m8.parent / "read" / m8.name), "--body-file", body("x")], session=A, check=False)
    expect("T15 reply to a program refused", p.returncode != 0 and "is a program" in p.stderr, p.stderr)
    p = run(["send", "--to", "all", "--subject", "x", "--body-file", body("x")], session=S0, check=False)
    expect("T16 --to all needs --new", p.returncode != 0)
    p = run(["send", "--to", "all", "--new", "--subject", "T16 all", "--body-file", body("x")], session=S0)
    expect("T16 --to all --new reaches every other machine's Home", p.stdout.count("sent ") == 6 and "Pulvermaar/" not in p.stdout, p.stdout)
    p = run(["send", "--to", "Mistral:General", "--new", "--subject", "x", "--body-file", body("x")], session=S0)
    expect("T16b General means Home", p.returncode == 0 and "desks/Home/" in p.stdout, p.stdout + p.stderr)

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

    expect("dispatcher still running", dispatcher.poll() is None, (BASE / "dispatch.err").read_text())
    expect("no session refused a post for a bad auth line", not list((HOME / "received").glob("*.refused")))
    (SVC / "mail/Pulvermaar/desks/Foo/garbage.md").write_bytes(b"\xff\xfe no header")
    (SVC / "mail/Pulvermaar/desks/Foo/noheader.md").write_text("just text")
    expect("malformed files go to rejected/ and the dispatcher lives on", wait_for(lambda: (SVC / "mail/Pulvermaar/desks/Foo/rejected/garbage.md").is_file()
           and (SVC / "mail/Pulvermaar/desks/Foo/rejected/noheader.md").is_file()) and dispatcher.poll() is None)
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
