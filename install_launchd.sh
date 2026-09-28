#!/bin/bash
# Sets up ClaudeMail on this Mac (the counterpart of install_task.ps1):
#   1. registers the Claude Code hooks in ~/.claude/settings.json: `mail.py hook` on SessionStart + UserPromptSubmit
#      and `mail.py wait` on Stop (asyncRewake). These commands never change, so code updates need no re-run;
#   2. installs and starts the LaunchAgent com.goocy.claudemail, which keeps `mail.py watch` (the dispatcher's
#      keeper) running through ClaudeMail.app from login on; the dispatcher restarts itself whenever mail.py changes.
# Re-running it cleanly replaces any existing copy. Afterwards open one desktop session in
# ~/ResilioSync/Services/ClaudeMail/mail/<Machine>/post office and pin it (the Post office).
#
#   bash install_launchd.sh           # install + (re)load
#   bash install_launchd.sh uninstall # unload + remove agent and hooks (mailbox left alone)
# Call it through bash: an edit made on a Windows machine drops the executable bit on the Macs (seen 2026-09-28).
set -euo pipefail

# Never under sudo: $(id -u) would be 0, so launchctl would target gui/0 ("125: Domain does not support
# specified action"), and ~/.claude/settings.json would be left owned by root (seen on Sandbank 2026-09-28).
if [[ "$EUID" -eq 0 ]]; then
    echo "Run this as your own user, not with sudo or as root." >&2
    exit 1
fi

LABEL="com.goocy.claudemail"
DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOGDIR="$HOME/.claude/claudemail/logs"                   # machine-local; $DIR/logs (synced, both Macs wrote one file) until 2026-09-28
LOG="$LOGDIR/launchd.log"
PYTHON="/opt/homebrew/bin/python3"                      # Apple Silicon Homebrew (Altiplano)
[[ -x "$PYTHON" ]] || PYTHON="/usr/local/bin/python3"   # Intel Homebrew (Sandbank)
mkdir -p "$LOGDIR"

if [[ "${1:-}" == "uninstall" ]]; then
    launchctl bootout "gui/$(id -u)/$LABEL" || true   # stderr kept: "not loaded" is harmless, anything else is worth seeing
    rm -f "$PLIST"
    "$PYTHON" -B "$DIR/mail.py" uninstall-hook
    echo "Removed LaunchAgent $LABEL and the hook."
    exit 0
fi

# Fails loudly if this hostname is not one of the mailbox machines in config/defaults.json.
"$PYTHON" -B "$DIR/mail.py" whoami
"$PYTHON" -B "$DIR/mail.py" install-hook

APP="$DIR/ClaudeMail.app"
WRAPPER="$APP/Contents/MacOS/claudemail"
if [[ ! -x "$WRAPPER" ]] || ! codesign --verify --strict "$APP" >/dev/null 2>&1 \
   || ! lipo -archs "$WRAPPER" 2>/dev/null | grep -qw "$(uname -m)"; then
    echo "Wrapper missing, unsigned or lacking $(uname -m) at $APP, building it."
    "$DIR/build_app.sh"
fi

mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>            <string>$LABEL</string>

    <!-- A LaunchAgent, not a Daemon: notifications only reach the logged-in user's session. -->
    <key>ProgramArguments</key>
    <array>
        <string>$WRAPPER</string>
        <string>-B</string>
        <string>$DIR/mail.py</string>
        <string>watch</string>
    </array>
    <key>WorkingDirectory</key> <string>$DIR</string>

    <!-- Continuous from login; relaunched if it dies, at most once a minute. -->
    <key>RunAtLoad</key>        <true/>
    <key>KeepAlive</key>        <true/>
    <key>ThrottleInterval</key> <integer>60</integer>
    <key>ProcessType</key>      <string>Background</string>

    <!-- Bootstrap output and tracebacks only; mail.py writes ~/.claude/claudemail/logs/claudemail.<machine>.<yyMMdd>.log itself. -->
    <key>StandardOutPath</key>  <string>$LOG</string>
    <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
PLISTEOF

launchctl bootout "gui/$(id -u)/$LABEL" || true   # stderr kept: "not loaded" is harmless, anything else is worth seeing
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Installed and loaded LaunchAgent $LABEL."
echo "Runs : $WRAPPER -B $DIR/mail.py watch"
echo "Log  : $LOGDIR/claudemail.<machine>.<yyMMdd>.log"
echo "Next: open one desktop session in ~/ResilioSync/Services/ClaudeMail/mail/<Machine>/post office and pin it."
