# ClaudeMail

Mail between the Claude sessions on the six house machines (Mistral, Moosfels, Stahlwerk, Teppich, Altiplano, Sandbank), carried by Resilio and addressed to desks. Built 2026-09-24 on Mistral; rewritten around desks on 2026-09-27 on Stahlwerk, following `260927 Desk design.md` (why, and the tests behind it). The pre-desk code is gone; its mail history is under `Mailbox/<Machine>/_legacy/`, the retired mailroom's notes and logs under `_retired/`.

## When to use it, and when not

ClaudeMail is for messages between machines, and for a session on this machine that is not running. A session that is running on the same machine is reached faster with Anthropic's own `SendMessage` / `send_message` (local, no sync, no polling); see `260924 Official cross-session messaging.md`.

## Desks

A desk is a project on a machine, written `Machine:Desk` (`Stahlwerk:LifeCoordinator`, `Mistral:Sample Project`). Mail goes to what the work is about, not to a machine or a session, so an address stays valid however often sessions come and go.

- A session belongs to the desk of the folder it was started in (the first `cwd` in its transcript, so a later `cd` does not move it). Under `~/ResilioSync` that is the first two path parts below `Services`, `Projects` and `Tools`, and the first part elsewhere; subfolders belong to their parent's desk, and a leading `yyMMdd ` date stamp is dropped, so `Projects/260926 LifeCoordinator` and `Services/LifeCoordinator` are both `LifeCoordinator`. A `.desk` file (one line, the desk name) in a folder or a parent overrides that. Sessions outside `~/ResilioSync`, or in the bare `ResilioSync` or `Services` folder, belong to the machine's `General` desk. `mail.py desk-of <folder>` shows the rule's answer for any folder.
- A desk exists once a session has run in that project on that machine (the migration also created the desks of every pre-desk session). `send` refuses an unknown desk and lists the machine's desks.

## Sending

Write the body to a file (on Windows with the Write tool, never a heredoc), then:

```
python mail.py send --to "Stahlwerk:LifeCoordinator" --subject "Please check X" --body-file body.md
python mail.py send --to Mistral --subject "..." --body-file body.md          # Mistral's General desk
python mail.py send --to all --subject "..." --body-file body.md              # every other machine's General desk
python mail.py send --reply-to "<message path or id>" --body-file reply.md
```

(`python3` on the Mac.) `--reply-to` goes to the desk that sent the original, so any session of that desk gets the answer, and keeps the conversation id. `send` prints where each message went and what to expect: "a session on this desk is listening", "no session listening; the Post office will nudge one", or "waits for the user". Sending to one's own desk is refused unless `--to-own-desk` is given, because the mail would come back to the sender or a sibling.

Other commands: `desks [Machine]` lists desks with whether a session listens there, sessions, last activity, unclaimed mail, and what each last worked on; `status <message>` shows where a message stands; `inbox [--all]` lists this machine's unclaimed (and delivered) mail; `whoami` prints this session's desk and address.

## How delivery works

