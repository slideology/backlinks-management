# Google Sheets 外链管理表结构设计

## 表结构概述
设计一个完整的外链管理表，用于跟踪和管理外链发布流程。

## 字段定义

### 基础信息字段（从原 Excel 迁移）
1. **ID** (A列) - 唯一标识符，自动递增
2. **Type** (B列) - 外链类型：profile, blog_comment
3. **URL** (C列) - 目标网站链接
4. **Discovered_From** (D列) - 发现来源
5. **Has_Captcha** (E列) - 是否有验证码：Yes/No
6. **Link_Strategy** (F列) - 链接策略：url_field, in_content, both
7. **Link_Format** (G列) - 链接格式：html, bbcode, markdown, unknown
8. **Has_URL_Field** (H列) - 是否有URL字段：Yes/No

### 管理和跟踪字段（新增）
9. **Status** (I列) - 执行状态：
   - pending（待处理）
   - in_progress（进行中）
   - completed（已完成）
   - failed（失败）
   - skipped（跳过）

10. **Priority** (J列) - 优先级：high, medium, low
11. **Target_Website** (K列) - 我们要推广的目标网站
12. **Keywords** (L列) - 相关关键词，逗号分隔
13. **Anchor_Text** (M列) - 锚文本
14. **Comment_Content** (N列) - 评论内容
15. **Execution_Date** (O列) - 执行日期
16. **Success_URL** (P列) - 成功发布后的链接
17. **Notes** (Q列) - 备注信息
18. **Last_Updated** (R列) - 最后更新时间
19. **Daily_Batch** (S列) - 所属批次（用于控制每日发布数量）

## 数据验证规则

### Status 字段下拉选项
```
pending, in_progress, completed, failed, skipped
```

### Priority 字段下拉选项
```
high, medium, low
```

### Type 字段下拉选项
```
profile, blog_comment
```

### Link_Strategy 字段下拉选项
```
url_field, in_content, both
```

### Link_Format 字段下拉选项
```
html, bbcode, markdown, plain_text, unknown
```

## 条件格式设置

### 状态颜色标识
- **pending**: 灰色背景
- **in_progress**: 黄色背景
- **completed**: 绿色背景
- **failed**: 红色背景
- **skipped**: 橙色背景

### 优先级颜色标识
- **high**: 红色文字
- **medium**: 橙色文字
- **low**: 绿色文字

## 筛选器设置
- 所有列都启用筛选器
- 常用筛选组合：
  - 按状态筛选（查看待处理项目）
  - 按类型筛选（查看特定类型的外链）
  - 按执行日期筛选（查看特定日期的任务）

## 统计仪表板
在表格顶部添加统计信息：
- 总记录数
- 各状态数量统计
- 今日已完成数量
- 本周完成数量
- 成功率统计

## API 访问权限
- 读取权限：用于数据分析和任务获取
- 写入权限：用于状态更新和结果记录
- 共享权限：团队成员只读访问