import json
import requests
import logging
import os

logger = logging.getLogger(__name__)

class WebhookSender:
    def __init__(self, webhook_url: str):
        """初始化 Webhook 发送器"""
        self.webhook_url = webhook_url

    def send_summary(self, title: str, summary: dict) -> bool:
        """发送汇总消息卡片"""
        try:
            # 组装卡片的核心元素
            card = {
                "config": {"wide_screen_mode": True},
                "header": {
                    "title": {"tag": "plain_text", "content": title},
                    "template": "blue"  # 卡片头部颜色
                },
                "elements": [
                    {
                        "tag": "div",
                        "fields": [
                            {
                                "is_short": True,
                                "text": {"tag": "lark_md", "content": f"**🤖 成功任务：**\n{summary.get('success', 0)} 条"}
                            },
                            {
                                "is_short": True,
                                "text": {"tag": "lark_md", "content": f"**❌ 失败任务：**\n{summary.get('failed', 0)} 条"}
                            }
                        ]
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
