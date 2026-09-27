#!/bin/bash
# Sets up ClaudeMail on this Mac (the counterpart of install_task.ps1):
#   1. registers the Claude Code hook (SessionStart + UserPromptSubmit) in ~/.claude/settings.json,
#      which is what actually hands arriving mail to a Claude session;
#   2. installs the LaunchAgent com.goocy.claudemail, which keeps `mail.py watch` running through
#      ClaudeMail.app from login on. The watcher only raises a notification per new message.
# Re-running it cleanly replaces any existing copy.
#
#   ./install_launchd.sh           # install + (re)load
#   ./install_launchd.sh uninstall # unload + remove agent and hook (mailbox left alone)
set -euo pipefail

LABEL="com.goocy.claudemail"
DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOGDIR="$DIR/logs"
LOG="$LOGDIR/launchd.log"
PYTHON="/opt/homebrew/bin/python3"
mkdir -p "$LOGDIR"

if [[ "${1:-}" == "uninstall" ]]; then
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
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
if [[ ! -x "$WRAPPER" ]] || ! codesign --verify --strict "$APP" >/dev/null 2>&1; then
    echo "Wrapper missing or unsigned at $APP, building it."
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

    <!-- Bootstrap output and tracebacks only; mail.py writes logs/claudemail.<machine>.<yyMMdd>.log itself. -->
    <key>StandardOutPath</key>  <string>$LOG</string>
    <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
PLISTEOF

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Installed and loaded LaunchAgent $LABEL."
echo "Runs : $WRAPPER -B $DIR/mail.py watch"
echo "Log  : $LOGDIR/claudemail.<machine>.<yyMMdd>.log"
echo "Next: open one desktop session in ~/ResilioSync/Mailbox/<Machine>/post office and pin it."
