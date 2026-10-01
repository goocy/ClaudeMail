"""Read-only audit of the ClaudeMail archive: how often mail between Claude sessions reached the wrong session.

Reads   ~/ResilioSync/Services/ClaudeMail/mail/ (messages, status files, desk.json, reports),
        ~/ResilioSync/Services/ClaudeMail/logs/ and ~/.claude/claudemail/logs/ (delivery logs),
        ~/.claude/claudemail/sessions/ (this machine's session registry),
        ~/Library/Application Support/LeaseResources/leases/ and ~/ResilioSync/Services/LeaseResources/leases/ (leases).
Writes  report.md beside this script, nothing else. No regular expressions, no glob patterns (house rules).
"""
import collections
import datetime
import json
import os
import sys

HOME = os.path.expanduser("~")
SERVICE = os.path.join(HOME, "ResilioSync", "Services", "ClaudeMail")
MAIL_ROOT = os.path.join(SERVICE, "mail")
SYNCED_LOGS = os.path.join(SERVICE, "logs")
LOCAL_LOGS = os.path.join(HOME, ".claude", "claudemail", "logs")
REGISTRY = os.path.join(HOME, ".claude", "claudemail", "sessions")
LOCAL_LEASES = os.path.join(HOME, "Library", "Application Support", "LeaseResources", "leases")
SYNCED_LEASES = os.path.join(HOME, "ResilioSync", "Services", "LeaseResources", "leases")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "report.md")

THIS_MACHINE = "Altiplano"
STATUS_SUFFIX = ".status.json"
POST_OFFICE = "Post office"
CUTOFF = datetime.datetime.fromisoformat("2026-09-27T00:00:00-05:00")
FIX = datetime.datetime.fromisoformat("2026-09-30T13:20:34-05:00")     # for_session went live (design note)
MOVE = datetime.datetime.fromisoformat("2026-09-29T16:27:00-05:00")    # General -> Home move of home-folder sessions
GENERAL, HOME_DESK = "General", "Home"
LISTEN_HOURS = 24                                                       # a Stop waiter lives up to wait_hours


# ---------------------------------------------------------------- small helpers

def parse_message(text):
    """The same split as mail.py parse_message: (header dict, body)."""
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


def clean_id(value):
    return "".join(c for c in str(value or "") if c.isalnum() or c == "-")


def read_json(path):
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def read_text(path):
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.read()


def parse_iso(value):
    try:
        stamp = datetime.datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=CUTOFF.tzinfo)
    return stamp


def same_session(a, b):
    return bool(a) and bool(b) and (a.startswith(b) or b.startswith(a))


def short(session):
    return session[:8] if session else "-"


def names_in(folder):
    try:
        return sorted(entry.name for entry in os.scandir(folder))
    except OSError:
        return []


def is_dir(path):
    return os.path.isdir(path)


