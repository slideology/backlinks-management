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

## ⏰ 每日自动运行

项目现在已经支持 **macOS `launchd` 每天 10:15 自动启动**。

- 定时任务入口：[/Users/dahuang/Library/LaunchAgents/com.backlink.robot.daily.plist](/Users/dahuang/Library/LaunchAgents/com.backlink.robot.daily.plist)
- 安装脚本：[/Users/dahuang/CascadeProjects/test/backlink-management/scripts/install_launch_agent.sh](/Users/dahuang/CascadeProjects/test/backlink-management/scripts/install_launch_agent.sh)
- 自动运行日志目录：[/Users/dahuang/CascadeProjects/test/backlink-management/logs](/Users/dahuang/CascadeProjects/test/backlink-management/logs)

当前自动运行策略是：
- 每天本地时间 **10:15** 自动执行 `Start_Robot.command`
- 自动模式下会跳过首次登录确认提示，不会卡在终端等待输入
- 调度器会按“**今天还差多少成功数**”动态补任务池，而不是只拿固定 1 条
- 发帖主程序会在“**当天成功 10 条**”后自动停止
- 每日总控结束后会自动刷新一份飞书中文运营总表，供人工查看来源、评论、目标站和历史去重信息

如果你想立刻手动触发一次，不等到第二天 10 点，可以执行：

```bash
launchctl kickstart -k gui/$(id -u)/com.backlink.robot.daily
```

首次正式自动运行前，建议先手动启动一次机器人 Chrome，确认专用发帖账号已经登录。

---

## 📘 中文运营总表

项目现在会额外维护一份**中文运营总表**，专门给人工查看，不直接参与自动化主链路。

