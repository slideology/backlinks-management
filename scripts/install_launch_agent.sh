#!/bin/bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$LAUNCH_AGENTS_DIR/com.backlink.robot.daily.plist"
LOG_DIR="$PROJECT_DIR/logs"

mkdir -p "$LAUNCH_AGENTS_DIR"
mkdir -p "$LOG_DIR"

cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.backlink.robot.daily</string>

    <key>ProgramArguments</key>
    <array>
        <string>/bin/zsh</string>
        <string>-lc</string>
        <string>cd "$PROJECT_DIR" &amp;&amp; AUTO_MODE=1 PYTHONUNBUFFERED=1 ./Start_Robot.command</string>
    </array>

    <key>WorkingDirectory</key>
    <string>$PROJECT_DIR</string>

    <key>StartCalendarInterval</key>
    <array>
        <dict>
            <key>Hour</key>
            <integer>8</integer>
            <key>Minute</key>
            <integer>0</integer>
        </dict>
        <dict>
            <key>Hour</key>
            <integer>12</integer>
            <key>Minute</key>
            <integer>0</integer>
        </dict>
    </array>

    <key>RunAtLoad</key>
    <false/>

    <key>StandardOutPath</key>
    <string>$LOG_DIR/launchd.stdout.log</string>
    <key>StandardErrorPath</key>
    <string>$LOG_DIR/launchd.stderr.log</string>
</dict>
</plist>
PLIST

launchctl unload "$PLIST_PATH" >/dev/null 2>&1 || true
launchctl load "$PLIST_PATH"

echo "✅ 已安装 launchd 定时任务：每天 08:00 和 12:00 自动运行"
echo "📄 plist: $PLIST_PATH"
echo "📝 日志目录: $LOG_DIR"
