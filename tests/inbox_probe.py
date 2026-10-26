"""Checks, on one machine, the one step of ClaudeMail v4 that the Mac sandbox cannot: writing a message into a running
session's inbox exactly the way the v4 dispatcher does (on Windows: the named pipe with the published peer key).

From a terminal on that machine (python3 on a Mac):
    python "%USERPROFILE%\\ResilioSync\\Services\\ClaudeMail\\tests\\inbox_probe.py"
lists the Claude sessions running there; then
    python "...\\inbox_probe.py" <first 8 characters of one id>
sends that session the probe. Success: it receives "ClaudeMail inbox probe ..." as a message from another session
within a few seconds (an idle session starts a turn with it).
"""
import importlib.util
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("candidate", Path(__file__).resolve().parent.parent / "mail.py")
candidate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(candidate)

running = candidate.running_sessions()
entries = {entry["pid"]: entry for entry in running.values()}               # each process listed once
if len(sys.argv) < 2:
    for entry in entries.values():
        print(entry.get("hostSessionId", "") or entry["sessionId"], entry["status"], entry.get("name", ""))
    sys.exit("\nGive the first 8 characters of one id above to send it the probe.")
wanted = candidate.bare_id(sys.argv[1])
chosen = [e for e in entries.values() if candidate.bare_id(e.get("hostSessionId", "")).startswith(wanted)
          or e["sessionId"].startswith(wanted)]
assert len(wanted) >= 8 and len(chosen) == 1, "'" + sys.argv[1] + "' names " + str(len(chosen)) + " running sessions"
candidate.post_to_session(chosen[0], candidate.inbox_payload(chosen[0], "ClaudeMail inbox probe: written into this"
                          " session's inbox the way the v4 dispatcher does it, from " + candidate.socket.gethostname()
                          + ". Nothing to do; just note its arrival in one line."))
print("posted to " + chosen[0]["messagingSocketPath"])
