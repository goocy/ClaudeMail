"""reports_via: a report published through the postman's spool is what readers then take, and with 'files' the slots
work as before and the postman is never touched.

Runs on temp folders only: a temp spool with its own whoami.json (as the postman writes it when it starts) and a temp
mail/ folder. Nothing is sent, no session or dispatcher is started.

    python3 -B -m unittest tests/postman_reports.py      (from the ClaudeMail folder; tests the mail.py beside tests/)
"""
import datetime
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
SCRIPT = Path(__file__).resolve().parent.parent / "mail.py"
spec = importlib.util.spec_from_file_location("mail_under_test", SCRIPT)
mail = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mail)
sys.path.insert(0, str(mail.POSTMAN_DIR))
import postman  # noqa: E402  the one mail.report_spool imports

HERE, PEER, QUIET = "Pektolith", "Mistral", "Teppich"


def config(via: str) -> dict:
    return {"machines": [HERE, PEER, QUIET], "hostnames": {}, "stale_minutes": 25, "reports_via": via}


def iso(minutes_ago: float) -> str:
    return (datetime.datetime.now().astimezone() - datetime.timedelta(minutes=minutes_ago)).isoformat(timespec="milliseconds")


def report(machine: str, minutes_ago: float = 0, **extra) -> dict:
    return dict({"sessions": [{"session": "local_1", "desk": "Home", "folder": "/x", "title": "t", "state": "idle",
                               "last_active": iso(minutes_ago), "doing": ""}],
                 "desks": {"Home": "~"}, "undelivered": {"count": 0, "unseen": 0}, "settings_problem": "",
                 "new_code_does_not_compile": False, "machine": machine, "pid": 1, "version": "v", "protocol": mail.PROTOCOL,
                 "seen": {}, "updated": iso(minutes_ago)}, **extra)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="claudemail-postman-"))
        self.saved = (mail.MAILBOX_ROOT, postman.spool_root, mail.report_spool, os.environ.get("CLAUDE_MAILBOX_MACHINE"))
        mail.MAILBOX_ROOT = self.tmp / "mail"
        os.environ["CLAUDE_MAILBOX_MACHINE"] = HERE

    def tearDown(self):
        mail.MAILBOX_ROOT, postman.spool_root, mail.report_spool, machine = self.saved
        if machine is None:
            del os.environ["CLAUDE_MAILBOX_MACHINE"]
        else:
            os.environ["CLAUDE_MAILBOX_MACHINE"] = machine
        shutil.rmtree(self.tmp)


