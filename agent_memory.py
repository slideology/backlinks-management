"""
agent_memory.py
================
Agent 记忆模块 - 存储并学习每个站点的历史成功/失败规律

使用场景：
  - 每次发帖成功/失败后，Agent 将结果记录到本地
  - 下次调度时，Agent 读取记忆来估算各站点的最优策略和成功率
  - 让系统能"越用越聪明"，自动提高高成功率站点的优先级

存储格式（artifacts/agent_memory/site_profiles.json）：
  {
    "example.com": {
      "attempts": 10,
      "successes": 7,
      "best_strategy": "dom",         # dom / vision / sso
      "avg_time_seconds": 12.5,
      "blacklisted": false,
      "blacklist_reason": "",
      "last_updated": "2026-03-27T12:00:00"
    }
  }
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

# 记忆文件默认存储位置
DEFAULT_MEMORY_DIR = "artifacts/agent_memory"
DEFAULT_MEMORY_FILE = "site_profiles.json"

# 一个站点最多记录多少次历史（防止文件无限增大）
MAX_HISTORY_PER_SITE = 100
DEFAULT_DOMAIN_COOLDOWN_HOURS = 12
DEFAULT_TEMP_BLACKLIST_HOURS = 72


def _parse_iso_dt(value: str) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _extract_domain(url: str) -> str:
    """从 URL 中提取根域名，作为站点的唯一 Key。"""
    try:
        hostname = urlparse(url).hostname or ""
        # 去掉 www. 前缀
        if hostname.startswith("www."):
            hostname = hostname[4:]
        return hostname.lower()
    except Exception:
        return str(url or "")[:60]


def normalize_failure_category(diagnostic_category: str = "", reason: str = "") -> str:
    """
    将执行层的细碎错误统一收敛为少数治理分类，便于调度止损。
    """
    raw_category = str(diagnostic_category or "").strip().lower()
    raw_reason = str(reason or "").strip().lower()
    text = f"{raw_category} | {raw_reason}"

    if any(marker in text for marker in ("cloudflare", "captcha", "challenge", "login", "auth", "protected", "登录墙", "验证码")):
        return "page_protected"
    if any(marker in text for marker in ("comments are closed", "评论已关闭", "不允许评论", "无评论区", "no comment", "comment_unavailable", "hard_blocker")):
        return "comment_unavailable"
    if any(marker in text for marker in ("textarea_not_found", "click_no_effect", "dom_not_found", "editor", "iframe", "没有找到可以点击的提交按钮")):
        return "editor_complex"
    if any(marker in text for marker in ("vision", "handshake", "熔断", "vision_api_error", "vision_temporarily_paused")):
        return "vision_unavailable"
    if any(marker in text for marker in ("post_verify_failed", "submit_unconfirmed", "未出现明确成功信号", "post_unverified")):
        return "post_unverified"
    if any(marker in text for marker in ("timeout", "err_name_not_resolved", "err_connection_refused", "net::err", "network", "连接")):
        return "network_timeout"
    return "other_failure"


class AgentMemory:
    """
    Agent 记忆管理器。

    用法：
      memory = AgentMemory()
      memory.record_result("https://example.com/blog", success=True, strategy="dom", elapsed_seconds=8.5)
      profile = memory.get_site_profile("https://example.com/blog")
      print(profile["success_rate"])  # 0.7
    """

    def __init__(self, memory_dir: str = DEFAULT_MEMORY_DIR):
        self._memory_dir = Path(memory_dir)
        self._memory_file = self._memory_dir / DEFAULT_MEMORY_FILE
        self._profiles: dict = {}
        self._load()

    # ------------------------------------------------------------------
    # 内部读写
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """从磁盘读取记忆文件，失败时静默初始化为空。"""
        try:
            if self._memory_file.exists():
                content = self._memory_file.read_text(encoding="utf-8")
                self._profiles = json.loads(content) or {}
        except Exception:
            self._profiles = {}

    def _save(self) -> None:
        """将当前记忆写入磁盘。"""
        try:
            self._memory_dir.mkdir(parents=True, exist_ok=True)
            self._memory_file.write_text(
                json.dumps(self._profiles, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            print(f"  ⚠️ AgentMemory 写入失败: {exc}")

    def _get_or_create(self, domain: str) -> dict:
        """获取或初始化某个域名的记忆档案。"""
        if domain not in self._profiles:
            self._profiles[domain] = {
                "attempts": 0,
                "successes": 0,
                "best_strategy": "dom",          # 历史最常用成功策略
                "strategy_stats": {},             # {策略名: {attempts, successes}}
                "avg_time_seconds": 0.0,
                "blacklisted": False,
                "blacklist_reason": "",
                "last_updated": "",
                "consecutive_failures": 0,        # 连续失败次数（过高则自动暂缓）
                "temporary_blacklisted_until": "",
                "temporary_blacklist_reason": "",
                "cooldown_until": "",
                "cooldown_reason": "",
                "recent_failure_category": "",
                "recent_failure_reason": "",
                "last_execution_mode": "",
                "last_strategy": "dom",
            }
        return self._profiles[domain]

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------

    def record_result(
        self,
        url: str,
        success: bool,
        strategy: str = "dom",
        elapsed_seconds: float = 0.0,
        failure_reason: str = "",
        failure_category: str = "",
        execution_mode: str = "",
    ) -> None:
        """
        记录一次发帖结果。

        参数：
          url            - 目标站点 URL
          success        - 是否成功
          strategy       - 使用的策略（dom / vision / sso）
          elapsed_seconds - 本次耗时（秒）
          failure_reason - 失败原因（失败时填写）
        """
        domain = _extract_domain(url)
        profile = self._get_or_create(domain)

        profile["attempts"] = min(profile["attempts"] + 1, MAX_HISTORY_PER_SITE)
        if success:
            profile["successes"] = min(profile["successes"] + 1, MAX_HISTORY_PER_SITE)
            profile["consecutive_failures"] = 0
            profile["recent_failure_category"] = ""
            profile["recent_failure_reason"] = ""
            profile["cooldown_until"] = ""
            profile["cooldown_reason"] = ""
        else:
            profile["consecutive_failures"] = profile.get("consecutive_failures", 0) + 1
            profile["recent_failure_category"] = failure_category or normalize_failure_category(
                reason=failure_reason
            )
            profile["recent_failure_reason"] = failure_reason or ""

        # 更新策略统计
        stats = profile.setdefault("strategy_stats", {})
        stat = stats.setdefault(strategy, {"attempts": 0, "successes": 0})
        stat["attempts"] += 1
        if success:
            stat["successes"] += 1

        # 更新最优策略：取成功率最高的那个
        best_strategy = strategy
        best_rate = 0.0
        for s, st in stats.items():
            rate = st["successes"] / st["attempts"] if st["attempts"] > 0 else 0
            if rate > best_rate:
                best_rate = rate
                best_strategy = s
        profile["best_strategy"] = best_strategy
        profile["last_strategy"] = strategy or profile.get("last_strategy", "dom")
        profile["last_execution_mode"] = execution_mode or profile.get("last_execution_mode", "")

        # 更新平均耗时（加权平均）
        old_avg = float(profile.get("avg_time_seconds") or 0)
        n = profile["attempts"]
        profile["avg_time_seconds"] = round((old_avg * (n - 1) + elapsed_seconds) / n, 1)

        profile["last_updated"] = datetime.now().isoformat(timespec="seconds")
        self._save()

    def mark_blacklist(self, url: str, reason: str = "") -> None:
        """将某个站点标记为长期黑名单（评论关闭、reCAPTCHA 等）。"""
        domain = _extract_domain(url)
        profile = self._get_or_create(domain)
        profile["blacklisted"] = True
        profile["blacklist_reason"] = reason or "已被 Agent 决策标记为黑名单"
        profile["last_updated"] = datetime.now().isoformat(timespec="seconds")
        self._save()
        print(f"  🚫 已将 {domain} 标记为黑名单：{reason}")

    def mark_temporary_blacklist(
        self,
        url: str,
        reason: str = "",
        hours: int = DEFAULT_TEMP_BLACKLIST_HOURS,
    ) -> None:
        domain = _extract_domain(url)
        profile = self._get_or_create(domain)
        until = datetime.now() + timedelta(hours=max(1, int(hours or DEFAULT_TEMP_BLACKLIST_HOURS)))
        profile["temporary_blacklisted_until"] = until.isoformat(timespec="seconds")
        profile["temporary_blacklist_reason"] = reason or "临时黑名单"
        profile["last_updated"] = datetime.now().isoformat(timespec="seconds")
        self._save()

    def set_cooldown(
        self,
        url: str,
        reason: str = "",
        hours: int = DEFAULT_DOMAIN_COOLDOWN_HOURS,
    ) -> None:
        domain = _extract_domain(url)
        profile = self._get_or_create(domain)
        until = datetime.now() + timedelta(hours=max(1, int(hours or DEFAULT_DOMAIN_COOLDOWN_HOURS)))
        profile["cooldown_until"] = until.isoformat(timespec="seconds")
        profile["cooldown_reason"] = reason or "域名冷却"
        profile["last_updated"] = datetime.now().isoformat(timespec="seconds")
        self._save()

    def is_in_cooldown(self, url: str, now: Optional[datetime] = None) -> bool:
        domain = _extract_domain(url)
        profile = self._profiles.get(domain, {})
        cooldown_until = _parse_iso_dt(profile.get("cooldown_until", ""))
        current = now or datetime.now()
        return bool(cooldown_until and cooldown_until > current)

    def get_cooldown_until(self, url: str) -> str:
        domain = _extract_domain(url)
        return str(self._profiles.get(domain, {}).get("cooldown_until", "") or "")

    def is_temporarily_blacklisted(self, url: str, now: Optional[datetime] = None) -> bool:
        domain = _extract_domain(url)
        profile = self._profiles.get(domain, {})
        until = _parse_iso_dt(profile.get("temporary_blacklisted_until", ""))
        current = now or datetime.now()
        return bool(until and until > current)

    def is_blacklisted(self, url: str) -> bool:
        """判断某个站点是否在黑名单中。"""
        domain = _extract_domain(url)
        return self._profiles.get(domain, {}).get("blacklisted", False)

    def get_site_profile(self, url: str) -> dict:
        """
        获取某站点的完整记忆档案，并附加计算字段。

        返回示例：
          {
            "domain": "example.com",
            "attempts": 10,
            "successes": 7,
            "success_rate": 0.7,       # ← 计算得出
            "best_strategy": "dom",
            "is_worth_trying": True,   # ← 成功率 > 0.3 且不在黑名单
            ...
          }
        """
        domain = _extract_domain(url)
        # 注意：使用 _get_or_create 确保全新站点也有完整的默认字段
        # 但不保存（只希望 record_result 来触发保存）
        raw = dict(self._profiles.get(domain) or self._get_or_create(domain))
        attempts = raw.get("attempts", 0)
        successes = raw.get("successes", 0)
        raw["domain"] = domain
        raw["success_rate"] = round(successes / attempts, 2) if attempts > 0 else 0.5  # 默认中性
        raw["in_cooldown"] = self.is_in_cooldown(url)
        raw["temporarily_blacklisted"] = self.is_temporarily_blacklisted(url)
        raw["is_worth_trying"] = (
            not raw.get("blacklisted", False)
            and not raw.get("temporarily_blacklisted", False)
            and raw["success_rate"] >= 0.2          # 至少 20% 成功率才值得继续尝试
            and raw.get("consecutive_failures", 0) < 5  # 不能连续失败太多次
        )
        return raw

    def get_recommended_strategy(self, url: str) -> str:
        """
        根据历史记录推荐最佳策略。
        没有历史记录的新站点默认返回 'dom'（最保守）。
        """
        profile = self.get_site_profile(url)
        return profile.get("best_strategy") or "dom"

    def get_stats_summary(self) -> dict:
        """获取整体记忆统计摘要（供 Agent 日报使用）。"""
        total_sites = len(self._profiles)
        total_attempts = sum(p.get("attempts", 0) for p in self._profiles.values())
        total_successes = sum(p.get("successes", 0) for p in self._profiles.values())
        blacklisted_count = sum(1 for p in self._profiles.values() if p.get("blacklisted"))
        return {
            "total_sites_tracked": total_sites,
            "total_attempts": total_attempts,
            "total_successes": total_successes,
            "overall_success_rate": round(total_successes / total_attempts, 2) if total_attempts > 0 else 0,
            "blacklisted_sites": blacklisted_count,
        }

    def export_context_for_agent(self, urls: list) -> str:
        """
        将多个 URL 的记忆信息导出为自然语言摘要，
        用于注入到 Agent 的 System Prompt 中。
        """
        lines = ["【站点历史记忆】"]
        for url in urls[:20]:  # 最多传 20 个，避免 prompt 过长
            domain = _extract_domain(url)
            profile = self.get_site_profile(url)
            if profile.get("blacklisted"):
                lines.append(f"- {domain}: ⛔ 黑名单（{profile.get('blacklist_reason', '')}）")
            elif profile.get("attempts", 0) == 0:
                lines.append(f"- {domain}: 🆕 新站点，无历史数据，建议先用 DOM 方式尝试")
            else:
                rate = profile.get("success_rate", 0)
                best = profile.get("best_strategy", "dom")
                avg_t = profile.get("avg_time_seconds", 0)
                lines.append(
                    f"- {domain}: 成功率 {rate:.0%}，最佳策略={best}，平均耗时={avg_t}s"
                )
        return "\n".join(lines)
