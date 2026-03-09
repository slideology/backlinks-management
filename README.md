# 外链管理自动化系统

基于 Google Workspace CLI 的智能外链管理和自动化发布系统。

## 功能特性

- 🔍 **智能网站分析**: 自动检测目标网站的技术特征和内容格式支持
- 📊 **Google Sheets 集成**: 通过 Google Workspace CLI 实现数据集中管理
- 🎯 **关键词匹配**: 基于网站内容智能生成相关锚文本和评论
- ⏰ **定时发布**: 每日自动选择 3-5 个高质量外链目标进行发布
- 📈 **进度跟踪**: 实时状态更新和成功率统计
- 🛡️ **风险控制**: 合规性检查和反垃圾检测机制

## 安装和设置

### 1. 克隆项目
```bash
git clone https://github.com/slideology/backlinks-management.git
cd backlinks-management
```

### 2. 安装依赖
```bash
# 安装 Node.js 依赖
npm install

# 安装 Python 依赖
pip3 install beautifulsoup4 requests pandas openpyxl
```

### 3. Google Workspace CLI 认证
详细设置步骤请参考 [PROJECT_PLAN.md](PROJECT_PLAN.md#阶段-1-google-workspace-集成)

## 使用方法

### 网站格式检测
```python
from website_format_detector import WebsiteFormatDetector

detector = WebsiteFormatDetector()
result = detector.analyze_website('https://example.com')
print(result['supported_formats'])  # ['html', 'plain_text']
```

### 批量分析
```python
urls = ['https://site1.com', 'https://site2.com']
results = detector.batch_analyze(urls)
```

## 项目结构

```
├── package.json                     # Google Workspace CLI 配置
├── backlink_sheet_structure.md      # Google Sheets 表结构设计
├── website_format_detector.py       # 网站格式检测器
├── PROJECT_PLAN.md                 # 详细项目计划和进度
└── README.md                       # 项目说明文档
```

## 当前状态

- ✅ 基础环境搭建
- ✅ Google Sheets 表结构设计
- ✅ 网站格式检测器
- 🔄 Google Workspace 认证配置 (待用户完成)
- ⏳ 数据迁移和后续模块开发

详细进度请查看 [PROJECT_PLAN.md](PROJECT_PLAN.md)

## 技术栈

- **Google Workspace CLI**: 数据管理和 API 访问
- **Python**: 核心逻辑和网页分析
- **BeautifulSoup4**: HTML 解析和内容提取
- **Pandas**: 数据处理和分析

## 贡献

欢迎提交 Issue 和 Pull Request 来改进这个项目。

## 许可证

MIT License