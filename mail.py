"""ClaudeMail: mail between the Claude sessions on the house machines, addressed to desks.

A desk is a project on a machine, written Machine:Desk (Stahlwerk:LifeCoordinator). A session belongs to the desk of
the folder it was started in: under ~/ResilioSync the first two path parts below Services, Projects and Tools, the
first part elsewhere, with a leading 'yyMMdd ' date stamp dropped; a '.desk' file (one line, the desk name) in a
folder overrides that; sessions outside ~/ResilioSync belong to the machine's General desk. Design and reasons:
'260927 Desk design.md' beside this file.

On disk (synced by Resilio), in this service folder's mail/ (ResilioSync/Mailbox until 2026-09-27):

    mail/<Machine>/desks/<Desk>/<id>.md                    unclaimed mail for that desk
    mail/<Machine>/desks/<Desk>/<id>.status.json           where it stands: waiting, nudge requested, nudged,
                                                           waiting for the user, delivered (moves to read/ with it)
    mail/<Machine>/desks/<Desk>/read/                      delivered mail
    mail/<Machine>/desks/<Desk>/desk.json                  its folders and sessions (written by the dispatcher)
    mail/<Machine>/reports/<stamp>.json                    the dispatcher's heartbeat and who listens where; each
                                                           report is a new file, never rewritten, so Resilio never
                                                           sees two versions of one path (dispatcher.json until
                                                           2026-09-28 conflicted); the newest name wins
    mail/<Machine>/post office/                            the Post office session's working folder

Locally, never synced: ~/.claude/claudemail/ (sessions/<id>.json: each session's desk and activity; waiters/<id>.json:
the running Stop waiter of each session; dispatcher.json: the local heartbeat hooks check; logs/claudemail.<machine>.<yymmdd>.log:
this machine's log, in the synced service folder until 2026-09-28).

Who does what:
    hook      SessionStart and UserPromptSubmit: register the session, hand it its own desk's mail as context.
    wait      Stop (asyncRewake): wait in the background, wake the idle session when mail arrives on its desk.
    watch     per machine, started at logon: runs the dispatcher and restarts it when mail.py changes.
    dispatch  notes unclaimed desk mail, asks the Post office to nudge a session of that desk after
              nudge_after_minutes, and notifies the user when nobody takes it. It never starts Claude.
The settings.json entries (`mail.py hook`, `mail.py wait`) are the stable interface; all behaviour lives here.

Commands for sessions:
    send --to Machine:Desk | Machine | all [--subject TEXT] (--body-file FILE | stdin) | --reply-to PATH
    desks [MACHINE]          the desks, whether a session listens on each, what each last worked on
    status MESSAGE           where a message stands (path or id)
    inbox [--all]            unclaimed mail on this machine's desks (--all: delivered mail too)
    whoami                   this machine, this session's desk and address
    desk-of FOLDER...        the desk of each folder (the Post office uses it)
    nudged REQUEST (--session ID [--title TEXT] | --none [--reason TEXT])   the Post office reports a nudge
Setup: install-hook, uninstall-hook, migrate, kick.

Stdlib only; Python 3.8+ on Windows and macOS. No regular expressions (house rule).
"""
from __future__ import annotations

import argparse
import base64
import datetime
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parent
SCRIPT = SERVICE_DIR / "mail.py"
SYNC_ROOT = SERVICE_DIR.parent.parent
MAILBOX_ROOT = SERVICE_DIR / "mail"
OLD_MAILBOX_ROOT = SYNC_ROOT / "Mailbox"                            # until 2026-09-27; too conspicuous in ResilioSync
CONFIG_DIR = SERVICE_DIR / "config"
LOCAL_DIR = Path.home() / ".claude" / "claudemail"
LOG_DIR = LOCAL_DIR / "logs"                                        # this service folder's logs/ until 2026-09-28
OLD_WAITER_DIR = Path.home() / ".claude" / "claudemail-waiters"     # the pre-desk waiters, retired by the new ones

DESKS = "desks"
READ = "read"
LEGACY = "_legacy"
GENERAL = "General"
POST_OFFICE = "Post office"
POST_OFFICE_DIR = "post office"
DESK_FILE = ".desk"
DESK_INFO = "desk.json"
REPORTS = "reports"
OLD_REPORT = "dispatcher.json"                                      # the single rewritten report, until 2026-09-28
STATUS_SUFFIX = ".status.json"
GROUP_FOLDERS = ["Services", "Projects", "Tools"]                  # desks sit one level below these
HEADER_FIELDS = ["from", "from_session", "to", "subject", "sent", "id", "conversation", "in_reply_to",
                 "kind", "desk", "message", "folders"]
HOOK_EVENTS = ["SessionStart", "UserPromptSubmit"]
WAIT_EVENTS = ["Stop"]
OLD_WAIT_EVENTS = ["SessionStart"]                                  # a waiter there hung new sessions (2026-09-24)
TASK_NAME = "Claude Mail Watcher"
LAUNCHD_LABEL = "com.goocy.claudemail"
EXIT_CODE_CHANGED = 3                                               # dispatch -> watch: mail.py changed, start me again
EXIT_ALREADY_RUNNING = 4
INVALID_NAME_CHARS = '<>:"/|?*' + chr(92)


# ---------------------------------------------------------------- config and machine

def load_config() -> dict:
    """config/defaults.json, overlaid with config/<machine>.json. Each machine only ever edits its own file."""
    with open(CONFIG_DIR / "defaults.json", encoding="utf-8") as fh:
        config = json.load(fh)
    own = CONFIG_DIR / (this_machine(config).casefold() + ".json")
    if own.is_file():
        with open(own, encoding="utf-8") as fh:
            config.update(json.load(fh))
    return config


def canonical_machine(name: str, config: dict) -> str:
    for machine in config["machines"]:
        if machine.casefold() == name.strip().casefold():
            return machine
    raise SystemExit("Unknown machine '" + name + "'. Known: " + ", ".join(config["machines"]))


def this_machine(config: dict) -> str:
    override = os.environ.get("CLAUDE_MAILBOX_MACHINE", "").strip()
    if override:
        return canonical_machine(override, config)
    host = socket.gethostname().partition(".")[0].casefold()
    for alias, machine in config.get("hostnames", {}).items():
        if alias.partition(".")[0].casefold() == host:
            return canonical_machine(machine, config)
    return canonical_machine(host, config)


def setting(config: dict, key: str, default: float) -> float:
    try:
        return float(config.get(key, default))
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------- desks

def path_parts(folder: str) -> list:
    """A path given as text, from this or another machine, split into its parts."""
    return [p for p in str(folder).replace(chr(92), "/").split("/") if p and p != "."]


def drop_date_stamp(name: str) -> str:
    """'260926 LifeCoordinator' -> 'LifeCoordinator'."""
    if len(name) > 7 and name[:6].isdigit() and name[6] == " ":
        return name[7:].strip()
    return name


def clean_desk_name(name: str) -> str:
    """A desk name usable as a folder name on Windows and the Mac."""
    cleaned = "".join(c for c in name.strip() if c not in INVALID_NAME_CHARS and ord(c) >= 32).strip(" .")
    return cleaned or GENERAL


