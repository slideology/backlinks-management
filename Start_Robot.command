#!/bin/bash

# ==========================================
# 🚀 外链自动化机器人 - Mac 一键启动中心 🚀
# ==========================================

# 1. 获取当前脚本所在的绝对路径 (也就是我们的项目根文件夹)
PROJECT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$PROJECT_DIR"

echo "=================================================="
echo "🤖 环节1: 正在为你打开携带你个人真实指纹的 Chrome 浏览器..."
echo "=================================================="

# 杀掉所有之前可能是正常打开的 Chrome (因为我们需要用调试端口启动它)
pkill -9 "Google Chrome" 2>/dev/null
sleep 1

# 以极客模式 (调试端口 9222) 启动你 Mac 上自带的真实 Google Chrome!
# 注意：在 Mac 上启用调试端口需要指定一个额外的 user-data-dir，我们把它挂载在临时目录
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9222 --user-data-dir="/tmp/chrome_dev_data" --restore-last-session &

echo "-> Chrome 已在后台启动 (正在监听 9222 端口)，等待 3 秒钟加载..."
sleep 3


echo ""
echo "=================================================="
echo "🎯 环节2: 正在运行 daily_scheduler.py 挑选今日优质猎物..."
echo "=================================================="
python3 daily_scheduler.py


echo ""
echo "=================================================="
echo "🚀 环节3: 正在运行 form_automation_local.py 让 AI 自动接管并疯狂发帖..."
echo "=================================================="
# 注意：我们这里调用的是即将给你准备的新版发帖脚本 (专门接管 9222 的真实浏览器)
python3 form_automation_local.py


echo ""
echo "🎉 恭喜老板！今日的外链全线自动化任务已经收工啦！你可以关闭这个黑框框了。"
