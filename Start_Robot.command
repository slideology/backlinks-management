#!/bin/bash

# ==========================================
# 🚀 外链自动化机器人 - Mac 一键启动中心 🚀
# ==========================================

# 1. 获取当前脚本所在的绝对路径 (也就是我们的项目根文件夹)
PROJECT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$PROJECT_DIR"

echo "=================================================="
echo "🤖 环节1: 正在启动机器人的专属 Chrome（你的日常 Chrome 不受影响）"
echo "=================================================="


# 【重要升级】机器人现在拥有自己的専属 Chrome！
# 原来的做法是杀掉你的主 Chrome 然后重启，这次我们彻底改变策略：
#   - 你的日常 Chrome 继续照常使用，完全不受影响
#   - 机器人启动一个独立的第二个 Chrome 实例（使用专属账号目录 ~/ChromeAutoBot）
#   - 两个 Chrome 实例互不干扰、和平共处！

BOT_CHROME_DATA="$HOME/ChromeAutoBot"
AUTO_MODE="${AUTO_MODE:-0}"

# 首次运行时自动创建机器人专属目录
mkdir -p "$BOT_CHROME_DATA"

# 检查机器人 Chrome 是否已在运行（避免重复启动）
if curl -s http://localhost:9222/json/version > /dev/null 2>&1; then
    echo "-> 机器人 Chrome 已在运行，无需重启。"
else
    echo "-> 正在启动机器人专属 Chrome（不影响你的日常 Chrome）..."
    open -na "Google Chrome" --args \
        --remote-debugging-port=9222 \
        --user-data-dir="$BOT_CHROME_DATA" \
        --no-first-run \
        --no-default-browser-check
    
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

echo "-> Chrome 已在后台启动 (正在监听 9222 端口)，等待 3 秒钟加载..."
sleep 3


echo ""
echo "=================================================="
echo "🎯 环节2: 正在运行 daily_run_orchestrator.py 自动补批次直到成功目标..."
echo "=================================================="
python3 -u daily_run_orchestrator.py


echo ""
echo "🎉 恭喜老板！今日的外链全线自动化任务已经收工啦！你可以关闭这个黑框框了。"