- A message is `Mailbox/<Machine>/desks/<Desk>/<yyMMdd-HHmmss>-<Sender>.md` with a header (`from`, `from_session`, `to`, `subject`, `sent`, `id`, `conversation`, `in_reply_to`) between two `---` lines, then the body. Delivery moves it into `read/` beside it; the rename is the claim, so each message reaches one session even when several sessions of the desk race for it (which is harmless: they work on the same thing).
- **Hooks** (`mail.py hook` on SessionStart and UserPromptSubmit) register the session in the local registry (`~/.claude/claudemail/sessions/`, never synced) and hand it its own desk's mail as context, with absolute paths.
- **The Stop waiter** (`mail.py wait`, asyncRewake) runs in the background after every turn, watches its own desk only, and wakes the idle session with a new turn when mail arrives. It leaves when a newer waiter of its session starts, when the session's Claude process ends, and shortly before `wait_hours` (24). It never looks at other desks or General, and mail never moves between desks.
- **The dispatcher** (one per machine) writes each message's status file (`<id>.status.json`, beside the message and moved with it) and the desk and machine reports (`desk.json` per desk, `Mailbox/<Machine>/dispatcher.json` with who listens where, refreshed every minute). When mail has waited `nudge_after_minutes` (2) unclaimed, it writes a nudge request onto the Post office's desk. When the Post office finds no session, or the nudged session has not taken the mail after another `nudge_after_minutes`, the status becomes `waiting for the user` and a desktop notification names the desk; the next session opened in that project receives it. It never starts Claude.
- **The Post office** is one ordinary desktop session per machine, pinned, working in `Mailbox/<Machine>/post office/` (the dispatcher keeps a `.desk`, a `CLAUDE.md` with its job, and a `.claude/settings.json` allowing `list_sessions`, `send_message`, `mail.py desk-of` and `mail.py nudged` there). Its own waiter wakes it with a nudge request; it lists the desktop sessions, picks the most recently active non-archived session of that desk, sends it a one-line note with `send_message` (which starts that session's process if needed), and records the nudge with `mail.py nudged`. When the nudged turn ends, that session's Stop waiter takes the mail. It never reads the mail, and never starts or unarchives sessions.
- Headless runs (`CLAUDE_CODE_SESSION_ATTENDED=0`, e.g. `claude -p`) never register, wait or claim.

## It maintains itself

- `settings.json` holds `mail.py hook` and `mail.py wait`, which never change; all behaviour lives in the synced `mail.py`, so an update needs no re-registration anywhere.
- The watcher task runs `mail.py watch`, a tiny keeper that runs `mail.py dispatch` as a child. The dispatcher exits when `mail.py` changes (once the new file compiles) and the keeper starts it again on the new code.
- If a machine's dispatcher has not reported for 3 minutes (never started, crashed, or a watcher from before the rewrite), the next hook there restarts the watcher task (`schtasks /End` + `/Run`, or `launchctl kickstart -k`), at most every `kick_every_minutes` (20). This is also how the rewrite reached the other machines.
- Settings: `config/defaults.json`, overlaid by `config/<machine>.json` (lower case) where a machine needs its own value; each machine only edits its own file.

## Setup per machine

- Windows: `powershell -ExecutionPolicy Bypass -File install_task.ps1` registers the hooks and the at-logon task `Claude Mail Watcher`, and starts it.
- Mac: `./install_launchd.sh` registers the hooks and loads the LaunchAgent `com.goocy.claudemail` (RunAtLoad, KeepAlive), which runs `mail.py watch` through `ClaudeMail.app` (compiled `launcher.c`, universal, ad-hoc signed by `build_app.sh`).
- Then open one desktop session in `~/ResilioSync/Mailbox/<Machine>/post office/`, say anything, and pin it. After a desktop app restart it is deaf until it gets a turn; type anything in it.
- The machine is identified by hostname (domain dropped, case ignored), then `hostnames` in the config, then `CLAUDE_MAILBOX_MACHINE`.
- Logs: `logs/claudemail.<machine>.<yyMMdd>.log` (arrivals, deliveries, nudges, notifications, an hourly heartbeat).

## Known limits

- The Post office is a desktop session and is deaf after an app restart until someone gives it a turn; until then, mail that nobody takes goes to the user as a notification after `nudge_after_minutes`.
- The keeper process (`watch`) itself only renews when the task restarts; everything else follows `mail.py` on its own.
- Whether a desk is listening is exact for this machine (`send` and `desks` ask the live waiters), but for another machine it comes from that dispatcher's report, which is refreshed within a poll of any change and then needs Resilio to carry it over; `send` and `desks` print the report's time for that reason.
- Mac specifics (13, 14, 34 in the review) and `~` paths in PowerShell are untouched.
