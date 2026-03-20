# 外链自动化系统实现说明

这份文档不是操作手册，而是给开发者参考的“实现逻辑说明”。  
目标是帮助另一位工程师基于同样的思路，复刻一套类似的外链自动化系统。

---

## 1. 系统目标

这套系统解决的不是“批量采集链接”本身，而是把下面几件事串成一个可持续运行的闭环：

1. 从候选外链来源中挑出当天要处理的任务
2. 根据目标推广站点和页面上下文自动生成评论内容
3. 用真实浏览器在站点上自动发帖
4. 把成功/失败结果写回表格
5. 把历史数据沉淀成可运营、可避重、可复盘的台账

它的核心思路是：

- **Google Sheets / 飞书表格负责状态与台账**
- **Gemini 负责内容理解、评论生成和翻译**
- **Playwright + 本地真实 Chrome 负责高成功率自动发帖**
- **飞书机器人负责日报通知**

---

## 2. 总体架构

### 2.1 核心模块

- [daily_run_orchestrator.py](/Users/dahuang/CascadeProjects/test/backlink-management/daily_run_orchestrator.py)
  - 日总控
  - 负责循环执行“调度 -> 发帖 -> 检查是否达到今日成功目标 -> 刷新运营总表 -> 发飞书日报”

- [daily_scheduler.py](/Users/dahuang/CascadeProjects/test/backlink-management/daily_scheduler.py)
  - 每日调度器
  - 从 Google 主表中挑选当天待执行任务
  - 结合旧历史库做发帖前去重

- [form_automation_local.py](/Users/dahuang/CascadeProjects/test/backlink-management/form_automation_local.py)
  - 发帖执行器
  - 接管本地真实 Chrome（9222 调试端口）
  - DOM / iframe 优先，Vision 兜底

- [ai_generator.py](/Users/dahuang/CascadeProjects/test/backlink-management/ai_generator.py)
  - 内容生成器
  - 负责关键词、锚文本、评论内容、评论中文翻译

- [gws_integration.py](/Users/dahuang/CascadeProjects/test/backlink-management/gws_integration.py)
  - Google Sheets 读写层
  - 负责主任务表的 schema、读写、状态回写

- [feishu_integration.py](/Users/dahuang/CascadeProjects/test/backlink-management/feishu_integration.py)
  - 飞书表格 API 层
  - 负责飞书读写、多 sheet 创建、覆盖同步

- [legacy_feishu_history.py](/Users/dahuang/CascadeProjects/test/backlink-management/legacy_feishu_history.py)
  - 旧飞书历史库适配层
  - 把旧的人工台账标准化为“历史事实库 + 全量来源库”

- [sync_reporting_workbook.py](/Users/dahuang/CascadeProjects/test/backlink-management/sync_reporting_workbook.py)
  - 中文运营总表同步器
  - 把工程主表和旧历史库转换成更适合人工查看的中文视图

- [webhook_sender.py](/Users/dahuang/CascadeProjects/test/backlink-management/webhook_sender.py)
  - 飞书机器人通知层
  - 负责发日报卡片

### 2.2 数据层分工

系统不是只有一张表，而是分成三层：

1. **工程主表**
   - 当前存在于 Google Sheets
   - 一行是一个自动化任务
   - 关注状态流转、AI 产出、执行结果

2. **旧历史库**
   - 来自你以前手工维护的飞书表
   - 只读
   - 用于发帖前避重与历史追溯

3. **中文运营总表**
   - 面向人工查看
   - 不参与调度决策
   - 只做结果呈现与台账汇总

---

## 3. 每日执行流程

### 3.1 日总控

