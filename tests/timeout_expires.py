"""A delivery child cut off at DELIVER_SECONDS before it wrote the message's status record must not end the dispatcher.

    python3 tests/timeout_expires.py <mail.py to test> <scratch folder>

Pektolith, 2026-11-09: on a thrashing Mac the first delivery of a message ran past DELIVER_SECONDS, the child was killed
before it had written the record with `arrived` and `expires`, and deliver_isolated handed to_user an empty record, so
the dispatcher died on KeyError 'expires' and died again on every restart while the message waited. This test runs
deliver_isolated in a sandbox home with a delivery that times out, and checks that it returns, that the message waits
for the user with an expiry an hour after arrival, and that the one notification names that expiry. Exit 0: passed.
"""
import datetime, json, os, shutil, subprocess, sys
from pathlib import Path

if sys.argv[1] != "child":                                 # the parent: build the sandbox, run the check in a child
    assert len(sys.argv) == 3, sys.argv
    tested, scratch = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve() / "timeout-expires"
    shutil.rmtree(scratch, ignore_errors=True)
    svc = scratch / "ResilioSync" / "Services" / "ClaudeMail"
    home = scratch / "home"
    (svc / "config").mkdir(parents=True)
    (home / ".claude" / "claudemail" / "logs").mkdir(parents=True)
    shutil.copy(tested, svc / "mail.py")
    shutil.copy(tested.parent / "signed_mail.py", svc / "signed_mail.py")
    shutil.copy(tested.parent / "config" / "defaults.json", svc / "config" / "defaults.json")
    run = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()), "child", str(svc)],
                         env=dict(os.environ, HOME=str(home), CLAUDE_MAILBOX_MACHINE="Pektolith"),
                         capture_output=True, text=True, timeout=120)
    sys.stdout.write(run.stdout)
    sys.stderr.write(run.stderr)
    print("PASS" if run.returncode == 0 else "FAIL (exit " + str(run.returncode) + ")")
    sys.exit(run.returncode)

assert sys.argv[1] == "child", sys.argv
svc = Path(sys.argv[2])
sys.path.insert(0, str(svc))
import mail                                                        # the copy in the sandbox, under the sandbox home

config = mail.load_config()
desk_dir = svc / "mail" / "Pektolith" / "desks" / "Home"
desk_dir.mkdir(parents=True)
sent = datetime.datetime.now().astimezone().replace(microsecond=0)
ident = "261109-000000Z-Pulvermaar-0000abcd"
header = {"from": "Pulvermaar:Home", "to": "Pektolith:Home", "subject": "Timeout test", "sent": sent.isoformat(), "id": ident}
message = desk_dir / (ident + ".md")
message.write_text(mail.format_message(header, "Body."), encoding="utf-8")
assert mail.read_status(message) == {}, "the sandbox message must start without a status record"

real_run = subprocess.run


def timing_out(argv, *args, **kwargs):
    if "deliver-one" in argv:                              # the delivery child: killed at the limit, before it wrote anything
        raise subprocess.TimeoutExpired(argv, mail.DELIVER_SECONDS)
    return real_run(argv, *args, **kwargs)


notified = []
mail.subprocess.run = timing_out
mail.notify = lambda machine, title, body: notified.append(body)

mail.deliver_isolated("Pektolith", "Home", message, {}, {}, "test-version", config)

status = mail.read_status(message)
print("status after the timeout:", json.dumps(status, sort_keys=True))
assert message.is_file(), "the message must still wait in its desk"
assert status["state"] == mail.WAITING_FOR_USER, status
assert "did not end within" in status["reason"], status
arrived = datetime.datetime.fromisoformat(status["arrived"])
expires = datetime.datetime.fromisoformat(status["expires"])
assert expires - arrived == datetime.timedelta(minutes=float(config["expires_minutes"])), (arrived, expires)
assert len(notified) == 1 and status["expires"] in notified[0], notified
print("ok: the dispatcher returned, the message waits for the user, expires", status["expires"])
