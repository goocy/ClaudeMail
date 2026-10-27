# ClaudeMail

Mail between the Claude sessions on the seven house machines (Mistral, Moosfels, Stahlwerk, Teppich, Altiplano, Sandbank, Pulvermaar), carried by Resilio. Version 4, rewritten on Pulvermaar on 2026-10-26: mail goes to a session the sender picked, or to a new session, and never to whichever session happens to be listening. The Post office, the Steward and the per-session Stop waiters are gone. Earlier versions: `_retired/README.v3.md` (desks, Post office, Steward, 2026-09-27 to 2026-10-26), `_retired/Running-on.pre-desk.md`.

## Why version 4

From 2026-10-12 to 2026-10-26, 55 messages between sessions were sent (besides 56 lease notices, which all arrived). About 16 of them ended in a session that had nothing to do with them, because a desk with several sessions handed the mail to whichever one listened first, or the Post office woke the most recently active one; and no message ever started a session for a new topic. The two pinned sessions per machine, Post office and Steward, were deaf most of the time: a desktop app restart or one failed turn ended their listeners, and on 2026-10-26 neither listened on five machines. The user asked on 2026-10-20 for the sender to choose ("Could we have a two-stage process where the sender has to inquire what sessions exist on the target computer, and decides which one to send it to (or to a new session, if that can be accomplished on the recipient computer)"), and on 2026-10-26: "any style of communication is valid, as long as it results in visible threads in Claude Code Desktop, and work is regulated by the lease manager." The evidence, with paths: `_design/261026 Failures since 261012.md`.

## Sending: two stages

1. `python3 mail.py sessions Machine[:Desk]` (`python` on Windows) lists the desktop sessions there: id, running (`idle`, `busy`) or `closed`, last activity, title, and what its last turn did. For another machine it reads that machine's newest report, refreshed every minute; the time it was written is printed, and a machine that is asleep or off still gets mail, delivered when it is back.
2. `python3 mail.py send --to Machine:Desk --for-session <id> --subject "..." --body-file body.md` for one of them (eight characters of the id are enough), or `--new --name "..."` for a new session in that project. A Claude session that gives neither is refused and shown the list. The sender proposes the new session's name with `--name` (required from a Claude session, refused for a session that exists): it becomes the session's title in the receiving machine's sidebar from the start, and the app keeps it after the first turn instead of titling the session itself (tested on Pulvermaar on 2026-10-27). A program may leave it out, and the app then titles the session itself, as before. `--to Machine` alone is that machine's Home desk (its home folder); `--to all --new` reaches every other machine's Home desk. To answer a message: `send --reply-to <message path> --body-file reply.md`, which goes to the session that sent it.

A desk is a project on a machine, derived from the session's folder: under `~/ResilioSync` the first part below `Services`, `Projects` or `Tools` (the first part elsewhere), without a leading `yyMMdd ` date stamp; a `.desk` file in a folder or a parent overrides that; everything outside the projects is `Home`. The old `General` desk is `Home` now. A desk is where its mail lies on disk (`mail/<Machine>/desks/<Desk>/`) and the folder a new session for it starts in, which is the folder of the newest desktop session there.

A program that is not a Claude session (LeaseResources, a script) and names no session gets a new session, since it cannot choose. LeaseResources names the session that asked for a lease, so its notices reach that session.

## Receiving: the dispatcher

One dispatcher per machine (`mail.py watch`, started at logon) looks at every message on that machine's desks every few seconds:

- **The session runs** (idle or busy): the message is written into the session's inbox, the local socket (a named pipe on Windows) through which Claude Code takes messages from other sessions. An idle session starts a turn with it; a busy one reads it between tool calls. Claude Code lists running sessions and their inboxes itself in `~/.claude/sessions/<pid>.json`, and publishes beside each a peer key, which the dispatcher sends first (Windows requires it; on the Mac it is optional, and it is the way that was tested live).
- **The session is closed** (no process, e.g. after an app restart) **or archived**: the dispatcher opens it with the desktop app's own import link, `claude://resume?session=<its CLI id>`, which starts its process (and restores an archived session to the sidebar), and delivers as above. The app's `claude://code/continue` link only shows a session and starts nothing, so it is not used (live test, 2026-10-26).
- **A new session**, or the named one is not on this machine: the app's bundled command-line Claude creates a session in the desk's folder by running the local command `/cost` (`claude -p --session-id <id> --name=<proposed name> /cost`: no model call, so no sign-in of its own; `--name` writes the name into the transcript as its title), the app imports it into its sidebar with `claude://resume?session=<id>` and starts its process, and the message follows as above. From then on the session runs on the app's own sign-in. A message meant for a session that is not on this machine says so.
- Each session the dispatcher opens brings the app to the front. Mail it cannot deliver within `open_minutes` (3) waits for the user, with one notification, and is tried again every `retry_minutes` (60); a session that starts running in the meantime gets it at once.

The delivered text tells the session that the message comes from a colleague Claude, not from the user, how to answer, and that long jobs go through LeaseResources. Signed commands are framed as before (`signed_mail.py`). Delivered mail moves to the desk's `read/`, with a `<id>.delivery.json` beside it (state, times, the session). `mail.py status <message>` shows it; `mail.py inbox` lists what waits on this machine.

Each dispatcher writes `mail/<Machine>/reports/<stamp>.json` every minute (not on every change, because sessions turn busy and idle all day): the sessions (as `sessions` prints them), the folder of each desk, and what waits undelivered. Only the newest five are kept. Its log is machine-local: `~/.claude/claudemail/logs/claudemail.<machine>.<yyMMdd>.log`; a dispatcher that fails leaves its traceback there, and the watcher starts it again 30 seconds later. When `mail.py` changes, the dispatcher restarts onto the new code.

## Setup per machine

- Windows: `powershell -ExecutionPolicy Bypass -File install_task.ps1`; Mac: `bash install_launchd.sh`. Both register the prompt hook and the watcher. Re-running `mail.py install-hook` removes the Stop hook (`mail.py wait`) of version 3; until then that hook does nothing.
- `~/.claude/settings.json` holds `"permissions": {"defaultMode": "auto"}` (the user's ruling of 2026-10-26, D-261026-1), so that a session ClaudeMail opens works without stopping at its first permission prompt: the app gives an imported session the default mode, not the mode of your other sessions. It is a security setting, so the user sets it on each machine.
- The prompt hook (`mail.py hook`, SessionStart and UserPromptSubmit) only records each session's desk and Claude process in `~/.claude/claudemail/sessions/`, which LeaseResources reads to tell whose lease a job is.
- The machine is identified by `CLAUDE_MAILBOX_MACHINE` if set, else by `hostnames` in the config, else by the hostname. Settings: `config/defaults.json`, overlaid by `config/<machine>.json`.

## Known limits

- A session in bypass-permissions mode holds a message from another session for approval, and the desktop app cannot show that dialog, so such a session does not get mail (Claude Code's cross-session messaging rules).
- A file on a desk that is not a message (no header, not UTF-8) is moved to `rejected/` beside it, with one notification.
- A machine that is asleep or logged out delivers when it wakes; the desktop app must be running for a closed or new session to be opened (`open` starts it on the Mac).
- Mail that arrived before version 4 and was never delivered was moved to `held/` in its desk on 2026-10-26 instead of starting a session per message; `mail.py inbox` does not list it.
