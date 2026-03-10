# AI 驱动的外链自动发布与管理系统 (Backlink Management System)

本项目旨在通过 AI 内容生成 (Gemini) 和本地浏览器自动化操作 (Playwright) 以及 Google Sheets 协同，搭建一套半/全自动化的 SEO 外链分发管理系统。

---

## 🚀 核心架构与运行流程

整个爬虫分发流水线实现了 **每日自动调度 -> AI 自动撰写话术 -> 接管带指纹真实浏览器自动跟帖 -> 结果核验回传 Google 表 -> 飞书战报送达** 的全闭环链路。

1. **`daily_scheduler.py` (每日配额调度)**：
   它每天启动时会读取你的 Google Sheets，挑选 5-10 条尚未发布（`pending`）且优先级最高的外链任务，将它们打上今日的专属标签和标记为 `in_progress`。
2. **`ai_generator.py` (智能大脑内容生成)**：
   借助 Gemini 大模型 API，根据你要推广的网站（默认为 Slideology）自动生成优质的 SEO Keyword，并根据该网站提取行业信息，生成长段自然真实的英文推荐帖。
3. **`form_automation_local.py` (无头变有头：真实指纹防封印分发)**：
   不再开启容易被当作爬虫的“无头（Headless）”默认浏览器，而是强行接管并在你平时一直在用的真实 **Google Chrome（开启 9222 调试端点）** 中运行！这意味着你保留了最真实的 Cookie 并在不用输入账号密码的情况下在多论坛实现发帖和规避人机验证码。该脚本尝试智能识别网页的 `textarea` 及提交按钮，实现 20%~30% 左右的盲发。
4. **`webhook_sender.py` (飞书集成播报)**：
   发布跑单结束后，不管成果或失败多少笔都会组合成精美的卡片推送到您的飞书机器人群组中。
5. **`gws_integration.py` (Google Sheets 内核)**：
   作为数据操作总控端，全权负责 Google OAuth 2 鉴权与表格单行改写。

---

## 🛠️ 如何每天一键启动？

这是专门为了非技术人员（例如初中生或日常运营者）设计的**极简运行模式**。每天上班时只需要按下面的步骤操作即可，全宇宙最简单：

1. 打开 Finder 进入该系统所在的文件夹： `/Users/dahuang/CascadeProjects/test/backlink-management`
2. 找到名为 `Start_Robot.command` 的文件，**直接双击运行它**！
3. 它会自动帮你弹出属于你的真实 Chrome浏览器，并启动后台的所有 Python 脚本。
4. 你可以切出去干别的活儿了（发外链的过程在后台全自动模拟人类进行），直到收到飞书的报告卡片响起，告诉你今天赚到了多少条免费外链！

---

## 🔧 给黑客/开发者的配置提示

如果您是从另一台电脑重新拉取这个仓库，您需要配置好核心驱动密码：
1. **Google Sheets OAuth**: 请确保你配置了桌面级 Oauth 并放在了 `~/.config/gws/client_secret.json` 中，并在第一次运行时生成 `token.json`。
2. **环境变量**: 编辑项目目录下的 `.env` 文件，输入 `GEMINI_API_KEY=xxx` 以启用 AI 写稿系统。
3. **飞书机器人**: 打开项目目录下的 `config.json` 文件，粘贴你的飞书群组自定义机器人 Webhook URL。

---

## 📝 Google Sheets 列设计文档参考

*更详细的表格各列定义、条件格式化请参阅此文件：[backlink_sheet_structure.md](./backlink_sheet_structure.md)*

## ✨ 总结
系统设计中采用 **真实本地浏览器** 接管是一个对抗外链反作弊极佳的解法，结合了 **AI 造文** 让系统无需等待纯人工而实现真正的 Auto Pilot。
在由于不同网站建站千奇百怪、前端 React / Vue 技术栈隔离导致的识别失败任务会在 `Notes` 内详尽抛出原因，让运营者事后追溯更加容易。