#!/bin/bash
# Compile and sign the ClaudeMail.app wrapper bundle (launcher.c -> ClaudeMail.app/Contents/MacOS/claudemail).
# The built bundle lives in the ResilioSync folder, so this only needs running if the launcher is
# missing or launcher.c / Info.plist changed.
#
#   ./build_app.sh                              # ad-hoc signature (the default, as ExportScreenTime.app)
#   SIGN_ID=<sha1-of-identity> ./build_app.sh   # sign with a real certificate instead
#
# ClaudeMail holds no TCC grant, so a rebuild (new ad-hoc cdhash) costs nothing. If it ever needs
# one, read ../ExportScreenTime/build_app.sh first: an ad-hoc grant dies with every changed byte.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
APP="$DIR/ClaudeMail.app"
SIGN_ID="${SIGN_ID:--}"

mkdir -p "$APP/Contents/MacOS"
# Universal (arm64 for Altiplano, x86_64 for Sandbank): the one bundle is synced to both Macs.
cc -O2 -Wall -Wextra -arch arm64 -arch x86_64 -o "$APP/Contents/MacOS/claudemail" "$DIR/launcher.c"
codesign --force --sign "$SIGN_ID" "$APP"
codesign --verify --strict "$APP"
codesign -dvvv "$APP" 2>&1 | grep -E "^(Identifier|Format|Signature|Authority|CDHash)" || true
echo "Built and signed $APP"
