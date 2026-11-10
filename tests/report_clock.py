"""Report slots: the newest report is read whatever the clock does, and nothing is deleted once a machine runs it.

Since 2026-11-09 a machine overwrites the oldest of five fixed files, slot-0.json to slot-4.json, instead of writing a
new <stamp>.json and deleting old ones (every deletion left a record that all Resilio peers keep and walk for good).
The 2026-10-27 case stays covered: a time zone change (Pulvermaar, CET to UTC-5 on 2026-10-26) must not make a new
report look older. This switches the process from European time to UTC-5 between writes, with leftover
<stamp>.json reports from before, and checks what is kept and what is read. Then: slots written while the clock ran
ahead are overwritten first; a slot that cannot be written is skipped for the next; and the one-way-sync check
(`seen`, peer_stamps, dispatcher_warning) works from the `updated` inside the reports, in the stamp form that machines
still on the code before parse.

    python3 -B tests/report_clock.py [path to mail.py]    (default: the live one beside this folder)
"""
import datetime, importlib.util, json, os, shutil, sys, tempfile, time
from pathlib import Path

sys.dont_write_bytecode = True
SCRIPT = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "mail.py").resolve()
spec = importlib.util.spec_from_file_location("mail_under_test", SCRIPT)
mail = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mail)
scratch = Path(tempfile.mkdtemp(prefix="claudemail-clock-"))
mail.MAILBOX_ROOT = scratch
folder = scratch / "Testmachine" / mail.REPORTS
folder.mkdir(parents=True)
files = {"reports_via": "files"}                  # the slots through Resilio; tests/postman_reports.py covers the postman


def set_zone(zone: str) -> None:
    os.environ["TZ"] = zone
    time.tzset()


failures = []


def expect(label: str, cond: bool, detail="") -> None:
    print(("PASS " if cond else "FAIL ") + label + ("" if cond else "  " + str(detail)))
    if not cond:
        failures.append(label)


set_zone("Europe/Paris")
local = datetime.datetime.now()
for minutes in [3, 2, 1]:                       # reports of the code before: local-time names, from minutes ago
    old = folder / (local - datetime.timedelta(minutes=minutes)).strftime("%y%m%d-%H%M%S-000.json")
    old.write_text(json.dumps({"n": -1}))
    os.utime(old, (time.time() - 60 * minutes,) * 2)
mail.write_report("Testmachine", {"n": 0, "updated": mail.now_iso()}, files)
expect("a report written on European time is the newest", mail.read_report("Testmachine", files)["n"] == 0)
set_zone("America/Bogota")                     # the local clock goes back 6 hours
for n in range(1, 8):
    time.sleep(0.01)
    mail.write_report("Testmachine", {"n": n, "updated": mail.now_iso()}, files)
    names = sorted(p.name for p in folder.glob("*.json"))
    expect("report " + str(n) + " after the clock went back is kept and read", mail.read_report("Testmachine", files)["n"] == n, names)
expect("five reports kept, the five slots", names == ["slot-%d.json" % i for i in range(5)], names)
expect("the leftover local-time reports are gone", all(json.loads((folder / n).read_text())["n"] >= 0 for n in names), names)
expect("the kept ones are the last five written", sorted(json.loads((folder / n).read_text())["n"] for n in names) == [3, 4, 5, 6, 7], names)
inodes = {n: (folder / n).stat().st_ino for n in names}
mail.write_report("Testmachine", {"n": 8, "updated": mail.now_iso()}, files)
expect("report 8 is read", mail.read_report("Testmachine", files)["n"] == 8)
expect("report 8 replaced the oldest slot, the one holding report 3",
       [json.loads((folder / n).read_text())["n"] for n in names].count(3) == 0, names)
expect("still exactly the five slots", sorted(p.name for p in folder.glob("*.json")) == names)

# a clock that ran ahead (Teppich, 2026-10-30: three hours): two slots written then hold a future time and must not
# stay the newest once the clock is right
ahead = time.time() + 3 * 3600
for n in ["slot-1.json", "slot-3.json"]:
    future = datetime.datetime.fromtimestamp(ahead).astimezone().isoformat(timespec="seconds")
    (folder / n).write_text(json.dumps({"n": 100, "updated": future}))
    os.utime(folder / n, (ahead, ahead))
expect("a report written while the clock ran ahead reads as newest", mail.read_report("Testmachine", files)["n"] == 100)
mail.write_report("Testmachine", {"n": 9, "updated": mail.now_iso()}, files)
expect("after one write report 9 is read, not the one from the future", mail.read_report("Testmachine", files)["n"] == 9)
expect("both future slots now hold report 9", [json.loads((folder / n).read_text())["n"] for n in ["slot-1.json", "slot-3.json"]] == [9, 9])
expect("no slot keeps a future file time", all((folder / n).stat().st_mtime < time.time() + 60 for n in names))
mail.write_report("Testmachine", {"n": 10, "updated": mail.now_iso()}, files)
expect("the next report goes into one slot only", [json.loads((folder / n).read_text())["n"] for n in names].count(10) == 1)

