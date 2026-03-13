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
   不再开启容易被当作爬虫的“无头（Headless）”默认浏览器，而是接管独立的机器人 **Google Chrome（开启 9222 调试端点）** 中运行。默认不会主动抢前台，也不会显式调用 `bring_to_front()`；但如果站点自身触发登录弹窗或系统切换窗口，机器人 Chrome 仍可能偶发被激活。该脚本会优先走 DOM/iframe 自动化，只有在失败后才启用 Vision 兜底。
4. **`webhook_sender.py` (飞书集成播报)**：
   发布跑单结束后，不管成果或失败多少笔都会组合成精美的卡片推送到您的飞书机器人群组中。
5. **`feishu_integration.py` (飞书表格写入)**：
   在保留飞书群机器人通知的同时，新增飞书开放平台应用集成，可将每条任务执行记录同步写入飞书表格，便于后续做台账和筛选。
6. **`gws_integration.py` (Google Sheets 内核)**：
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
4. **飞书表格（可选）**: 若要同步写飞书表格，请在 `config.json` 中补齐 `feishu.enabled/app_id/app_secret/spreadsheet_token/sheet_id`。

---

## 📝 Google Sheets 列设计文档参考

*更详细的表格各列定义、条件格式化请参阅此文件：[backlink_sheet_structure.md](./backlink_sheet_structure.md)*

## ✨ 总结
系统设计中采用 **真实本地浏览器** 接管是一个对抗外链反作弊极佳的解法，结合了 **AI 造文** 让系统无需等待纯人工而实现真正的 Auto Pilot。
在由于不同网站建站千奇百怪、前端 React / Vue 技术栈隔离导致的识别失败任务会在 `Notes` 内详尽抛出原因，让运营者事后追溯更加容易。

## 🔍 当前运行特性

- 默认保留独立机器人 Chrome，而不是接管你的日常主浏览器。
- 默认关闭 Google SSO 自动登录分支，减少新窗口弹出和前台打扰。
- Vision 失败现场会保存在 `artifacts/vision/日期/` 目录，包含截图、模型原始返回和解析后的 JSON。
- 飞书侧现已拆成两层：`webhook_sender.py` 负责机器人通知，`feishu_integration.py` 负责飞书表格记录。

---

## 📅 2026-03-13 工作日志

### ✅ 今日完成的功能增强

#### 1. 深度滚动优化（`_deep_scroll_to_bottom`）
- **问题**：评论在页面最底部（通常有 100+ 条旧评论压着），评论框因为懒加载而未渲染
- **解法**：新增分段深滚动函数，每隔 1.5 秒滚动一屏，最多滚 5 屏。发现评论框立即提前停止
- **效果**：所有多评论长页面的评论框识别成功率显著提升

#### 2. Blogger 匿名发布适配（`_handle_blogger_identity`）
- **问题**：Blogger 站点在评论框 iframe 内有身份选择下拉菜单（须选"匿名"或"名称/网址"），未选则提交无效
- **解法**：新增函数专门识别 `#identityMenu`，优先选"匿名"，若只有"名称/网址"则填入 Bear Clicker 与网址
- **状态**：Blogger 评论区嵌套在导航栏 iframe 而非评论 iframe 中，仍有一定概率穿透失败（待后续优化）

#### 3. 递归 iframe 深度扫描（`scan_frames`）
- **问题**：原先只遍历第一层 iframe，遇到双层嵌套（如 Blogger）直接放弃
- **解法**：将 iframe 遍历改为递归函数，自动跳过广告 iframe（youtube/doubleclick 等），深度扫描所有子 frame

#### 4. 多语言成功审核词库扩充（`_verify_post_success`）
- **新词**：增加西班牙语 `moderación`、`pendiente`、`su comentario ha sido` 等
- **效果**：西班牙语博客（deusto.es）立即识别成功

#### 5. 失败自动诊断系统（`_diagnose_site_status`）
- 所有失败任务在退出前自动分析页面文本，输出：登录墙 / 评论关闭 / 附件页 / 未知 等具体原因
- 诊断结果写入 Google Sheets `Notes` 列并同步飞书通知

#### 6. 主程序累计目标停止机制（`SUCCESS_GOAL`）
- 在 `main()` 中新增全局成功计数，读取历史完成数叠加本批成功数
- 达到目标（默认 10 个）后自动停止并发送汇总飞书通知
- 进度实时打印：`>>> 进度: 2/5 | 当前成功: 6/10`

#### 7. 最多重试次数从 2 次降至 1 次
- 避免同一站点反复重跑产生"死循环"的体感

---

### 📊 今日发布成果

| 指标 | 数据 |
|------|------|
| 总测试站点 | ~25 个 |
| 累计成功 ✅ | **10 个** |
| 失败 ❌ | ~15 个（约 60%，大部分因需登录或 reCAPTCHA） |

**10 个成功外链的目标站点**：
- idli-kurma 测试站、pixel77.com
- bakerella.com（美食博客）
- deusto.es（西班牙语博客）
- nasze-lasie.pl（波兰语社区）
- holdtoreset.com（GTA 攻略站）
- learnalanguage.com（语言学习博客）
- 以及 3 个评论进入审核队列（moderate）的博客

**常见失败原因汇总**：
| 原因 | 典型站点 |
|------|---------|
| 需要登录账号 | myanimelist.net、mastodon.social、chandigarhcity.com |
| reCAPTCHA 保护 | madrimasd.org（ID 20） |
| Blogger 特殊 iframe | blog.metastock.com（ID 22）|
| 广告弹窗遮挡评论框 | greenerideal.com |
| 页面加载超时 | beautythroughimperfection.com、manilashopper.com |
| 页面无评论区（登录页/用户主页） | pharmahub.org、myanimelist 用户页 |

---

### 📋 待办清单 (TODO)

#### 高优先级
- [ ] **Blogger 评论 iframe 精准穿透**：脚本目前命中 Blogger 导航栏 iframe 而非评论 iframe（URL 含 `/comment/fullpage/`），需在 scan_frames 里优先匹配带 `comment` 关键词的 iframe
- [ ] **广告弹窗智能关闭**：greenerideal.com 类广告密集站出现弹窗后，Vision AI 虽然检测到关闭按钮坐标，但关闭后未二次尝试 DOM 找评论框，应补充"关闭弹窗后重新扫描"逻辑
- [ ] **站点预过滤黑名单机制**：对已知无法自动发贴的站点类型（大型社区/登录墙/Blogger），在 daily_scheduler 阶段直接跳过，不纳入 in_progress 队列，提升整体批次成功率

#### 中优先级
- [ ] **超时页面重试策略优化**：beautythroughimperfection.com 类国际站加载超时，可考虑先设置代理或降低图片加载（`--blink-settings=imagesEnabled=false`）
- [ ] **自动诊断词库持续扩充**：目前诊断语言覆盖英/中/西班牙语，后续可加入法语、德语、意大利语等关键词
- [ ] **飞书报告加入成功率趋势**：在飞书卡片中追加最近 7 天成功率折线图或统计摘要

#### 低优先级
- [ ] **daily_scheduler.py 配额策略升级**：目前手动选取 5 条，需与目标停止机制打通，自动计算还需多少条并按差额准备任务
- [ ] **README 自动更新**：每次批量发布后自动追加工作日志到 README（用脚本实现）
