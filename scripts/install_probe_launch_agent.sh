#!/bin/bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$LAUNCH_AGENTS_DIR/com.backlink.source-probe.daily.plist"
LOG_DIR="$PROJECT_DIR/logs"
PYTHON_BIN="$PROJECT_DIR/.venv311/bin/python"

mkdir -p "$LAUNCH_AGENTS_DIR"
mkdir -p "$LOG_DIR"

cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.backlink.source-probe.daily</string>

    <key>ProgramArguments</key>
    <array>
        <string>/bin/zsh</string>
        <string>-lc</string>
        <string>cd "$PROJECT_DIR" &amp;&amp; PYTHONUNBUFFERED=1 "$PYTHON_BIN" source_probe_audit.py --force --worth-filter 待确认</string>
    </array>

    <key>WorkingDirectory</key>
    <string>$PROJECT_DIR</string>

    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>19</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>

    <key>RunAtLoad</key>
    <false/>

    <key>StandardOutPath</key>
    <string>$LOG_DIR/source_probe.launchd.stdout.log</string>
    <key>StandardErrorPath</key>
    <string>$LOG_DIR/source_probe.launchd.stderr.log</string>
</dict>
</plist>
PLIST

launchctl bootout "gui/$(id -u)" "$PLIST_PATH" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"

echo "✅ 已安装批量探测 launchd 定时任务：每天 19:00 强制重跑“待确认”来源"
echo "📄 plist: $PLIST_PATH"
echo "📝 日志目录: $LOG_DIR"
