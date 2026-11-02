#!/bin/bash
# Install, or remove, the LaunchAgent dev.tristan.asd-questions that keeps
# shared/questions/server.ts running (references/owner-questions.md).
#
# Usage:
#   install_questions_agent.sh --slack-user U0123ABCD [--dry-run]
#   install_questions_agent.sh --uninstall
set -euo pipefail

LABEL=dev.tristan.asd-questions
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/asd-questions.log"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOMAIN="gui/$(id -u)"
SLACK_USER=""
DRY=0
UNINSTALL=0

while [ $# -gt 0 ]; do
  case "$1" in
    --slack-user) SLACK_USER="${2:-}"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [ "$UNINSTALL" = 1 ]; then
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "removed $LABEL"
  exit 0
fi

if [ -z "$SLACK_USER" ]; then
  echo "--slack-user U... is required (your Slack member ID)" >&2
  exit 2
fi
BUN="$(command -v bun || true)"
if [ -z "$BUN" ]; then
  echo "bun not found on PATH" >&2
  exit 1
fi

render() {
cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$BUN</string>
    <string>run</string>
    <string>$REPO/shared/questions/server.ts</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>EnvironmentVariables</key>
  <dict>
    <key>ASD_SLACK_USER</key>
    <string>$SLACK_USER</string>
    <key>PATH</key>
    <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
  </dict>
  <key>StandardOutPath</key>
  <string>$LOG</string>
  <key>StandardErrorPath</key>
  <string>$LOG</string>
  <key>WorkingDirectory</key>
  <string>$REPO</string>
</dict>
</plist>
EOF
}

if [ "$DRY" = 1 ]; then
  render
  exit 0
fi

mkdir -p "$(dirname "$PLIST")" "$(dirname "$LOG")"
render > "$PLIST"
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$PLIST"
cat <<EOF
installed $LABEL
check:  launchctl print $DOMAIN/$LABEL | head
log:    $LOG
page:   http://127.0.0.1:7317
EOF
