"""
agent_core.py
==============
BacklinkAgent 主 Agent - 基于 Gemini Function Calling 实现

这是整个 AI Agent 系统的"大脑"。
它使用 Gemini 的 Function Calling 能力，让 AI 自主决定：
  - 今天先做什么
  - 发帖失败后是继续还是换策略
  - 什么时候发飞书日报
  - 什么时候停止

与第一阶段（strategy_decider）的区别：
  - strategy_decider 只在单次发帖失败时做局部决策
  - BacklinkAgent 负责整个一天的宏观调度和策略规划

架构：
  BacklinkAgent
    ↓ (读取记忆)
  AgentMemory
    ↓ (选择工具)
  agent_tools: get_today_tasks / run_batch_posting / get_daily_progress / send_daily_report
    ↓ (记录结果)
  AgentMemory.record_result()

用法：
  from agent_core import BacklinkAgent
  agent = BacklinkAgent()
  agent.run_daily_session()       # 完整的一天自主运行
"""

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

from agent_memory import AgentMemory
from agent_tools import TOOL_DECLARATIONS, dispatch_tool_call

# =====================================================================
# Agent 配置
# =====================================================================

AGENT_DEFAULTS = {
    "model": "gemini-2.5-flash",              # 推荐使用 flash 平衡速度和成本
    "max_agent_rounds": 20,                    # Agent 最多循环多少轮（防止无限循环）
    "request_timeout_seconds": 30,
    "dry_run": False,                           # dry_run=True 时 Agent 只规划不执行
    "agent_log_dir": "artifacts/agent_logs",   # Agent 决策日志目录
}


# =====================================================================
# Gemini 客户端（复用 vision_agent 的风格）
# =====================================================================

def _get_gemini_client(timeout: int = 30):
    """初始化 Gemini 客户端。"""
    from google import genai
    from google.genai import types

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("环境变量 GEMINI_API_KEY 未配置！")

    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=timeout),
    )


# =====================================================================
# 系统提示词构建
# =====================================================================

def _build_system_prompt(memory: AgentMemory, today_context: str = "") -> str:
    """构建 Agent 的系统提示词，注入记忆和当日背景。"""
    stats = memory.get_stats_summary()
    return f"""你是一个专业的外链自动化 Agent，负责每天自主完成外链发布任务。

【你的工作目标】
每天为多个目标站点（如 bearclicker.net、nanobananaimage.org）各发布 10 条外链。
当所有站点都完成每日目标时，发送飞书日报并结束当天工作。

【你的工作流程】
1. 首先调用 get_today_tasks 了解今日任务
2. 调用 get_daily_progress 了解各站点当前进度
3. 如果还有未完成的站点，调用 run_batch_posting 执行发帖
4. 每轮发帖后，再次调用 get_daily_progress 查看进度
5. 重复步骤 3-4 直到所有站点完成目标
6. 最后调用 send_daily_report 发送日报

【历史记忆摘要】
- 已追踪站点总数: {stats['total_sites_tracked']}
- 历史总尝试次数: {stats['total_attempts']}
- 历史总成功次数: {stats['total_successes']}
- 历史整体成功率: {stats['overall_success_rate']:.0%}
- 黑名单站点数: {stats['blacklisted_sites']}

【当日背景】
{today_context or '（无额外背景信息）'}

【重要规则】
- 每轮至多调用 3 次工具，避免过于频繁
- 如果连续 3 轮都没有新增成功，说明任务池耗尽，直接发日报结束
- 如果 get_today_tasks 返回 0 条任务，直接发日报结束
- 优先用 run_batch_posting（批量模式），不要逐条 post_backlink（效率低）
- 决策时用中文，工具调用参数用正确格式

当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
""".strip()


# =====================================================================
# 主 Agent 类
# =====================================================================

