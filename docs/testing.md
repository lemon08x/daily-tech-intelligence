# 测试流程

`tests/` 是一条回归网，用来锁住已经修过的契约，而不是把每条产品规则都写成用例。
它不替代读日报、也不调用真实模型或外网。

删除过的用例主要是：个股打分、提示词字符串嗅探、schema 最小长度、过时 JSON 形状、以及只验证 CLI 开关存在的测试。那些要么产品已经不做，要么改文案就会误报。

## 怎么跑

在项目根目录：

```powershell
.\.venv\Scripts\python.exe -m pytest
```

只跑某一类：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_intelligence_quality.py
.\.venv\Scripts\python.exe -m pytest tests\test_normalize.py -k rank_market_news
```

全部用例使用固定响应、假 HTTP 和临时 SQLite，不需要网络、密钥或 `output/` 里的历史日报。

改源码、提示词版本、质量门阈值、来源解析或发布模板后，应先跑完整套件，再生成日报看效果。

## 文件对应什么

| 文件 | 锁住的行为 |
| --- | --- |
| `test_intelligence_quality.py` | 质量门裁剪过长输出；`unsupported_claims` 即使 `pass` 也降级；重复/伪造引文不能凑证据数；重复事实不能凑最低条数；单源置信度封顶 |
| `test_intelligence_pipeline.py` | 假模型走通 scout→analyst→verifier；同日同实验复用缓存；不同实验不共用分析；无效 JSON 重试一次后跳过；无 AI 只发线索；`--require-ai` 在缺密钥或离线时失败 |
| `test_sources_and_clustering.py` | RSS/Atom/arXiv/sitemap/HF Daily Papers 解析；`publisher_id` 与 collector 分离；同类标题 72 小时内合并；同一 GitHub 项目跨周刊/Release 合并；周刊 Markdown 抽链；短链还原；nightly 噪音丢弃；全文失败时保留摘要 |
| `test_settings_and_publication.py` | 配置路径与来源数量；重复 id / 未知 API 类型拒绝；HTML/Markdown 页签、今日速读、Git 卡片、市场事件与不展示个股扫描；同日多次运行不写日期根副本；编排器记录真实模型元数据；主题词看标题和速读句，不看埋在 key_facts 里的词 |
| `test_normalize.py` | 行情字段单位；全球指数列名；新浪 `hq.sinajs.cn` 解析；可归因新闻优先、资金流/中报/ST 简称变更丢弃；事件过少时用重要新闻填满；同花顺+新浪快讯合并去重 |
| `test_storage_and_mapping.py` | 文档幂等写入；同一事件按实验保存多份分析；公司映射没有官方公告不得标已核验 |
| `test_github_trending.py` | Trending HTML 解析与最热/最快合并；可视化宽度；前一天已发布事件第二天降权 |
| `test_http_proxy.py` | 国内站先直连再代理，海外站相反；代理失败回退直连 |

`test_scoring.py` 已删除：日报不再做个股打分，也不在报告里展示个股扫描。

## 故意不测的

- 真实 LLM 调用、真实密钥、局域网 Qwen / DeepSeek 可用性
- 真实外网抓取、代理是否在你这台机器上连通
- `output/` 历史 HTML 的像素或措辞
- 提示词全文是否包含某句中文（提示词改写不应导致测试红）
- SMTP 发信、Windows 定时任务是否已安装

这些要用 `启动日报.cmd`、`启动日报-qwen.cmd` 或 `scripts\diagnostics\probe_sources.py` 做一次真实运行来看。

## 改什么时要补测

- 质量门规则、证据要求、降级条件 → `test_intelligence_quality.py`
- 采集解析、聚类、短链、周刊、lane → `test_sources_and_clustering.py`
- 今日速读、页签、禁止出现的栏目 → `test_settings_and_publication.py`
- 市场事件过滤/填充 → `test_normalize.py`
- Git 榜单合并或次日降权 → `test_github_trending.py`

不要为了覆盖率去测打印文案、私有函数内部步骤，或把一次真实日报 JSON 检进仓库当金样。
