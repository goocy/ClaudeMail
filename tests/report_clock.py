"""The local clock goes back: reports written after it must survive and be the newest (2026-10-27).

A time zone change (Pulvermaar, CET to UTC-5 on 2026-10-26) or the DST fall-back once made every new report sort
as the oldest by its local-time name, so write_report deleted it at once. This switches the process from European time
to UTC-5 between writes, with leftover local-time reports from before, and checks what is kept and what is read.

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
for minutes in [3, 2, 1]:                       # what Pulvermaar had: local-time names from before the switch
    old = (local - datetime.timedelta(minutes=minutes)).strftime("%y%m%d-%H%M%S-000.json")
    (folder / old).write_text(json.dumps({"n": -1}))
mail.write_report("Testmachine", {"n": 0})
expect("a report written on European time is the newest", mail.read_report("Testmachine")["n"] == 0)
set_zone("America/Bogota")                     # the local clock goes back 6 hours
for n in range(1, 8):
    time.sleep(0.01)
    mail.write_report("Testmachine", {"n": n})
    names = sorted(p.name for p in folder.glob("*.json"))
    expect("report " + str(n) + " after the clock went back is kept and read", mail.read_report("Testmachine")["n"] == n, names)
expect("five reports kept", len(names) == 5, names)
expect("the leftover local-time reports are gone", all(json.loads((folder / n).read_text())["n"] >= 0 for n in names), names)
expect("the kept ones are the last five written", sorted(json.loads((folder / n).read_text())["n"] for n in names) == [3, 4, 5, 6, 7], names)
shutil.rmtree(scratch)
print(str(len(failures)) + " failed" if failures else "all passed")
sys.exit(1 if failures else 0)