def percentile(values, share):
    """Nearest-rank percentile of a sorted list."""
    if not values:
        return None
    rank = int(-(-share * len(values) // 1))                 # ceiling
    return values[max(0, min(len(values) - 1, rank - 1))]


def median(values):
    if not values:
        return None
    mid = len(values) // 2
    return values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2


def span(seconds):
    if seconds is None:
        return "-"
    seconds = float(seconds)
    if seconds < 0:
        return "-" + span(-seconds)
    if seconds < 120:
        return str(int(round(seconds))) + " s"
    if seconds < 7200:
        return str(round(seconds / 60, 1)) + " min"
    return str(round(seconds / 3600, 1)) + " h"


def cell(value):
    return " ".join(str(value).replace("|", "/").split())


def table(head, rows):
    lines = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(cell(v) for v in row) + " |")
    return lines


# ---------------------------------------------------------------- reading the archive

def load_message(path, machine, desk, where):
    header, body = parse_message(read_text(path))
    name = os.path.basename(path)
    stem = name[:-3]
    status_file = path[:-3] + STATUS_SUFFIX
    has_status = os.path.isfile(status_file)
    return {"path": path, "machine": machine, "desk": desk, "where": where, "name": name, "header": header, "body": body,
            "id": header.get("id") or stem, "stem": stem, "status": read_json(status_file) if has_status else {},
            "has_status": has_status, "sent": parse_iso(header.get("sent", "")),
            "new_format": bool(header.get("id"))}


def scan_mail():
    """Every message under mail/<Machine>/desks/<Desk>/ and its read/, plus the _legacy files (for looking up originals)."""
    desk_mail, legacy = [], []
    for machine in names_in(MAIL_ROOT):
        desks = os.path.join(MAIL_ROOT, machine, "desks")
        if not is_dir(desks):
            continue
        for desk in names_in(desks):
            folder = os.path.join(desks, desk)
            if not is_dir(folder):
                continue
            for where, sub in (("desk", folder), ("read", os.path.join(folder, "read"))):
                for name in names_in(sub):
                    path = os.path.join(sub, name)
                    if name.endswith(".md") and os.path.isfile(path):
                        desk_mail.append(load_message(path, machine, desk, where))
        for root, _dirs, files in os.walk(os.path.join(MAIL_ROOT, machine, "_legacy")):
            for name in sorted(files):
                if name.endswith(".md"):
                    legacy.append(load_message(os.path.join(root, name), machine, "_legacy", "legacy"))
    return desk_mail, legacy


def scan_logs():
    """Delivery and release lines of every log that can be read here: {(machine, file name): [events]}."""
    events = collections.defaultdict(list)
    files = []
    for folder in (SYNCED_LOGS, LOCAL_LOGS):
        for name in names_in(folder):
            parts = name.split(".")
            if len(parts) == 4 and parts[0] == "claudemail" and parts[3] == "log":
                files.append((os.path.join(folder, name), parts[1]))
    machines = {m.casefold(): m for m in names_in(MAIL_ROOT)}
    for path, machine_key in files:
        machine = machines.get(machine_key, machine_key)
        for line in read_text(path).split("\n"):
            stamp, rest = line[:19], line[20:]
            if rest.startswith("delivered to session "):
                prefix, _, tail = rest[len("delivered to session "):].partition(" (")
                route, _, tail = tail.partition(") on ")
                desk, _, name = tail.rpartition(": ")
                events[(machine, name.strip())].append({"kind": "delivered", "at": stamp, "session": prefix, "route": route,
                                                        "desk": desk})
            elif rest.startswith("released "):
                name, _, tail = rest[len("released "):].partition(" on ")
                events[(machine, name.strip())].append({"kind": "released", "at": stamp, "text": tail})
            elif rest.startswith("requeued "):
                name, _, tail = rest[len("requeued "):].partition(" ")
                events[(machine, name.strip())].append({"kind": "requeued", "at": stamp, "text": tail})
            elif rest.startswith("asked the Post office to "):
                name = rest.rpartition(" for ")[2]
                events[(machine, name.strip())].append({"kind": "nudge asked", "at": stamp, "text": rest})
    return events, [p for p, _ in files]


def scan_sessions():
    """Session records per machine and desk: desk.json of every desk, plus this machine's registry.
    Returns {(machine, desk): {session: {started, last_active, sources}}}."""
    found = collections.defaultdict(dict)

    def add(machine, desk, session, started, last_active, source):
        if not session:
            return
        record = found[(machine, desk)].setdefault(session, {"started": None, "last_active": None, "sources": set()})
        started, last_active = parse_iso(started), parse_iso(last_active)
        if started and (record["started"] is None or started < record["started"]):
            record["started"] = started
        if last_active and (record["last_active"] is None or last_active > record["last_active"]):
            record["last_active"] = last_active
        record["sources"].add(source)

    for machine in names_in(MAIL_ROOT):
        desks = os.path.join(MAIL_ROOT, machine, "desks")
        for desk in names_in(desks):
            info = read_json(os.path.join(desks, desk, "desk.json"))
            for item in info.get("sessions") or []:
                add(machine, desk, clean_id(item.get("session")), item.get("started"), item.get("last_active"), "desk.json")
    registry_count = 0
    for name in names_in(REGISTRY):
        if name.endswith(".json"):
            record = read_json(os.path.join(REGISTRY, name))
            if record.get("session") and record.get("desk"):
                registry_count += 1
                add(THIS_MACHINE, record["desk"], clean_id(record["session"]), record.get("started"),
                    record.get("last_active"), "registry")
    return found, registry_count


def scan_reports():
    """The newest dispatcher report of each machine: who listens where right now."""
    out = {}
    for machine in names_in(MAIL_ROOT):
        folder = os.path.join(MAIL_ROOT, machine, "reports")
        names = [n for n in names_in(folder) if n.endswith(".json")]
        if names:
            out[machine] = read_json(os.path.join(folder, names[-1]))
    return out


def scan_leases():
    """{lease id: (requesting session, lease desk, source)} from the lease files that can be read on this machine."""
    leases = {}
    folders = [(LOCAL_LEASES, "local lease file on " + THIS_MACHINE)]
    for machine in names_in(SYNCED_LEASES):
        folders.append((os.path.join(SYNCED_LEASES, machine), "synced legacy lease file of " + machine))
    for folder, source in folders:
        for name in names_in(folder):
            if name.startswith("L") and name.endswith(".json") and name.count(".") == 1:
                lease = read_json(os.path.join(folder, name))
                ident = lease.get("id") or name[:-5]
                if ident not in leases or not leases[ident][0]:
                    leases[ident] = (clean_id(lease.get("session")), str(lease.get("desk") or ""), source)
    return leases


def lease_id_of(message):
    for word in (message["header"].get("subject", "") + " " + message["body"][:400]).split():
        word = word.strip(".,:;()\"'`")
        if word.startswith("L") and len(word) > 9 and word[1:7].isdigit() and "-" in word:
            return word
    return ""


# ---------------------------------------------------------------- classification

def sender_class(message):
    header = message["header"]
    sender_desk = header.get("from", "").partition(":")[2]
    subject = header.get("subject", "")
    if header.get("from_session"):
        return "session"
    if header.get("kind") == "nudge" or sender_desk == "dispatcher":
        return "dispatcher nudge request"
    if sender_desk == "LeaseResources" and subject.startswith("Lease") and lease_id_of(message):
        return "LeaseResources notice"
    return "other without from_session"


def same_desk(a, b):
    """Whether two Machine:Desk addresses name the same desk; an address without a desk (pre-desk mail) matches any."""
    machine_a, _, desk_a = a.partition(":")
    machine_b, _, desk_b = b.partition(":")
    if machine_a.strip().casefold() != machine_b.strip().casefold():
        return False
    return not desk_a.strip() or not desk_b.strip() or desk_a.strip().casefold() == desk_b.strip().casefold()


def receiver_of(message, log_events):
    """(session, source). The status file's delivered_to is the full id; the log gives the first eight characters."""
    if message["where"] != "read":
        return "", "not delivered"
    session = clean_id(message["status"].get("delivered_to"))
    if session:
        return session, "status file"
    delivered = [e for e in log_events.get((message["machine"], message["name"]), []) if e["kind"] == "delivered"]
    if delivered:
        return delivered[-1]["session"], "log"
    return "", "not recorded"


def members(sessions, machine, desk, moment):
    """The session records of a desk as it was at that moment. Until the move of 2026-09-29 the sessions now listed on
    Home (home-folder sessions) belonged to General, and Home did not exist."""
    records = dict(sessions.get((machine, desk), {}))
    if moment is not None and moment < MOVE:
        if desk == HOME_DESK:
            return {}
        if desk == GENERAL:
            for known, record in sessions.get((machine, HOME_DESK), {}).items():
                if record["started"] and record["started"] < MOVE:
                    records.setdefault(known, record)
    return records


def session_record(sessions, machine, desk, session, moment):
    for known, record in members(sessions, machine, desk, moment).items():
        if same_session(known, session):
            return record
    return None


def where_known(sessions, machine, session):
    """Desks of this machine whose session lists hold the session."""
    return sorted(desk for (m, desk), records in sessions.items() if m == machine
                  and any(same_session(known, session) for known in records))


def asker_note(sessions, message, intended):
    """What is known about the intended session at the time the mail was sent."""
    record = session_record(sessions, message["machine"], message["desk"], intended, message["sent"])
    if record is None:
        elsewhere = where_known(sessions, message["machine"], intended)
        if elsewhere:
            return "other desk", "listed on another desk of this machine (" + ", ".join(elsewhere) + ")"
        return "unknown", "not in this desk's session list"
    last, sent = record["last_active"], message["sent"]
    if last is None or sent is None:
        return "unknown", "on this desk, activity times unknown"
    if last >= sent:
        return "active later", "on this desk, still active " + span((last - sent).total_seconds()) + " after the mail was sent"
    gap = (sent - last).total_seconds()
    if gap <= LISTEN_HOURS * 3600:
        return "idle", "on this desk, last hook event " + span(gap) + " before the mail was sent (may still have listened)"
    return "gone", "on this desk, last hook event " + span(gap) + " before the mail was sent"


def live_counts(sessions, machine, desk, moment):
    """(sessions with a hook event before and after the moment, sessions started before it and active within 24 h before)."""
    strict = loose = 0
    for record in members(sessions, machine, desk, moment).values():
        started, last = record["started"], record["last_active"]
        if started is None or last is None or started > moment:
            continue
        if last >= moment:
            strict += 1
        if last >= moment - datetime.timedelta(hours=LISTEN_HOURS):
            loose += 1
    return strict, loose


def max_overlap(records):
    points = []
    for record in records:
        if record["started"] and record["last_active"] and record["last_active"] >= record["started"]:
            points.append((record["started"], 0, 1))
            points.append((record["last_active"], 1, -1))
    best = now = 0
    for _stamp, _order, step in sorted(points):
        now += step
        best = max(best, now)
    return best


# ---------------------------------------------------------------- the audit

def main():
    desk_mail, legacy = scan_mail()
    log_events, log_files = scan_logs()
    sessions, registry_count = scan_sessions()
    reports = scan_reports()
    leases = scan_leases()
    out = []
    say = out.append

    undated = [m for m in desk_mail if m["sent"] is None]
    older = [m for m in desk_mail if m["sent"] is not None and m["sent"] < CUTOFF]
    recent = [m for m in desk_mail if m["sent"] is not None and m["sent"] >= CUTOFF]
    nudges = [m for m in recent if m["desk"] == POST_OFFICE or sender_class(m) == "dispatcher nudge request"]
    scope = [m for m in recent if m not in nudges]
    for m in scope:
        m["class"] = sender_class(m)
        m["receiver"], m["receiver_source"] = receiver_of(m, log_events)
        m["delivered"] = m["where"] == "read"
        m["delivered_at"] = parse_iso(m["status"].get("delivered_at", "")) if m["delivered"] else None
        m["for_session"] = clean_id(m["header"].get("for_session"))
        m["era"] = "before fix" if m["sent"] < FIX else "after fix"
    by_id = collections.defaultdict(list)
    for m in desk_mail + legacy:
        by_id[m["id"]].append(m)
        if m["stem"] != m["id"]:
            by_id[m["stem"]].append(m)
    last_sent = max(m["sent"] for m in scope)

    say("# ClaudeMail misrouting audit")
    say("")
    say("Generated " + datetime.datetime.now().astimezone().isoformat(timespec="seconds") + " on " + THIS_MACHINE
        + " by `audit.py`, which only reads. Scope: every message file under `mail/<Machine>/desks/<Desk>/` and its `read/`"
        + " whose `sent` header is 2026-09-27T00:00:00-05:00 or later; the newest is " + last_sent.isoformat() + "."
        + " Nudge requests on the Post office desks are dispatcher-internal and counted apart.")
    say("")
    say("Not in scope: " + str(len(older)) + " messages sent before 2026-09-27 (old headers, no status file), "
        + str(len(undated)) + " files without a readable `sent`, and " + str(len(legacy)) + " files under `_legacy/`"
        + " (used only to look up originals of replies). Post office nudge requests still on disk: " + str(len(nudges))
        + " (" + str(len([m for m in nudges if m["where"] == "read"])) + " taken, "
        + str(len([m for m in nudges if m["where"] != "read"])) + " not); delivered ones are deleted after seven days"
        + " and unanswered ones when their mail is taken, so this is not a count of all nudges.")
    say("")
    say("Fix time used for before/after splits: " + FIX.isoformat() + " (`for_session` live, from the design note).")
    say("")

    # ------------------------------------------------------------ 1
    say("## 1. Volume, delivery, final status")
    say("")
    say("Measured by counting files: a message in `read/` is delivered, one directly in the desk folder is unclaimed;"
        + " the status value is `state` in `<id>.status.json`.")
    say("")
    delivered = [m for m in scope if m["delivered"]]
    unclaimed = [m for m in scope if not m["delivered"]]
    old_format = [m for m in scope if not m["new_format"]]
    say("- Messages in scope: " + str(len(scope)) + " (" + str(len(old_format)) + " of them in the pre-desk header format"
        + " without `id`, sent on 2026-09-27).")
    say("- Delivered: " + str(len(delivered)) + ". Unclaimed: " + str(len(unclaimed)) + ".")
    say("- With a status file: " + str(len([m for m in scope if m["has_status"]])) + "; without: "
        + str(len([m for m in scope if not m["has_status"]])) + ".")
    say("")
    states = collections.Counter((m["status"].get("state") or "(no status file)", "read/" if m["delivered"] else "desk")
                                 for m in scope)
    out.extend(table(["final state", "where the file is", "messages"],
                     [[k[0], k[1], v] for k, v in sorted(states.items(), key=lambda kv: -kv[1])]))
    say("")
    say("Per day sent:")
    say("")
    days = collections.Counter(m["sent"].date().isoformat() for m in scope)
    out.extend(table(["day", "messages"], [[d, days[d]] for d in sorted(days)]))
    say("")
    say("Per machine (the receiving machine):")
    say("")
    rows = []
    for machine in sorted(set(m["machine"] for m in scope)):
        mine = [m for m in scope if m["machine"] == machine]
        rows.append([machine, len(mine), len([m for m in mine if m["delivered"]]), len([m for m in mine if not m["delivered"]]),
                     len([m for m in mine if m["class"] == "session"]), len([m for m in mine if m["class"] != "session"])])
    out.extend(table(["machine", "messages", "delivered", "unclaimed", "from a session", "not from a session"], rows))
    say("")
    say("Unclaimed messages:")
    say("")
    out.extend(table(["machine", "desk", "id", "sent", "state", "reason", "from", "subject"],
                     [[m["machine"], m["desk"], m["id"], m["header"].get("sent", ""), m["status"].get("state", "(no status file)"),
                       m["status"].get("reason", ""), m["header"].get("from", ""), m["header"].get("subject", "")]
                      for m in sorted(unclaimed, key=lambda m: m["sent"])]))
    say("")

    # ------------------------------------------------------------ 2
    say("## 2. Who sends")
    say("")
    say("A session sender has a non-empty `from_session`. A LeaseResources notice has an empty `from_session`, a `from` desk"
        + " named `LeaseResources`, a subject starting with `Lease` and a lease id (`L<yymmdd>-...`) in it; `res.py` strips"
        + " `CLAUDE_CODE_SESSION_ID` before calling `mail.py send`, which is why these carry no session. Everything else"
        + " without `from_session` is listed by name below.")
    say("")
    classes = collections.Counter(m["class"] for m in scope)
    out.extend(table(["sender class", "messages"], [[k, v] for k, v in classes.most_common()]))
    say("")
    session_from_lease_desk = [m for m in scope if m["class"] == "session"
                               and m["header"].get("from", "").partition(":")[2] == "LeaseResources"]
    say("Of the session-sent messages, " + str(len(session_from_lease_desk)) + " come from a session working at a"
        + " LeaseResources desk (real sessions, not the service).")
    say("")
    others = [m for m in scope if m["class"] == "other without from_session"]
    if others:
        say("Other messages without `from_session`:")
        say("")
        out.extend(table(["machine", "desk", "id", "sent", "from", "subject", "format"],
                         [[m["machine"], m["desk"], m["id"], m["header"].get("sent", ""), m["header"].get("from", ""),
                           m["header"].get("subject", ""), "desk" if m["new_format"] else "pre-desk"] for m in others]))
        say("")
    senders = collections.Counter((m["header"].get("from", ""), m["class"]) for m in scope)
    say("By sender desk:")
    say("")
    out.extend(table(["from", "class", "messages"], [[k[0], k[1], v] for k, v in senders.most_common()]))
    say("")

    # ------------------------------------------------------------ 3
    say("## 3. Replies")
    say("")
    say("A reply is a message with `in_reply_to`, or with a `conversation` that differs from its own `id`. The original is"
        + " looked up by that id among all message files under `mail/` (desks, `read/`, `_legacy/`); where several files"
        + " carry the id (one send to several machines), the one addressed to the replier's desk is preferred, and all"
        + " copies must agree on `from_session`. The intended session is the original's `from_session`; the receiving"
        + " session is `delivered_to` in the reply's status file. Session ids are compared in full. A message that refers"
        + " to an original but is addressed to a desk other than the original's sender (a follow-up by the asking side,"
        + " sent with an explicit `--to`) is not a reply to the asker and is left out of the comparison.")
    say("")
    replies = []
    for m in scope:
        header = m["header"]
        target = header.get("in_reply_to") or ""
        how = "in_reply_to"
        if not target and header.get("conversation") and header["conversation"] != m["id"]:
            target, how = header["conversation"], "conversation only"
        if not target:
            continue
        candidates = [c for c in by_id.get(target, []) if c is not m]
        preferred = [c for c in candidates if c["header"].get("to", "") == header.get("from", "")]
        pool = preferred or candidates
        asked = sorted(set(clean_id(c["header"].get("from_session")) for c in pool))
        intended = asked[0] if len(asked) == 1 else ""
        back = [c for c in pool if same_desk(c["header"].get("from", ""), header.get("to", ""))]
        if not candidates:
            outcome = "not determinable: original not found"
        elif not back:
            outcome = "not a reply to the asker: sent to a desk other than the original's sender (follow-up)"
        elif len(asked) > 1:
            outcome = "not determinable: originals disagree on from_session"
        elif not intended:
            outcome = "not determinable: original has no from_session"
        elif not m["delivered"]:
            outcome = "not delivered yet"
        elif not m["receiver"]:
            outcome = "not determinable: receiving session not recorded"
        elif same_session(m["receiver"], intended):
            outcome = "delivered to the asking session"
        else:
            outcome = "delivered to a different session"
        m["reply"] = {"how": how, "target": target, "intended": intended, "outcome": outcome}
        if outcome == "delivered to a different session":
            m["reply"]["asker_class"], m["reply"]["asker_note"] = asker_note(sessions, m, intended)
        replies.append(m)
    outcomes = collections.Counter(m["reply"]["outcome"] for m in replies)
    say("Replies in scope: " + str(len(replies)) + " (" + str(len([m for m in replies if m["reply"]["how"] == "in_reply_to"]))
        + " by `in_reply_to`, " + str(len([m for m in replies if m["reply"]["how"] != "in_reply_to"])) + " by conversation id only).")
    say("")
    out.extend(table(["outcome", "replies", "before fix", "after fix"],
                     [[k, v, len([m for m in replies if m["reply"]["outcome"] == k and m["era"] == "before fix"]),
                       len([m for m in replies if m["reply"]["outcome"] == k and m["era"] == "after fix"])]
                      for k, v in outcomes.most_common()]))
    say("")
    wrong = sorted([m for m in replies if m["reply"]["outcome"] == "delivered to a different session"], key=lambda m: m["sent"])
    say("What the session lists say about the asking session of each mismatch (from `desk.json` of the receiving desk, and"
        + " for " + THIS_MACHINE + " the local registry; `last_active` is the last hook event, so a session that was idle but"
        + " still listening counts as idle here):")
    say("")
    asker_classes = collections.Counter(m["reply"]["asker_class"] for m in wrong)
    labels = {"active later": "asker on this desk and active after the reply was sent (a sibling took it from a live asker)",
              "idle": "asker on this desk, last hook event within 24 h before the reply (may still have listened)",
              "gone": "asker on this desk, last hook event more than 24 h before the reply",
              "other desk": "asker listed only on another desk of the machine",
              "unknown": "asker not in the receiving desk's session list (list holds 20, or the session is older)"}
    out.extend(table(["asker state", "mismatches"], [[labels.get(k, k), v] for k, v in asker_classes.most_common()]))
    say("")
    say("By receiving desk:")
    say("")
    per_desk = collections.Counter((m["machine"], m["desk"]) for m in replies)
    out.extend(table(["machine", "desk", "replies", "to the asker", "to a different session", "not delivered", "not determinable or follow-up"],
                     [[k[0], k[1], v,
                       len([m for m in replies if (m["machine"], m["desk"]) == k and m["reply"]["outcome"] == "delivered to the asking session"]),
                       len([m for m in replies if (m["machine"], m["desk"]) == k and m["reply"]["outcome"] == "delivered to a different session"]),
                       len([m for m in replies if (m["machine"], m["desk"]) == k and m["reply"]["outcome"] == "not delivered yet"]),
                       len([m for m in replies if (m["machine"], m["desk"]) == k and (m["reply"]["outcome"].startswith("not determinable") or m["reply"]["outcome"].startswith("not a reply"))])]
                      for k, v in sorted(per_desk.items())]))
    say("")
    say("Every reply that reached a different session than the one that asked (" + str(len(wrong)) + "):")
    say("")
    out.extend(table(["id", "desk", "sent", "intended", "received by", "for_session", "asker", "from", "subject"],
                     [[m["id"], m["machine"] + ":" + m["desk"], m["header"].get("sent", ""), short(m["reply"]["intended"]),
                       short(m["receiver"]), short(m["for_session"]), m["reply"]["asker_note"], m["header"].get("from", ""),
                       m["header"].get("subject", "")] for m in wrong]))
    say("")
    undet = [m for m in replies if m["reply"]["outcome"].startswith("not ")]
    say("Replies that could not be judged (" + str(len(undet)) + "):")
    say("")
    out.extend(table(["id", "desk", "sent", "outcome", "refers to", "subject"],
                     [[m["id"], m["machine"] + ":" + m["desk"], m["header"].get("sent", ""), m["reply"]["outcome"],
                       m["reply"]["target"], m["header"].get("subject", "")] for m in sorted(undet, key=lambda m: m["sent"])]))
    say("")

    # ------------------------------------------------------------ 4
    say("## 4. Mail with `for_session`")
    say("")
    say("Measured from the `for_session` header against `delivered_to`; a release is `released_at` / `released_why` in the"
        + " status file, cross-checked against `released` lines in the logs readable here.")
    say("")
    owned = [m for m in scope if m["for_session"]]
    rows = []
    for m in owned:
        released = m["status"].get("released_at")
        log_released = [e for e in log_events.get((m["machine"], m["name"]), []) if e["kind"] == "released"]
        if not m["delivered"]:
            m["owned"] = "unclaimed"
        elif same_session(m["receiver"], m["for_session"]):
            m["owned"] = "delivered to that session"
        elif not m["receiver"]:
            m["owned"] = "delivered, receiver not recorded"
        elif released or log_released:
            m["owned"] = "released, then taken by another session"
        else:
            m["owned"] = "delivered to another session without a recorded release"
        m["released_why"] = m["status"].get("released_why") or (log_released[-1]["text"] if log_released else "")
    counts = collections.Counter(m["owned"] for m in owned)
    say("Messages with `for_session`: " + str(len(owned)) + " (first sent "
        + (min(m["sent"] for m in owned).isoformat() if owned else "-") + "; "
        + str(len([m for m in owned if m["class"] == "LeaseResources notice"])) + " LeaseResources notices, "
        + str(len([m for m in owned if m["class"] == "session"])) + " from sessions).")
    say("")
    out.extend(table(["outcome", "messages"], [[k, v] for k, v in counts.most_common()]))
    say("")
    say("Status files with `released_at`: " + str(len([m for m in owned if m["status"].get("released_at")]))
        + ". Log lines `released ...` in the readable logs: "
        + str(len([e for events in log_events.values() for e in events if e["kind"] == "released"])) + ".")
    say("")
    not_owner = [m for m in owned if m["owned"] != "delivered to that session"]
    if not_owner:
        say("Every `for_session` message that did not reach that session:")
        say("")
        out.extend(table(["id", "desk", "sent", "for_session", "received by", "outcome", "release reason", "subject"],
                         [[m["id"], m["machine"] + ":" + m["desk"], m["header"].get("sent", ""), short(m["for_session"]),
                           short(m["receiver"]), m["owned"], m["released_why"], m["header"].get("subject", "")]
                          for m in sorted(not_owner, key=lambda m: m["sent"])]))
        say("")
    out.extend(table(["machine", "desk", "with for_session", "to that session", "other outcome"],
                     [[k[0], k[1], v, len([m for m in owned if (m["machine"], m["desk"]) == k and m["owned"] == "delivered to that session"]),
                       len([m for m in owned if (m["machine"], m["desk"]) == k and m["owned"] != "delivered to that session"])]
                      for k, v in sorted(collections.Counter((m["machine"], m["desk"]) for m in owned).items())]))
    say("")

    # ------------------------------------------------------------ 5
    say("## 5. LeaseResources notices against the lease's requesting session")
    say("")
    say("The lease id is read from the subject; the requesting session is the `session` field of the lease file. Lease files"
        + " are machine-local since 2026-09-28 (`~/Library/Application Support/LeaseResources/leases/` here), so only"
        + " " + THIS_MACHINE + "'s leases and the few synced legacy files of 2026-09-27 under"
        + " `Services/LeaseResources/leases/<Machine>/` can be read; the synced lease reports of other machines carry no"
        + " session field. Lease files readable: " + str(len(leases)) + ", with a session: "
        + str(len([1 for v in leases.values() if v[0]])) + ".")
    say("")
    notices = [m for m in scope if m["class"] == "LeaseResources notice"]
    for m in notices:
        ident = lease_id_of(m)
        session, lease_desk, source = leases.get(ident, ("", "", ""))
        m["lease"] = {"id": ident, "session": session, "desk": lease_desk, "source": source}
        if ident not in leases:
            verdict = "lease file not readable here"
        elif not session:
            verdict = "lease file has no session"
        elif not m["delivered"]:
            verdict = "not delivered"
        elif not m["receiver"]:
            verdict = "receiver not recorded"
        elif same_session(m["receiver"], session):
            verdict = "delivered to the requesting session"
        else:
            verdict = "delivered to a different session"
        m["lease"]["verdict"] = verdict
        if verdict == "delivered to a different session":
            m["lease"]["asker_class"], m["lease"]["asker_note"] = asker_note(sessions, m, session)
    rows = []
    for era, with_owner in (("before fix", False), ("before fix", True), ("after fix", False), ("after fix", True)):
        part = [m for m in notices if m["era"] == era and bool(m["for_session"]) == with_owner]
        if part:
            c = collections.Counter(m["lease"]["verdict"] for m in part)
            rows.append([era, "yes" if with_owner else "no", len(part), c["delivered to the requesting session"],
                         c["delivered to a different session"], c["lease file not readable here"],
                         c["lease file has no session"] + c["not delivered"] + c["receiver not recorded"]])
    out.extend(table(["sent", "has for_session", "notices", "to the requester", "to a different session",
                      "lease file not readable here", "other"], rows))
    say("")
    out.extend(table(["receiving machine", "notices", "to the requester", "to a different session", "lease file not readable here"],
                     [[machine, len([m for m in notices if m["machine"] == machine]),
                       len([m for m in notices if m["machine"] == machine and m["lease"]["verdict"] == "delivered to the requesting session"]),
                       len([m for m in notices if m["machine"] == machine and m["lease"]["verdict"] == "delivered to a different session"]),
                       len([m for m in notices if m["machine"] == machine and m["lease"]["verdict"] == "lease file not readable here"])]
                      for machine in sorted(set(m["machine"] for m in notices))]))
    say("")
    owner_differs = [m for m in notices if m["for_session"] and m["lease"]["session"]
                     and not same_session(m["for_session"], m["lease"]["session"])]
    say("Notices whose `for_session` differs from the lease file's session: " + str(len(owner_differs)) + ".")
    say("")
    lease_wrong = sorted([m for m in notices if m["lease"]["verdict"] == "delivered to a different session"], key=lambda m: m["sent"])
    say("Every notice that reached a different session than the one that requested the lease (" + str(len(lease_wrong)) + "):")
    say("")
    out.extend(table(["id", "desk", "sent", "lease", "requested by", "received by", "for_session", "requester", "subject"],
                     [[m["id"], m["machine"] + ":" + m["desk"], m["header"].get("sent", ""), m["lease"]["id"],
                       short(m["lease"]["session"]), short(m["receiver"]), short(m["for_session"]), m["lease"]["asker_note"],
                       m["header"].get("subject", "")] for m in lease_wrong]))
    say("")
    c = collections.Counter(m["lease"]["asker_class"] for m in lease_wrong)
    out.extend(table(["requester state", "mismatches"], [[labels.get(k, k), v] for k, v in c.most_common()]))
    say("")

    # ------------------------------------------------------------ 6
    say("## 6. Sessions per desk")
    say("")
    say("Sessions that took mail: distinct `delivered_to` values of the desk's in-scope mail. Sessions registered: the"
        + " `sessions` list in the desk's `desk.json` (at most the 20 most recently active, written by that machine's"
        + " dispatcher), and for " + THIS_MACHINE + " also the local registry (" + str(registry_count) + " records with a desk)."
        + " A session counts for a day when its span from `started` to `last_active` touches that day. `last_active` is the"
        + " last hook event, so the spans understate how long an idle session kept listening (up to 24 h), and `max at once`"
        + " is the largest number of overlapping spans among the sessions listed on the desk today."
        + " General and Home: until the move of " + MOVE.isoformat() + " (time taken from " + THIS_MACHINE + "'s log; other"
        + " machines moved when their dispatcher took that code) home-folder sessions belonged to General; they are listed"
        + " on Home today. The day columns, the takers-not-listed column and all asker checks therefore count Home's"
        + " sessions started before the move as General's for moments before the move, and Home as empty; a day is judged"
        + " at its start, so 09-29 counts as before the move.")
    say("")
    day_list = []
    day = CUTOFF.date()
    while day <= last_sent.date():
        day_list.append(day)
        day += datetime.timedelta(days=1)
    desk_keys = sorted(set((m["machine"], m["desk"]) for m in scope) | set(k for k in sessions if k[1] != POST_OFFICE))
    rows = []
    desk_days = multi_days = 0
    for key in desk_keys:
        mine = [m for m in scope if (m["machine"], m["desk"]) == key]
        takers = set(m["receiver"] for m in mine if m["receiver"])
        records = sessions.get(key, {})
        ever = dict(members(sessions, key[0], key[1], CUTOFF))
        ever.update(records)
        unknown_takers = [t for t in takers if not any(same_session(t, known) for known in ever)]
        per_day = []
        for d in day_list:
            start = datetime.datetime.combine(d, datetime.time(0, 0), tzinfo=CUTOFF.tzinfo)
            end = start + datetime.timedelta(days=1)
            count = len([1 for r in members(sessions, key[0], key[1], start).values() if r["started"] and r["last_active"]
                         and r["started"] < end and r["last_active"] >= start])
            per_day.append(count)
            if count >= 1:
                desk_days += 1
            if count >= 2:
                multi_days += 1
        max_takers_day = 0
        for d in day_list:
            max_takers_day = max(max_takers_day, len(set(m["receiver"] for m in mine if m["receiver"] and m["delivered_at"]
                                                         and m["delivered_at"].date() == d)))
        rows.append([key[0], key[1], len(mine), len(takers), max_takers_day, len(records), len(unknown_takers)]
                    + per_day + [max_overlap(records.values())])
    out.extend(table(["machine", "desk", "mail", "sessions that took mail", "most takers on one day", "sessions listed",
                      "takers not listed"] + [d.strftime("%m-%d") for d in day_list] + ["max at once"], rows))
    say("")
    say("Desk-days (a desk on a day with at least one listed session): " + str(desk_days) + "; with two or more sessions: "
        + str(multi_days) + ".")
    multi_taker_desks = [r for r in rows if r[3] >= 2]
    say("Desks with mail in scope: " + str(len([r for r in rows if r[2]])) + "; desks where two or more sessions took mail: "
        + str(len(multi_taker_desks)) + "; where two or more took mail on the same day: " + str(len([r for r in rows if r[4] >= 2])) + ".")
    say("")
    say("Sessions of the receiving desk at the moment each message was sent. Strict: sessions with a hook event both before"
        + " and after the send time. Loose: sessions started before it with a hook event at most 24 h earlier (could still"
        + " have had a live waiter).")
    say("")
    strict_counts, loose_counts = collections.Counter(), collections.Counter()
    for m in scope:
        strict, loose = live_counts(sessions, m["machine"], m["desk"], m["sent"])
        m["strict"], m["loose"] = strict, loose
        strict_counts[min(strict, 5)] += 1
        loose_counts[min(loose, 5)] += 1
    out.extend(table(["sessions on the desk at send time", "messages (strict)", "messages (loose)"],
                     [[("5 or more" if n == 5 else str(n)), strict_counts[n], loose_counts[n]] for n in range(6)]))
    say("")
    say("Messages sent while two or more sessions were on the desk: strict " + str(len([m for m in scope if m["strict"] >= 2]))
        + " of " + str(len(scope)) + ", loose " + str(len([m for m in scope if m["loose"] >= 2])) + " of " + str(len(scope)) + ".")
    say("")
    say("Listening right now, from the newest dispatcher report of each machine (reports keep only the last five, so there"
        + " is no history):")
    say("")
    rows = []
    for machine, report in sorted(reports.items()):
        listening = report.get("listening") or {}
        rows.append([machine, report.get("updated", ""), len(listening),
                     ", ".join(d + " (" + str(len(v)) + ")" for d, v in sorted(listening.items()) if isinstance(v, list) and len(v) >= 2) or "-"])
    out.extend(table(["machine", "report time", "desks listening", "desks with two or more listeners"], rows))
    say("")

    # ------------------------------------------------------------ 7
    say("## 7. Delay from `sent` to delivery")
    say("")
    say("Delay is `delivered_at` in the status file minus `sent` in the header, for delivered messages that have both."
        + " Sender and receiver clocks are different machines' clocks. A nudge is `nudge_requested_at` (or `owner_nudge`) in"
        + " the status file; told to the user is `user_notified_at` or the state `waiting for the user`. A requeued message"
        + " lost its earlier status, so its nudge history is gone and its delay runs to the second delivery.")
    say("")
    timed = [m for m in scope if m["delivered"] and m["delivered_at"]]
    for m in timed:
        m["delay"] = (m["delivered_at"] - m["sent"]).total_seconds()

    def delay_row(label, part):
        values = sorted(m["delay"] for m in part)
        return [label, len(values), span(median(values)), span(percentile(values, 0.9)), span(values[-1] if values else None),
                len([v for v in values if v <= 10]), len([v for v in values if v > 300]), len([v for v in values if v > 3600])]

    rows = [delay_row("all", timed), delay_row("from a session", [m for m in timed if m["class"] == "session"]),
            delay_row("LeaseResources notices", [m for m in timed if m["class"] == "LeaseResources notice"])]
    for machine in sorted(set(m["machine"] for m in timed)):
        rows.append(delay_row("received on " + machine, [m for m in timed if m["machine"] == machine]))
    for d in day_list:
        rows.append(delay_row("sent " + d.isoformat(), [m for m in timed if m["sent"].date() == d]))
    out.extend(table(["messages", "count", "median", "90th percentile", "maximum", "within 10 s", "over 5 min", "over 1 h"], rows))
    say("")
    say("Delivered messages without a usable `delivered_at`: " + str(len(delivered) - len(timed)) + ". Negative delays (clock"
        + " difference): " + str(len([m for m in timed if m["delay"] < 0])) + ".")
    say("")

    def nudged(m):
        return bool(m["status"].get("nudge_requested_at") or m["status"].get("nudged_at") or m["status"].get("owner_nudge")
                    or m["status"].get("nudge_failed_at"))

    def told_user(m):
        return bool(m["status"].get("user_notified_at")) or m["status"].get("state") == "waiting for the user"

    asked = [m for m in scope if nudged(m)]
    user = [m for m in scope if told_user(m)]
    requeued = [m for m in scope if m["status"].get("requeued_at")]
    say("- Messages for which the Post office was asked to nudge: " + str(len(asked)) + " (" + str(len([m for m in asked if m["delivered"]]))
        + " delivered later, " + str(len([m for m in asked if not m["delivered"]])) + " still unclaimed); the Post office"
        + " recorded a nudged session for " + str(len([m for m in asked if m["status"].get("nudged_at")])) + " of them.")
    say("- Messages that reached `waiting for the user` at some point: " + str(len(user)) + " ("
        + str(len([m for m in user if m["delivered"]])) + " delivered later, " + str(len([m for m in user if not m["delivered"]]))
        + " still in that state).")
    say("- Requeued messages (earlier status lost): " + str(len(requeued)) + ".")
    say("")
    out.extend(table(["machine", "messages", "nudge asked", "told to the user", "still unclaimed"],
                     [[machine, len([m for m in scope if m["machine"] == machine]), len([m for m in asked if m["machine"] == machine]),
                       len([m for m in user if m["machine"] == machine]), len([m for m in unclaimed if m["machine"] == machine])]
                      for machine in sorted(set(m["machine"] for m in scope))]))
    say("")
    reasons = collections.Counter(m["status"].get("reason", "") for m in user)
    out.extend(table(["reason given to the user", "messages"], [[k or "(none)", v] for k, v in reasons.most_common()]))
    say("")
    say("The twenty slowest deliveries:")
    say("")
    out.extend(table(["id", "desk", "sent", "delay", "nudge asked", "told to the user", "subject"],
                     [[m["id"], m["machine"] + ":" + m["desk"], m["header"].get("sent", ""), span(m["delay"]),
                       "yes" if nudged(m) else "", "yes" if told_user(m) else "", m["header"].get("subject", "")]
                      for m in sorted(timed, key=lambda m: -m["delay"])[:20]]))
    say("")

    # ------------------------------------------------------------ cross-checks and per-desk table
    say("## Cross-checks")
    say("")
    compared = agree = 0
    routes = collections.Counter()
    disagreements = []
    for m in scope:
        events = [e for e in log_events.get((m["machine"], m["name"]), []) if e["kind"] == "delivered"]
        if events and clean_id(m["status"].get("delivered_to")):
            compared += 1
            routes[events[-1]["route"]] += 1
            if m["status"]["delivered_to"].startswith(events[-1]["session"]):
                agree += 1
            else:
                disagreements.append(m["id"] + " on " + m["machine"] + ":" + m["desk"])
    say("- Logs read: " + str(len(log_files)) + " files (" + THIS_MACHINE + "'s local logs from 2026-09-28 on, and the remnants"
        + " in the synced `logs/`). Deliveries found both in a log and in a status file: " + str(compared)
        + "; the log's session prefix matches `delivered_to` in " + str(agree) + "."
        + (" Disagreements: " + "; ".join(disagreements) + "." if disagreements else ""))
    say("- Route of those deliveries: " + ", ".join(k + " " + str(v) for k, v in routes.most_common()) + ".")
    say("- Receiving session taken from the status file for " + str(len([m for m in scope if m["receiver_source"] == "status file"]))
        + " messages, from the log alone for " + str(len([m for m in scope if m["receiver_source"] == "log"]))
        + ", not recorded for " + str(len([m for m in scope if m["receiver_source"] == "not recorded"])) + ".")
    union = set(id(m) for m in wrong) | set(id(m) for m in lease_wrong) | set(id(m) for m in not_owner if m["delivered"] and m["receiver"])
    say("- Distinct messages that reached a session other than the one they were meant for (sections 3, 4 and 5 together): "
        + str(len(union)) + " of " + str(len(delivered)) + " delivered.")
    say("")
    say("## Per desk")
    say("")
    rows = []
    for key in sorted(set((m["machine"], m["desk"]) for m in scope)):
        mine = [m for m in scope if (m["machine"], m["desk"]) == key]
        rows.append([key[0], key[1], len(mine), len([m for m in mine if m["delivered"]]), len([m for m in mine if not m["delivered"]]),
                     len([m for m in mine if m["class"] == "session"]), len([m for m in mine if m["class"] == "LeaseResources notice"]),
                     len([m for m in mine if m.get("reply")]), len([m for m in mine if m["for_session"]]),
                     len([m for m in mine if id(m) in union])])
    out.extend(table(["machine", "desk", "messages", "delivered", "unclaimed", "from a session", "lease notices", "replies",
                      "with for_session", "reached another session"], rows))
    say("")

    with open(OUT, "w", encoding="utf-8") as handle:
        handle.write("\n".join(out) + "\n")
    print("wrote " + OUT + " (" + str(len(out)) + " lines); " + str(len(scope)) + " messages in scope, "
          + str(len(union)) + " reached another session")


if __name__ == "__main__":
    sys.exit(main())