- 工作簿链接：[外链运营总表](https://gcnbv8csilt1.feishu.cn/sheets/XvM6s6XrRhConltP1pUcaLpznDc)
- 状态文件：[/Users/dahuang/CascadeProjects/test/backlink-management/artifacts/reporting_workbook/state.json](/Users/dahuang/CascadeProjects/test/backlink-management/artifacts/reporting_workbook/state.json)
- 手动同步脚本：[/Users/dahuang/CascadeProjects/test/backlink-management/sync_reporting_workbook.py](/Users/dahuang/CascadeProjects/test/backlink-management/sync_reporting_workbook.py)

这份工作簿当前包含 5 个中文 sheet：
- `来源主表`：一行一个来源 URL，展示当前应发站点、最近成功站点、下次可推进时间和各站点展开状态
- `站点发布状态表`：一行 = 一个 `来源 URL x 目标站` 的当前状态，是新的调度事实表
- `目标站表`：维护当前推广站点、站点标识、优先级、冷却天数、每日成功目标与是否启用
- `旧表历史事实表`：把旧飞书历史库标准化成“历史已成功事实”，用于初始化新状态模型
- `旧表全量来源库`：保留旧表高分来源与原始标记，供历史核对和后续补数

展示规则已经收口为：
- 表头全部中文
- URL、域名、目标网站 URL 保留原值
- `评论内容` 保留原语言
- `评论内容中文` 单独提供中文核查
- 站点标识已统一改成域名全称，例如 `bearclicker.net`、`nanobananaimage.org`
- `slideology.com` 已从真实投放链路和运营总表中移除，仅保留在少量测试代码里作为示例

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

如需给其他开发者解释“这套系统是怎么实现出来的”，请直接参考：
[SYSTEM_IMPLEMENTATION_GUIDE.md](/Users/dahuang/CascadeProjects/test/backlink-management/SYSTEM_IMPLEMENTATION_GUIDE.md)

## ✨ 总结
系统设计中采用 **真实本地浏览器** 接管是一个对抗外链反作弊极佳的解法，结合了 **AI 造文** 让系统无需等待纯人工而实现真正的 Auto Pilot。
在由于不同网站建站千奇百怪、前端 React / Vue 技术栈隔离导致的识别失败任务会在 `Notes` 内详尽抛出原因，让运营者事后追溯更加容易。

## 🔍 当前运行特性

- 默认保留独立机器人 Chrome，而不是接管你的日常主浏览器。
- 默认关闭 Google SSO 自动登录分支，减少新窗口弹出和前台打扰。
- Vision 失败现场会保存在 `artifacts/vision/日期/` 目录，包含截图、模型原始返回和解析后的 JSON。
- 飞书侧现已拆成两层：`webhook_sender.py` 负责机器人通知，`feishu_integration.py` 负责飞书表格记录。
- `daily_scheduler.py` 已按“当天成功目标”动态补任务，而不是固定只挑极少量任务。
- `form_automation_local.py` 现在按“**当天成功 10 条即停止**”计算，不再把历史累计成功数算进当日停止条件。
- `Start_Robot.command` 已支持 `AUTO_MODE=1`，适合 `launchd`/定时任务无交互运行。

---

## 📅 2026-03-20 工作日志

### ✅ 最近完成

#### 1. 飞书升级为唯一业务事实源
- 调度与展示主链路已经切到飞书，不再依赖 Google Sheets 作为业务状态真源。
- 新增 [feishu_workbook.py](/Users/dahuang/CascadeProjects/test/backlink-management/feishu_workbook.py)、[backlink_state.py](/Users/dahuang/CascadeProjects/test/backlink-management/backlink_state.py)、[sync_reporting_workbook.py](/Users/dahuang/CascadeProjects/test/backlink-management/sync_reporting_workbook.py) 作为新的状态层与同步入口。
- 运营总表已经收口为 5 张表：`来源主表`、`站点发布状态表`、`目标站表`、`旧表历史事实表`、`旧表全量来源库`。

#### 2. 多站点推进规则正式落地
- 每个站点按 `目标站表` 的优先级顺序独立推进。
- 每站每天单独计算成功数，达到 `10` 条即当天停止。
- 同一来源在前序站点成功后，后续站点按冷却时间推进；若旧历史只有成功标记没有成功时间，也会视为可推进事实。
- 调度器会优先挑“已被其他站成功过”的来源，不够时再补新来源。

#### 3. 站点标识和目标站配置完成清理
- 站点标识已从短别名 `b/n` 改为域名全称，当前真实站点统一为：
  - `bearclicker.net`
  - `nanobananaimage.org`
- `slideology.com` 已从 `targets.json`、`目标站表`、`站点发布状态表` 中移除，不再参与真实投放。
- 旧历史迁移逻辑已兼容短别名和新域名标识，避免后续同步把旧 `b/n` 再写回来。

#### 4. `nanobananaimage.org` 执行链路关键问题已修复
- 修复了 Python 3.9 下 [form_automation_local.py](/Users/dahuang/CascadeProjects/test/backlink-management/form_automation_local.py) 的类型注解兼容问题。
- 修复了飞书富文本 `来源链接` 被当成字符串对象直接传给 `page.goto()` 的问题。
- 修复了“字符串化富文本 URL”解析失败导致的 `Protocol error` 批量假失败问题。
- 修复后，`nanobananaimage.org` 已经可以真正发出成功记录，不再是整轮统一协议错误。

#### 5. 本轮 `nanobananaimage.org` 验证结果
- 当前已验证至少 3 条当日成功记录。
- 已确认成功样本更偏向标准 HTML 评论表单页面。
- 失败样本主要集中在：
  - `Vision 未识别到评论输入框坐标`
  - `Vision 点击评论框后未能稳定输入`
  - 个别慢站 `Timeout`

### 📋 接下来待办

#### 高优先级
- [ ] 先把 `nanobananaimage.org` 当前残留的 `进行中` 任务清理为可重试状态，避免脏状态影响下一轮调度。
- [ ] 针对 `Vision 未识别到评论框` 的高频站型补规则，优先看 Wix、MIT PubPub、普通 WordPress 评论区。
- [ ] 针对 `Vision 点击后未能稳定输入` 增加“点击后焦点验证 + 二次输入 + 回退 DOM 检测”。

#### 中优先级
- [ ] 为 `Timeout` 类站点补更保守的等待和提交后验证策略，降低假失败。
- [ ] 给 `daily_scheduler.py` 增加更明确的重试回收策略，让中断后的 `进行中` 记录自动回流。
- [ ] 给失败样本按站型做标签，形成一套“先补哪个站最划算”的失败库。

#### 低优先级
- [ ] 清理测试代码里残留的 `slideology.com` 示例值，避免新同学读代码时混淆真实站点。
- [ ] 把 README 的工作日志继续压缩成阶段性里程碑，减少历史日志过长的问题。

---

## 📅 2026-03-16 工作日志

### ✅ 今日完成

#### 1. 飞书表格正式打通（应用身份 + 用户身份）
- 新增并验证了 `feishu_integration.py` 的真实写入能力，飞书表格不再只停留在机器人通知层。
- 后续已切到**用户身份创建/写入表格**，新建的飞书表格可直接作为人工核查与台账使用。
- Google Sheets 与飞书表格已完成一轮整表镜像同步。

#### 2. 表格中文化与内容列语言策略收口
- Google / 飞书表格除链接字段外，展示层基本收口为中文。
- `Keywords / Anchor_Text / Comment_Content` 不再强制中文化，而是保留目标站点语言。
- 新增 `Comment_Content_ZH` 列，用于人工核查评论内容中文翻译。

#### 3. 评论生成链升级为“页面上下文 + 评论区上下文”
- 评论生成不再只看标题，而是结合页面标题、摘要、正文摘要与评论区上下文。
- AI 生成内容现在会更贴近原页面主题，减少泛化评论。
- 历史内容已做过一轮回填，后续新写入默认带上 `Comment_Content_ZH`。

#### 4. `Link_Format` 检测重构与批量回填
- 检测逻辑已从“整页弱信号”改成“编辑器提示 + 历史评论 DOM”。
- 新增 `plain_text_autolink`，用于表示“裸 URL 会自动变成链接”的站点。
- `unknown` 默认兜底不再冒进回退到 `html`，而是走更保守的纯文本 URL 路径。

#### 5. 第二轮 `unknown` 清理规则上线
- 新增更宽松但可解释的 `html` 规则：**评论表单 + Website 字段 + 评论列表存在**。
- 补充了评论块选择器：`.comment-entry`、`.comment-container`、`.commentContainer`、`.commentList`、`.media`。
- 同时加入**硬跳过 URL 规则**，避免 profile/member/company/wiki/sound 等明显非文章页被误判。
- 本轮批量回填后，`Link_Format=unknown` 已从 `87` 降到 `60`。
- 当前剩余 `unknown` 中：
  - `56` 条是 `profile` 型页面
  - `4` 条是仍值得深挖的 `blog_comment` 页面

#### 6. 真实链路验证
- 真实浏览器跑通了至少 1 条新外链任务，Google / 飞书两边都已完成回写。
- Vision 留痕、飞书写入、中文翻译列、`Link_Format` 回填都已实测走通。

#### 7. Chrome DevTools MCP 已接入并验证
- 已安装 `chrome-devtools-mcp`，同时保留两种模式：
  - `chrome-devtools`：连接独立 `9222` 机器人 Chrome
  - `chrome-devtools-auto`：通过 `--autoConnect` 连接当前正在使用的 Chrome
- `autoConnect` 已验证可以列出现有页签、开测试页，但稳定性仍弱于 `9222` 独立浏览器模式。

### 📊 今日关键结果

| 指标 | 数据 |
|------|------|
| 第二轮 `unknown` 清理前 | 87 |
| 第二轮 `unknown` 清理后 | **60** |
| 本轮新回填 `Link_Format` | **29 行** |
| 剩余 `profile` 型 `unknown` | 56 |
| 剩余 `blog_comment` 型 `unknown` | 4 |

**剩余 4 条仍值得继续深挖的 `blog_comment unknown`**：
- `70` - https://blogs.ucl.ac.uk/brits/2014/06/01/sales-growth-curves/
- `125` - https://joaniesimon.com/94f737822923f4567e1a7ce9681e5b9a-2/
- `150` - https://cartoonresearch.com/index.php/forgotten-anime-57-kirara-2000
- `181` - https://scandasia.com/binh-duong-province-of-vietnam-attracts-more-than-4000-foreign-direct-investment-projects/

### 📋 当前待办（更新版）

#### 高优先级
- [ ] **把剩余 4 条 `blog_comment unknown` 做定点分析**：逐条看评论表单、评论列表与历史评论 DOM，尽量不要再扩一轮全局规则。
- [ ] **在调度器加入硬跳过黑名单**：对 profile/member/company/wiki/forum-support 等站型在 `daily_scheduler.py` 阶段直接跳过，减少无效 `pending -> in_progress`。
- [ ] **继续修 `Vision click_no_effect` 焦点问题**：当前 Vision 已能区分“识别不到坐标”和“点到了但没真正进入输入态”，下一步应在点击后验证焦点并补二次策略。

#### 中优先级
- [ ] **为 `Link_Format` 回填增加审计日志**：记录每次回填命中的证据类型和置信度，便于后续回看误判。
- [ ] **把 `profile` 与真实文章页再做一次类型校正**：当前表内仍有不少 `Type=profile` 但 URL 实际像文章页的历史遗留项。
- [ ] **飞书写入限流退避**：逐行写飞书时仍可能遇到限流，现阶段主要靠整表同步兜底。

#### 低优先级
- [ ] **把 `chrome-devtools-auto` 的使用说明写成操作手册**：明确它适合轻量调试，不适合长流程自动化。
- [ ] **把 README 的工作日志整理成更短的阶段性里程碑**：避免日志越来越长、后续不易维护。

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