入口是 [daily_run_orchestrator.py](/Users/dahuang/CascadeProjects/test/backlink-management/daily_run_orchestrator.py#L27)。

它的逻辑是：

1. 读取配置，拿到当天成功目标和最大轮次
2. 调用 `daily_scheduler.main()` 选出当天任务
3. 调用 `run_once()` 真正执行发帖
4. 如果当天成功数还没达到目标，就再来一轮
5. 全部轮次结束后，刷新中文运营总表
6. 最后给飞书机器人发日报

这层的价值是：

- 不把“今天挑多少任务”和“今天一定要成功多少条”混在一起
- 成功目标达成后可以提前停
- 第一轮不够时可以自动补下一批

### 3.2 调度器

[daily_scheduler.py](/Users/dahuang/CascadeProjects/test/backlink-management/daily_scheduler.py#L65) 负责从工程主表里挑候选任务。

它做的事情包括：

- 统计今天已经成功多少条
- 统计今天已经在跑多少条
- 计算还需要补多少任务
- 按 `pending > retry` 和优先级排序挑任务
- 把选中的任务标成 `in_progress`
- 写入 `Execution_Date` 和 `Daily_Batch`

在当前实现里，调度器还会做一层**历史去重**：

- 从旧飞书历史库中读取“以前明确发过的 URL”
- 用 `精确 URL + 目标站标识` 做硬去重
- 同域只记软提示，不直接拦截

这样可以避免把你已经发过的外链来源再次塞进当天任务池。

### 3.3 发帖执行器

[form_automation_local.py](/Users/dahuang/CascadeProjects/test/backlink-management/form_automation_local.py#L749) 是最核心的执行模块。

当前实现采用的是：

- **Playwright**
- **连接本地真实 Chrome**
- **通过 `http://127.0.0.1:9222` 接管浏览器**

这里没有走默认 headless 浏览器，而是刻意选用“独立的真实 Chrome + 用户登录态 + 指纹环境”，原因是：

- 纯 headless 更容易触发反作弊
- 很多评论区、登录态、iframe 交互在真实 Chrome 里更稳定
- 可以复用你已经登录的账号状态

执行策略是：

1. 优先尝试 DOM 选择器
2. DOM 找不到时尝试 iframe 深度扫描
3. 仍失败时再启用 Vision
4. 成功或失败后都写回结果

Vision 不是主链路，而是兜底链路。这样可以把模型依赖和不稳定性压到最低。

---

## 4. AI 内容生成策略

### 4.1 为什么不能只靠模板

一开始最容易做的是固定模板评论，例如：

- “Thanks for sharing”
- “Great post”

但这种内容会带来几个问题：

- 重复度高
- 与原页面内容无关
- 更像垃圾评论

所以当前做法是让模型先理解页面，再生成评论。

### 4.2 当前生成链路

[ai_generator.py](/Users/dahuang/CascadeProjects/test/backlink-management/ai_generator.py) 当前负责四类事情：

1. 生成关键词
2. 生成锚文本
3. 生成评论内容
4. 把评论翻译成中文供人工核查

实际链路已经升级成：

- 标题
- meta 摘要
- 正文摘要
- 评论区摘要（如果能抓到）
- 页面语言

然后再一起喂给 Gemini，输出：

- `keywords`
- `anchor_text`
- `comment_content`
- `comment_content_zh`

这里有一个重要原则：

- **评论内容保留目标站语言**
- **评论内容中文单独存一列**

这样既能保证发帖自然度，也方便中文人工检查。

### 4.3 链接格式策略

不是所有评论系统都支持 HTML / BBCode / Markdown。

所以系统会先做 `Link_Format` 检测，再决定怎么生成锚文本。

目前支持的类型包括：

- `html`
- `bbcode`
- `markdown`
- `plain_text`
- `plain_text_autolink`
- `url_field`
- `unknown`

检测逻辑主要来自 [website_format_detector.py](/Users/dahuang/CascadeProjects/test/backlink-management/website_format_detector.py)：

- 先看编辑器提示
- 再看历史评论 DOM
- 再决定推荐格式

---

## 5. 表格设计思路

### 5.1 为什么工程主表和运营总表要分开

自动化系统需要很多工程字段，例如：

- `Status`
- `Priority`
- `Execution_Date`
- `Daily_Batch`
- `Link_Format`
- `Has_URL_Field`
- `Has_Captcha`

这些字段对程序很重要，但对运营查看并不友好。

所以现在分成两层：

- **工程主表**
  - 程序驱动
  - 状态机导向

- **中文运营总表**
  - 人看
  - 结果导向

### 5.2 中文运营总表的 5 张 sheet

当前飞书运营总表包括：

1. `来源总表`
   - 一行一个来源 URL
   - 看这个来源当前总体状态

2. `发布记录表`
   - 一行一个当前任务记录
   - 看具体给哪个目标站发了什么评论，失败原因是什么

3. `目标站表`
   - 一行一个推广网站
   - 维护站点标识、默认锚文本、说明和启用状态

4. `历史去重表`
   - 只放旧表里明确 `n/b` 标记过的历史事实
   - 主要用于避重

5. `旧表全量来源库`
   - 放旧表里所有 `Page ascore > 10` 的来源
   - 并按根域名去重
   - 当前保留规则是：
     - 每个根域名只保留 1 条
     - 优先保留页面评分最高的那条
     - 同分时优先保留带 `n/b` 标记的那条

### 5.3 中文化规则

当前规则已经收口：

- 表头全部中文
- URL、域名、目标网站 URL 保持原值
- 评论内容保留原语言
- 评论内容中文单独一列
- 其他状态/布尔/格式/类型字段统一中文化

这个规则的好处是：

- 程序仍使用内部规范值
- 展示层保持中文可读
- 不会因为中文化而破坏链接和评论原文

---

## 6. 旧历史库接入方式

### 6.1 为什么旧表不能直接导入主表

旧表本质上是人工维护的历史台账，不是自动化任务表。

它的问题包括：

- 一个来源 URL 可能同时给多个目标站发过
- 没有当前主表这套状态机
- tab 结构是按站点分组，不是平铺任务结构
- 很多行只是候选，不代表已经真正发过

所以当前做法不是“硬导入”，而是接成**只读历史库**。

### 6.2 当前接入逻辑

[legacy_feishu_history.py](/Users/dahuang/CascadeProjects/test/backlink-management/legacy_feishu_history.py) 会做这些事：

- 读取旧飞书表
- 跳过 `汇总` 和 `Sheet54`
- 只读前几列稳定结构
- 标准化为两类数据：
  - `LegacyHistoryRecord`
  - `LegacySourceRow`

其中：

- `LegacyHistoryRecord` 表示“明确发过的事实”
- `LegacySourceRow` 表示“旧表里的全量来源候选”

当前已知映射是：

- `n` => `nanobananaimage.com`
- `b` => `bearclicker.net`

而且已经增加了筛选规则：

- **旧表中 `Page ascore <= 10` 的行全部忽略**

---

## 7. 飞书集成设计

### 7.1 为什么保留两种飞书能力

飞书在系统里承担了两种完全不同的角色：

1. **机器人通知**
   - 负责日报/战报
   - 快速提醒人

2. **飞书表格**
   - 负责台账、筛选、人工复盘
   - 更适合长期查看

所以当前系统里这两者是分开的：

- [webhook_sender.py](/Users/dahuang/CascadeProjects/test/backlink-management/webhook_sender.py)
- [feishu_integration.py](/Users/dahuang/CascadeProjects/test/backlink-management/feishu_integration.py)

### 7.2 为什么用“用户身份”写飞书

飞书开放平台既可以用应用身份，也可以用用户身份。

当前已经切到**用户身份模式**，原因是：

- 新建的表更接近“你自己创建”
- 权限体验更自然
- 后续人工编辑不容易卡权限

### 7.3 大表写入时的坑

飞书表格在覆盖写入时有两个坑：

1. 大数据量时要分块写
2. 数据量缩短时要主动清掉后面的旧残留行

当前 [feishu_integration.py](/Users/dahuang/CascadeProjects/test/backlink-management/feishu_integration.py#L383) 已经处理了这两个问题：

- 分块写入
- 自动清尾

---

## 8. 自动运行与部署方式

### 8.1 为什么选 macOS `launchd`

这个项目运行在个人桌面环境里，而且依赖：

- 本地 Chrome
- 已登录账号
- GUI 用户环境

所以没有用服务器 cron，而是用 macOS 的 `LaunchAgent`。

当前定时是：

- **每天 10:15 本地时间自动启动**

入口文件是：

- [Start_Robot.command](/Users/dahuang/CascadeProjects/test/backlink-management/Start_Robot.command)

### 8.2 运行前置条件

要稳定运行，需要满足：

- 电脑开机
- 用户已登录桌面
- Chrome 可用
- 9222 调试端口对应的机器人 Chrome 已启动
- 目标账号已登录

这类系统本质上是“桌面自动化系统”，不是纯服务端任务。

---

## 9. 关键设计决策

### 9.1 主链路选择 DOM，Vision 只做兜底

原因：

- DOM 更稳定
- 更容易调试
- 出错更可解释
- 成本更低

Vision 只在：

- DOM 失败
- iframe 扫描失败

之后才启用。

### 9.2 目标不是“当天跑多少条”，而是“当天成功多少条”

当前系统不是固定跑一批就结束，而是：

- 先看当天已经成功多少
- 没达到目标就继续调度下一批

这比固定任务数更适合外链系统，因为站点成功率波动很大。

### 9.3 历史库用于避重，不用于直接驱动任务

旧表很有价值，但直接把它塞进主任务表会把语义搞乱。

更稳的方式是：

- 旧表只读
- 调度前查询
- 命中就避重

这样能最大化利用旧资产，同时不污染当前任务流。

---

## 10. 如果要复刻，建议的最小实现顺序

如果你的朋友要做一个类似系统，建议按下面顺序来，而不是一开始就做全功能：

### 第一步：先做最小闭环

先只做：

- Google Sheets 主表
- Playwright 发帖
- 成功/失败回写

不要一开始就做：

- Vision
- 飞书表格
- 旧历史库
- 自动日报

### 第二步：加 AI 生成

当最小发帖流程稳定后，再接：

- 关键词
- 锚文本
- 评论生成
- 评论翻译

### 第三步：加格式检测和去重

先补：

- `Link_Format`
- 基础历史去重

再补：

- 评论区上下文
- 旧历史库标准化

### 第四步：最后再做运营视图

不要把“程序跑的表”和“人看的表”混成一张。

更稳的是：

- 程序先把底层表跑稳
- 最后用同步器生成一层运营视图

---

## 11. 当前系统仍存在的可优化点

即使现在已经能跑，这套系统仍然有一些典型待优化点：

- Vision 最后一跳点击焦点仍有失败率
- 有些站点需要登录或验证码，成功率天然受限
- 飞书大表同步在数据量继续扩大后仍需要继续做分层和分页策略
- Chrome 自动化依赖本地 GUI 环境，不适合完全无人值守的服务器部署

所以复制这套系统时，建议把它理解为：

- **一个可实用的桌面自动化 SEO 工具**
- 而不是一个完全无人工参与、无环境依赖的云端任务系统

---

## 12. 一句话总结

这套系统的本质不是“写一个自动评论脚本”，而是：

**用表格做状态层，用 AI 做内容层，用真实浏览器做执行层，用历史库做避重层，用飞书做运营展示层。**

如果复刻时也按这个分层去做，系统会比“所有逻辑堆在一个脚本里”的方案稳很多，也更容易长期维护。
