"""Windows probe for Deploy B (2026-10-30): run under pythonw.exe, as the dispatcher does, it starts the candidate's
`deliver-one` child the way deliver_isolated does (piped stdin and stderr, CREATE_NO_WINDOW) with a job for a message
that does not exist, and writes what came back to a file, since pythonw has no console. Expected: exit code 1 and a
traceback whose frames include ", in deliver", read without decoding errors.

    pythonw -B "_design\\261030 Deploy B\\tests\\windows_child_probe.py" <output file>
"""
import json
import subprocess
import sys
from pathlib import Path

CANDIDATE = Path(__file__).resolve().parent.parent / "mail.py"
out = Path(sys.argv[1])
config = json.loads((CANDIDATE.parent / "config" / "defaults.json").read_text(encoding="utf-8"))
job = {"machine": "Probe", "desk": "Probe", "message": str(CANDIDATE.parent / "no such desk" / "261030-000000-Probe.md"),
       "running": {}, "desks": {}, "config": config, "desktop": {"local_x": {"sessionId": "local_x", "title": "Grüße ü"}}}
run = subprocess.run([sys.executable, "-B", str(CANDIDATE), "deliver-one"], input=json.dumps(job, ensure_ascii=True),
                     capture_output=True, encoding="utf-8", errors="replace", timeout=60,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
out.write_text(json.dumps({"python": sys.executable, "returncode": run.returncode, "has_deliver_frame": ", in deliver\n" in
                           run.stderr.replace("\r\n", "\n"), "stderr_tail": run.stderr[-1500:]}, indent=1), encoding="utf-8")