class BacklinkAgent:
    """
    外链发布 AI Agent。

    使用 Gemini Function Calling 让 AI 自主规划和执行发帖任务。
    """

    def __init__(self, config_path: str = "config.json"):
        self._config = self._load_config(config_path)
        self._memory = AgentMemory(
            memory_dir=self._config.get("agent_log_dir", AGENT_DEFAULTS["agent_log_dir"]).replace(
                "agent_logs", "agent_memory"
            )
        )
        self._log_dir = Path(str(self._config.get("agent_log_dir", AGENT_DEFAULTS["agent_log_dir"])))
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._dry_run = bool(self._config.get("dry_run", False))

    def _load_config(self, config_path: str) -> dict:
        """加载配置文件，合并默认值。"""
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            return {**AGENT_DEFAULTS, **config.get("agent", {})}
        except Exception:
            return dict(AGENT_DEFAULTS)

    def _build_tools_for_gemini(self):
        """构建传给 Gemini API 的 tools 参数。"""
        from google.genai import types

        function_declarations = []
        for decl in TOOL_DECLARATIONS:
            function_declarations.append(
                types.FunctionDeclaration(
                    name=decl["name"],
                    description=decl["description"],
                    parameters=decl.get("parameters"),
                )
            )
        return [types.Tool(function_declarations=function_declarations)]

    def _save_session_log(self, session_log: list) -> None:
        """保存本次 Agent 运行的完整决策日志。"""
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            log_file = self._log_dir / f"{today}_session.jsonl"
            with open(log_file, "a", encoding="utf-8") as f:
                for entry in session_log:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as exc:
            print(f"  ⚠️ Agent 日志写入失败: {exc}")

    def run_daily_session(self, today_context: str = "") -> dict:
        """
        执行完整的每日 Agent 工作流。

        这是 Agent 的主入口，替代原有的 `daily_run_orchestrator.main()`。
        干运行模式（dry_run=True）下只规划不实际执行。

        返回：
          {
            "success_count": 10,
            "rounds": 3,
            "stop_reason": "所有站点完成今日目标",
            ...
          }
        """
        print("\n" + "=" * 60)
        print(f"🤖 BacklinkAgent 启动 - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print(f"   模式: {'DRY RUN（只规划不执行）' if self._dry_run else '正式发帖'}")
        print("=" * 60)

        timeout = int(self._config.get("request_timeout_seconds", 30))
        max_rounds = int(self._config.get("max_agent_rounds", 20))
        model = str(self._config.get("model", "gemini-2.5-flash"))

        try:
            client = _get_gemini_client(timeout)
        except Exception as exc:
            print(f"❌ Gemini 客户端初始化失败: {exc}")
            return {"ok": False, "error": str(exc)}

        tools = self._build_tools_for_gemini()
        system_prompt = _build_system_prompt(self._memory, today_context)

        # 会话历史（多轮对话）
        from google.genai import types
        contents = [
            types.Content(
                role="user",
                parts=[types.Part.from_text(
                    "请开始今日的外链发布工作。先了解任务情况，然后自主完成所有站点的今日目标。"
                    + ("（注意：当前是 DRY RUN 模式，请只规划不要调用实际发帖工具）" if self._dry_run else "")
                )],
            )
        ]

        session_log = []
        round_count = 0
        consecutive_no_progress = 0
        total_success = 0
        stop_reason = "达到最大轮次"

        for round_idx in range(1, max_rounds + 1):
            round_count = round_idx
            print(f"\n{'─' * 40}")
            print(f"🔄 Agent 第 {round_idx} 轮思考中...")

            try:
                response = client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=system_prompt,
                        tools=tools,
                        temperature=0.1,   # 低温度 = 更确定性的决策
                    ),
                )
            except Exception as exc:
                print(f"  ❌ Gemini API 调用失败: {exc}")
                stop_reason = f"API 调用失败: {str(exc)[:100]}"
                break

            # 解析 Agent 的响应
            candidate = response.candidates[0] if response.candidates else None
            if not candidate:
                print("  ⚠️ Agent 未返回任何响应")
                break

            # 收集 Agent 的所有 function calls
            function_calls = []
            text_parts = []
            for part in candidate.content.parts:
                if hasattr(part, "function_call") and part.function_call:
                    function_calls.append(part.function_call)
                elif hasattr(part, "text") and part.text:
                    text_parts.append(part.text)

            # 如果 Agent 没有调用工具，说明它认为任务完成了
            if not function_calls:
                agent_text = " ".join(text_parts).strip()
                print(f"  💬 Agent 结论: {agent_text[:200]}")
                stop_reason = agent_text[:200] or "Agent 自主判断任务完成"
                session_log.append({"round": round_idx, "type": "final_text", "text": agent_text})
                break

            # 把 Agent 的思考加入对话历史
            contents.append(candidate.content)

            # 执行工具调用
            tool_results = []
            round_new_success = 0

            for fc in function_calls:
                tool_name = fc.name
                tool_args = dict(fc.args) if fc.args else {}

                print(f"  🔧 Agent 调用工具: {tool_name}({json.dumps(tool_args, ensure_ascii=False)[:80]})")

                if self._dry_run and tool_name in {"post_backlink", "run_batch_posting"}:
                    # DRY RUN: 拦截实际发帖工具
                    result = {
                        "ok": True,
                        "message": "[DRY RUN] 模拟执行，未实际发帖",
                        "success_count": 0,
                        "dry_run": True,
                    }
                else:
                    result = dispatch_tool_call(tool_name, tool_args)

                # 统计新增成功数
                new_success = int(result.get("success_count", 0) or 0)
                round_new_success += new_success
                total_success += new_success

                print(f"  ✅ 工具结果: {result.get('message', '')[:120]}")

                session_log.append({
                    "round": round_idx,
                    "type": "tool_call",
                    "tool": tool_name,
                    "args": tool_args,
                    "result_ok": result.get("ok", False),
                    "result_summary": result.get("message", "")[:200],
                    "new_success": new_success,
                })

                # 构建工具响应 Part
                tool_results.append(
                    types.Part.from_function_response(
                        name=tool_name,
                        response=result,
                    )
                )

                # 如果发送了日报，说明 Agent 判断任务完成
                if tool_name == "send_daily_report":
                    stop_reason = f"Agent 发送了日报（{result.get('message', '')}）"
                    break

            # 将工具结果加入对话历史
            contents.append(
                types.Content(
                    role="user",
                    parts=tool_results,
                )
            )

            # 检查是否完成（已发日报）
            if any(fc.name == "send_daily_report" for fc in function_calls):
                break

            # 检查是否连续无进展
            if round_new_success == 0:
                consecutive_no_progress += 1
                if consecutive_no_progress >= 3:
                    stop_reason = "连续 3 轮无新增成功，任务池可能已耗尽"
                    print(f"\n  ℹ️ {stop_reason}")
                    break
            else:
                consecutive_no_progress = 0

            time.sleep(1)  # 轮次间隔，避免 API 限速

        self._save_session_log(session_log)
        print(f"\n{'=' * 60}")
        print(f"🏁 BacklinkAgent 结束: {stop_reason}")
        print(f"   总轮次: {round_count} | 本次新增成功: {total_success}")
        print("=" * 60)

        return {
            "ok": True,
            "rounds": round_count,
            "total_success": total_success,
            "stop_reason": stop_reason,
            "dry_run": self._dry_run,
        }


# =====================================================================
# 命令行入口
# =====================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="BacklinkAgent - AI 自主外链发布 Agent")
    parser.add_argument("--dry-run", action="store_true", help="只规划不实际发帖")
    parser.add_argument("--context", type=str, default="", help="给 Agent 的额外背景信息")
    args = parser.parse_args()

    agent = BacklinkAgent()
    if args.dry_run:
        # 临时开启 dry_run
        agent._dry_run = True

    result = agent.run_daily_session(today_context=args.context)
    print(f"\n运行结果: {json.dumps(result, ensure_ascii=False, indent=2)}")