class PostmanBranch(Base):
    def setUp(self):
        super().setUp()
        root = str(self.tmp / "spool")
        postman.spool_root = lambda: root
        mail.report_spool.cache_clear()                   # each test has its own spool
        self.spool = postman.Spool(root, postman.load_config())
        postman.write_json(self.spool.path("whoami.json"), {"machine": HERE, "protocol": postman.PROTOCOL, "started": postman.stamp()})
        self.cfg = config("postman")

    def learn(self, origin: str, service: str, seq: int, body: dict) -> None:
        """A peer's report as the postman stores it when it arrives (Postman.learn)."""
        os.makedirs(self.spool.path("state", origin), exist_ok=True)
        postman.write_json(self.spool.path("state", origin, service + ".json"),
                           {"origin": origin, "service": service, "seq": seq, "written": postman.stamp(), "body": body})

    def test_published_report_is_what_readers_take(self):
        first, second = report(HERE, n=1), report(HERE, n=2)
        self.assertIs(mail.write_report(HERE, first, self.cfg), True)
        self.assertEqual(mail.read_report(HERE, self.cfg), first)
        mail.write_report(HERE, second, self.cfg)
        self.assertEqual(mail.read_report(HERE, self.cfg), second)
        [state] = self.spool.states("ClaudeMail")
        self.assertEqual((state["origin"], state["seq"], state["body"]), (HERE, 2, second))
        self.assertFalse(mail.MAILBOX_ROOT.exists(), "no slot file is written")

    def test_crash_report_goes_through_the_spool(self):
        mail.write_report(HERE, report(HERE), self.cfg)
        newest = mail.read_report(HERE, self.cfg)
        mail.write_report(HERE, dict(newest, crash={"signature": "KeyError at mail.py:1 in f"}, updated=mail.report_time()), self.cfg)
        self.assertEqual(mail.read_report(HERE, self.cfg)["crash"]["signature"], "KeyError at mail.py:1 in f")

    def test_a_report_under_another_machine_name_is_refused(self):
        with self.assertRaises(AssertionError):
            mail.write_report(PEER, report(PEER), self.cfg)
        self.assertEqual(self.spool.states(), [])

    def test_no_postman_running_fails(self):
        os.remove(self.spool.path("whoami.json"))
        with self.assertRaises(FileNotFoundError):
            mail.write_report(HERE, report(HERE), self.cfg)

    def test_peer_report_from_the_spool(self):
        theirs = report(PEER, seen={HERE: mail.report_stamp(None, {"updated": iso(1)})})
        self.learn(PEER, "ClaudeMail", 7, theirs)
        self.learn(PEER, "LeaseResources", 9, {"other": "service"})
        self.assertEqual(mail.read_report(PEER, self.cfg), theirs)
        self.assertEqual(mail.report_names(PEER, self.cfg), [None])
        self.assertEqual(mail.read_report(QUIET, self.cfg), {})
        view = mail.machine_view(PEER, self.cfg)
        self.assertEqual((view["sessions"], view["desks"], view["warning"]), (theirs["sessions"], theirs["desks"], ""))
        self.assertEqual(mail.dispatcher_warning(QUIET, self.cfg), QUIET + "'s dispatcher has never reported; mail waits until it runs")

    def test_seen_from_the_states(self):
        theirs = report(PEER, minutes_ago=2)
        self.learn(PEER, "ClaudeMail", 1, theirs)
        mail.write_report(HERE, report(HERE), self.cfg)
        when = datetime.datetime.fromisoformat(theirs["updated"]).astimezone(datetime.timezone.utc)
        stamp = when.strftime("%y%m%d-%H%M%S-") + "%03dZ" % (when.microsecond // 1000)
        self.assertEqual(mail.peer_stamps(HERE, self.cfg), {PEER: stamp})
        self.assertEqual(list(mail.peer_stamps(PEER, self.cfg)), [HERE])

    def test_freshness_from_updated(self):
        self.learn(PEER, "ClaudeMail", 1, report(PEER, minutes_ago=40))
        self.assertIn("dispatcher is silent since", mail.dispatcher_warning(PEER, self.cfg))
        old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=60)).strftime("%y%m%d-%H%M%S-000Z")
        self.learn(PEER, "ClaudeMail", 2, report(PEER, seen={HERE: old}))
        self.assertIn("has not seen this machine's reports since " + old, mail.dispatcher_warning(PEER, self.cfg))
        self.learn(PEER, "ClaudeMail", 3, report(PEER, seen={HERE: mail.report_stamp(None, {"updated": iso(1)})}))
        self.assertEqual(mail.dispatcher_warning(PEER, self.cfg), "")

    def test_a_peer_report_without_updated_fails_loudly(self):
        self.learn(PEER, "ClaudeMail", 1, {"sessions": []})
        with self.assertRaises(AssertionError):
            mail.peer_stamps(HERE, self.cfg)


class FilesBranch(Base):
    def setUp(self):
        super().setUp()
        def untouched():
            raise AssertionError("the postman was used while reports_via is 'files'")
        mail.report_spool = untouched
        self.cfg = config("files")

    def test_slots_written_and_read(self):
        mine = report(HERE)
        self.assertIs(mail.write_report(HERE, mine, self.cfg), True)
        folder = mail.MAILBOX_ROOT / HERE / mail.REPORTS
        self.assertEqual([p.name for p in folder.iterdir()], ["slot-0.json"])
        self.assertEqual(json.loads((folder / "slot-0.json").read_text(encoding="utf-8")), mine)
        self.assertEqual(mail.read_report(HERE, self.cfg), mine)
        self.assertEqual(mail.report_names(HERE, self.cfg), [folder / "slot-0.json"])

    def test_peer_slot_read_and_judged(self):
        folder = mail.MAILBOX_ROOT / PEER / mail.REPORTS
        folder.mkdir(parents=True)
        (folder / "slot-3.json").write_text(json.dumps(report(PEER, minutes_ago=40)), encoding="utf-8")
        self.assertEqual(mail.read_report(PEER, self.cfg)["machine"], PEER)
        self.assertIn("dispatcher is silent since", mail.dispatcher_warning(PEER, self.cfg))
        self.assertEqual(list(mail.peer_stamps(HERE, self.cfg)), [PEER])


class Setting(unittest.TestCase):
    def test_only_two_values_and_no_default(self):
        self.assertEqual(mail.reports_via(config("files")), "files")
        self.assertEqual(mail.reports_via(config("postman")), "postman")
        with self.assertRaises(AssertionError):
            mail.reports_via(config("both"))
        with self.assertRaises(KeyError):
            mail.reports_via({"machines": []})

    def test_shipped_value_is_files(self):
        shipped = json.loads((mail.CONFIG_DIR / "defaults.json").read_text(encoding="utf-8"))
        self.assertEqual(shipped["reports_via"], "files")


if __name__ == "__main__":
    unittest.main()
