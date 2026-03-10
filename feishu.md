# 项目自动化运行与飞书机器人通知流程指南

本文档提炼了本项目中**“基于 GitHub Actions 的定时自动化执行”**与**“飞书机器人 Webhook 消息通知”**的完整工作流。你可以将这些流程和核心代码片段直接应用到其他需要自动化和通知的 Python 项目中。

---

## 一、流程概述

本项目的自动化及通知完整流程如下：

1. **GitHub Actions 触发**：通过 `cron` 定时任务每天自动触发，或在 GitHub 网页上手动触发。
2. **环境准备与密钥注入**：GitHub Actions 拉取代码并安装 Python 依赖。然后从 GitHub Secrets 中读取真实的飞书 Webhook URL，并将其动态写入 `config.json`，避免敏感信息硬编码。
3. **执行核心逻辑**：执行 `sitemap_analyser.py`，这部分是你需要定制的具体业务逻辑。
4. **飞书通知下发**：Python 脚本在运行结束时，调用 `webhook_sender.py` 中装载的通知类，将运行结果的数据组装成 **富文本卡片（Interactive Card）**，并通过 HTTP POST 发送到飞书机器人的 Webhook 接口。
5. **变更推送回代码库**：如果脚本执行期间生成了新的文件或修改了本地状态，GitHub Actions 会动用 Git 命令将这些变更 Commit 并 Push 到仓库中，实现状态持久化。

---

## 二、自动化运行核心代码 (GitHub Actions)

### `.github/workflows/sitemap_analysis.yml`

该文件定义了自动化工作流，主要精髓在于**定时触发机制**、**密钥注入**和**结果提交**。

```yaml
name: Daily Sitemap Analysis

on:
  schedule:
    - cron: '0 18 * * *'  # 每天UTC 18:00 运行（北京时间凌晨2:00）
  workflow_dispatch:  # 允许手动触发

# 授予写入权限，以便 Action 能提交最新结果到仓库
permissions:
  contents: write

jobs:
  analyze:
    runs-on: ubuntu-latest
    timeout-minutes: 30

    steps:
    - name: Checkout repository
      uses: actions/checkout@v4
      with:
        fetch-depth: 1 

    - name: Set up Python
      uses: actions/setup-python@v5
      with:
        python-version: '3.11'
        cache: 'pip'

    - name: Install dependencies
      run: |
        python -m pip install --upgrade pip
        pip install -r requirements.txt

    - name: Run analysis & Inject Secret
      env:
        # 从仓库的 Secrets 中读取环境变量
        FEISHU_WEBHOOK_URL: ${{ secrets.FEISHU_WEBHOOK_URL }}
      run: |
        # 将环境变量注入到本地的 config.json 中
        if [ -n "$FEISHU_WEBHOOK_URL" ]; then
          python -c "
        import json, os
        with open('config.json', 'r') as f:
            config = json.load(f)
        config['webhook']['url'] = os.environ['FEISHU_WEBHOOK_URL']
        with open('config.json', 'w') as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
          "
        fi
        # 运行核心 Python 代码
        python sitemap_analyser.py

    - name: Commit and push analysis results
      run: |
        git config --local user.email "github-actions[bot]@users.noreply.github.com"
        git config --local user.name "github-actions[bot]"
        
        # 添加在代码运行中产生的本地修改，例如 sitemaps/ 文件夹和 diff/ 文件夹
        git add sitemaps/ diff/
        
        # 拦截没有变更就 commit 会报错的问题
        git commit -m "🤖 Auto: analysis $(date +'%Y-%m-%d %H:%M UTC')" || echo "没有新变化，无需提交"
        git push origin HEAD:main || echo "推送失败或无需推送"
```

---

## 三、发送通知到飞书机器人核心代码 (Python)

向飞书自定义机器人发送消息主要利用 Webhook 机制。推荐发送**交互式卡片 (Interactive Card)**，可以使用 Markdown 语法 (`lark_md`) 来组织内容，展现效果极佳。

### `webhook_sender.py`

以下是用于发送请求的核心代码封装。

```python
import json
import requests
import logging

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
                                "text": {"tag": "lark_md", "content": f"**成功任务：**\n{summary.get('success', 0)}"}
                            },
                            {
                                "is_short": True,
                                "text": {"tag": "lark_md", "content": f"**失败任务：**\n{summary.get('failed', 0)}"}
                            }
                        ]
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
                return True
            else:
                logger.error(f"消息发送失败: {result}")
                return False
        except Exception as e:
            logger.error(f"发送消息异常: {str(e)}")
            return False

# 工厂函数：读取 config 中的链接并初始化
def create_webhook_sender(config_path: str = "config.json") -> WebhookSender:
    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)
        
    webhook_config = config.get('webhook', {})
    if not webhook_config.get('url'):
        return None
        
    return WebhookSender(webhook_config['url'])
```

### 业务代码结合点 (`sitemap_analyser.py`)

在你的主干程序中，只需按如下方式调用即可：

```python
from webhook_sender import create_webhook_sender

def main():
    # 1. 业务逻辑初始化
    webhook_sender = create_webhook_sender("config.json")
    
    # 2. 执行你的程序，收集数据
    results = do_my_business_logic()
    
    # 3. 产生摘要数据
    summary = {
        "success": 10,
        "failed": 0
    }
    
    # 4. 结尾发送通知给飞书机器人
    if webhook_sender:
        webhook_sender.send_summary("✅ 任务执行报告", summary)

if __name__ == "__main__":
    main()
```

---

## 四、应用到新项目的配置表单

当你把这套框架移植到新项目时，只需完成以下两步：

1. **飞书端配置**：
   - 找一个飞书群，在群设置 -> 群机器人 -> 添加机器人 -> 选择**自定义机器人**。
   - 复制生成的 `Webhook URL`。

2. **GitHub 端配置**：
   - 在 Github 项目页面中进入 **Settings -> Secrets and variables -> Actions**。
   - 创建一个新的 Repository secret，名字叫做 `FEISHU_WEBHOOK_URL`，值粘贴上面的那个真实 Webhook 链接。
   - （代码里有一个占位用的假 `config.json` 即可，GitHub Actions 会在每次运行时用 Secret 将它替换为真正的 URL）。
