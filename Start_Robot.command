#!/bin/bash

# ==========================================
# 🚀 外链自动化机器人 - Mac 一键启动中心 🚀
# ==========================================

# 1. 获取当前脚本所在的绝对路径 (也就是我们的项目根文件夹)
PROJECT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$PROJECT_DIR"

PYTHON_BIN="$PROJECT_DIR/.venv311/bin/python"
if [[ ! -x "$PYTHON_BIN" ]]; then
    if [[ -x "/opt/homebrew/bin/python3.11" ]]; then
        echo "-> 未找到 .venv311，正在用 python3.11 创建项目运行环境..."
        /opt/homebrew/bin/python3.11 -m venv "$PROJECT_DIR/.venv311"
        PYTHON_BIN="$PROJECT_DIR/.venv311/bin/python"
    else
        echo "❌ 未找到可用的 Python 3.11 运行环境。"
        exit 1
    fi
fi

CHROME_CANARY_APP="/Applications/Google Chrome Canary.app"
CHROME_STABLE_APP="/Applications/Google Chrome.app"
if [[ -d "$CHROME_CANARY_APP" ]]; then
    BOT_CHROME_APP="$CHROME_CANARY_APP"
    BOT_CHROME_NAME="Google Chrome Canary"
else
    BOT_CHROME_APP="$CHROME_STABLE_APP"
    BOT_CHROME_NAME="Google Chrome"
fi

CDP_URL="$("$PYTHON_BIN" - <<'PY'
import json
from pathlib import Path

default = "http://127.0.0.1:9666"
try:
    payload = json.loads(Path("config.json").read_text(encoding="utf-8"))
    print((payload.get("browser") or {}).get("connect_cdp_url") or default)
except Exception:
    print(default)
PY
)"

CDP_PORT="$("$PYTHON_BIN" - <<'PY'
import json
from pathlib import Path
from urllib.parse import urlparse

default = 9666
try:
    payload = json.loads(Path("config.json").read_text(encoding="utf-8"))
    url = (payload.get("browser") or {}).get("connect_cdp_url") or f"http://127.0.0.1:{default}"
    parsed = urlparse(url)
    print(parsed.port or default)
except Exception:
    print(default)
PY
)"

check_bot_cdp_health() {
    "$PYTHON_BIN" - <<'PY'
from playwright.sync_api import sync_playwright
from browser_cdp import ensure_cdp_blank_page
import json
from pathlib import Path

default = "http://127.0.0.1:9666"
try:
    payload = json.loads(Path("config.json").read_text(encoding="utf-8"))
    cdp = (payload.get("browser") or {}).get("connect_cdp_url") or default
except Exception:
    cdp = default
try:
    ensure_cdp_blank_page(cdp)
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(cdp)
        contexts = list(browser.contexts)
        if not contexts:
            raise RuntimeError("no contexts")
        pages = list(contexts[0].pages)
        if not pages:
            raise RuntimeError("no pages")
        print("cdp-health-ok")
except Exception as exc:
    print(f"cdp-health-failed: {exc}")
    raise SystemExit(1)
PY
}

restart_bot_chrome() {
    echo "-> 正在重启机器人专属 $BOT_CHROME_NAME..."
    pkill -f "Google Chrome Canary.*--remote-debugging-port=$CDP_PORT.*ChromeAutoBot" >/dev/null 2>&1 || true
    pkill -f "Google Chrome.*--remote-debugging-port=$CDP_PORT.*ChromeAutoBot" >/dev/null 2>&1 || true
    pkill -f "ChromeAutoBot" >/dev/null 2>&1 || true
    sleep 2
    open -na "$BOT_CHROME_APP" --args \
        --remote-debugging-port="$CDP_PORT" \
        --user-data-dir="$BOT_CHROME_DATA" \
        --no-first-run \
        --no-default-browser-check
}

echo "=================================================="
echo "🤖 环节1: 正在启动机器人的专属 $BOT_CHROME_NAME（你的日常 Chrome 不受影响）"
echo "=================================================="


# 【重要升级】机器人现在拥有自己的専属 Chrome！
# 原来的做法是杀掉你的主 Chrome 然后重启，这次我们彻底改变策略：
#   - 你的日常 Chrome 继续照常使用，完全不受影响
#   - 机器人优先启动独立的 Chrome Canary（使用专属账号目录 ~/ChromeAutoBot）
#   - 两个 Chrome 实例互不干扰、和平共处！

BOT_CHROME_DATA="$HOME/ChromeAutoBot"
AUTO_MODE="${AUTO_MODE:-0}"

# 首次运行时自动创建机器人专属目录
mkdir -p "$BOT_CHROME_DATA"

# 检查机器人 Chrome 是否已在运行（避免重复启动）
if curl -s "http://localhost:$CDP_PORT/json/version" > /dev/null 2>&1; then
    echo "-> 检测到 $CDP_PORT 端口已打开，正在做 CDP 健康检查..."
    if check_bot_cdp_health >/dev/null 2>&1; then
        echo "-> 机器人 Chrome 已在运行且状态健康，无需重启。"
    else
        echo "-> $CDP_PORT 虽然可访问，但 CDP 会话不健康，准备自动重启机器人 Chrome。"
        restart_bot_chrome
    fi
else
    echo "-> 正在启动机器人专属 $BOT_CHROME_NAME（不影响你的日常 Chrome）..."
    restart_bot_chrome
    
    echo "-> 等待机器人 Chrome 就绪..."
    sleep 4
    
    if [[ "$AUTO_MODE" != "1" && -t 0 ]]; then
        echo ""
        echo "⚠️  【首次使用提示】"
        echo "   如果这是第一次运行，请在刚刚弹出的 Chrome 窗口里"
        echo "   登录你的专用发帖 Google 账号。"
        echo "   登录完成后，按任意键继续..."
        read -n 1 -s -r
    else
        echo "-> 自动模式：跳过首次登录确认提示。"
    fi
fi

echo "-> 正在确认机器人 Chrome 的 CDP 会话健康..."
for _ in {1..8}; do
    if curl -s "http://localhost:$CDP_PORT/json/version" > /dev/null 2>&1 && check_bot_cdp_health >/dev/null 2>&1; then
        echo "-> Chrome 已在后台启动并通过 CDP 健康检查。"
        break
    fi
    sleep 2
done

if ! curl -s "http://localhost:$CDP_PORT/json/version" > /dev/null 2>&1 || ! check_bot_cdp_health >/dev/null 2>&1; then
    echo "❌ 机器人 Chrome 启动后仍未通过 CDP 健康检查，停止本轮任务。"
    exit 1
fi

echo "-> Chrome 已在后台启动 (正在监听 $CDP_PORT 端口)，等待 2 秒钟加载..."
sleep 2


echo ""
echo "=================================================="
echo "🎯 环节2: 正在运行 daily_run_orchestrator.py 自动补批次直到成功目标..."
echo "=================================================="
"$PYTHON_BIN" -u daily_run_orchestrator.py


echo ""
echo "🎉 恭喜老板！今日的外链全线自动化任务已经收工啦！你可以关闭这个黑框框了。"
