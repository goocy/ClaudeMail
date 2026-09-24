# ClaudeMail

Lets the Claude instances on the five house machines (Mistral, Moosfels, Stahlwerk, Teppich, Altiplano) send each other messages through Resilio. Built 2026-09-24 on Mistral.

## How it works

- A message for a machine is a Markdown file in its inbox: `ResilioSync/Mailbox/<Machine>/<yyMMdd-HHmmss>.md`, with a header (`from`, `to`, `subject`, `sent`, optionally `in_reply_to`) between two `---` lines, then the body.
- Delivery is a Claude Code hook on `SessionStart` and `UserPromptSubmit`. When a session on that machine starts or gets a prompt, `mail.py hook` moves each waiting message into `Mailbox/<Machine>/read/` and gives its text to that session as context. The move is the claim, so a message reaches exactly one session even when several are open. `read/` is synced too, so the sender's side can see that a message was picked up.
- The watcher (`mail.py watch`, one per machine) polls its own inbox every 5 seconds and raises a desktop notification per arriving message, so the user knows to open or prompt a Claude session there. It does not start Claude and does not act on anything; nothing runs without a session the user is in.
- A reply is an ordinary message with `in_reply_to` pointing at the original.

## Sending

Write the body to a file (on Windows with the Write tool, never a heredoc), then:

```
python mail.py send --to Stahlwerk --subject "Please check X" --body-file body.md
python mail.py send --reply-to Mistral/read/260924-143000.md --body-file reply.md
python mail.py send --to all --subject "..." --body-file body.md
```

`--to` takes a machine name in any case, a comma list, or `all` (every machine but this one). `inbox` lists unread mail, `inbox --all` the read ones too, `whoami` prints which mailbox this machine is.

## Setup per machine

- Windows: `powershell -ExecutionPolicy Bypass -File install_task.ps1`, then restart open Claude sessions. It registers the hook and the at-logon task `Claude Mail Watcher`.
- Mac (Altiplano): `./install_launchd.sh` (`./install_launchd.sh uninstall` to remove), then restart open Claude sessions. It registers the hook with the stable `/opt/homebrew/bin/python3` and loads the LaunchAgent `com.goocy.claudemail` (RunAtLoad + KeepAlive), which runs `mail.py watch` through `ClaudeMail.app`. That bundle is a compiled launcher (`launcher.c`, copied from ExportScreenTime) built and ad-hoc signed by `build_app.sh`; it needs no privacy grant, so rebuilding it costs nothing. `logs/launchd.log` only catches tracebacks and wrapper errors. If the Mac's hostname is not `altiplano`, add it under `hostnames` in `config.json`.
- The machine is identified by hostname (domain part dropped, case ignored), then `config.json` `hostnames`, then the `CLAUDE_MAILBOX_MACHINE` environment variable, which overrides both.

## Known limits

- Two machines sending to the same inbox in the same second from different machines would create the same file name on both, and Resilio would keep only one. The `-2` suffix only protects against collisions it can see locally. Rare enough to accept for now; if it ever matters, add the sender to the file name.
- Mail waits until a session on the recipient machine starts or receives a prompt. An idle open session does not see it until the user types something.
- The hook uses the Python that ran `install-hook`; if that interpreter moves, run `install-hook` again.
