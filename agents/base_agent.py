"""
agents/base_agent.py
=====================
Multi-Agent 系统基类 - 所有专职 Agent 的公共基础

定义：
  - Agent 间的消息传递格式（AgentMessage）
  - 公共的 Gemini 客户端创建方法
  - 标准化的工具调用执行接口

消息传递机制（Agent 间通信协议）：
  Supervisor → 子 Agent：发送任务指令（AgentMessage.type = "task"）
  子 Agent → Supervisor：返回执行结果（AgentMessage.type = "result"）
  子 Agent → 子 Agent：共享上下文数据（AgentMessage.type = "context"）
"""

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Optional

from dotenv import load_dotenv
from gemini_key_manager import get_active_key

load_dotenv()


def resolve_multi_agent_model(config_path: str, agent_key: str, default_model: str) -> str:
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        models = config.get("multi_agent", {}).get("models", {}) or {}
        model = str(models.get(agent_key, "") or "").strip()
        return model or default_model
    except Exception:
        return default_model

# =====================================================================
# Agent 间消息格式
# =====================================================================

@dataclass
class AgentMessage:
    """
    Agent 之间传递信息的标准消息格式。

    就像员工之间发备忘录：
      from_agent   - 谁发的（"supervisor" / "scheduler" / "executor" / "analyzer"）
      to_agent     - 发给谁
      type         - 消息类型：task / result / context / error
      payload      - 消息内容（任意 JSON 可序列化数据）
      timestamp    - 发送时间
    """
    from_agent: str
    to_agent: str
    type: str                                  # task | result | context | error
    payload: dict = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def task(cls, from_agent: str, to_agent: str, **payload) -> "AgentMessage":
        """快捷方法：创建任务消息"""
        return cls(from_agent=from_agent, to_agent=to_agent, type="task", payload=payload)

    @classmethod
    def result(cls, from_agent: str, to_agent: str, success: bool, **payload) -> "AgentMessage":
        """快捷方法：创建结果消息"""
        return cls(
            from_agent=from_agent, to_agent=to_agent, type="result",
            payload={"success": success, **payload}
        )

    @classmethod
    def error(cls, from_agent: str, to_agent: str, error_msg: str) -> "AgentMessage":
        """快捷方法：创建错误消息"""
        return cls(
            from_agent=from_agent, to_agent=to_agent, type="error",
            payload={"error": error_msg}
        )


# =====================================================================
# Agent 基类
# =====================================================================

class BaseAgent:
    """
    所有专职 Agent 的公共基类。

    每个 Agent 就像公司里的一个员工：
      - 有自己的名字（name）和职责说明（role_description）
      - 能接收消息（handle_message）并返回结果
      - 有自己的工具（tools）列表
      - 可以调用 Gemini AI 辅助决策
    """

    def __init__(
        self,
        name: str,
        role_description: str,
        model: str = "gemini-2.0-flash",
        config_path: str = "config.json",
        timeout_seconds: int = 30,
    ):
        self.name = name
        self.role_description = role_description
        self.model = model
        self.timeout_seconds = timeout_seconds
        self._config = self._load_config(config_path)
        self._gemini_client = None  # 懒加载，避免启动时就连接 API

    def _load_config(self, config_path: str) -> dict:
        """加载配置文件。"""
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _get_gemini_client(self):
        """懒加载 Gemini 客户端（只在真正需要时才初始化）。"""
        if self._gemini_client is None:
            from google import genai
            from google.genai import types

            api_key = get_active_key()
            if not api_key:
                raise ValueError("GEMINI_API_KEY 未配置！")
            self._gemini_client = genai.Client(
                api_key=api_key,
                http_options=types.HttpOptions(timeout=self.timeout_seconds),
            )
        return self._gemini_client

    def _call_gemini(self, prompt: str, tools: Optional[list] = None) -> Any:
        """
        调用 Gemini API 辅助决策（不含 Function Calling 循环）。
        返回第一轮响应对象，由子类自行处理。
        """
        from google.genai import types

        client = self._get_gemini_client()
        config_kwargs = {"temperature": 0.1}
        if tools:
            config_kwargs["tools"] = tools

        return client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(**config_kwargs),
        )

    def handle_message(self, message: AgentMessage) -> AgentMessage:
        """
        处理接收到的消息，返回响应消息。
        子类必须实现此方法。
        """
        raise NotImplementedError(f"Agent {self.name} 未实现 handle_message 方法！")

    def log(self, msg: str, level: str = "INFO") -> None:
        """统一的日志输出格式。"""
        prefix = {"INFO": "ℹ️", "OK": "✅", "WARN": "⚠️", "ERROR": "❌", "THINK": "🤔"}.get(level, "  ")
        print(f"  [{self.name}] {prefix} {msg}")