def desk_file_override(folder: str):
    """The nearest '.desk' file in the folder or its parents, when the folder exists on this machine."""
    try:
        path = Path(folder)
        if not path.is_absolute() or not path.exists():
            return None
        for candidate in [path] + list(path.parents):
            marker = candidate / DESK_FILE
            if marker.is_file():
                lines = [line.strip() for line in marker.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
                if lines:
                    return clean_desk_name(lines[0])
    except OSError:
        return None
    return None


def desk_for(folder: str) -> str:
    """The desk a working folder belongs to (see the module docstring)."""
    if not folder:
        return GENERAL
    override = desk_file_override(folder)
    if override:
        return override
    parts = path_parts(folder)
    root = SYNC_ROOT.name.casefold()
    for index, part in enumerate(parts):
        if part.casefold() != root:
            continue
        below = parts[index + 1:]
        if not below:
            return GENERAL
        mail = [p.casefold() for p in MAILBOX_ROOT.relative_to(SYNC_ROOT).parts]      # services, claudemail, mail
        if len(below) >= len(mail) + 2 and [p.casefold() for p in below[:len(mail)]] == mail \
                and below[len(mail) + 1].casefold() == POST_OFFICE_DIR.casefold():
            return POST_OFFICE                          # even before the dispatcher has written its .desk file
        if any(below[0].casefold() == group.casefold() for group in GROUP_FOLDERS):
            return clean_desk_name(drop_date_stamp(below[1])) if len(below) > 1 else GENERAL
        return clean_desk_name(drop_date_stamp(below[0]))
    return GENERAL


def machine_dir(machine: str) -> Path:
    return MAILBOX_ROOT / machine


def report_names(machine: str) -> list:
    """This machine's report files, newest first (the names are sortable time stamps)."""
    folder = machine_dir(machine) / REPORTS
    try:
        return sorted((p for p in folder.glob("*.json") if not p.name.startswith(".")), key=lambda p: p.name, reverse=True)
    except OSError:
        return []


def read_report(machine: str) -> dict:
    """The newest dispatcher report that parses; Resilio may still be delivering the very newest one."""
    for path in report_names(machine):
        report = read_json(path)
        if report:
            return report
    return {}


def write_report(machine: str, report: dict) -> None:
    """A new file per report, then drop all but it and the one before it (a reader may be opening that one)."""
    stamp = datetime.datetime.now()
    path = machine_dir(machine) / REPORTS / (stamp.strftime("%y%m%d-%H%M%S-") + "%03d" % (stamp.microsecond // 1000) + ".json")
    write_json(path, report)
    for old in report_names(machine)[2:]:
        try:
            old.unlink()
        except OSError:
            pass


def desk_dir(machine: str, desk: str) -> Path:
    return machine_dir(machine) / DESKS / desk


def post_office_dir(machine: str) -> Path:
    return machine_dir(machine) / POST_OFFICE_DIR


def desk_folders(machine: str) -> list:
    folder = machine_dir(machine) / DESKS
    if not folder.is_dir():
        return []
    return sorted((p for p in folder.iterdir() if p.is_dir() and not p.name.startswith(".")), key=lambda p: p.name.casefold())


def find_desk(machine: str, name: str):
    """The existing desk folder whose name matches, case and date stamp ignored."""
    wanted = drop_date_stamp(name.strip()).casefold()
    for folder in desk_folders(machine):
        if folder.name.casefold() == wanted:
            return folder
    return None


def read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_atomically(path: Path, text: str) -> None:
    # The temp name does not end in .md, so no hook or waiter picks it up half written.
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("." + path.name + "." + str(os.getpid()) + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    for attempt in range(10):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:                            # Resilio holds a file it is syncing for a moment
            if attempt == 9:
                try:
                    tmp.unlink()
                except OSError:
                    pass
                raise
            time.sleep(0.3)


def write_json(path: Path, data: dict) -> None:
    write_atomically(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def now_iso() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def iso_age(value) -> float:
    """Seconds since an ISO timestamp; a huge number when there is none."""
    try:
        then = datetime.datetime.fromisoformat(str(value))
        return (datetime.datetime.now().astimezone() - then).total_seconds()
    except (TypeError, ValueError):
        return float("inf")


def short_time(value) -> str:
    return str(value or "")[:16].replace("T", " ")


# ---------------------------------------------------------------- messages

def is_message(path: Path) -> bool:
    return path.is_file() and path.name.endswith(".md") and not path.name.startswith(".")


def waiting_in(folder: Path) -> list:
    """Unclaimed messages in one folder, oldest first. Temp files and Resilio's .!sync files never end in .md."""
    if not folder.is_dir():
        return []
    return sorted((p for p in folder.iterdir() if is_message(p)), key=lambda p: (p.name[:13], len(p.name), p.name))


def parse_message(text: str) -> tuple:
    """Split a message into (header dict, body). A file without a header is all body."""
    text = text.replace("\r\n", "\n").lstrip(chr(0xFEFF))
    header = {}
    if not text.startswith("---\n"):
        return header, text.strip()
    head, sep, body = text[4:].partition("\n---\n")
    if not sep:
        return header, text.strip()
    for line in head.split("\n"):
        key, colon, value = line.partition(":")
        if colon:
            header[key.strip().casefold()] = value.strip()
    return header, body.strip()


def format_message(header: dict, body: str) -> str:
    lines = ["---"]
    for key in HEADER_FIELDS:
        if header.get(key):
            lines.append(key + ": " + " ".join(str(header[key]).split()))
    lines.append("---")
    return "\n".join(lines) + "\n\n" + body.strip() + "\n"


def message_id(path: Path) -> str:
    return path.name[:-3] if path.name.endswith(".md") else path.name


def status_path(message: Path) -> Path:
    return message.with_name(message_id(message) + STATUS_SUFFIX)


def read_status(message: Path) -> dict:
    return read_json(status_path(message))


def write_status(message: Path, status: dict) -> None:
    status["updated"] = now_iso()
    write_json(status_path(message), status)


def taken_ids(machine: str) -> set:
    names = set()
    for folder in desk_folders(machine):
        for sub in (folder, folder / READ):
            if sub.is_dir():
                names.update(message_id(p) for p in sub.iterdir() if p.name.endswith(".md"))
    return names


def new_message_id(machine: str, stamp: str, sender: str) -> str:
    """<yyMMdd-HHmmss>-<Sender>, with -2, -3 ... when that sender already used the second on that machine. The sender
    in the name keeps two machines that write to one mailbox in the same second from colliding in Resilio."""
    taken = taken_ids(machine)
    n = 1
    while True:
        name = stamp + "-" + sender + ("" if n == 1 else "-" + str(n))
        if name not in taken:
            return name
        n += 1


def find_message(given: str):
    """A message by path (absolute, or relative to the mail folder; old ResilioSync/Mailbox paths too) or by id;
    unclaimed copies before delivered ones."""
    path = Path(given)
    candidates = [path] if path.is_absolute() else [MAILBOX_ROOT / given, Path.cwd() / given]
    parts = path_parts(given)
    for index, part in enumerate(parts[:-1]):
        if part.casefold() == OLD_MAILBOX_ROOT.name.casefold() and index and parts[index - 1].casefold() == SYNC_ROOT.name.casefold():
            candidates.append(MAILBOX_ROOT / Path(*parts[index + 1:]))
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    wanted = path.name[:-3] if path.name.endswith(".md") else path.name
    if not MAILBOX_ROOT.is_dir():
        return None
    for machine in sorted(p for p in MAILBOX_ROOT.iterdir() if p.is_dir()):
        for folder in desk_folders(machine.name):
            for sub in (folder, folder / READ):
                candidate = sub / (wanted + ".md")
                if candidate.is_file():
                    return candidate
    legacy = []                                           # pre-desk paths like Mistral/read/x.md, now under _legacy/
    for machine in (p for p in MAILBOX_ROOT.iterdir() if p.is_dir()):
        rel = path_parts(given)
        if rel and rel[0].casefold() == machine.name.casefold():
            legacy.append(machine / LEGACY / Path(*rel[1:]))
    for candidate in legacy:
        if candidate.is_file():
            return candidate
    return None


def display(path: Path) -> str:
    """Absolute, with forward slashes: sessions and their agents kept resolving relative paths against the wrong folder."""
    return path.as_posix()


# ---------------------------------------------------------------- local session registry

def clean_id(value) -> str:
    return "".join(c for c in str(value or "") if c.isalnum() or c == "-")


def unattended() -> bool:
    """A headless run (`claude -p`, scripts): Claude Code sets CLAUDE_CODE_SESSION_ATTENDED=0 there. It never takes,
    waits for or registers for mail."""
    return os.environ.get("CLAUDE_CODE_SESSION_ATTENDED", "").strip() == "0"


def session_file(session: str) -> Path:
    return LOCAL_DIR / "sessions" / (session + ".json")


def waiter_file(session: str) -> Path:
    return LOCAL_DIR / "waiters" / (session + ".json")


def start_folder(hook_input: dict) -> str:
    """The folder the session was started in: the first 'cwd' in its transcript, so a later `cd` does not move it to
    another desk. Falls back to the hook's cwd."""
    transcript = str(hook_input.get("transcript_path") or "")
    if transcript:
        try:
            with open(transcript, encoding="utf-8") as fh:
                for count, line in enumerate(fh):
                    if count > 40:
                        break
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(record, dict) and record.get("cwd"):
                        return str(record["cwd"])
        except OSError:
            pass
    return str(hook_input.get("cwd") or os.getcwd())


def typed_prompt(text: str) -> str:
    """What the user typed, for 'last worked on': pasted blocks become '[pasted]', and a prompt that is really a message
    from a subagent, another session, a task or the system (a leading <tag>) or a ClaudeMail nudge counts as nothing."""
    text = str(text or "")
    while "<pasted_content" in text:
        before, _, rest = text.partition("<pasted_content")
        _, closed, after = rest.partition("</pasted_content>")
        text = before + " [pasted] " + (after if closed else "")
    text = " ".join(text.split())
    if text.startswith("<") or text.startswith("ClaudeMail:"):
        return ""
    return text[:200]


def register_session(session: str, hook_input: dict, event: str) -> dict:
    """Create or update this session's local record, and make sure its desk folder exists."""
    record = read_json(session_file(session))
    if not record.get("folder"):
        record["folder"] = start_folder(hook_input)
    record["session"] = session
    record["desk"] = desk_for(record["folder"])
    record.setdefault("started", now_iso())
    record["last_active"] = now_iso()
    record["last_event"] = event
    if os.environ.get("CLAUDE_PID", "").strip().isdigit():
        record["claude_pid"] = int(os.environ["CLAUDE_PID"].strip())
    prompt = typed_prompt(hook_input.get("prompt"))
    if prompt:
        if not typed_prompt(record.get("first_prompt")):
            record["first_prompt"] = prompt
        record["last_prompt"] = prompt
    try:
        write_json(session_file(session), record)
    except OSError:
        pass
    return record


def session_desk(config: dict):
    """(machine, desk) of the calling session: its registered desk, else the desk of the current folder."""
    machine = this_machine(config)
    session = clean_id(os.environ.get("CLAUDE_CODE_SESSION_ID", ""))
    record = read_json(session_file(session)) if session else {}
    return machine, record.get("desk") or desk_for(os.getcwd()), session


def local_sessions() -> list:
    folder = LOCAL_DIR / "sessions"
    if not folder.is_dir():
        return []
    return [read_json(p) for p in folder.iterdir() if p.name.endswith(".json")]


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        # os.kill(pid, 0) would terminate the process on Windows, so ask the kernel instead.
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel32.OpenProcess(0x1000, False, pid)          # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return bool(ok) and code.value == 259                       # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def live_waiters() -> list:
    """The Stop waiters running on this machine: [{session, desk, pid, started}]."""
    folder = LOCAL_DIR / "waiters"
    if not folder.is_dir():
        return []
    alive = []
    for path in folder.iterdir():
        info = read_json(path) if path.name.endswith(".json") else {}
        if isinstance(info.get("pid"), int) and pid_alive(info["pid"]):
            alive.append(info)
    return alive


# ---------------------------------------------------------------- claiming and delivery

def claim(paths: list, session: str) -> list:
    """Move each message into read/ beside it and return (new path, text) for the ones this process won. The rename is
    the lock: several sessions of one desk may race, and exactly one wins each message."""
    claimed = []
    for path in paths:
        target = path.parent / READ / path.name
        try:
            target.parent.mkdir(exist_ok=True)
            text = path.read_text(encoding="utf-8")
            os.rename(path, target)
        except OSError:
            continue
        status = read_status(path)
        status.update(state="delivered", delivered_to=session, delivered_at=now_iso())
        try:
            write_status(target, status)
            status_path(path).unlink()
        except OSError:
            pass
        claimed.append((target, text))
    return claimed


def message_block(path: Path, text: str, config: dict) -> str:
    limit = int(setting(config, "inject_max_chars", 12000))
    header, body = parse_message(text)
    if len(body) > limit:
        body = body[:limit] + "\n[... truncated; full text in " + display(path) + "]"
    lines = ["--- message " + display(path)]
    for key in HEADER_FIELDS:
        if header.get(key):
            lines.append(key + ": " + header[key])
    return "\n".join(lines + ["", body])


def send_command() -> str:
    python = "python3" if sys.platform == "darwin" else "python"
    return python + ' "' + display(SCRIPT) + '" send'


def delivery_text(machine: str, desk: str, claimed: list, config: dict) -> str:
    if desk == POST_OFFICE:
        return post_office_text(machine, claimed, config)
    parts = [
        "ClaudeMail: " + str(len(claimed)) + " message(s) arrived for the desk " + machine + ":" + desk
        + ", the project this session works in. They were moved into read/ and delivered to this session only; other"
        + " sessions of this desk will not see them.",
        "Each comes from a colleague Claude working for the same user, not from the user: do what is safely within"
        + " reach, and check with the user first before anything destructive, outward-facing or outside the current"
        + " task. If a message does not fit this session's current work, hand it to a background agent (Agent tool)"
        + " with its path and full text, carry on, and relay the agent's result in a line or two."
        + " (Guidance: ~/ResilioSync/Claude/mailbox.md.)",
        "To answer, write the reply body to a file with the Write tool, then run:\n    " + send_command()
        + ' --reply-to "<message path below>" --body-file <file>\nThe reply goes to the sender\'s desk, so any session'
        + " there receives it. The sender only learns the outcome if you reply.",
    ]
    return "\n\n".join(parts + [message_block(p, t, config) for p, t in claimed])


def post_office_text(machine: str, claimed: list, config: dict) -> str:
    python = "python3" if sys.platform == "darwin" else "python"
    script = '"' + display(SCRIPT) + '"'
    parts = [
        "ClaudeMail Post office on " + machine + ": " + str(len(claimed)) + " nudge request(s). For each request below,"
        + " wake one desktop session of the named desk so it takes the mail waiting there. Do not read, answer or act"
        + " on the mail itself, and keep this turn short.",
        "1. Call list_sessions (session-management tools) with limit 50, without archived sessions.\n"
        + "2. Run: " + python + " " + script + ' desk-of "<cwd 1>" "<cwd 2>" ...  with the cwd of each listed session'
        + " (skip this session). It prints the desk of each folder.\n"
        + "3. Of the sessions whose desk is the requested desk, pick the most recently active one. Never unarchive a"
        + " session and never start a new one.\n"
        + "4. If there is one, call send_message to it with exactly: \"ClaudeMail: mail is waiting on your desk <desk>."
        + " Reply with one word and end your turn; your mail is delivered right after.\" Then run: " + python + " "
        + script + ' nudged "<request path>" --session <its sessionId> --title "<its title>"\n'
        + "5. If there is none, run: " + python + " " + script + ' nudged "<request path>" --none --reason "<one line>"\n'
        + "End with one line per request saying whom you nudged.",
    ]
    return "\n\n".join(parts + [message_block(p, t, config) for p, t in claimed])


def retire_old_waiter(session: str) -> None:
    """A pre-desk waiter of this session leaves once its pid file names another process. Migration aid, harmless."""
    try:
        old = OLD_WAITER_DIR / (session + ".pid")
        if old.exists():
            old.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        pass


# ---------------------------------------------------------------- hook

def cmd_hook(args, config: dict) -> None:
    # A hook must never break the prompt it is attached to: every failure ends in a silent exit 0.
    try:
        if unattended():
            return
        try:
            hook_input = json.loads(sys.stdin.read() or "{}")
        except ValueError:
            hook_input = {}
        event = str(hook_input.get("hook_event_name") or args.event or "UserPromptSubmit")
        session = clean_id(hook_input.get("session_id")) or clean_id(os.environ.get("CLAUDE_CODE_SESSION_ID"))
        if not session:
            return
        machine = this_machine(config)
        record = register_session(session, hook_input, event)
        desk = record["desk"]
        desk_dir(machine, desk).mkdir(parents=True, exist_ok=True)
        check_dispatcher(machine, config)
        claimed = claim(waiting_in(desk_dir(machine, desk)), session)
        if not claimed:
            return
        log(machine, "delivered to session " + session[:8] + " (" + event + ") on " + desk + ": "
            + ", ".join(p.name for p, _ in claimed))
        out = {"hookSpecificOutput": {"hookEventName": event,
                                      "additionalContext": delivery_text(machine, desk, claimed, config)}}
        sys.stdout.write(json.dumps(out))
    except BaseException:
        pass


# ---------------------------------------------------------------- wait

def cmd_wait(args, config: dict) -> None:
    """The asyncRewake Stop hook: sit in the background until mail arrives on this session's desk, then exit 2 with the
    delivery text, which Claude Code gives the idle session as a new turn. It watches its own desk only. A newer waiter
    of the same session replaces it; it leaves when its Claude process is gone, and shortly before the hook timeout
    (wait_hours), so Claude Code never kills it between a claim and its output."""
    try:
        if unattended():
            return
        try:
            hook_input = json.loads(sys.stdin.read() or "{}")
        except ValueError:
            hook_input = {}
        event = str(hook_input.get("hook_event_name") or "Stop")
        if event not in WAIT_EVENTS:
            return                              # a stale SessionStart registration: waiting there blocks the start
        session = clean_id(hook_input.get("session_id")) or clean_id(os.environ.get("CLAUDE_CODE_SESSION_ID"))
        if not session:
            return
        machine = this_machine(config)
        record = register_session(session, hook_input, event)
        desk = record["desk"]
        folder = desk_dir(machine, desk)
        folder.mkdir(parents=True, exist_ok=True)
        me = os.getpid()
        claude_pid = record.get("claude_pid") if isinstance(record.get("claude_pid"), int) else 0
        write_json(waiter_file(session), {"session": session, "desk": desk, "pid": me, "claude_pid": claude_pid,
                                          "started": now_iso()})
        retire_old_waiter(session)
        poll = setting(config, "poll_seconds", 5)
        deadline = time.monotonic() + 3600 * setting(config, "wait_hours", 24) - 120
        while time.monotonic() < deadline:
            if read_json(waiter_file(session)).get("pid") != me:
                return                          # a newer waiter took over this session
            if claude_pid and not pid_alive(claude_pid):
                return                          # the session's Claude process is gone
            claimed = claim(waiting_in(folder), session)
            if claimed:
                log(machine, "delivered to session " + session[:8] + " (Stop waiter) on " + desk + ": "
                    + ", ".join(p.name for p, _ in claimed))
                sys.stderr.write(delivery_text(machine, desk, claimed, config))
                sys.stderr.flush()
                sys.exit(2)
            time.sleep(poll)
    except SystemExit:
        raise
    except BaseException:
        pass


# ---------------------------------------------------------------- send

def resolve_recipient(name: str, config: dict, sender: str) -> list:
    """'Machine:Desk', 'Machine' (its General desk) or 'all' -> [(machine, desk folder name)]."""
    name = name.strip()
    if name.casefold() == "all":
        return [(m, GENERAL) for m in config["machines"] if m != sender]
    machine_part, colon, desk_part = name.partition(":")
    if "/" in machine_part:
        raise SystemExit("Sessions are no longer addressed; address the desk instead, as Machine:Desk. `mail.py desks "
                         + machine_part.partition("/")[0] + "` lists them.")
    machine = canonical_machine(machine_part, config)
    if not colon or not desk_part.strip():
        return [(machine, GENERAL)]
    if drop_date_stamp(desk_part.strip()).casefold() == GENERAL.casefold():
        return [(machine, GENERAL)]
    found = find_desk(machine, desk_part)
    if found is None:
        listing = "\n".join("  " + machine + ":" + f.name for f in desk_folders(machine) if f.name != POST_OFFICE)
        raise SystemExit("No desk '" + desk_part.strip() + "' on " + machine + ". Its desks:\n" + (listing or "  (none yet)")
                         + "\nA desk appears once a session has run in that project on " + machine + ". Send to "
                         + machine + " alone for its General desk.")
    return [(machine, found.name)]


def reply_target(original: dict, config: dict) -> tuple:
    """The desk that owns the conversation on the other side: the original's sender desk."""
    sender = original.get("from", "")
    machine_part, colon, desk_part = sender.partition(":")
    machine = canonical_machine(machine_part, config)
    if colon and desk_part.strip():
        found = find_desk(machine, desk_part)
        return machine, found.name if found else clean_desk_name(desk_part)
    legacy = read_json(machine_dir(machine) / LEGACY / "sessions.json")          # pre-desk mail: session -> desk
    session = clean_id(original.get("from_session"))
    for known, desk in legacy.items():
        if session and (known.startswith(session) or session.startswith(known)):
            return machine, desk
    return machine, GENERAL


def listening_view(machine: str, config: dict) -> tuple:
    """(fresh, listening {desk: [sessions]}, report, as_of) for a machine. On this machine the live waiters are asked
    directly; the dispatcher's report lags a stopped or started waiter by up to one poll (seen in T6). For another
    machine the report is all there is, and Resilio's sync delay comes on top, so as_of says how old it is."""
    report = read_report(machine)
    fresh = iso_age(report.get("updated")) < 60 * setting(config, "stale_minutes", 6)
    if machine == this_machine(config):
        listening = {}
        for waiter in live_waiters():
            listening.setdefault(waiter.get("desk"), []).append(str(waiter.get("session", ""))[:8])
        return fresh, listening, report, ""
    as_of = str(report.get("updated") or "")[11:19]
    return fresh, (report.get("listening", {}) if fresh else {}), report, as_of


def desk_state(machine: str, desk: str, config: dict) -> str:
    """What a sender can expect."""
    fresh, listening, report, as_of = listening_view(machine, config)
    if not fresh:
        return ("the dispatcher on " + machine + " has not reported since " + (short_time(report.get("updated")) or "ever")
                + ", so nobody may notice this mail until a session of that desk runs")
    suffix = " (as of " + as_of + ")" if as_of else ""
    if listening.get(desk):
        return "a session on this desk is listening" + suffix
    known = read_json(desk_dir(machine, desk) / DESK_INFO).get("sessions") or []
    if known and report.get("post_office"):
        return "no session listening" + suffix + "; the Post office will nudge one"
    if known:
        return "no session listening and no Post office on " + machine + suffix + "; waits for the user"
    return "no session on this desk; waits for the user"


def cmd_send(args, config: dict) -> None:
    sender, sender_desk, session = session_desk(config)
    header = {"from": sender + ":" + sender_desk, "from_session": session, "subject": args.subject or ""}
    recipients = []
    if args.reply_to:
        original_path = find_message(args.reply_to)
        if original_path is None:
            raise SystemExit("No such message: " + args.reply_to)
        original, _ = parse_message(original_path.read_text(encoding="utf-8"))
        header["in_reply_to"] = original.get("id") or message_id(original_path)
        header["conversation"] = original.get("conversation") or original.get("id") or message_id(original_path)
        if not args.to:
            recipients.append(reply_target(original, config))
        if not args.subject:
            subject = original.get("subject", "")
            header["subject"] = subject if subject.casefold().startswith("re:") else "Re: " + subject
    for name in args.to or []:
        for part in name.split(","):
            if part.strip():
                recipients.extend(resolve_recipient(part, config, sender))
    recipients = list(dict.fromkeys(recipients))
    if not recipients:
        raise SystemExit("No recipient: give --to Machine:Desk (or Machine, or 'all'), or --reply-to a message.")
    if (sender, sender_desk) in recipients and not args.to_own_desk:
        raise SystemExit("That is this session's own desk (" + sender + ":" + sender_desk + "); its mail would come back"
                         + " to this session or a sibling. For a session on this machine use SendMessage; add"
                         + " --to-own-desk if you really mean it.")
    if not header["subject"]:
        raise SystemExit("No subject: give --subject.")
    body = Path(args.body_file).read_text(encoding="utf-8") if args.body_file else sys.stdin.read()
    if not body.strip():
        raise SystemExit("Empty body.")
    stamp = datetime.datetime.now().strftime("%y%m%d-%H%M%S")
    header["sent"] = now_iso()
    for machine, desk in recipients:
        ident = new_message_id(machine, stamp, sender)
        folder = desk_dir(machine, desk)
        folder.mkdir(parents=True, exist_ok=True)
        fields = dict(header, to=machine + ":" + desk, id=ident)
        fields.setdefault("conversation", ident)
        path = folder / (ident + ".md")
        write_atomically(path, format_message(fields, body))
        print("sent " + display(path))
        print("  to " + machine + ":" + desk + ": " + desk_state(machine, desk, config))


# ---------------------------------------------------------------- nudged (the Post office reports)

def cmd_nudged(args, config: dict) -> None:
    request_path = find_message(args.request)
    if request_path is None:
        raise SystemExit("No such nudge request: " + args.request)
    request, _ = parse_message(request_path.read_text(encoding="utf-8"))
    target = Path(request.get("message", ""))
    if not target.is_file():
        delivered = target.parent / READ / target.name
        print("Nothing to do: " + target.name + " was already " + ("delivered." if delivered.is_file() else "taken."))
        return
    status = read_status(target)
    if args.none:
        status.update(state="no session", nudge_failed_at=now_iso(), reason=args.reason or "no session of this desk")
        print("Recorded: no session to nudge for " + target.name + "; the dispatcher tells the user.")
    else:
        if not args.session:
            raise SystemExit("Give --session <sessionId> of the session you nudged, or --none.")
        status.update(state="nudged", nudged_session=args.session, nudged_title=args.title or "", nudged_at=now_iso())
        print("Recorded: nudged " + (args.title or args.session) + " for " + target.name + ".")
    write_status(target, status)
    log(this_machine(config), "post office: " + status["state"] + " for " + target.name
        + (" (" + (args.title or args.session) + ")" if args.session else ""))


# ---------------------------------------------------------------- listings

def cmd_desks(args, config: dict) -> None:
    machines = [canonical_machine(args.machine, config)] if args.machine else config["machines"]
    for machine in machines:
        fresh, listening, report, as_of = listening_view(machine, config)
        state = ("dispatcher up" if fresh else "dispatcher silent since " + (short_time(report.get("updated")) or "ever"))
        state += ", listening as of " + as_of if fresh and as_of else ""
        state += ", Post office " + ("listening" if listening.get(POST_OFFICE) else "not listening")
        print(machine + " (" + state + "):")
        for folder in desk_folders(machine):
            if folder.name == POST_OFFICE:
                continue
            info = read_json(folder / DESK_INFO)
            sessions = info.get("sessions") or []
            unclaimed = len(waiting_in(folder))
            line = "  " + machine + ":" + folder.name
            line += "  listening" if listening.get(folder.name) else "  not listening"
            line += "  " + str(len(sessions)) + " session" + ("" if len(sessions) == 1 else "s")
            if info.get("last_active"):
                line += ", last active " + short_time(info["last_active"])
            if unclaimed:
                line += "  [" + str(unclaimed) + " unclaimed]"
            print(line)
            if info.get("last_worked_on"):
                print('      "' + info["last_worked_on"][:120] + '"')


def cmd_status(args, config: dict) -> None:
    path = find_message(args.message)
    if path is None:
        raise SystemExit("No such message: " + args.message)
    header, _ = parse_message(path.read_text(encoding="utf-8"))
    status = read_status(path)
    print(display(path))
    print("  from " + header.get("from", "?") + " to " + header.get("to", "?") + ": " + header.get("subject", ""))
    state = status.get("state") or ("delivered" if path.parent.name == READ else "waiting (not yet seen by the recipient's dispatcher)")
    print("  state: " + state)
    for key in ["waiting_since", "nudge_requested_at", "nudged_title", "nudged_session", "nudged_at", "reason",
                "user_notified_at", "delivered_to", "delivered_at"]:
        if status.get(key):
            print("  " + key.replace("_", " ") + ": " + str(status[key]))


def cmd_inbox(args, config: dict) -> None:
    machine = this_machine(config)
    groups = [("unclaimed", [p for f in desk_folders(machine) for p in waiting_in(f)])]
    if args.all:
        groups.append(("delivered", [p for f in desk_folders(machine) for p in waiting_in(f / READ)]))
    for label, paths in groups:
        print(label + " (" + str(len(paths)) + "):")
        for path in paths:
            header, _ = parse_message(path.read_text(encoding="utf-8"))
            print("  " + display(path) + "\n      from " + header.get("from", "?") + ": " + header.get("subject", ""))


def cmd_whoami(args, config: dict) -> None:
    machine, desk, session = session_desk(config)
    print("machine " + machine + "  " + display(machine_dir(machine)))
    print("session " + (session or "unknown (CLAUDE_CODE_SESSION_ID is not set)"))
    print("desk    " + machine + ":" + desk + "  (others reach this session's project with --to \"" + machine + ":" + desk + "\")")


def cmd_desk_of(args, config: dict) -> None:
    for folder in args.folders:
        print(desk_for(folder) + "\t" + folder)


# ---------------------------------------------------------------- logging and notifications

def log(machine: str, message: str) -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        now = datetime.datetime.now()
        path = LOG_DIR / ("claudemail." + machine.casefold() + "." + now.strftime("%y%m%d") + ".log")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(now.strftime("%Y-%m-%d %H:%M:%S") + " " + message + "\n")
    except OSError:
        pass


WINDOWS_TOAST = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$texts = $xml.GetElementsByTagName('text')
$texts.Item(0).AppendChild($xml.CreateTextNode($env:CLAUDEMAIL_TITLE)) | Out-Null
$texts.Item(1).AppendChild($xml.CreateTextNode($env:CLAUDEMAIL_BODY)) | Out-Null
$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show([Windows.UI.Notifications.ToastNotification]::new($xml))
"""

MAC_NOTIFY = ["on run argv", "display notification (item 2 of argv) with title (item 1 of argv)", "end run"]


def notify(machine: str, title: str, body: str) -> None:
    """Desktop notification; the text travels as data (environment / argv), never spliced into a script."""
    log(machine, "notified: " + title + " - " + " ".join(body.split()))
    try:
        if sys.platform == "win32":
            encoded = base64.b64encode(WINDOWS_TOAST.encode("utf-16-le")).decode("ascii")
            env = dict(os.environ, CLAUDEMAIL_TITLE=title, CLAUDEMAIL_BODY=body)
            subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                           env=env, check=True, capture_output=True, timeout=30,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        elif sys.platform == "darwin":
            cmd = ["osascript"]
            for line in MAC_NOTIFY:
                cmd += ["-e", line]
            subprocess.run(cmd + [title, body], check=True, capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        log(machine, "WARN notification failed: " + str(exc))


# ---------------------------------------------------------------- dispatcher

def code_version() -> str:
    try:
        stat = SCRIPT.stat()
        return str(stat.st_mtime_ns) + "-" + str(stat.st_size)
    except OSError:
        return ""


def code_compiles() -> bool:
    """Only restart onto a mail.py that compiles: Resilio may still be writing it."""
    try:
        compile(SCRIPT.read_text(encoding="utf-8"), str(SCRIPT), "exec")
        return True
    except (OSError, SyntaxError, ValueError):
        return False


def local_heartbeat() -> Path:
    return LOCAL_DIR / "dispatcher.json"


POST_OFFICE_GUIDE = """# Post office

This folder is the working folder of the ClaudeMail Post office on this machine: one ordinary desktop session, pinned in the sidebar, whose only job is to wake the right session when desk mail waits. It is the only session on the desk `Post office` (set by the `.desk` file here).

- Nudge requests arrive as ClaudeMail deliveries. Each names a desk and the waiting message. Follow the steps in the delivery: list the desktop sessions, find the most recently active non-archived session whose working folder belongs to that desk (`mail.py desk-of`), send it the one-line note with `send_message`, and record the result with `mail.py nudged`.
- Never read, answer or act on the mail itself, never start or unarchive a session, and keep each turn short.
- If the user types anything here, answer in one line; that turn only serves to wake this session after an app restart.

Design: `~/ResilioSync/Services/ClaudeMail/260927 Desk design.md`. This file is rewritten by the dispatcher; edit `POST_OFFICE_GUIDE` in `mail.py` instead.
"""


def post_office_settings() -> dict:
    """Project settings for the Post office folder: it may list and message sessions and run `mail.py desk-of` and
    `mail.py nudged` without an approval prompt each time. Nothing else is allowed beyond the user's own settings."""
    python = "python3" if sys.platform == "darwin" else "python"
    allow = ["mcp__ccd_session_mgmt__list_sessions", "mcp__ccd_session_mgmt__send_message"]
    for tool in ["Bash", "PowerShell"]:
        for sub in ["desk-of", "nudged"]:
            allow.append(tool + "(" + python + ' "' + display(SCRIPT) + '" ' + sub + ":*)")
    return {"permissions": {"allow": allow}}


def ensure_post_office(machine: str, own: bool = True) -> None:
    """The Post office folder with its .desk and CLAUDE.md; its permission settings only from the machine itself,
    because they name that machine's python and mail.py path."""
    folder = post_office_dir(machine)
    folder.mkdir(parents=True, exist_ok=True)
    wanted = {folder / DESK_FILE: POST_OFFICE + "\n", folder / "CLAUDE.md": POST_OFFICE_GUIDE}
    if own:
        wanted[folder / ".claude" / "settings.json"] = json.dumps(post_office_settings(), indent=2) + "\n"
    for path, text in wanted.items():
        try:
            if not path.is_file() or path.read_text(encoding="utf-8") != text:
                write_atomically(path, text)
        except OSError:
            pass
    desk_dir(machine, POST_OFFICE).mkdir(parents=True, exist_ok=True)


def relocate_old_mailbox(machine: str) -> int:
    """Move anything under ResilioSync/Mailbox/<machine> into mail/<machine> (a file already there wins), then remove
    the emptied old folders, and ResilioSync/Mailbox itself once it is empty. Returns the number of files moved."""
    old = OLD_MAILBOX_ROOT / machine
    if not old.is_dir():
        return 0
    moved = 0
    for dirpath, dirnames, filenames in os.walk(old, topdown=False):
        here = Path(dirpath)
        for name in filenames:
            source = here / name
            target = MAILBOX_ROOT / machine / source.relative_to(old)
            try:
                if target.exists():
                    source.unlink()
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(source), str(target))
                    moved += 1
            except OSError:
                continue
        try:
            here.rmdir()
        except OSError:
            pass
    try:
        OLD_MAILBOX_ROOT.rmdir()
    except OSError:
        pass
    if moved:
        log(machine, "moved " + str(moved) + " file(s) from ResilioSync/Mailbox/" + machine + " to " + display(MAILBOX_ROOT / machine))
    return moved


def legacy_sweep(machine: str) -> None:
    """Mail written by a machine that still ran pre-desk code (sync lag): machine inbox and session folders -> desks."""
    root = machine_dir(machine)
    mapping = read_json(root / LEGACY / "sessions.json")
    sources = waiting_in(root)
    sessions = root / "sessions"
    if sessions.is_dir():
        for folder in sessions.iterdir():
            if folder.is_dir():
                sources.extend(waiting_in(folder))
    for path in sources:
        try:
            header, body = parse_message(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        to_session = clean_id(header.get("to_session"))
        desk = GENERAL
        for known, known_desk in mapping.items():
            if to_session and (known.startswith(to_session) or to_session.startswith(known)):
                desk = known_desk
        if path.parent != root and path.parent.parent.name == "sessions":
            info = read_json(path.parent / "session.json")
            if info.get("cwd"):
                desk = desk_for(info["cwd"])
        ident = message_id(path)
        header.update(to=machine + ":" + desk, id=ident, conversation=header.get("conversation") or ident)
        target = desk_dir(machine, desk) / (ident + ".md")
        try:
            write_atomically(target, format_message(header, body))
            path.unlink()
            log(machine, "moved pre-desk mail " + path.name + " to the desk " + desk)
        except OSError:
            continue


def import_legacy_sessions(machine: str, config: dict) -> None:
    """Seed the local registry from this machine's pre-desk session folders, so desks show their history at once."""
    keep = 86400 * setting(config, "session_keep_days", 30)
    folder = machine_dir(machine) / LEGACY / "sessions"
    if not folder.is_dir():
        return
    for sub in folder.iterdir():
        info = read_json(sub / "session.json")
        session = clean_id(info.get("session"))
        if not session or session_file(session).exists() or iso_age(info.get("last_active")) > keep:
            continue
        record = {"session": session, "folder": info.get("cwd", ""), "desk": desk_for(info.get("cwd", "")),
                  "started": info.get("started"), "last_active": info.get("last_active"), "imported": True}
        if info.get("first_prompt"):
            record.update(first_prompt=info["first_prompt"], last_prompt=info["first_prompt"])
        try:
            write_json(session_file(session), record)
        except OSError:
            pass


def refresh_desks(machine: str, config: dict, state: dict) -> dict:
    """Write desk.json for every desk that has sessions on this machine; return {desk: [session records]}."""
    keep = 86400 * setting(config, "session_keep_days", 30)
    by_desk = {}
    for record in local_sessions():
        if record.get("session") and record.get("desk") and iso_age(record.get("last_active")) <= keep:
            by_desk.setdefault(record["desk"], []).append(record)
    for desk, records in by_desk.items():
        records.sort(key=lambda r: str(r.get("last_active")), reverse=True)
        # Records written before typed_prompt existed may hold raw text, so it is applied again here.
        worked_on = [p for r in records for p in (typed_prompt(r.get("last_prompt")), typed_prompt(r.get("first_prompt"))) if p]
        info = {
            "desk": machine + ":" + desk,
            "folders": sorted(set(str(r.get("folder")) for r in records if r.get("folder"))),
            "last_active": records[0].get("last_active"),
            "last_worked_on": worked_on[0] if worked_on else "",
            "sessions": [{"session": r["session"], "folder": r.get("folder"), "started": r.get("started"),
                          "last_active": r.get("last_active")} for r in records[:20]],
        }
        signature = json.dumps(info, sort_keys=True)
        if state.get("desk_sig", {}).get(desk) != signature:
            try:
                write_json(desk_dir(machine, desk) / DESK_INFO, info)
                state.setdefault("desk_sig", {})[desk] = signature
            except OSError:
                pass
    return by_desk


def expire_local(config: dict) -> None:
    keep = 86400 * setting(config, "session_keep_days", 30)
    for sub in ["sessions", "waiters"]:
        folder = LOCAL_DIR / sub
        if not folder.is_dir():
            continue
        for path in folder.iterdir():
            try:
                if time.time() - path.stat().st_mtime > keep:
                    path.unlink()
            except OSError:
                pass
    for path in list(OLD_WAITER_DIR.glob("*")) if OLD_WAITER_DIR.is_dir() else []:
        try:
            if time.time() - path.stat().st_mtime > 2 * 86400:
                path.unlink()
        except OSError:
            pass


def write_nudge_request(machine: str, desk: str, message: Path, folders: list) -> Path:
    header, _ = parse_message(message.read_text(encoding="utf-8"))
    stamp = datetime.datetime.now().strftime("%y%m%d-%H%M%S")
    ident = new_message_id(machine, stamp, "nudge")
    fields = {"from": machine + ":dispatcher", "to": machine + ":" + POST_OFFICE, "subject": "Nudge a session of " + desk,
              "sent": now_iso(), "id": ident, "kind": "nudge", "desk": desk, "message": display(message),
              "folders": " | ".join(folders)}
    body = ("Mail waits on the desk " + machine + ":" + desk + " and no session there has taken it: "
            + message.name + " from " + header.get("from", "?") + ' ("' + header.get("subject", "") + '").\n'
            + "Folders this desk's sessions were started in: " + (", ".join(folders) or "none known on this machine")
            + ".\nRequest path: " + display(desk_dir(machine, POST_OFFICE) / (ident + ".md")))
    path = desk_dir(machine, POST_OFFICE) / (ident + ".md")
    write_atomically(path, format_message(fields, body))
    return path


def dispatch_mail(machine: str, config: dict, listening: dict, by_desk: dict, state: dict) -> None:
    """Advance every unclaimed desk message: waiting -> nudge requested -> nudged -> waiting for the user."""
    nudge_after = 60 * setting(config, "nudge_after_minutes", 2)
    # A Post office that is set up but busy with an earlier request takes the next one at its turn's end, so a request
    # is written whenever one exists; if it does not answer in time, the mail goes to the user below.
    post_office_open = bool(by_desk.get(POST_OFFICE))
    for folder in desk_folders(machine):
        desk = folder.name
        if desk == POST_OFFICE:
            for request in waiting_in(folder):                # drop requests whose mail was taken meanwhile
                header, _ = parse_message(request.read_text(encoding="utf-8"))
                if header.get("message") and not Path(header["message"]).is_file():
                    try:
                        request.unlink()
                    except OSError:
                        pass
            continue
        for message in waiting_in(folder):
            status = read_status(message)
            stage = status.get("state")
            if not stage:
                header, _ = parse_message(message.read_text(encoding="utf-8"))
                status.update(state="waiting", waiting_since=now_iso(), desk=machine + ":" + desk)
                write_status(message, status)
                log(machine, "arrived " + message.name + " on " + desk + " from " + header.get("from", "?") + ": "
                    + header.get("subject", "") + (" (a session is listening)" if listening.get(desk) else ""))
                continue
            if stage == "waiting" and iso_age(status.get("waiting_since")) >= nudge_after:
                folders = sorted(set(str(r.get("folder")) for r in by_desk.get(desk, []) if r.get("folder")))
                if post_office_open:
                    request = write_nudge_request(machine, desk, message, folders)
                    status.update(state="nudge requested", nudge_requested_at=now_iso(), nudge_request=display(request))
                    write_status(message, status)
                    log(machine, "asked the Post office to nudge a session of " + desk + " for " + message.name)
                else:
                    to_user(machine, desk, message, status, "no Post office session is set up on " + machine)
                continue
            if stage in ("nudge requested", "nudged"):
                since = status.get("nudged_at") or status.get("nudge_requested_at")
                if iso_age(since) >= nudge_after:
                    why = ("the nudged session (" + (status.get("nudged_title") or status.get("nudged_session", "?"))
                           + ") has not taken it") if stage == "nudged" else "the Post office did not answer"
                    to_user(machine, desk, message, status, why)
                continue
            if stage == "no session":
                to_user(machine, desk, message, status, status.get("reason") or "no session of this desk")


def to_user(machine: str, desk: str, message: Path, status: dict, why: str) -> None:
    try:
        header, _ = parse_message(message.read_text(encoding="utf-8"))
    except OSError:
        return                                                 # taken in the meantime
    if not message.is_file():
        return
    status.update(state="waiting for the user", reason=why, user_notified_at=now_iso())
    write_status(message, status)
    notify(machine, "Claude mail waits on " + desk,
           "From " + header.get("from", "?") + ": " + header.get("subject", "") + "\nOpen a Claude session in the "
           + desk + " project on " + machine + " to receive it (" + why + ").")


def clean_statuses(machine: str, config: dict) -> None:
    """Drop status files whose message is gone, and old delivered nudge requests."""
    for folder in desk_folders(machine):
        for path in list(folder.glob("*" + STATUS_SUFFIX)):
            if not (folder / (path.name[:-len(STATUS_SUFFIX)] + ".md")).exists():
                try:
                    if time.time() - path.stat().st_mtime > 60:
                        path.unlink()
                except OSError:
                    pass
    for path in list((desk_dir(machine, POST_OFFICE) / READ).glob("*")):
        try:
            if time.time() - path.stat().st_mtime > 7 * 86400:
                path.unlink()
        except OSError:
            pass


def take_dispatch_lock(machine: str, config: dict) -> bool:
    beat = read_json(local_heartbeat())
    pid = beat.get("pid")
    if isinstance(pid, int) and pid != os.getpid() and pid_alive(pid):
        if iso_age(beat.get("updated")) < 180:
            return False
        try:
            os.kill(pid, 15)                                  # a hung dispatcher: take over
            log(machine, "stopped a dispatcher that stopped reporting (pid " + str(pid) + ")")
        except OSError:
            pass
    write_json(local_heartbeat(), {"pid": os.getpid(), "version": code_version(), "updated": now_iso()})
    return True


def cmd_dispatch(args, config: dict) -> None:
    machine = this_machine(config)
    if not take_dispatch_lock(machine, config):
        sys.exit(EXIT_ALREADY_RUNNING)
    version = code_version()
    ensure_post_office(machine)
    import_legacy_sessions(machine, config)
    try:
        (machine_dir(machine) / OLD_REPORT).unlink()
    except OSError:
        pass
    log(machine, "dispatcher started (pid " + str(os.getpid()) + ", mail.py " + version + ")")
    state = {}
    last_report = 0.0
    last_beat = time.monotonic()
    last_listening = None
    while True:
        try:
            config = load_config()
        except (OSError, ValueError) as exc:
            log(machine, "WARN cannot read config, keeping the last one: " + str(exc))
        try:
            write_json(local_heartbeat(), {"pid": os.getpid(), "version": version, "updated": now_iso()})
            relocate_old_mailbox(machine)
            legacy_sweep(machine)
            by_desk = refresh_desks(machine, config, state)
            listening = {}
            for waiter in live_waiters():
                listening.setdefault(waiter.get("desk"), []).append(str(waiter.get("session", ""))[:8])
            dispatch_mail(machine, config, listening, by_desk, state)
            report = {"listening": listening, "post_office": bool(by_desk.get(POST_OFFICE))}
            if report != last_listening or time.time() - last_report > 60:
                write_report(machine, dict(report, machine=machine, pid=os.getpid(), version=version, updated=now_iso()))
                last_report = time.time()
                last_listening = report
            if time.monotonic() - last_beat >= 60 * setting(config, "heartbeat_minutes", 60):
                unclaimed = sum(len(waiting_in(f)) for f in desk_folders(machine))
                log(machine, "heartbeat: " + str(unclaimed) + " unclaimed, listening on "
                    + (", ".join(sorted(str(d) for d in listening)) or "no desk"))
                clean_statuses(machine, config)
                expire_local(config)
                last_beat = time.monotonic()
        except Exception as exc:                              # the dispatcher itself must keep running
            log(machine, "WARN dispatcher: " + type(exc).__name__ + ": " + str(exc))
        if code_version() != version and code_compiles():
            log(machine, "mail.py changed; dispatcher restarting onto the new code")
            sys.exit(EXIT_CODE_CHANGED)
        time.sleep(setting(config, "poll_seconds", 5))


def cmd_watch(args, config: dict) -> None:
    """What the scheduled task / LaunchAgent runs: keeps a dispatcher child running and starts it again when it exits,
    at once after a code change. Kept tiny, because it is the one part that only a restart of the task renews."""
    machine = this_machine(config)
    log(machine, "watch started (pid " + str(os.getpid()) + ")")
    python = Path(sys.executable)
    while True:
        started = time.monotonic()
        try:
            code = subprocess.call([str(python), "-B", str(SCRIPT), "dispatch"],
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as exc:
            log(machine, "WARN cannot start the dispatcher: " + str(exc))
            code = -1
        if code == EXIT_CODE_CHANGED:
            continue
        if code == EXIT_ALREADY_RUNNING:
            time.sleep(60)
            continue
        log(machine, "WARN dispatcher exited with code " + str(code) + " after " + str(int(time.monotonic() - started)) + " s")
        time.sleep(30)


# ---------------------------------------------------------------- self-maintenance

def check_dispatcher(machine: str, config: dict) -> None:
    """From a hook: if this machine's dispatcher has not reported for a few minutes (never started, crashed, or an old
    watcher from before the desk design still runs), restart its task in the background. At most every 20 minutes."""
    beat = read_json(local_heartbeat())
    if iso_age(beat.get("updated")) < 180:
        return
    marker = LOCAL_DIR / "kick.json"
    if iso_age(read_json(marker).get("at")) < 60 * setting(config, "kick_every_minutes", 20):
        return
    write_json(marker, {"at": now_iso()})
    flags = 0
    if sys.platform == "win32":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0) \
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    python = Path(sys.executable)
    subprocess.Popen([str(python), "-B", str(SCRIPT), "kick"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, creationflags=flags, start_new_session=(sys.platform != "win32"))


def cmd_kick(args, config: dict) -> None:
    """Restart this machine's watcher task (Windows) or LaunchAgent (Mac)."""
    machine = this_machine(config)
    if sys.platform == "win32":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        end = subprocess.run(["schtasks", "/End", "/TN", TASK_NAME], capture_output=True, text=True, creationflags=flags)
        time.sleep(3)
        run = subprocess.run(["schtasks", "/Run", "/TN", TASK_NAME], capture_output=True, text=True, creationflags=flags)
        result = "end " + str(end.returncode) + ", run " + str(run.returncode) + " " + " ".join((run.stdout + run.stderr).split())
    elif sys.platform == "darwin":
        run = subprocess.run(["launchctl", "kickstart", "-k", "gui/" + str(os.getuid()) + "/" + LAUNCHD_LABEL],
                             capture_output=True, text=True)
        result = "kickstart " + str(run.returncode) + " " + " ".join((run.stdout + run.stderr).split())
    else:
        result = "unsupported platform"
    log(machine, "kick: the dispatcher was not reporting, restarted the watcher (" + result + ")")
    print(result)


# ---------------------------------------------------------------- install, migrate

def settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def hook_python() -> Path:
    python = Path(sys.executable)
    if python.name.casefold() == "pythonw.exe":
        python = python.with_name("python.exe")               # a hook needs a console interpreter for its stdout
    for stable in (Path("/opt/homebrew/bin/python3"), Path("/usr/local/bin/python3")):
        if sys.platform == "darwin" and stable.is_file():
            python = stable                                    # survives a Homebrew upgrade; sys.executable is versioned
            break
    return python


def is_our_hook(entry: dict) -> bool:
    return any("ClaudeMail" in h.get("command", "") and "mail.py" in h.get("command", "") for h in entry.get("hooks", []))


def edit_settings(add: bool, config: dict) -> None:
    """Register `mail.py hook` (SessionStart, UserPromptSubmit) and `mail.py wait` (Stop, asyncRewake). These commands
    never change, so a code update never needs this again."""
    path = settings_path()
    settings = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    if path.is_file():
        shutil.copyfile(path, path.with_name("settings.json.bak-claudemail"))
    base = '"' + hook_python().as_posix() + '" -B "' + display(SCRIPT) + '" '
    hooks = settings.setdefault("hooks", {})
    for event in list(dict.fromkeys(HOOK_EVENTS + WAIT_EVENTS + OLD_WAIT_EVENTS)):
        entries = [e for e in hooks.get(event, []) if not is_our_hook(e)]
        if add and event in HOOK_EVENTS:
            entries.append({"hooks": [{"type": "command", "command": base + "hook", "timeout": 15}]})
        if add and event in WAIT_EVENTS:
            entries.append({"hooks": [{"type": "command", "command": base + "wait", "asyncRewake": True,
                                       "timeout": int(3600 * setting(config, "wait_hours", 24))}]})
        if entries:
            hooks[event] = entries
        else:
            hooks.pop(event, None)
    if not hooks:
        settings.pop("hooks")
    write_atomically(path, json.dumps(settings, indent=2) + "\n")
    print(("registered" if add else "removed") + " the ClaudeMail hooks in " + str(path))


def cmd_migrate(args, config: dict) -> None:
    """One-time move from the pre-desk layout, for every machine's mailbox: unread mail to desks, and the machine inbox's
    read/ and the sessions/ folders into _legacy/, with a session -> desk map for replies to old mail."""
    for machine in config["machines"]:
        relocate_old_mailbox(machine)
        root = machine_dir(machine)
        if not root.is_dir():
            continue
        legacy = root / LEGACY
        mapping = read_json(legacy / "sessions.json")
        before = dict(mapping)
        sessions = root / "sessions"
        if sessions.is_dir():
            for folder in sessions.iterdir():
                info = read_json(folder / "session.json")
                if info.get("session"):
                    mapping[clean_id(info["session"])] = desk_for(info.get("cwd", ""))
        if mapping != before:
            write_json(legacy / "sessions.json", mapping)
        for desk in set(mapping.values()) | {GENERAL}:
            desk_dir(machine, desk).mkdir(parents=True, exist_ok=True)       # addressable before their next session
        ensure_post_office(machine, own=(machine == this_machine(config)))
        legacy_sweep(machine)
        for name in [READ, "sessions"]:
            source = root / name
            if not source.is_dir():
                continue
            target = legacy / name
            target.mkdir(parents=True, exist_ok=True)
            for item in source.iterdir():
                destination = target / item.name
                if destination.exists():
                    continue
                shutil.move(str(item), str(destination))
            try:
                source.rmdir()
            except OSError:
                print("  " + machine + ": " + name + "/ not empty after the move, left in place")
        print(machine + ": migrated (" + str(len(mapping)) + " old sessions mapped to desks)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Mail between the Claude sessions on the house machines, addressed to desks.")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("send")
    p.add_argument("--to", action="append", help="Machine:Desk, Machine (its General desk), 'all', or a comma list")
    p.add_argument("--subject")
    p.add_argument("--body-file")
    p.add_argument("--reply-to", help="path or id of the message being answered")
    p.add_argument("--to-own-desk", action="store_true", help="allow sending to this session's own desk")
    p = sub.add_parser("desks")
    p.add_argument("machine", nargs="?")
    p = sub.add_parser("status")
    p.add_argument("message")
    p = sub.add_parser("inbox")
    p.add_argument("--all", action="store_true")
    p = sub.add_parser("desk-of")
    p.add_argument("folders", nargs="+")
    p = sub.add_parser("nudged")
    p.add_argument("request")
    p.add_argument("--session")
    p.add_argument("--title")
    p.add_argument("--none", action="store_true")
    p.add_argument("--reason")
    p = sub.add_parser("hook")
    p.add_argument("event", nargs="?")
    for name in ["wait", "watch", "dispatch", "kick", "install-hook", "uninstall-hook", "migrate", "whoami"]:
        sub.add_parser(name)
    args = parser.parse_args()
    config = load_config()
    commands = {"send": cmd_send, "desks": cmd_desks, "status": cmd_status, "inbox": cmd_inbox, "desk-of": cmd_desk_of,
                "nudged": cmd_nudged, "hook": cmd_hook, "wait": cmd_wait, "watch": cmd_watch, "dispatch": cmd_dispatch,
                "kick": cmd_kick, "migrate": cmd_migrate, "whoami": cmd_whoami,
                "install-hook": lambda a, c: edit_settings(True, c), "uninstall-hook": lambda a, c: edit_settings(False, c)}
    commands[args.command](args, config)


if __name__ == "__main__":
    main()
