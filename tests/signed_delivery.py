"""A signed message goes through the dispatcher's real deliver() and must reach the session framed as verified.

Run under the SignedCommands venv, with the mail.py to test and a scratch folder outside the synced tree:
    ~/venvs/SignedCommands/bin/python -B tests/signed_delivery.py <mail.py> <scratch>

Found on 2026-11-11: v4's deliver() built the delivery text, and so ran the signature check, with the message's read/
path before moving the file there, so every signed mail arrived as SIGNATURE NOT VERIFIABLE HERE (FileNotFoundError).
The Signed Commands suite calls signed_block() with a file that exists and could not see it. Everything here lives in
<scratch>: HOME points there (so mail.py's logs and the trust store are the scratch ones), a throwaway software key is
enrolled there, and the session is a stand-in whose post is captured. The scratch folder is deleted on success.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

sys.dont_write_bytecode = True
TESTED = Path(sys.argv[1]).resolve()
SCRATCH = Path(sys.argv[2]).resolve()
assert not SCRATCH.exists(), "scratch folder exists already: " + str(SCRATCH)
assert "ResilioSync" not in SCRATCH.parts, "scratch folder must not be in the synced tree"
REAL_VENV = Path.home() / "venvs" / "SignedCommands"
TOOL = REAL_VENV / "signed_command.py"
MACHINE, DESK, SESSION = "Altiplano", "TestDesk", "local_signedtest"

home = SCRATCH / "home"
state = SCRATCH / "signed-state"
svc = SCRATCH / "Services" / "ClaudeMail"
(home / "venvs").mkdir(parents=True)
(home / "venvs" / "SignedCommands").symlink_to(REAL_VENV)          # the real verifier, reading the scratch trust store
(svc / "config").mkdir(parents=True)
shutil.copy(TESTED, svc / "mail.py")
shutil.copy(TESTED.parent / "signed_mail.py", svc / "signed_mail.py")
shutil.copy(TESTED.parent / "config" / "defaults.json", svc / "config" / "defaults.json")
os.environ["HOME"] = str(home)
os.environ["SIGNED_COMMANDS_HOME"] = str(state)


def tool(arguments):
    done = subprocess.run([sys.executable, str(TOOL)] + arguments, capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
    return done.stdout


private = ec.generate_private_key(ec.SECP256R1())
pem = SCRATCH / "test.pem"
pem.write_bytes(private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption()))
import base64  # noqa: E402
point = private.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
(SCRATCH / "test.pub.json").write_text(json.dumps({"public_key": base64.b64encode(point).decode("ascii")}))
tool(["enrol", "--machine", MACHINE, "--label", "test", "--pubkey-file", str(SCRATCH / "test.pub.json"), "--yes"])
text = "Delete the throwaway folder and nothing else."
(SCRATCH / "text.txt").write_text(text, encoding="utf-8")
envelope = SCRATCH / "test.envelope.json"
tool(["sign", "--test-key", str(pem), "--to", MACHINE + ":" + DESK, "--text-file", str(SCRATCH / "text.txt"),
      "--out", str(envelope)])

spec = importlib.util.spec_from_file_location("mail_under_test", svc / "mail.py")
mail = importlib.util.module_from_spec(spec)
sys.path.insert(0, str(svc))
spec.loader.exec_module(mail)
import signed_mail  # noqa: E402  (the scratch copy, beside the tested mail.py)
assert Path(signed_mail.__file__).resolve() == (svc / "signed_mail.py").resolve()

posted = []
mail.post_to_session = lambda entry, payload: posted.append(payload)
mail.inbox_payload = lambda entry, text: text.encode("utf-8")

desk_dir = svc / "mail" / MACHINE / "desks" / DESK
desk_dir.mkdir(parents=True)
name = "261111-000000Z-Sandbank-test0001"
header = {"from": "Sandbank:Home", "to": MACHINE + ":" + DESK, "for_session": SESSION, "subject": "Signed test",
          "sent": "2026-11-11T12:00:00-06:00", "id": name, "conversation": name, "protocol": "4"}
message = desk_dir / (name + ".md")
message.write_text(mail.format_message(header, signed_mail.attach(envelope)), encoding="utf-8")
config = json.loads((svc / "config" / "defaults.json").read_text())
entry = {"sessionId": SESSION, "startedAt": 1, "status": "idle"}
mail.deliver(MACHINE, DESK, message, config, {SESSION: entry}, {})

results = []


def expect(label, passed, detail=""):
    results.append(passed)
    print(("ok    " if passed else "FAIL  ") + label + ("" if passed else "  [" + detail + "]"))


delivered = posted[0].decode("utf-8") if posted else ""
# The message block only: the delivery's general rules above it name "SIGNED BY THE USER, VERIFIED" for every message.
block = delivered[delivered.find("--- message "):]
claimed = desk_dir / "read" / message.name
expect("the message was posted to the session once", len(posted) == 1, str(len(posted)))
expect("the message moved to read/", claimed.is_file() and not message.exists())
expect("the block is framed as verified by the local verifier",
       "SIGNED BY THE USER, VERIFIED by this machine's local verifier" in block and "NOT VERIFIABLE" not in block,
       block[:600])
expect("the block carries the exact signed text between its boundary lines", "=== signed text " in block
       and "\n" + text + "\n=== end of signed text " in block)
expect("the verify command shown names the read/ path", "verify " + signed_mail.shown(claimed) in block)
expect("delivery did not consume the nonce", not any((state / "used-nonces").iterdir()) if (state / "used-nonces").is_dir() else True)
done = subprocess.run([sys.executable, str(TOOL), "verify", str(claimed), "--desk", DESK], capture_output=True, text=True)
expect("the session's own verify of the delivered file says VALID", done.returncode == 0 and done.stdout.startswith("VALID"),
       done.stdout[:200])
print(str(sum(results)) + " of " + str(len(results)) + " passed")
if all(results):
    shutil.rmtree(SCRATCH)
sys.exit(0 if all(results) else 1)
