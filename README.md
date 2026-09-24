# ClaudeMail

Lets the Claude instances on the five house machines (Mistral, Moosfels, Stahlwerk, Teppich, Altiplano) send each other messages through Resilio. Built 2026-09-24 on Mistral.

## How it works

- A message for a machine is a Markdown file in its inbox: `ResilioSync/Mailbox/<Machine>/<yyMMdd-HHmmss>.md`, with a header (`from`, `to`, `subject`, `sent`, optionally `in_reply_to`) between two `---` lines, then the body.
- Delivery is a Claude Code hook on `SessionStart` and `UserPromptSubmit`. When a session on that machine starts or gets a prompt, `mail.py hook` moves each waiting message into `Mailbox/<Machine>/read/` and gives its text to that session as context. The move is the claim, so a message reaches exactly one session even when several are open. `read/` is synced too, so the sender's side can see that a message was picked up.
- An idle open session is woken by `mail.py wait`, registered as an `asyncRewake` hook on `Stop` only. (It was on `SessionStart` too until 2026-09-24 06:00, but Claude Code 2.1.280 on Windows runs a SessionStart hook in the foreground, so every new session hung at "Starting session" until its waiter ended; a waiter started on SessionStart now exits at once, and `install-hook` removes that registration.) It runs in the background after every turn, and leaves when its session's Claude process is gone. Meanwhile it polls the inbox, claims arriving mail with the same move into `read/`, and exits 2 with the delivery text, which Claude Code gives the session as a new turn. A newer waiter of the same session replaces the older one (pid files in the local, unsynced `~/.claude/claudemail-waiters/`), and a waiter leaves on its own shortly before the hook timeout (`wait_hours` in `config.json`, default 24), so an untouched session stops listening after a day until its next turn. With several idle sessions open, whichever waiter polls first gets the mail.
- The watcher (`mail.py watch`, one per machine) polls its own inbox every 5 seconds and raises a desktop notification per arriving message, so the user knows to open or prompt a Claude session there. It does not start Claude and does not act on anything; nothing runs without a session the user is in.
- A reply is an ordinary message with `in_reply_to` pointing at the original.
- Every session has its own folder, `Mailbox/<Machine>/sessions/<yyMMdd-HHmmss>-<session id>/`, made by its first hook (SessionStart; a session older than the feature gets it on its next prompt or turn end), stamped with that moment. A resumed session keeps its folder, and `/clear` starts a new session and folder. The folder's `session.json` holds `started`, `last_active` (updated on every hook), `cwd` and the first 200 characters of the first prompt, so other machines can see what a session is about: `mail.py sessions [Machine]` lists them, newest first. A folder is deleted `session_keep_days` (default 30) after its last activity, by that machine's SessionStart hook and watcher (at start and hourly); a folder that still holds unread mail is never deleted, and unread mail of a session that no longer listens is moved to the machine inbox anyway (see below).
- Sessions can be addressed individually (added 2026-09-24). `send` records the sending session (`from_session`, from `CLAUDE_CODE_SESSION_ID`), and `--reply-to` delivers the reply to that session's folder (or, if the folder is gone, to the machine inbox with a note), so an answer reaches the session that asked instead of whichever one polls first. `--to Mistral/11aa11aa` addresses a session by its id or its first 8+ characters (`whoami` prints the current one, `sessions` all of them); an unknown or ambiguous prefix is refused. A session's hooks and waiter claim from its own folder first, then from the machine inbox, which stays the address for "any session". A session counts as listening while its Claude process (`CLAUDE_PID`, noted in the local `~/.claude/claudemail-waiters/<id>.claude`) or its waiter runs, or it registered within `session_fallback_minutes` (default 10). Mail for a session that no longer listens is moved to the machine inbox by any hook, waiter or watcher on that machine, and delivered with a note saying whom it was meant for.

## Sending

Write the body to a file (on Windows with the Write tool, never a heredoc), then:

```
python mail.py send --to Stahlwerk --subject "Please check X" --body-file body.md
python mail.py send --reply-to Mistral/read/260924-143000.md --body-file reply.md
python mail.py send --to all --subject "..." --body-file body.md
```

`--to` takes a machine name in any case, a comma list, `all` (every machine but this one), or `Machine/<session id or its first 8+ characters>` for one session. `--reply-to` goes to the session that sent the original when it recorded one. `sessions [Machine]` lists the session folders with start, last activity, working folder and first prompt, `inbox` lists unread mail (machine and session folders), `inbox --all` the read ones too, `whoami` prints which mailbox this machine is and this session's address.

## Setup per machine

- Windows: `powershell -ExecutionPolicy Bypass -File install_task.ps1`, then restart open Claude sessions. It registers the hook and the at-logon task `Claude Mail Watcher`.
- Mac (Altiplano): `./install_launchd.sh` (`./install_launchd.sh uninstall` to remove), then restart open Claude sessions. It registers the hook with the stable `/opt/homebrew/bin/python3` and loads the LaunchAgent `com.goocy.claudemail` (RunAtLoad + KeepAlive), which runs `mail.py watch` through `ClaudeMail.app`. That bundle is a compiled launcher (`launcher.c`, copied from ExportScreenTime) built and ad-hoc signed by `build_app.sh`; it needs no privacy grant, so rebuilding it costs nothing. `logs/launchd.log` only catches tracebacks and wrapper errors. If the Mac's hostname is not `altiplano`, add it under `hostnames` in `config.json`.
- The machine is identified by hostname (domain part dropped, case ignored), then `config.json` `hostnames`, then the `CLAUDE_MAILBOX_MACHINE` environment variable, which overrides both.

## Known limits

- Mail waits until a session on the recipient machine starts, receives a prompt, or (with the waiter installed) finishes a turn within the last `wait_hours`. With no Claude session open at all, only the notification tells anyone. The waiter is active only where `install-hook` was run after 2026-09-24 05:10 (Altiplano and Mistral so far; see `Running-on.md`); elsewhere re-run it and restart the sessions.
- The hook uses the Python that ran `install-hook`; if that interpreter moves, run `install-hook` again.