# a slot Resilio holds (Windows refuses the rename) is skipped for the next oldest, and nothing is lost
oldest = min((folder / n for n in names), key=lambda p: p.stat().st_mtime)
real_write, logged = mail.write_json, []
def refusing(path, data):
    if Path(path) == oldest:
        raise PermissionError(13, "held by Resilio", str(path))
    real_write(path, data)
mail.write_json, real_log, mail.log = refusing, mail.log, lambda machine, text: logged.append(text)
mail.write_report("Testmachine", {"n": 11, "updated": mail.now_iso()}, files)
expect("a held slot is skipped: report 11 is read", mail.read_report("Testmachine", files)["n"] == 11)
expect("the held slot is unchanged", json.loads(oldest.read_text())["n"] != 11)
mail.write_json = lambda path, data: (_ for _ in ()).throw(PermissionError(13, "held", str(path)))
before = {n: (folder / n).read_text() for n in names}
expect("with every slot held write_report says so", mail.write_report("Testmachine", {"n": 12, "updated": mail.now_iso()}, files) is False)
expect("... the report is left out and logged, nothing raised",
       len(logged) == 1 and "every slot refused" in logged[0] and before == {n: (folder / n).read_text() for n in names}, logged)
mail.write_json, mail.log = real_write, real_log

# the one-way-sync check: `seen` stamps come from `updated`, in the UTC stamp form of the old file names
os.environ["CLAUDE_MAILBOX_MACHINE"] = "Here"
config = {"machines": ["Here", "Peer", "Quiet"], "hostnames": {}, "stale_minutes": 25, "reports_via": "files"}
now = datetime.datetime.now().astimezone()
def iso(minutes_ago):
    return (now - datetime.timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")
mail.write_report("Here", {"updated": iso(3)}, files)
mail.write_report("Here", {"updated": iso(1)}, files)
stamps = mail.peer_stamps("Peer", config)
expect("peer_stamps lists only machines with reports", sorted(stamps) == ["Here"], stamps)
want = (now - datetime.timedelta(minutes=1)).astimezone(datetime.timezone.utc)
expect("the stamp is the newest report's `updated` in UTC", stamps.get("Here") == want.strftime("%y%m%d-%H%M%S-000Z"), stamps)
expect("the old code's report_age reads the stamp", 50 < mail.report_age(stamps["Here"]) < 80, stamps)
def peer(seen, minutes_ago=0):
    for slot in (scratch / "Peer" / mail.REPORTS).glob("*.json") if (scratch / "Peer" / mail.REPORTS).is_dir() else []:
        slot.unlink()
    mail.write_report("Peer", {"updated": iso(minutes_ago), "seen": {"Here": seen}}, files)
    return mail.dispatcher_warning("Peer", config)
fresh = mail.peer_stamps("Peer", config)["Here"]
expect("no warning when the peer saw this machine's newest report", peer(fresh) == "", peer(fresh))
old_stamp = (now - datetime.timedelta(minutes=60)).astimezone(datetime.timezone.utc).strftime("%y%m%d-%H%M%S-000Z")
expect("a warning when the peer's newest view of this machine is an hour older than its report",
       "has not seen this machine's reports since " + old_stamp in peer(old_stamp), peer(old_stamp))
older = (now - datetime.timedelta(minutes=40)).astimezone(datetime.timezone.utc).strftime("%y%m%d-%H%M%S-000Z")
expect("measured against the peer's `updated`: a view 40 minutes old in a report written 20 minutes ago is fine",
       peer(older, minutes_ago=20) == "", peer(older, minutes_ago=20))
expect("a slot name from a peer still on the code before is not judged", peer("slot-3") == "", peer("slot-3"))
gone = scratch / "gone.json"
bare = scratch / "no-updated.json"
bare.write_text("{}")
os.utime(bare, (0, 0))
expect("report_stamp falls back to the file time without `updated`", mail.report_stamp(bare, {}) == "700101-000000-000Z",
       mail.report_stamp(bare, {}))
expect("report_stamp of a file gone since it was read is empty, not an exception", mail.report_stamp(gone, {"updated": "x"}) == "")
expect("report_time has milliseconds and its offset", len(mail.report_time()) == 29 and mail.age(mail.report_time()) < 5, mail.report_time())
expect("never seen at all still warns", "has not seen this machine's reports since ever" in peer(""), peer(""))
shutil.rmtree(scratch)
print(str(len(failures)) + " failed" if failures else "all passed")
sys.exit(1 if failures else 0)
