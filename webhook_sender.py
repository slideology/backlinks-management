import json
import requests
import logging
import os

logger = logging.getLogger(__name__)

class WebhookSender:
    def __init__(self, webhook_url: str):
        """初始化 Webhook 发送器"""
        self.webhook_url = webhook_url

    def send_summary_report(self, title: str, summary: dict) -> bool:
        """发送精简汇总消息卡片，只保留站点级别结果。"""
        try:
            site_rows = summary.get("sites", [])
            stop_reason = str(summary.get("stop_reason", "") or "")
            total_success = int(summary.get("total_success", 0) or 0)
            total_failed = int(summary.get("total_failed", 0) or 0)

            md_lines = [
                f"**总新增成功**: {total_success}",
                f"**总失败尝试**: {total_failed}",
            ]
            if stop_reason:
                md_lines.append(f"**结束原因**: {stop_reason}")
            md_lines.append("")

            if site_rows:
                md_lines.append("**站点汇总：**")
                for item in site_rows:
                    site_key = str(item.get("site_key", "") or "")
                    today_success = int(item.get("today_success", 0) or 0)
                    daily_goal = int(item.get("daily_goal", 0) or 0)
                    run_success = int(item.get("run_success", 0) or 0)
                    run_failed = int(item.get("run_failed", 0) or 0)
                    md_lines.append(
                        f"- **{site_key}**: 今日累计成功 {today_success}/{daily_goal}，本轮新增成功 {run_success}，本轮失败 {run_failed}"
                    )
            else:
                md_lines.append("今日无站点执行结果。")

            content_md = "\n".join(md_lines)
            card = {
                "config": {"wide_screen_mode": True},
                "header": {
                    "title": {"tag": "plain_text", "content": title},
                    "template": "blue",
                },
                "elements": [
                    {
                        "tag": "markdown",
                        "content": content_md,
                    },
                    {"tag": "hr"},
                    {
                        "tag": "markdown",
                        "content": "详细任务记录请直接查看飞书表格。",
                    },
                ],
            }
            payload = {
                "msg_type": "interactive",
                "card": card,
            }
            return self._send_payload(payload)
        except Exception as e:
            logger.error(f"发送汇总消息异常: {str(e)}")
            return False

    def send_detailed_report(self, title: str, summary: dict) -> bool:
        """发送详细消息卡片"""
        try:
            success_list = summary.get("success", [])
            failed_list = summary.get("failed", [])
            
            # 构建富文本Markdown
            md_lines = []
            
            if success_list:
                md_lines.append(f"**✅ 成功任务 ({len(success_list)} 条)：**")
                for i, item in enumerate(success_list, 1):
                    md_lines.append(f"{i}. **网址**: {item.get('url', '')}")
                    md_lines.append(f"   **评论格式**: {item.get('format', '')}")
                    if item.get('used_vision'):
                        md_lines.append("   **执行方式**: Vision 兜底")
                    if item.get('feishu_row'):
                        md_lines.append(f"   **飞书记录**: 第 {item.get('feishu_row')} 行")
                    # 新增：显示成功的具体判定详情（例如是否在审核）
                    reason = item.get('reason', '')
                    if reason:
                        md_lines.append(f"   **结果详情**: {reason}")
                md_lines.append("")
                
            if failed_list:
                md_lines.append(f"**❌ 失败任务 ({len(failed_list)} 条)：**")
                for i, item in enumerate(failed_list, 1):
                    md_lines.append(f"{i}. **网址**: {item.get('url', '')}")
                    category = item.get('diagnostic_category', '')
                    if category:
                        md_lines.append(f"   **失败分类**: {category}")
                    if item.get('feishu_row'):
                        md_lines.append(f"   **飞书记录**: 第 {item.get('feishu_row')} 行")
                    # 取原因中开头的部分避免过长
                    reason = item.get('reason', '')
                    if len(reason) > 80:
                        reason = reason[:80] + "..."
                    md_lines.append(f"   **失败原因**: {reason}")
                md_lines.append("")
                
            if not success_list and not failed_list:
                md_lines.append("今日无发帖记录。")
                
            content_md = "\n".join(md_lines)
            
            # 组装卡片的核心元素
            card = {
                "config": {"wide_screen_mode": True},
                "header": {
                    "title": {"tag": "plain_text", "content": title},
                    "template": "blue"  # 卡片头部颜色
                },
                "elements": [
                    {
                        "tag": "markdown",
                        "content": content_md
                    },
                    {
                        "tag": "hr"
                    },
                    {
                        "tag": "markdown",
                        "content": "✨ 今日的外链发布列车已抵达终点，请进入 Google Sheets 查看详情记录！"
                    }
                ]
            }

            payload = {
                "msg_type": "interactive",  # 交互式卡片消息类型
                "card": card
            }

            return self._send_payload(payload)

        except Exception as e:
            logger.error(f"发送消息异常: {str(e)}")
            return False

    def _send_payload(self, payload: dict) -> bool:
        """底层方法：发送消息到飞书 Webhook"""
        try:
            response = requests.post(
                self.webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=10
            )
            response.raise_for_status()
            result = response.json()

            # 飞书接口成功状态码有 0 的情况
            if result.get("StatusCode") == 0 or result.get("code") == 0:
                logger.info("消息发送成功")
                print("\n🔔 飞书机器人通知已成功发送！")
                return True
            else:
                logger.error(f"消息发送失败: {result}")
                print(f"\n❌ 飞书通知发送失败: {result}")
                return False
        except Exception as e:
            logger.error(f"发送消息异常: {str(e)}")
            print(f"\n❌ 发送飞书消息时发生异常: {str(e)}")
            return False

from typing import Optional

# 工厂函数：优先读环境变量，其次读 config.json 中的链接
def create_webhook_sender(config_path: str = "config.json") -> Optional[WebhookSender]:
    webhook_url = os.environ.get("FEISHU_WEBHOOK_URL")
    
    if not webhook_url:
        try:
            if os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                webhook_url = config.get('webhook', {}).get('url')
        except Exception as e:
            logger.warning(f"读取 config.json 失败: {e}")
            
    if not webhook_url:
        return None
        
    return WebhookSender(webhook_url)
