"""
agent_runner.py
================
Multi-Agent 系统统一入口

这是整个 Multi-Agent 系统的对外入口文件。
原本每次运行需要调用 daily_run_orchestrator.py，
现在根据 config.json 中的 multi_agent.enabled 配置，
自动选择：
  - multi_agent 模式：启动 SupervisorAgent（四个 Agent 协同）
  - agent 模式（单 Agent）：启动 BacklinkAgent（第二阶段）
  - 传统模式（降级）：调用 daily_run_orchestrator.main()

使用方法：
  python agent_runner.py             # 根据 config.json 自动选择模式
  python agent_runner.py --dry-run   # 安全预演（只规划不发帖）
  python agent_runner.py --mode multi_agent  # 强制使用 Multi-Agent 模式
  python agent_runner.py --mode agent        # 强制使用单 Agent 模式
  python agent_runner.py --mode classic      # 强制使用传统模式
"""

import argparse
import json
import sys
from datetime import datetime


def load_config(config_path: str = "config.json") -> dict:
    """加载配置文件。"""
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def run_multi_agent(dry_run: bool = False) -> dict:
    """
    启动 Multi-Agent 模式（第三阶段）：
    SupervisorAgent 协调 Scheduler + Executor + Analyzer
    """
    print("\n🎯 [Multi-Agent 模式] 启动 SupervisorAgent...")
    from agents.supervisor_agent import SupervisorAgent
    supervisor = SupervisorAgent(dry_run=dry_run)
    return supervisor.run_daily_session()


def run_single_agent(dry_run: bool = False) -> dict:
    """
    启动单 Agent 模式（第二阶段）：
    BacklinkAgent 自主规划和执行
    """
    print("\n🤖 [单 Agent 模式] 启动 BacklinkAgent...")
    from agent_core import BacklinkAgent
    agent = BacklinkAgent()
    if dry_run:
        agent._dry_run = True
    return agent.run_daily_session()


def run_classic() -> None:
    """
    启动传统模式（第一阶段/降级）：
    原有的 daily_run_orchestrator 定序流水线
    """
    print("\n📋 [传统模式] 启动 daily_run_orchestrator...")
    import daily_run_orchestrator
    daily_run_orchestrator.main()


def detect_mode(config: dict) -> str:
    """
    根据配置文件自动判断应该启动哪种模式。

    优先级：
      multi_agent.enabled=true → multi_agent
      agent.enabled=true       → agent
      默认                     → classic
    """
    if config.get("multi_agent", {}).get("enabled", False):
        return "multi_agent"
    elif config.get("agent", {}).get("enabled", False):
        return "agent"
    else:
        return "classic"


def main():
    parser = argparse.ArgumentParser(
        description="外链自动化 Agent 运行器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  python agent_runner.py                        # 根据 config.json 自动选择
  python agent_runner.py --dry-run              # 安全预演（不发帖）
  python agent_runner.py --mode multi_agent     # 强制 Multi-Agent 模式
  python agent_runner.py --mode classic         # 强制传统模式
        """
    )
    parser.add_argument("--dry-run", action="store_true", help="安全预演模式（只规划，不实际发帖）")
    parser.add_argument(
        "--mode",
        choices=["multi_agent", "agent", "classic", "auto"],
        default="auto",
        help="运行模式（默认 auto，根据 config.json 自动选择）",
    )
    args = parser.parse_args()

    config = load_config()
    mode = args.mode if args.mode != "auto" else detect_mode(config)
    dry_run = args.dry_run or config.get("multi_agent", {}).get("dry_run", False) or config.get("agent", {}).get("dry_run", False)

    print("=" * 60)
    print(f"🚀 外链自动化 Agent 运行器")
    print(f"   时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"   模式: {mode.upper()}")
    print(f"   预演: {'是（不会实际发帖）' if dry_run else '否（正式发帖）'}")
    print("=" * 60)

    result = None
    try:
        if mode == "multi_agent":
            result = run_multi_agent(dry_run=dry_run)
        elif mode == "agent":
            result = run_single_agent(dry_run=dry_run)
        else:
            run_classic()

        if result:
            print(f"\n✅ 运行完成：{result.get('stop_reason', '')}")
    except KeyboardInterrupt:
        print("\n⚠️ 用户手动中断运行")
        sys.exit(0)
    except Exception as exc:
        print(f"\n❌ 运行异常: {exc}")
        print("⚠️ 自动降级到传统模式...")
        try:
            run_classic()
        except Exception as exc2:
            print(f"❌ 传统模式也异常: {exc2}")
            sys.exit(1)


if __name__ == "__main__":
    main()
